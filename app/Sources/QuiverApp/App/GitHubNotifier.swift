import Foundation
import Observation
import QuiverCore
import UserNotifications

/// The GitHub control plane: a background inbox poll that survives page
/// switches and closed windows, turning new notifications into macOS
/// banners and repo-board invalidations. The Inbox page's own poll loop
/// is unchanged — it reads whatever this fetched last.
///
/// Delivery state is the set of notification ids already bannered,
/// persisted like the limit alerts' state file so a relaunch doesn't
/// repeat them. The first poll ever only seeds the set — history is
/// not news.
@MainActor
@Observable
final class GitHubNotifier {
    /// Off in Settings means banners stop; polling still runs so the
    /// inbox, badge and board invalidations stay live.
    var bannersEnabled: Bool {
        didSet { UserDefaults.standard.set(bannersEnabled, forKey: "githubNotifications") }
    }

    @ObservationIgnored private let model: GitHubModel
    @ObservationIgnored private let stateFile: URL
    @ObservationIgnored private let interval: TimeInterval
    @ObservationIgnored private var delivered: Set<String>
    @ObservationIgnored private var primed = false
    /// Sends the batch; `postToSystem` in the app, a capture in tests.
    @ObservationIgnored private let post: ([GhNotification]) async -> Void

    /// Same bound as the inbox fetch: the delivered set can never outgrow
    /// what a few polls of the endpoint could re-see.
    private static let deliveredCap = 500

    init(quiver: URL, model: GitHubModel, interval: TimeInterval = 60,
         post: (([GhNotification]) async -> Void)? = nil) {
        self.model = model
        self.interval = interval
        self.post = post ?? { notes in await Self.postToSystem(notes) }
        bannersEnabled = UserDefaults.standard.object(forKey: "githubNotifications") as? Bool ?? true
        stateFile = quiver.appending(path: "cache/github-notified.json")
        delivered = (try? Data(contentsOf: stateFile))
            .flatMap { try? JSONDecoder().decode(State.self, from: $0) }
            .map { Set($0.delivered) } ?? []
        primed = !delivered.isEmpty
    }

    /// The AppModel watcher loop: poll, sleep, repeat until the app
    /// tears it down. `swe` is read per poll so a path change in
    /// Settings needs no restart.
    func keepFresh(swe: @escaping () -> SweClient?) async {
        while !Task.isCancelled {
            await poll(swe: swe())
            try? await Task.sleep(for: .seconds(interval))
        }
    }

    /// One tick: refetch the inbox, banner ids not seen before, and mark
    /// each notified repo's boards stale so the next look refetches —
    /// the row updates from the same poll that produced the banner.
    func poll(swe: SweClient?) async {
        guard let swe else { return }
        await model.loadInbox(swe: swe, force: true)
        guard case .loaded(let notes) = model.inbox else { return }

        if !primed {
            primed = true
            delivered.formUnion(notes.map(\.id))
            save()
            return
        }

        let fresh = notes.filter { $0.unread && !delivered.contains($0.id) }
        for note in fresh {
            delivered.insert(note.id)
            model.invalidate(repo: note.repo, swe: swe)
        }
        if !fresh.isEmpty {
            if bannersEnabled { await post(fresh) }
            if delivered.count > Self.deliveredCap {
                delivered = Set(delivered.suffix(Self.deliveredCap))
            }
            save()
        }
    }

    /// One banner per notification while there are few; a burst (waking
    /// to a backlog) collapses into a single summary instead of stacking.
    private static func postToSystem(_ fresh: [GhNotification]) async {
        let center = UNUserNotificationCenter.current()
        guard (try? await center.requestAuthorization(options: [.alert, .sound])) == true else { return }
        if fresh.count <= 3 {
            for note in fresh {
                let content = UNMutableNotificationContent()
                content.title = note.repo
                content.subtitle = Self.reasonTitle(note.reason)
                content.body = note.title
                content.sound = .default
                content.threadIdentifier = "gh-\(note.repo)"
                content.userInfo = ["gh": true, "repo": note.repo, "type": note.type]
                // One identifier per GitHub thread: a re-poll of an
                // updated thread replaces its banner, never stacks.
                let request = UNNotificationRequest(
                    identifier: "gh-\(note.id)", content: content, trigger: nil)
                try? await center.add(request)
            }
        } else {
            let content = UNMutableNotificationContent()
            content.title = "GitHub"
            let repos = fresh.map(\.repo).uniqued()
            content.body = "\(fresh.count) new notifications in \(repos.joined(separator: ", "))"
            content.sound = .default
            content.threadIdentifier = "gh-inbox"
            // No repo: the click lands on the Inbox.
            content.userInfo = ["gh": true]
            let request = UNNotificationRequest(
                identifier: "gh-burst", content: content, trigger: nil)
            try? await center.add(request)
        }
    }

    private static func reasonTitle(_ reason: String) -> String {
        switch reason {
        case "review_requested": return "Review requested"
        case "mention": return "Mentioned you"
        case "team_mention": return "Team mentioned"
        case "assign": return "Assigned to you"
        case "ci_activity": return "CI activity"
        case "author": return "Author activity"
        case "comment": return "New comment"
        case "state_change": return "State changed"
        default:
            return reason.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func save() {
        do {
            try FileManager.default.createDirectory(
                at: stateFile.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder().encode(State(delivered: delivered.sorted()))
                .write(to: stateFile, options: .atomic)
        } catch {
            // Worst case a relaunch repeats a notification.
        }
    }

    private struct State: Codable {
        var delivered: [String]
    }
}

private extension Array where Element: Hashable {
    func uniqued() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}
