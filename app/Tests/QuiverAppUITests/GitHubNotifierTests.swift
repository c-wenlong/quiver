import Foundation
import Testing
@testable import QuiverApp
@testable import QuiverCore

/// `GitHubNotifier` is the GitHub control plane: an app-lifetime inbox
/// poll that seeds its delivered-id set on first contact, banners only
/// new unread ids, persists that set under `~/.quiver/cache/`, and
/// invalidates a notified repo's boards. `post` is the seam — tests
/// capture the banner batches instead of hitting
/// `UNUserNotificationCenter`.
@Suite(.serialized)
@MainActor
struct GitHubNotifierTests {
    /// argv-suffix router, same pattern as the core `GitHubModelTests`:
    /// values are envelope payloads wrapped into `{"schema":1,"data":…}`.
    final class Router: CommandRunning, @unchecked Sendable {
        var replies: [String: CommandResult] = [:]
        private(set) var calls: [[String]] = []
        init(_ replies: [String: String]) {
            for (key, payload) in replies { respond(key, payload) }
        }
        func respond(_ suffix: String, _ payload: String) {
            replies[suffix] = .init(status: 0, stdout: Data(
                "{\"schema\":1,\"data\":{\(payload)}}".utf8), stderr: "")
        }
        func run(_ arguments: [String]) async throws -> CommandResult {
            calls.append(arguments)
            for (suffix, reply) in replies where arguments.joined(separator: " ").hasSuffix(suffix) {
                return reply
            }
            return .init(status: 1, stdout: Data(), stderr: "no route")
        }
        func run(_ arguments: [String], input: Data?) async throws -> CommandResult {
            try await run(arguments)
        }
    }

    /// The captured banner batches; a class box so the `post` closure can
    /// mutate it from a nonisolated context.
    final class Posts: @unchecked Sendable {
        var batches: [[GhNotification]] = []
    }

    private let env = "\"github\":{\"status\":\"ok\",\"login\":\"me\",\"error\":null}"
    private let inboxKey = "gh inbox --limit=50 --all --json"

    private func note(
        _ id: String, repo: String = "o/r", unread: Bool = true,
        type: String = "PullRequest", reason: String = "review_requested"
    ) -> String {
        "{\"id\":\"\(id)\",\"repo\":\"\(repo)\",\"title\":\"t\(id)\",\"type\":\"\(type)\","
            + "\"reason\":\"\(reason)\",\"unread\":\(unread),"
            + "\"updated_at\":\"2026-10-20T00:00:00Z\","
            + "\"url\":\"https://github.com/\(repo)/pull/1\"}"
    }

    private func inbox(_ notes: [String]) -> String {
        "\(env),\"status\":\"ok\",\"notifications\":[\(notes.joined(separator: ","))]"
    }

    private func quiverDir() -> URL {
        FileManager.default.temporaryDirectory
            .appending(path: "quiver-notifier-tests-\(UUID().uuidString)")
    }

    private func deliveredIds(_ quiver: URL) throws -> [String] {
        struct State: Decodable { var delivered: [String] }
        return try JSONDecoder().decode(State.self, from: Data(
            contentsOf: quiver.appending(path: "cache/github-notified.json"))).delivered
    }

    /// Wait for a condition with a bound — `invalidate` refreshes the
    /// selected repo from a spawned Task.
    private func until(_ condition: @escaping () -> Bool) async {
        for _ in 0..<200 where !condition() {
            try? await Task.sleep(for: .milliseconds(10))
        }
    }

    /// History is not news: the very first poll only seeds the set.
    @Test func firstPollSeedsWithoutBanners() async throws {
        let dir = quiverDir()
        let posts = Posts()
        let swe = SweClient(runner: Router([inboxKey: inbox([note("a"), note("b")])]))
        let notifier = GitHubNotifier(quiver: dir, model: GitHubModel()) {
            posts.batches.append($0)
        }
        notifier.bannersEnabled = true
        await notifier.poll(swe: swe)
        #expect(posts.batches.isEmpty)
        #expect(try deliveredIds(dir).sorted() == ["a", "b"])
    }

    /// A notification id banners once; re-polls of it do not stack.
    @Test func newUnreadNoteBannersOnceThenDedupes() async throws {
        let dir = quiverDir()
        let posts = Posts()
        let router = Router([inboxKey: inbox([note("a")])])
        let swe = SweClient(runner: router)
        let notifier = GitHubNotifier(quiver: dir, model: GitHubModel()) {
            posts.batches.append($0)
        }
        notifier.bannersEnabled = true
        await notifier.poll(swe: swe)
        router.respond(inboxKey, inbox([note("a"), note("b", repo: "o/x")]))
        await notifier.poll(swe: swe)
        #expect(posts.batches.map { $0.map(\.id) } == [["b"]])
        // The same inbox again: nothing new to say.
        await notifier.poll(swe: swe)
        #expect(posts.batches.count == 1)
    }

    /// A note that arrives already read is not actionable — no banner.
    @Test func readNotesDoNotBanner() async throws {
        let dir = quiverDir()
        let posts = Posts()
        let router = Router([inboxKey: inbox([note("a")])])
        let swe = SweClient(runner: router)
        let notifier = GitHubNotifier(quiver: dir, model: GitHubModel()) {
            posts.batches.append($0)
        }
        notifier.bannersEnabled = true
        await notifier.poll(swe: swe)
        router.respond(inboxKey, inbox([note("a"), note("b", unread: false)]))
        await notifier.poll(swe: swe)
        #expect(posts.batches.isEmpty)
        #expect(try deliveredIds(dir) == ["a"])
    }

    /// The delivered set survives a relaunch: a new instance over the
    /// same `~/.quiver` dir does not re-banner what the last run sent.
    @Test func deliveredIdsPersistAcrossInstances() async throws {
        let dir = quiverDir()
        let posts = Posts()
        let router = Router([inboxKey: inbox([note("a")])])
        let swe = SweClient(runner: router)
        let first = GitHubNotifier(quiver: dir, model: GitHubModel()) {
            posts.batches.append($0)
        }
        first.bannersEnabled = true
        await first.poll(swe: swe)
        router.respond(inboxKey, inbox([note("a"), note("b")]))
        await first.poll(swe: swe)
        #expect(posts.batches.count == 1)
        // "Relaunch": fresh instance, same state file, same inbox.
        let posts2 = Posts()
        let second = GitHubNotifier(quiver: dir, model: GitHubModel()) {
            posts2.batches.append($0)
        }
        second.bannersEnabled = true
        await second.poll(swe: swe)
        #expect(posts2.batches.isEmpty)
    }

    /// The delivered set is bounded at 500; eviction drops the oldest
    /// first, so a just-bannered id is never the first out.
    @Test func deliveredSetCapsAtBound() async throws {
        let dir = quiverDir()
        let file = dir.appending(path: "cache/github-notified.json")
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        let seeded = (0..<600).map { "n\($0)" }
        try JSONEncoder().encode(["delivered": seeded]).write(to: file)
        let posts = Posts()
        let swe = SweClient(runner: Router([inboxKey: inbox([note("fresh")])]))
        let notifier = GitHubNotifier(quiver: dir, model: GitHubModel()) {
            posts.batches.append($0)
        }
        notifier.bannersEnabled = true
        await notifier.poll(swe: swe)
        #expect(posts.batches.map { $0.map(\.id) } == [["fresh"]])
        let ids = try deliveredIds(dir)
        #expect(ids.count == 500)
        #expect(!ids.contains("n0") && ids.contains("n101") && ids.contains("fresh"))
    }

    /// An empty first poll still completes the seed: a notification
    /// that arrived while the app was closed must banner on relaunch,
    /// not be silently re-seeded because no ids were on disk.
    @Test func emptyFirstPollStillMarksPrimed() async throws {
        let dir = quiverDir()
        let posts = Posts()
        let router = Router([inboxKey: inbox([])])
        let swe = SweClient(runner: router)
        let first = GitHubNotifier(quiver: dir, model: GitHubModel()) {
            posts.batches.append($0)
        }
        first.bannersEnabled = true
        await first.poll(swe: swe)
        #expect(posts.batches.isEmpty)
        #expect(try deliveredIds(dir).isEmpty)

        // A note lands while "closed"; the relaunched instance is
        // already primed, so it banners rather than seeding it away.
        router.respond(inboxKey, inbox([note("n1")]))
        let posts2 = Posts()
        let second = GitHubNotifier(quiver: dir, model: GitHubModel()) {
            posts2.batches.append($0)
        }
        second.bannersEnabled = true
        await second.poll(swe: swe)
        #expect(posts2.batches.map { $0.map(\.id) } == [["n1"]])
    }

    /// Banners off still polls: ids are recorded and the notified repo's
    /// visible board is invalidated and refetched.
    @Test func bannersDisabledStillPollsAndInvalidates() async throws {
        let dir = quiverDir()
        let posts = Posts()
        let router = Router([
            inboxKey: inbox([note("a")]),
            "--repo=o/r --json":
                "\(env),\"results\":[{\"repo\":\"o/r\",\"status\":\"ok\",\"pull_requests\":[]}]",
        ])
        let swe = SweClient(runner: router)
        let model = GitHubModel()
        model.selected = "o/r"
        await model.loadTab(.pulls, repo: "o/r", swe: swe)
        let boardFetches = router.calls.filter {
            $0.joined(separator: " ").contains("--repo=o/r")
        }.count
        let notifier = GitHubNotifier(quiver: dir, model: model) {
            posts.batches.append($0)
        }
        notifier.bannersEnabled = false
        defer { UserDefaults.standard.removeObject(forKey: "githubNotifications") }
        await notifier.poll(swe: swe)
        router.respond(inboxKey, inbox([note("a"), note("b")]))
        await notifier.poll(swe: swe)
        #expect(posts.batches.isEmpty)
        // The notification's repo is selected: its tab refetches now.
        let fetches = { router.calls.filter {
            $0.joined(separator: " ").contains("--repo=o/r") }.count }
        await until { fetches() > boardFetches }
        #expect(fetches() == boardFetches + 1)
        #expect(try deliveredIds(dir).contains("b"))
    }

    /// The watcher loop returns when its task is cancelled rather than
    /// holding the AppModel watcher slot forever.
    @Test func keepFreshStopsOnCancel() async throws {
        let dir = quiverDir()
        let notifier = GitHubNotifier(quiver: dir, model: GitHubModel(), interval: 60) { _ in }
        let swe = SweClient(runner: Router([inboxKey: inbox([note("a")])]))
        let task = Task { await notifier.keepFresh(swe: { swe }) }
        task.cancel()
        await task.value
    }
}
