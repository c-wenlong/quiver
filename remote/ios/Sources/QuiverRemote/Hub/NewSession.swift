import Foundation
import Observation

/// What the new-session sheet offers: machines that can launch, their
/// recent folders, and the harnesses each accepts. Pure, so tests can
/// feed it hub rows.
enum NewSessionOptions {
    /// Mirrors `protocol.LAUNCHABLE_HARNESSES`, for machines whose row
    /// predates the `launchable` field.
    static let launchable = ["claude", "codex", "cursor", "droid", "pi"]

    static let names = ["claude": "Claude Code", "codex": "Codex", "cursor": "Cursor", "droid": "Droid", "pi": "Pi"]

    static func harnesses(on machine: Machine, sessions: [SessionRow]) -> [String] {
        let offered = machine.launchable ?? (machine.harnesses ?? []).filter(launchable.contains)
        // The ones used most recently on this machine come first.
        var order: [String] = []
        for row in newestFirst(sessions) where row.machineId == machine.id {
            if let h = row.harness, offered.contains(h), !order.contains(h) { order.append(h) }
        }
        return order + launchable.filter { offered.contains($0) && !order.contains($0) }
            + offered.filter { !launchable.contains($0) && !order.contains($0) }
    }

    /// Online machines first, then by name; a machine that can launch
    /// nothing is left out.
    static func machines(_ machines: [Machine], sessions: [SessionRow]) -> [Machine] {
        machines
            .filter { !harnesses(on: $0, sessions: sessions).isEmpty }
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

    /// The harness last used in `folder`, when it is on offer.
    static func defaultHarness(folder: String?, machineId: String, offered: [String], sessions: [SessionRow]) -> String? {
        let last = newestFirst(sessions).first { $0.machineId == machineId && $0.cwd == folder }?.harness
        if let last, offered.contains(last) { return last }
        return offered.first
    }

    static func name(_ harness: String, sessions: [SessionRow]) -> String {
        names[harness] ?? sessions.first { $0.harness == harness }?.agent ?? harness
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

    var busy: Bool { phase == .starting || phase == .waiting }

    /// Returns the new session's key, or nil with `phase` saying why.
    /// `attempts` polls, `interval` apart: a cold Claude start takes a
    /// few seconds before its transcript exists.
    func run(post: () async throws -> LaunchStatus,
             fetch: (String) async throws -> LaunchStatus,
             attempts: Int = 90,
             interval: Duration = .seconds(1)) async -> String? {
        phase = .starting
        let accepted: LaunchStatus
        do {
            accepted = try await post()
        } catch {
            phase = .failed((error as? HubError)?.message ?? error.localizedDescription)
            return nil
        }
        phase = .waiting
        var status = accepted
        for _ in 0..<attempts {
            if let key = status.key, status.state == "started" {
                phase = .started(key)
                return key
            }
            if status.state == "failed" {
                phase = .failed(status.note.map { "The session didn't start: \($0)" } ?? "The session didn't start.")
                return nil
            }
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

    func reset() { phase = .idle }
}
