import Foundation
import Testing
@testable import SWEApp
@testable import SWECore

/// `GitHubNotifier` is the GitHub control plane: an app-lifetime inbox
/// poll that seeds its delivered-id set on first contact, banners only
/// new unread ids, persists that set under `~/.swe/cache/`, and
/// invalidates a notified repo's boards. `post` is the seam — tests
/// capture the banner batches instead of hitting
/// `UNUserNotificationCenter`.
@Suite(.serialized)
@MainActor
struct GitHubNotifierTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

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
        type: String = "PullRequest", reason: String = "review_requested",
        updated: String = "2026-10-20T00:00:00Z"
    ) -> String {
        "{\"id\":\"\(id)\",\"repo\":\"\(repo)\",\"title\":\"t\(id)\",\"type\":\"\(type)\","
            + "\"reason\":\"\(reason)\",\"unread\":\(unread),"
            + "\"updated_at\":\"\(updated)\","
            + "\"url\":\"https://github.com/\(repo)/pull/1\"}"
    }

    private func inbox(_ notes: [String]) -> String {
        "\(env),\"status\":\"ok\",\"notifications\":[\(notes.joined(separator: ","))]"
    }

    private func quiverDir() -> URL {
        scratch.url.appending(path: "quiver-\(UUID().uuidString)")
    }

    /// The thread ids in the state file, each key's `@updated_at` dropped.
    private func deliveredIds(_ quiver: URL) throws -> [String] {
        try deliveredKeys(quiver).map { String($0.prefix { $0 != "@" }) }
    }

    private func deliveredKeys(_ quiver: URL) throws -> [String] {
        struct State: Decodable { var delivered: [String] }
        return try JSONDecoder().decode(State.self, from: Data(
            contentsOf: quiver.appending(path: "cache/github-notified.json"))).delivered
    }

    /// History is not news: the very first poll only seeds the set.
    @Test func firstPollSeedsWithoutBanners() async throws {
        let dir = quiverDir()
        let posts = Posts()
        let swe = SweClient(runner: Router([inboxKey: inbox([note("a"), note("b")])]))
        let notifier = GitHubNotifier(quiver: dir, model: GitHubModel(), defaults: TestDefaults()) {
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
        let notifier = GitHubNotifier(quiver: dir, model: GitHubModel(), defaults: TestDefaults()) {
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

    /// GitHub keeps a thread's id when new activity lands and only moves
    /// `updated_at`: a later review on a PR already bannered banners again.
    @Test func newActivityOnASeenThreadBannersAgain() async throws {
        let dir = quiverDir()
        let posts = Posts()
        let router = Router([inboxKey: inbox([])])
        let swe = SweClient(runner: router)
        let notifier = GitHubNotifier(quiver: dir, model: GitHubModel(), defaults: TestDefaults()) {
            posts.batches.append($0)
        }
        notifier.bannersEnabled = true
        await notifier.poll(swe: swe)
        router.respond(inboxKey, inbox([note("a")]))
        await notifier.poll(swe: swe)
        router.respond(inboxKey, inbox([note("a", updated: "2026-10-21T09:30:00Z")]))
        await notifier.poll(swe: swe)
        await notifier.poll(swe: swe)
        #expect(posts.batches.map { $0.map(\.id) } == [["a"], ["a"]])
        // One key per thread: the older update's key is gone.
        #expect(try deliveredKeys(dir).count == 1)
    }

    /// A state file from before keys carried `updated_at` holds bare ids.
    /// Upgrading does not banner those threads again, and the next update
    /// on one of them does banner.
    @Test func bareIdsFromAnOlderFileMigrateWithoutBanners() async throws {
        let dir = quiverDir()
        let file = dir.appending(path: "cache/github-notified.json")
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(["delivered": ["a"]]).write(to: file)
        let posts = Posts()
        let router = Router([inboxKey: inbox([note("a")])])
        let swe = SweClient(runner: router)
        let notifier = GitHubNotifier(quiver: dir, model: GitHubModel(), defaults: TestDefaults()) {
            posts.batches.append($0)
        }
        notifier.bannersEnabled = true
        await notifier.poll(swe: swe)
        #expect(posts.batches.isEmpty)
        #expect(try deliveredKeys(dir) == [GitHubNotifier.key(try decodedNote("a"))])
        router.respond(inboxKey, inbox([note("a", updated: "2026-10-22T00:00:00Z")]))
        await notifier.poll(swe: swe)
        #expect(posts.batches.map { $0.map(\.id) } == [["a"]])
    }

    /// An older file's thread that is read at the upgrade still migrates,
    /// and a bare id the inbox no longer shows is dropped: either one's
    /// next update banners.
    @Test func bareIdsMigrateWhenReadAndLeftoversAreDropped() async throws {
        let dir = quiverDir()
        let file = dir.appending(path: "cache/github-notified.json")
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(["delivered": ["a", "gone"]]).write(to: file)
        let posts = Posts()
        let router = Router([inboxKey: inbox([note("a", unread: false)])])
        let swe = SweClient(runner: router)
        let notifier = GitHubNotifier(quiver: dir, model: GitHubModel(), defaults: TestDefaults()) {
            posts.batches.append($0)
        }
        notifier.bannersEnabled = true
        await notifier.poll(swe: swe)
        #expect(posts.batches.isEmpty)
        #expect(try deliveredIds(dir) == ["a"])
        router.respond(inboxKey, inbox([
            note("a", updated: "2026-10-22T00:00:00Z"),
            note("gone", updated: "2026-10-22T00:00:00Z"),
        ]))
        await notifier.poll(swe: swe)
        #expect(posts.batches.map { $0.map(\.id).sorted() } == [["a", "gone"]])
    }

    private func decodedNote(_ id: String) throws -> GhNotification {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(GhNotification.self, from: Data(note(id).utf8))
    }

    /// A note that arrives already read is not actionable — no banner.
    @Test func readNotesDoNotBanner() async throws {
        let dir = quiverDir()
        let posts = Posts()
        let router = Router([inboxKey: inbox([note("a")])])
        let swe = SweClient(runner: router)
        let notifier = GitHubNotifier(quiver: dir, model: GitHubModel(), defaults: TestDefaults()) {
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
    /// same `~/.swe` dir does not re-banner what the last run sent.
    @Test func deliveredIdsPersistAcrossInstances() async throws {
        let dir = quiverDir()
        let posts = Posts()
        let router = Router([inboxKey: inbox([note("a")])])
        let swe = SweClient(runner: router)
        let first = GitHubNotifier(quiver: dir, model: GitHubModel(), defaults: TestDefaults()) {
            posts.batches.append($0)
        }
        first.bannersEnabled = true
        await first.poll(swe: swe)
        router.respond(inboxKey, inbox([note("a"), note("b")]))
        await first.poll(swe: swe)
        #expect(posts.batches.count == 1)
        // "Relaunch": fresh instance, same state file, same inbox.
        let posts2 = Posts()
        let second = GitHubNotifier(quiver: dir, model: GitHubModel(), defaults: TestDefaults()) {
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
        let seeded = (0..<600).map { "n\($0)@0" }
        try JSONEncoder().encode(["delivered": seeded]).write(to: file)
        let posts = Posts()
        let swe = SweClient(runner: Router([inboxKey: inbox([note("fresh")])]))
        let notifier = GitHubNotifier(quiver: dir, model: GitHubModel(), defaults: TestDefaults()) {
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
        let first = GitHubNotifier(quiver: dir, model: GitHubModel(), defaults: TestDefaults()) {
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
        let second = GitHubNotifier(quiver: dir, model: GitHubModel(), defaults: TestDefaults()) {
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
        let notifier = GitHubNotifier(quiver: dir, model: model, defaults: TestDefaults()) {
            posts.batches.append($0)
        }
        notifier.bannersEnabled = false
        // The seeding poll starts no refresh.
        #expect(await notifier.poll(swe: swe).isEmpty)
        router.respond(inboxKey, inbox([note("a"), note("b")]))
        let refreshes = await notifier.poll(swe: swe)
        #expect(posts.batches.isEmpty)
        // The notification's repo is selected: its tab refetches now.
        #expect(refreshes.count == 1)
        for refresh in refreshes { await refresh.value }
        let fetches = router.calls.filter {
            $0.joined(separator: " ").contains("--repo=o/r") }.count
        #expect(fetches == boardFetches + 1)
        #expect(try deliveredIds(dir).contains("b"))
    }

    /// GitHub banners are opt-in: with nothing saved the switch reads off,
    /// and a switch turned on stays on for the next launch.
    @Test func bannersStartOffAndTheSwitchPersists() {
        let defaults = TestDefaults()
        let notifier = GitHubNotifier(quiver: quiverDir(), model: GitHubModel(), defaults: defaults) { _ in }
        #expect(!notifier.bannersEnabled)
        notifier.bannersEnabled = true
        let relaunched = GitHubNotifier(quiver: quiverDir(), model: GitHubModel(), defaults: defaults) { _ in }
        #expect(relaunched.bannersEnabled)
    }

    /// The watcher loop returns when its task is cancelled rather than
    /// holding the AppModel watcher slot forever.
    @Test func keepFreshStopsOnCancel() async throws {
        let dir = quiverDir()
        let notifier = GitHubNotifier(quiver: dir, model: GitHubModel(), interval: 60, defaults: TestDefaults()) { _ in }
        let swe = SweClient(runner: Router([inboxKey: inbox([note("a")])]))
        let task = Task { await notifier.keepFresh(swe: { swe }) }
        task.cancel()
        await task.value
    }
}
