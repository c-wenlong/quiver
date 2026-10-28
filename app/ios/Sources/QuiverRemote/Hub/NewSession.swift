import Foundation
import Observation

/// What the new-session sheet offers: machines that can launch, their
/// recent folders, and the harnesses each accepts. Pure, so tests can
/// feed it hub rows.
enum NewSessionOptions {
    /// What the machine says it can start, in Provider visibility order.
    /// A machine row older than `launchable` falls back to every harness
    /// it detected; the hub still refuses any it can't start.
    static func harnesses(on machine: Machine) -> [String] {
        machine.launchable ?? machine.harnesses ?? []
    }

    /// Online machines first, then by name; a machine that can launch
    /// nothing is left out.
    static func machines(_ machines: [Machine]) -> [Machine] {
        machines
            .filter { !harnesses(on: $0).isEmpty }
            .sorted { a, b in
                if (a.online ?? false) != (b.online ?? false) { return a.online ?? false }
                return a.displayName.localizedCaseInsensitiveCompare(b.displayName) == .orderedAscending
            }
    }

    /// Every folder a session ran in on `machineId`, most recent first.
    static func folders(on machineId: String, sessions: [SessionRow]) -> [String] {
        var seen = Set<String>()
        return newestFirst(sessions).compactMap { row in
            guard row.machineId == machineId, let cwd = row.cwd, cwd.hasPrefix("/"),
                  seen.insert(cwd).inserted else { return nil }
            return cwd
        }
    }

    /// Folders whose path holds every word of `query`, any case.
    static func matching(_ folders: [String], query: String) -> [String] {
        let words = query.lowercased().split(whereSeparator: \.isWhitespace)
        guard !words.isEmpty else { return folders }
        return folders.filter { path in
            let p = path.lowercased()
            return words.allSatisfy { p.contains($0) }
        }
    }

    /// The harness last used in `folder`, when it is on offer.
    static func defaultHarness(folder: String?, machineId: String, offered: [String], sessions: [SessionRow]) -> String? {
        let last = newestFirst(sessions).first { $0.machineId == machineId && $0.cwd == folder }?.harness
        if let last, offered.contains(last) { return last }
        return offered.first
    }

    /// The machine's name for it, else what its sessions call it, with a
    /// capital first letter.
    static func name(_ harness: String, on machine: Machine?, sessions: [SessionRow]) -> String {
        let raw = machine?.harnessNames?[harness] ?? sessions.first { $0.harness == harness }?.agent ?? harness
        return raw.prefix(1).uppercased() + raw.dropFirst()
    }

    private static func newestFirst(_ rows: [SessionRow]) -> [SessionRow] {
        rows.sorted { ($0.lastActiveAt ?? 0) > ($1.lastActiveAt ?? 0) }
    }
}

/// One launch from the sheet: post it, then poll the hub until it says
/// which session the launch became.
@MainActor @Observable
final class LaunchFlow {
    enum Phase: Equatable {
        case idle, starting, waiting
        case failed(String)
        /// Gave up waiting, but nothing says it failed: a slow start, or
        /// a satellite that is asleep.
        case unconfirmed(String)
        case started(String)
    }

    private(set) var phase: Phase = .idle

    /// The launch id the hub accepted, set before the polling starts:
    /// a caller that needs the launch to outlive the sheet (the task
    /// linker's delayed adoption) reads it even when `run` never
    /// answers a session key.
    private(set) var launchId: String?

    /// Starting, waiting, or started and about to open the chat: Start
    /// stays off the whole time, so one tap can't launch twice.
    var busy: Bool {
        switch phase {
        case .starting, .waiting, .started: true
        default: false
        }
    }

    /// Returns the new session's key, or nil with `phase` saying why.
    /// `attempts` polls, `interval` apart: a cold Claude start takes a
    /// few seconds before its transcript exists.
    func run(post: () async throws -> LaunchStatus,
             fetch: (String) async throws -> LaunchStatus,
             attempts: Int = 90,
             interval: Duration = .seconds(1)) async -> String? {
        guard !busy else { return nil }
        phase = .starting
        launchId = nil
        let accepted: LaunchStatus
        do {
            accepted = try await post()
        } catch {
            phase = .failed((error as? HubError)?.message ?? error.localizedDescription)
            return nil
        }
        launchId = accepted.launchId
        if Task.isCancelled { phase = .idle; return nil }
        phase = .waiting
        var status = accepted
        var polls = 0
        while true {
            if let key = status.key, status.state == "started" {
                phase = .started(key)
                return key
            }
            if status.state == "untracked" {
                phase = .unconfirmed("Started. Quiver can't read this harness's sessions yet, so there's no chat to open. It keeps running under tmux on the machine.")
                return nil
            }
            if status.state == "failed" {
                phase = .failed(status.note.map { "The session didn't start: \($0)" } ?? "The session didn't start.")
                return nil
            }
            if polls == attempts { break }
            polls += 1
            try? await Task.sleep(for: interval)
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
        phase = .unconfirmed(accepted.queued == true
            ? "Queued. It starts when that machine is online, then shows up in Sessions."
            : "It started but hasn't shown up yet. It'll appear in Sessions when it does.")
        return nil
    }

    /// Started: the sheet is refreshing the list before it opens the chat.
    var opening: Bool { if case .started = phase { true } else { false } }

    func reset() { phase = .idle }
}
