import Foundation
import Observation

/// What the resolve sheet decides before Start: which harnesses the hub
/// can launch, which one to preselect, and whether the context posts as
/// a comment. Pure, so tests feed it rows.
public enum IssueResolve {
    /// The hub's own `/v1/machines` row: the one whose id matches this
    /// Mac's `machine.json`, or the only row there is.
    public static func hubMachine(machines: [HubMachine], localMachineId: String?) -> HubMachine? {
        machines.first { $0.id == localMachineId }
            ?? (machines.count == 1 ? machines.first : nil)
    }

    /// The harnesses this Mac's hub can start, from its own
    /// `/v1/machines` row (`launchable`, else every harness it detected).
    /// The hub's row is the one whose id matches this Mac's
    /// `machine.json`, or the only row there is. With neither, `fallback`
    /// (the harnesses installed here) stands in; the hub still refuses
    /// one it can't start.
    public static func offeredHarnesses(machines: [HubMachine], localMachineId: String?,
                                        fallback: [String]) -> [String] {
        guard let own = hubMachine(machines: machines, localMachineId: localMachineId) else {
            return fallback
        }
        return own.launchable ?? own.harnesses ?? fallback
    }

    /// The harness last used in this repo, when it is on offer, else the
    /// first offered. "In this repo" is a session whose folder is one of
    /// SWE's worktrees of it (`<owner>__<name>…`) or a checkout whose
    /// path has a folder named like the repo.
    public static func defaultHarness(offered: [String], sessions: [SessionInfo],
                                      repo: String) -> String? {
        let newest = sessions.sorted { $0.lastActiveAt > $1.lastActiveAt }
        let last = newest.first { session in
            guard offered.contains(session.harness), let cwd = session.cwd else { return false }
            return inRepo(cwd, repo: repo)
        }
        return last?.harness ?? offered.first
    }

    /// Whether `cwd` is a checkout or SWE worktree of `repo` (`owner/name`).
    public static func inRepo(_ cwd: String, repo: String) -> Bool {
        let parts = repo.split(separator: "/").map { $0.lowercased() }
        guard parts.count == 2 else { return false }
        let components = (cwd as NSString).standardizingPath
            .split(separator: "/").map { $0.lowercased() }
        // The repo was quiver before it became swe, and checkouts and
        // worktrees made before the rename keep the old folder name.
        let names = parts[1] == "swe" ? ["swe", "quiver"] : [parts[1]]
        return components.contains { component in
            names.contains { component == $0 || component.hasPrefix("\(parts[0])__\($0)") }
        }
    }

    /// This Mac's remote machine id, from `~/.swe/data/remote/machine.json`.
    public static func localMachineId(home: URL) -> String? {
        let url = home.appending(path: ".swe/data/remote/machine.json")
        struct Saved: Decodable { let id: String? }
        guard let data = try? Data(contentsOf: url),
              let saved = try? JSONDecoder().decode(Saved.self, from: data),
              let id = saved.id, !id.isEmpty else { return nil }
        return id
    }

    /// The context posts as a comment only when it holds text, the box is
    /// ticked, and that text didn't already post (a Start retried after a
    /// failed launch must not comment twice).
    public static func postsComment(context: String, ticked: Bool, posted: String? = nil) -> Bool {
        let text = context.trimmingCharacters(in: .whitespacesAndNewlines)
        guard ticked, !text.isEmpty else { return false }
        return text != posted?.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// One resolve launch: post the context comment (optional), post the
/// launch, then poll the hub until it says which session the launch
/// became. The phases and wording follow the iPhone app's `LaunchFlow`
/// (app/ios), plus the hub's live `note` while it works.
@MainActor
@Observable
public final class IssueLaunchFlow {
    public enum Phase: Equatable, Sendable {
        case idle
        /// Posting the context as a comment on the issue.
        case commenting
        /// Posting the launch.
        case starting
        /// The hub accepted it; `note` is its current step.
        case waiting(note: String?)
        case failed(String)
        /// Gave up waiting, but nothing says it failed.
        case unconfirmed(String)
        case started(String)
    }

    public private(set) var phase: Phase = .idle
    /// The context comment didn't post. The launch went ahead anyway.
    public private(set) var commentWarning: String?

    public init() {}

    /// Busy from the first post until the chat opens: Start stays off the
    /// whole time, so one click can't launch twice.
    public var busy: Bool {
        switch phase {
        case .commenting, .starting, .waiting, .started: true
        default: false
        }
    }

    /// What the hub says it is doing, for the sheet's progress line.
    public var note: String? {
        switch phase {
        case .commenting: "Posting your context to GitHub…"
        case .starting: "Asking the hub to start it…"
        case .waiting(let note): note ?? "Waiting for the session…"
        case .started: "Opening the chat…"
        default: nil
        }
    }

    public nonisolated static let untrackedText = "Started. SWE can't read this harness's sessions yet, so there's no chat to open. It keeps running under tmux on the machine."
    public nonisolated static let queuedText = "Queued. It starts when that machine is online, then shows up in Sessions."
    public nonisolated static let slowText = "It started but hasn't shown up yet. It'll appear in Sessions when it does."

    /// Where a launch reply leaves the flow: a final phase, or nil while
    /// the hub is still working on it.
    public nonisolated static func outcome(_ status: HubLaunchStatus) -> Phase? {
        switch status.state {
        case "started":
            guard let key = status.key, !key.isEmpty else { return nil }
            return .started(key)
        case "untracked":
            return .unconfirmed(untrackedText)
        case "failed":
            return .failed(status.note.map { "The session didn't start: \($0)" } ?? "The session didn't start.")
        default:
            return nil
        }
    }

    /// The phase once the polls ran out with no answer.
    public nonisolated static func gaveUp(queued: Bool) -> Phase {
        .unconfirmed(queued ? queuedText : slowText)
    }

    /// Runs the launch and returns the new session's key, or nil with
    /// `phase` saying why. `comment` posts the context and returns its
    /// failure; nil skips it. `attempts` polls with `pause` between them
    /// (a second each in the app, about 90 s in all).
    public func run(comment: (() async -> String?)? = nil,
                    post: () async throws -> HubLaunchStatus,
                    fetch: (String) async throws -> HubLaunchStatus,
                    attempts: Int = 90,
                    pause: () async -> Void = { try? await Task.sleep(for: .seconds(1)) }) async -> String? {
        guard !busy else { return nil }
        commentWarning = nil
        if let comment {
            phase = .commenting
            if let failure = await comment() {
                commentWarning = "Couldn't post your context to GitHub: \(failure) The agent still gets it in the prompt."
            }
            if Task.isCancelled { phase = .idle; return nil }
        }
        phase = .starting
        let accepted: HubLaunchStatus
        do {
            accepted = try await post()
        } catch {
            phase = .failed((error as? HubError)?.message ?? error.localizedDescription)
            return nil
        }
        if Task.isCancelled { phase = .idle; return nil }
        var status = accepted
        var polls = 0
        while true {
            if let done = Self.outcome(status) {
                phase = done
                if case .started(let key) = done { return key }
                return nil
            }
            phase = .waiting(note: status.note ?? accepted.note)
            if polls == attempts { break }
            polls += 1
            await pause()
            if Task.isCancelled { phase = .idle; return nil }
            do {
                status = try await fetch(accepted.launchId)
            } catch let error as HubError where error.status == 404 {
                phase = .failed(error.message)
                return nil
            } catch {
                // A dropped poll is retried until the attempts run out.
            }
        }
        phase = Self.gaveUp(queued: accepted.queued == true)
        return nil
    }

    public func reset() {
        phase = .idle
        commentWarning = nil
    }
}
