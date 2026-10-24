import CryptoKit
import NIOSSH
import XCTest
@testable import QuiverRemote

/// A made-up key for tests: fixed bytes, so its public half is known.
private func testIdentity(_ seed: UInt8 = 7) -> SSHIdentity {
    let key = try! P256.Signing.PrivateKey(rawRepresentation: Data(repeating: seed, count: 32))
    return SSHIdentity(privateKey: NIOSSHPrivateKey(p256Key: key), inSecureEnclave: false)
}

final class SSHInfoTests: XCTestCase {
    func testDecodesTheHubsAnswer() throws {
        let json = """
        {"enabled": true, "user": "someone", "port": 22, "listening": true,
         "host_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAA"],
         "device": {"public_key": null, "installed": false}}
        """
        let info = try Hub.decoder.decode(SSHInfo.self, from: Data(json.utf8))
        XCTAssertEqual(info.user, "someone")
        XCTAssertEqual(info.hostKeys.count, 1)
        XCTAssertNil(info.device.publicKey)
        XCTAssertEqual(SSHReadiness.of(info), .ready)
    }

    func testReadinessNamesWhatsMissingFirst() {
        var info = SSHInfo(enabled: false, user: "u", port: 22, listening: false, hostKeys: [],
                           device: .init(publicKey: nil, installed: false))
        XCTAssertEqual(SSHReadiness.of(info), .hubOff)
        info.enabled = true
        XCTAssertEqual(SSHReadiness.of(info), .sshdOff(port: 22))
        info.listening = true
        XCTAssertEqual(SSHReadiness.of(info), .noHostKeys)
        info.hostKeys = ["ssh-ed25519 AAAA"]
        XCTAssertEqual(SSHReadiness.of(info), .ready)
    }
}

final class HostKeyPinTests: XCTestCase {
    func testAdmitsOnlyListedKeysWhateverTheirComment() {
        let pin = HostKeyPin(["ssh-ed25519 AAAAone root@box", "ecdsa-sha2-nistp256 AAAAtwo"])
        XCTAssertTrue(pin.admits("ssh-ed25519 AAAAone"))
        XCTAssertTrue(pin.admits("ecdsa-sha2-nistp256 AAAAtwo"))
        XCTAssertFalse(pin.admits("ssh-ed25519 AAAAthree"))
        XCTAssertFalse(HostKeyPin([]).admits("ssh-ed25519 AAAAone"))
    }
}

final class SSHTargetStoreTests: XCTestCase {
    private var suite = ""

    override func setUp() { suite = "quiver.tests.\(UUID().uuidString)" }
    override func tearDown() { UserDefaults().removePersistentDomain(forName: suite) }

    func testKeepsOneTargetPerHub() {
        let store = SSHTargetStore(defaults: UserDefaults(suiteName: suite)!)
        let mac = SSHTarget(host: "mac", port: 22, user: "u", hostKeys: ["ssh-ed25519 AAAA"])
        let box = SSHTarget(host: "box", port: 2222, user: "v", hostKeys: ["ecdsa-sha2-nistp256 BBBB"])
        store.save(mac, for: "http://mac:8477")
        store.save(box, for: "http://box:8477")
        XCTAssertEqual(store.target(for: "http://mac:8477"), mac)
        XCTAssertEqual(store.target(for: "http://box:8477"), box)
        store.remove("http://mac:8477")
        XCTAssertNil(store.target(for: "http://mac:8477"))
        XCTAssertEqual(store.target(for: "http://box:8477")?.address, "v@box")
    }
}

final class SSHIdentityTests: XCTestCase {
    func testPublicKeyIsAnOpenSSHP256Line() {
        let identity = testIdentity()
        XCTAssertTrue(identity.publicKey.hasPrefix("ecdsa-sha2-nistp256 "))
        XCTAssertNoThrow(try NIOSSHPublicKey(openSSHPublicKey: identity.publicKey))
        XCTAssertEqual(identity.publicKey, testIdentity().publicKey, "same key, same line")
    }

    func testStoredKeyIsReusedUntilDeleted() throws {
        let service = "com.quiver.remote.tests.\(UUID().uuidString)"
        defer { SSHIdentity.delete(service: service) }
        let first = try SSHIdentity.loadOrCreate(service: service)
        XCTAssertEqual(try SSHIdentity.loadOrCreate(service: service).publicKey, first.publicKey)
        SSHIdentity.delete(service: service)
        XCTAssertNotEqual(try SSHIdentity.loadOrCreate(service: service).publicKey, first.publicKey)
    }
}

@MainActor
final class ConsoleModelTests: XCTestCase {
    private var suite = ""
    private var store = SSHTargetStore()
    /// Nothing listens here, so the hub and the machine both look down.
    private let hub = PairedHub(baseURL: URL(string: "http://127.0.0.1:9")!,
                                credential: Credential(deviceId: "d", credential: "c", name: "phone"))

    override func setUp() {
        suite = "quiver.tests.\(UUID().uuidString)"
        store = SSHTargetStore(defaults: UserDefaults(suiteName: suite)!)
    }

    override func tearDown() { UserDefaults().removePersistentDomain(forName: suite) }

    private func model() -> ConsoleModel { ConsoleModel(store: store, loadIdentity: { testIdentity() }) }
    private var client: HubClient { HubClient(baseURL: hub.baseURL, credential: "c") }

    func testHubDownWithNoSavedTargetSaysWhy() async {
        let console = model()
        await console.start(hub: hub, client: client)
        guard case .failed(let message) = console.phase else { return XCTFail("\(console.phase)") }
        XCTAssertTrue(message.contains("host keys"), message)
    }

    func testHubDownDialsTheSavedTarget() async {
        store.save(SSHTarget(host: "127.0.0.1", port: 9, user: "u", hostKeys: ["ssh-ed25519 AAAA"]), for: hub.id)
        let console = model()
        await console.start(hub: hub, client: client)
        XCTAssertTrue(console.offlineTarget)
        XCTAssertEqual(console.target?.address, "u@127.0.0.1")
        guard case .failed(let message) = console.phase else { return XCTFail("\(console.phase)") }
        XCTAssertTrue(message.contains("SSH port"), message)
    }
}

/// The real transport against a throwaway sshd. Skipped unless
/// `TEST_RUNNER_QUIVER_SSH_PORT`, `…_USER` and `…_HOST_KEY` are set
/// (xcodebuild strips the prefix); `script/console-sshd.sh` starts one
/// that trusts `testIdentity()` and prints them.
final class SSHShellIntegrationTests: XCTestCase {
    func testOpensAShellAndEchoes() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let port = env["QUIVER_SSH_PORT"].flatMap(Int.init), let user = env["QUIVER_SSH_USER"],
              let hostKey = env["QUIVER_SSH_HOST_KEY"] else {
            throw XCTSkip("no test sshd")
        }
        let target = SSHTarget(host: "127.0.0.1", port: port, user: user, hostKeys: [hostKey])
        let seen = expectation(description: "echo comes back")
        let output = OutputLog(expect: "quiver-console-42", fulfil: seen)
        let shell = try await SSHShell.open(target: target, key: testIdentity().privateKey, cols: 80, rows: 24) { event in
            if case .output(let bytes) = event { output.append(bytes) }
        }
        // The typed line echoes back too; only the shell's answer says 42.
        shell.write(Array("echo quiver-console-$((40+2))\n".utf8))
        await fulfillment(of: [seen], timeout: 10)
        shell.close()
    }

    func testRefusesAHostKeyTheHubDidntList() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let port = env["QUIVER_SSH_PORT"].flatMap(Int.init), let user = env["QUIVER_SSH_USER"] else {
            throw XCTSkip("no test sshd")
        }
        let target = SSHTarget(host: "127.0.0.1", port: port, user: user, hostKeys: ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAA"])
        do {
            _ = try await SSHShell.open(target: target, key: testIdentity().privateKey, cols: 80, rows: 24) { _ in }
            XCTFail("connected to an unlisted host key")
        } catch let error as SSHConsoleError {
            XCTAssertEqual(error, .hostKeyMismatch)
        }
    }

    func testReportsARejectedKey() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let port = env["QUIVER_SSH_PORT"].flatMap(Int.init), let user = env["QUIVER_SSH_USER"],
              let hostKey = env["QUIVER_SSH_HOST_KEY"] else {
            throw XCTSkip("no test sshd")
        }
        let target = SSHTarget(host: "127.0.0.1", port: port, user: user, hostKeys: [hostKey])
        do {
            _ = try await SSHShell.open(target: target, key: testIdentity(9).privateKey, cols: 80, rows: 24) { _ in }
            XCTFail("logged in with a key sshd doesn't trust")
        } catch let error as SSHConsoleError {
            XCTAssertEqual(error, .keyRejected(user: user))
        }
    }
}

/// Collects shell output from NIO's thread until a marker shows up.
private final class OutputLog: @unchecked Sendable {
    private let lock = NSLock()
    private var text = ""
    private var done = false
    private let marker: String
    private let fulfil: XCTestExpectation

    init(expect marker: String, fulfil: XCTestExpectation) {
        self.marker = marker
        self.fulfil = fulfil
    }

    func append(_ bytes: [UInt8]) {
        lock.lock()
        defer { lock.unlock() }
        text += String(decoding: bytes, as: UTF8.self)
        if !done, text.contains(marker) {
            done = true
            fulfil.fulfill()
        }
    }
}
