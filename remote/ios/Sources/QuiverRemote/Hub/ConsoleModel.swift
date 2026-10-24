import Foundation
import Observation

/// Where the terminal view's bytes go: SwiftTerm's view, or a test sink.
@MainActor protocol ConsoleScreen: AnyObject {
    func feed(_ bytes: [UInt8])
}

/// One console: set up through the hub when it answers, then an SSH
/// shell straight to the machine's sshd, which needs no hub at all.
@MainActor @Observable
final class ConsoleModel {
    enum Phase: Equatable {
        case preparing
        /// The hub answered, but the machine isn't set up for the console.
        case needsSetup(SSHReadiness)
        case connecting
        case open
        /// The shell ended; nil is a plain logout.
        case closed(String?)
        case failed(String)
    }

    private(set) var phase: Phase = .preparing
    private(set) var target: SSHTarget?
    /// True when the hub didn't answer and the console runs on a saved target.
    private(set) var offlineTarget = false

    @ObservationIgnored weak var screen: ConsoleScreen?
    @ObservationIgnored private var shell: SSHShell?
    /// Bumped on every connect, so a dying shell's events can't touch the next one.
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var size = (cols: 80, rows: 24)
    @ObservationIgnored private let store: SSHTargetStore
    @ObservationIgnored private let loadIdentity: () throws -> SSHIdentity

    init(store: SSHTargetStore = SSHTargetStore(),
         loadIdentity: @escaping () throws -> SSHIdentity = { try SSHIdentity.loadOrCreate() }) {
        self.store = store
        self.loadIdentity = loadIdentity
    }

    var title: String { target?.address ?? "Console" }

    /// Registers this phone's key with the hub and opens the shell.
    func start(hub: PairedHub, client: HubClient) async {
        phase = .preparing
        let identity: SSHIdentity
        do {
            identity = try loadIdentity()
        } catch {
            phase = .failed(error.localizedDescription)
            return
        }
        do {
            var info = try await client.ssh()
            if info.device.publicKey != identity.publicKey {
                info = try await client.registerSSHKey(identity.publicKey)
            }
            let readiness = SSHReadiness.of(info)
            guard readiness == .ready else {
                phase = .needsSetup(readiness)
                return
            }
            let fresh = SSHTarget(host: hub.baseURL.host ?? "", port: info.port, user: info.user, hostKeys: info.hostKeys)
            store.save(fresh, for: hub.id)
            target = fresh
            offlineTarget = false
        } catch let error as HubError where error.status == 0 {
            // The hub is down: exactly when the console matters, so dial
            // the machine with what the hub said last time.
            guard let saved = store.target(for: hub.id) else {
                phase = .failed("The hub isn't answering, and this phone hasn't opened a console on \(hub.address) before, so it doesn't know which host keys to trust. Open it once while the hub is up.")
                return
            }
            target = saved
            offlineTarget = true
        } catch {
            phase = .failed((error as? HubError)?.message ?? error.localizedDescription)
            return
        }
        await connect(identity: identity)
    }

    /// Dials the target again after a logout or a dropped connection.
    func reconnect() async {
        guard target != nil else { return }
        do {
            await connect(identity: try loadIdentity())
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }

    private func connect(identity: SSHIdentity) async {
        guard let target else { return }
        shell?.close()
        shell = nil
        generation += 1
        let gen = generation
        phase = .connecting
        do {
            let opened = try await SSHShell.open(target: target, key: identity.privateKey,
                                                 cols: size.cols, rows: size.rows) { [weak self] event in
                // The main queue keeps output in order; separate Tasks don't promise to.
                DispatchQueue.main.async { MainActor.assumeIsolated { self?.handle(event, generation: gen) } }
            }
            guard gen == generation else {
                opened.close()
                return
            }
            shell = opened
            phase = .open
            // The view may have settled on a size while the shell opened.
            opened.resize(cols: size.cols, rows: size.rows)
        } catch {
            guard gen == generation else { return }
            phase = .failed(error.localizedDescription)
        }
    }

    private func handle(_ event: SSHShell.Event, generation gen: Int) {
        guard gen == generation else { return }
        switch event {
        case .output(let bytes):
            screen?.feed(bytes)
        case .closed(let error):
            guard phase == .open else { return }
            shell = nil
            phase = .closed(error?.localizedDescription)
        }
    }

    func send(_ bytes: [UInt8]) {
        shell?.write(bytes)
    }

    func resize(cols: Int, rows: Int) {
        guard cols > 0, rows > 0, (cols, rows) != size else { return }
        size = (cols, rows)
        shell?.resize(cols: cols, rows: rows)
    }

    func stop() {
        generation += 1
        shell?.close()
        shell = nil
    }
}
