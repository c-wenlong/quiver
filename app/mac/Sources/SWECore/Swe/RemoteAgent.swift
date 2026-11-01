import Foundation
import Observation

/// `swe remote agent --status --json`: whether this Mac is a paired
/// satellite, and whether its background service (the LaunchAgent
/// `com.cwenlong.swe.remote-agent`) is installed and running.
public struct RemoteAgentStatus: Decodable, Sendable, Equatable {
    /// `agent.json` holds a hub and a credential.
    public let configured: Bool
    /// The hub this Mac dials; never the credential.
    public let hub: String?
    /// False when this machine has no launchd or systemd user manager.
    public let supported: Bool
    public let installed: Bool
    public let running: Bool
    public let log: String?
    /// This Mac's own hub service is installed, so `--install` refuses
    /// the agent (both would share `data/remote/cursors/`).
    public let hubInstalled: Bool

    /// The Settings toggle shows only on a satellite of another hub: paired
    /// with a hub, and this Mac's own hub not installed. An agent that is
    /// installed keeps its toggle even unpaired, so it can be turned off.
    public var offersKeepRunning: Bool {
        supported && !hubInstalled && (configured || installed)
    }

    /// The toggle's help line: what it does, then where the service stands.
    public var detail: String {
        let target = hub.flatMap { URL(string: $0)?.host() } ?? "the hub"
        let what = "Starts swe remote agent at login and restarts it if it stops, so \(target) keeps this Mac's sessions."
        if running { return what + " Running now." }
        if installed {
            return what + " Installed, but not running" + (log.map { ": see \($0)." } ?? ".")
        }
        return what
    }
}

extension SweClient {
    public func remoteAgentStatus() async throws -> RemoteAgentStatus {
        try await call(["remote", "agent", "--status", "--json"])
    }

    /// Installs or removes the agent's LaunchAgent; answers the status
    /// after the change. A refusal (this Mac's hub is installed) throws
    /// with swe's message.
    public func remoteSetAgentService(_ on: Bool) async throws -> RemoteAgentStatus {
        try await call(["remote", "agent", on ? "--install" : "--uninstall", "--json"],
                       timeout: .seconds(60))
    }
}

/// Settings › Remote: the "Keep this Mac's agent running" switch. Hidden
/// until swe reports a status that offers it, so an older swe without
/// `agent --status` shows nothing.
@MainActor
@Observable
public final class RemoteAgentModel {
    public private(set) var status: RemoteAgentStatus?
    public private(set) var saving = false
    public private(set) var error: String?
    private let swe: SweClient
    private var generation = 0

    public init(swe: SweClient) {
        self.swe = swe
    }

    public var showsToggle: Bool { status?.offersKeepRunning == true }

    public func load() async {
        generation += 1
        let mine = generation
        do {
            let next = try await swe.remoteAgentStatus()
            if mine == generation, !saving { status = next }
        } catch {
            // An older swe has no --status: the switch stays hidden. A
            // later failure keeps the last answer.
        }
    }

    /// The switch shows the state swe reports after the change, so a
    /// failure leaves it where it was and says why.
    public func setKeepRunning(_ on: Bool) async {
        guard !saving else { return }
        saving = true
        error = nil
        defer { saving = false }
        do {
            status = try await swe.remoteSetAgentService(on)
            // A load that started before the change read the old state.
            generation += 1
        } catch is CancellationError {
        } catch {
            self.error = error.localizedDescription
        }
    }
}
