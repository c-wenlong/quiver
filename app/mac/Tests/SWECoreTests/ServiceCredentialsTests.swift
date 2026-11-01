import Foundation
import Testing
@testable import SWECore

/// An in-memory keychain; records the operation, never the value.
actor StubSecrets: SecretStoring {
    var items: [String: String] = [:]
    private(set) var calls: [[String]] = []
    private var nextError: Error?
    private var nextDeleteError: Error?

    func setNextError(_ error: Error) { nextError = error }
    func setNextDeleteError(_ error: Error) { nextDeleteError = error }
    /// Moves a stored item between services, as a migration would leave it.
    func moveItem(fromService: String, toService: String, account: String) {
        items[key(toService, account)] = items.removeValue(forKey: key(fromService, account))
    }

    private func key(_ service: String, _ account: String) -> String {
        "\(service)\u{0}\(account)"
    }
    func genericPassword(service: String, account: String) async -> String? {
        calls.append(["find", service, account])
        return items[key(service, account)]
    }
    func setGenericPassword(service: String, account: String, value: String) async throws {
        calls.append(["set", service, account])
        if let nextError {
            self.nextError = nil
            throw nextError
        }
        items[key(service, account)] = value
    }
    func deleteGenericPassword(service: String, account: String) async throws {
        calls.append(["delete", service, account])
        if let nextDeleteError {
            self.nextDeleteError = nil
            throw nextDeleteError
        }
        items.removeValue(forKey: key(service, account))
    }
}

/// Replies with a canned status and records the request it was asked.
final class ProbeTransport: HTTPTransport, @unchecked Sendable {
    var answer: Result<(Data, Int), Error>
    private(set) var requests: [URLRequest] = []

    init(_ answer: Result<(Data, Int), Error>) { self.answer = answer }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requests.append(request)
        let (body, status) = try answer.get()
        return (body, HTTPURLResponse(url: request.url!, statusCode: status,
                                      httpVersion: nil, headerFields: nil)!)
    }
}

@Suite struct ServiceCredentialsTests {
    /// Callers keep the `TempDir` alive to the end of the test: dropped
    /// at once, it deletes its folder and the next metadata write puts
    /// it back, outside anything that removes it.
    private func make(_ secrets: StubSecrets)
        throws -> (TempDir, ServiceCredentials) {
        let dir = try TempDir()
        return (dir, ServiceCredentials(
            secrets: secrets, service: "com.cwenlong.swe.services.test",
            metadataURL: dir.url.appending(path: "config/services.json"),
            now: { Date(timeIntervalSince1970: 1_791_000_000) }))
    }

    @Test func aSavedKeyReadsBackWithItsDates() async throws {
        let secrets = StubSecrets()
        let added = Date(timeIntervalSince1970: 1_791_000_000)
        let dir = try TempDir()
        defer { withExtendedLifetime(dir) {} }
        let credentials = ServiceCredentials(
            secrets: secrets, service: "svc",
            metadataURL: dir.url.appending(path: "config/services.json"),
            now: { added })
        try await credentials.set("DEVIN_API_KEY", value: "secret-token")
        #expect(await credentials.value(for: "DEVIN_API_KEY") == "secret-token")

        let status = await credentials.status(for: "DEVIN_API_KEY")
        #expect(status.isSet)
        #expect(status.added == added)
        #expect(status.lastUsed == added)
    }

    @Test func readingMarksLastUsedAndReplaceKeepsTheAddedDate() async throws {
        let secrets = StubSecrets()
        final class Clock: @unchecked Sendable { var date = Date(timeIntervalSince1970: 1_791_000_000) }
        let clock = Clock()
        let dir = try TempDir()
        defer { withExtendedLifetime(dir) {} }
        let credentials = ServiceCredentials(
            secrets: secrets, service: "svc",
            metadataURL: dir.url.appending(path: "config/services.json"),
            now: { clock.date })
        try await credentials.set("DEVIN_API_KEY", value: "one")
        clock.date = Date(timeIntervalSince1970: 1_792_000_000)
        _ = await credentials.value(for: "DEVIN_API_KEY")

        var status = await credentials.status(for: "DEVIN_API_KEY")
        #expect(status.added == Date(timeIntervalSince1970: 1_791_000_000))
        #expect(status.lastUsed == Date(timeIntervalSince1970: 1_792_000_000))

        // Replacing the value keeps the day it was first added.
        clock.date = Date(timeIntervalSince1970: 1_793_000_000)
        try await credentials.set("DEVIN_API_KEY", value: "two")
        status = await credentials.status(for: "DEVIN_API_KEY")
        #expect(status.added == Date(timeIntervalSince1970: 1_791_000_000))
        #expect(await credentials.value(for: "DEVIN_API_KEY") == "two")
    }

    @Test func removeDeletesTheValueAndItsDates() async throws {
        let secrets = StubSecrets()
        let (dir, credentials) = try make(secrets)
        defer { withExtendedLifetime(dir) {} }
        try await credentials.set("DEVIN_ORG_ID", value: "org-secret")
        try await credentials.remove("DEVIN_ORG_ID")
        let status = await credentials.status(for: "DEVIN_ORG_ID")
        #expect(!status.isSet)
        #expect(status.added == nil)
        #expect(await credentials.value(for: "DEVIN_ORG_ID") == nil)
    }

    @Test func theMetadataFileNeverHoldsAValue() async throws {
        let secrets = StubSecrets()
        let (dir, credentials) = try make(secrets)
        try await credentials.set("DEVIN_API_KEY", value: "secret-token")
        _ = await credentials.value(for: "DEVIN_API_KEY")
        let text = try String(contentsOf: dir.url.appending(path: "config/services.json"), encoding: .utf8)
        #expect(text.contains("secret-token") == false)
        #expect(text.contains("DEVIN_API_KEY"))
    }

    @Test func aFailedSetThrowsWithoutTheValue() async throws {
        let secrets = StubSecrets()
        let (dir, credentials) = try make(secrets)
        defer { withExtendedLifetime(dir) {} }
        await secrets.setNextError(SweError.commandFailed(status: 1, message: "keychain locked"))
        do {
            try await credentials.set("DEVIN_API_KEY", value: "secret-token")
            Issue.record("the write should have thrown")
        } catch {
            #expect(!error.localizedDescription.contains("secret-token"))
        }
    }

    @Test func aLegacyKeyMigratesToTheNewServiceOnFirstRead() async throws {
        let secrets = StubSecrets()
        try await secrets.setGenericPassword(
            service: "legacy", account: "DEVIN_API_KEY", value: "tok")
        let dir = try TempDir()
        defer { withExtendedLifetime(dir) {} }
        let credentials = ServiceCredentials(
            secrets: secrets, service: "svc", legacyService: "legacy",
            metadataURL: dir.url.appending(path: "config/services.json"))
        #expect(await credentials.value(for: "DEVIN_API_KEY") == "tok")
        #expect(await secrets.genericPassword(service: "svc", account: "DEVIN_API_KEY") == "tok")
        #expect(await secrets.genericPassword(service: "legacy", account: "DEVIN_API_KEY") == nil)
        // The second read comes from the new service alone.
        let before = await secrets.calls.count
        _ = await credentials.value(for: "DEVIN_API_KEY")
        let calls = await secrets.calls
        #expect(!calls[before...].contains { $0[0] == "find" && $0[1] == "legacy" })
    }

    @Test func aFailedMigrationKeepsTheOnlyStoredCopy() async throws {
        // The copy throws: the value still answers this read, but the
        // old item stays so later reads find it again.
        let secrets = StubSecrets()
        try await secrets.setGenericPassword(service: "legacy", account: "K", value: "tok")
        await secrets.setNextError(SweError.commandFailed(status: 1, message: "keychain locked"))
        let dir = try TempDir()
        defer { withExtendedLifetime(dir) {} }
        let credentials = ServiceCredentials(
            secrets: secrets, service: "svc", legacyService: "legacy",
            metadataURL: dir.url.appending(path: "config/services.json"))
        #expect(await credentials.value(for: "K") == "tok")
        #expect(await secrets.genericPassword(service: "legacy", account: "K") == "tok")
        #expect(await secrets.genericPassword(service: "svc", account: "K") == nil)
    }

    @Test func aScratchServiceNeverReadsTheLegacyStore() async throws {
        // SWE_SERVICES_KEYCHAIN exists so a test build cannot touch real
        // keys: a custom service turns the legacy fallback off unless the
        // caller passes a legacy service by hand.
        let secrets = StubSecrets()
        try await secrets.setGenericPassword(
            service: ServiceCredentials.legacyKeychainService, account: "K", value: "tok")
        let before = await secrets.calls.count
        let dir = try TempDir()
        defer { withExtendedLifetime(dir) {} }
        let credentials = ServiceCredentials(
            secrets: secrets, service: "scratch",
            metadataURL: dir.url.appending(path: "config/services.json"))
        #expect(await credentials.value(for: "K") == nil)
        #expect(await credentials.status(for: "K").isSet == false)
        try await credentials.remove("K")
        let calls = await secrets.calls
        #expect(calls[before...].allSatisfy { $0[1] == "scratch" })
        #expect(await secrets.genericPassword(
            service: ServiceCredentials.legacyKeychainService, account: "K") == "tok")
    }

    @Test func aFailedLegacyDeleteFailsRemove() async throws {
        // The legacy-only key's delete throws: remove propagates it and
        // the metadata stays, so the key does not look deleted while the
        // fallback can still read it.
        let secrets = StubSecrets()
        let dir = try TempDir()
        defer { withExtendedLifetime(dir) {} }
        let credentials = ServiceCredentials(
            secrets: secrets, service: "svc", legacyService: "legacy",
            metadataURL: dir.url.appending(path: "config/services.json"))
        try await credentials.set("K", value: "tok")
        await secrets.moveItem(fromService: "svc", toService: "legacy", account: "K")
        await secrets.setNextDeleteError(
            SweError.commandFailed(status: 1, message: "keychain locked"))
        await #expect(throws: (any Error).self) {
            try await credentials.remove("K")
        }
        let status = await credentials.status(for: "K")
        #expect(status.isSet)
        #expect(status.added != nil)
    }

    @Test func probeCallsDevinSelfWithTheBearerToken() async throws {
        let secrets = StubSecrets()
        let (dir, credentials) = try make(secrets)
        defer { withExtendedLifetime(dir) {} }
        try await credentials.set("DEVIN_API_KEY", value: "tok")
        let transport = ProbeTransport(.success((Data(), 200)))
        let devin = try #require(ServiceCredentials.services.first { $0.id == "devin" })
        #expect(await credentials.probe(devin, transport: transport) == .works)
        let request = try #require(transport.requests.first)
        #expect(request.url?.absoluteString == "https://api.devin.ai/v3/self")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer tok")
    }

    @Test func probeReportsHTTPAndNetworkFailures() async throws {
        let secrets = StubSecrets()
        let (dir, credentials) = try make(secrets)
        defer { withExtendedLifetime(dir) {} }
        try await credentials.set("DEVIN_API_KEY", value: "tok")
        let devin = try #require(ServiceCredentials.services.first { $0.id == "devin" })
        #expect(await credentials.probe(devin, transport: ProbeTransport(.success((Data(), 401)))) == .httpError(401))
        let result = await credentials.probe(devin, transport: ProbeTransport(.failure(URLError(.timedOut))))
        guard case .unavailable(let message) = result else {
            Issue.record("expected unavailable, got \(result)")
            return
        }
        #expect(!message.contains("tok"))
    }

    @Test func probeWithoutAKeySaysSo() async throws {
        let secrets = StubSecrets()
        let (dir, credentials) = try make(secrets)
        defer { withExtendedLifetime(dir) {} }
        let devin = try #require(ServiceCredentials.services.first { $0.id == "devin" })
        #expect(await credentials.probe(devin, transport: ProbeTransport(.success((Data(), 200))))
                == .unavailable("DEVIN_API_KEY is not saved."))
    }

    @Test func artificialAnalysisSendsItsKeyHeader() async throws {
        let secrets = StubSecrets()
        let (dir, credentials) = try make(secrets)
        defer { withExtendedLifetime(dir) {} }
        try await credentials.set("ARTIFICIAL_ANALYSIS_API_KEY", value: "aa-key")
        let transport = ProbeTransport(.success((Data(), 200)))
        let service = try #require(ServiceCredentials.services.first { $0.id == "artificial-analysis" })
        #expect(service.probe?.label == "Test (uses 1 of 100 daily requests)")
        #expect(await credentials.probe(service, transport: transport) == .works)
        let request = try #require(transport.requests.first)
        #expect(request.url?.absoluteString == "https://artificialanalysis.ai/api/v2/language/models/free")
        #expect(request.value(forHTTPHeaderField: "x-api-key") == "aa-key")
    }
}

/// The Services section's state: a probe's result belongs to the value it
/// ran against, so replacing or removing the key clears it — even while
/// the request is still flying — and a test refreshes the key's dates.
@Suite @MainActor struct ServicesModelTests {
    /// A keychain stub that parks the first read of one account at
    /// `gate` until `release` answers it, so a reload can be caught
    /// mid-read while a save, remove or another reload runs. The test
    /// waits with `gate.arrived()`, which gives up after `gateTimeout`.
    private actor GatedSecrets: SecretStoring {
        var items: [String: String] = [:]
        nonisolated let gate = HoldGate()
        private var holdAccount: String?
        private var held = false
        private var answer: String?

        func hold(_ account: String) { holdAccount = account }

        private func key(_ service: String, _ account: String) -> String {
            "\(service)\u{0}\(account)"
        }

        func genericPassword(service: String, account: String) async -> String? {
            if account == holdAccount, !held {
                held = true
                try? await gate.pass()
                return answer
            }
            return items[key(service, account)]
        }
        func setGenericPassword(service: String, account: String, value: String) async throws {
            items[key(service, account)] = value
        }
        func deleteGenericPassword(service: String, account: String) async throws {
            items.removeValue(forKey: key(service, account))
        }

        /// Answers the parked read.
        func release(value: String?) {
            answer = value
            gate.open()
        }
    }

    private func devin() throws -> KnownService {
        try #require(ServiceCredentials.services.first { $0.id == "devin" })
    }

    /// A probe transport that parks `send` at `gate` until the test
    /// opens it, then answers 200, so a test can change the key while the
    /// request is in flight.
    private final class GatedTransport: HTTPTransport, Sendable {
        let gate = HoldGate()

        func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
            try await gate.pass()
            return (Data(), HTTPURLResponse(
                url: URL(string: "https://probe.test")!, statusCode: 200,
                httpVersion: nil, headerFields: nil)!)
        }
    }

    private func make(now: @escaping @Sendable () -> Date = {
        Date(timeIntervalSince1970: 1_791_000_000)
    }) throws -> (TempDir, ServiceCredentials, ServicesModel) {
        let dir = try TempDir()
        let credentials = ServiceCredentials(
            secrets: StubSecrets(), service: "com.cwenlong.swe.services.test",
            metadataURL: dir.url.appending(path: "config/services.json"), now: now)
        return (dir, credentials, ServicesModel(
            credentials: credentials, transport: ProbeTransport(.success((Data(), 200)))))
    }

    @Test func replacingOrRemovingTheKeyClearsAWorksResult() async throws {
        let (dir, _, model) = try make()
        defer { withExtendedLifetime(dir) {} }
        let devin = try devin()
        await model.set("DEVIN_API_KEY", value: "one")
        await model.probe(devin)
        #expect(model.probeResults["devin"] == .works)

        // A new value hasn't been tested: the old answer must go.
        await model.set("DEVIN_API_KEY", value: "two")
        #expect(model.probeResults["devin"] == nil)

        await model.probe(devin)
        #expect(model.probeResults["devin"] == .works)
        await model.remove("DEVIN_API_KEY")
        #expect(model.probeResults["devin"] == nil)
    }

    @Test func anInFlightProbeCantWriteOverANewerKey() async throws {
        let dir = try TempDir()
        defer { withExtendedLifetime(dir) {} }
        let credentials = ServiceCredentials(
            secrets: StubSecrets(), service: "svc",
            metadataURL: dir.url.appending(path: "config/services.json"))
        let transport = GatedTransport()
        let model = ServicesModel(credentials: credentials, transport: transport)
        let devin = try devin()
        await model.set("DEVIN_API_KEY", value: "one")

        // The probe is parked inside the request when the key changes.
        let probe = Task { await model.probe(devin) }
        #expect(await transport.gate.arrived())
        await model.set("DEVIN_API_KEY", value: "two")
        transport.gate.open()
        await probe.value

        // The 200 answered the old key; showing "Works" for it would be wrong.
        #expect(model.probeResults["devin"] == nil)
        #expect(model.statuses["DEVIN_API_KEY"]?.isSet == true)
    }

    @Test func aProbeRefreshesTheKeysLastUsedDate() async throws {
        final class Clock: @unchecked Sendable {
            var date = Date(timeIntervalSince1970: 1_791_000_000)
        }
        let clock = Clock()
        let (dir, _, model) = try make(now: { clock.date })
        defer { withExtendedLifetime(dir) {} }
        let devin = try devin()
        await model.set("DEVIN_API_KEY", value: "tok")
        #expect(model.statuses["DEVIN_API_KEY"]?.lastUsed == nil)

        clock.date = Date(timeIntervalSince1970: 1_792_000_000)
        await model.probe(devin)
        #expect(model.statuses["DEVIN_API_KEY"]?.lastUsed
                    == Date(timeIntervalSince1970: 1_792_000_000))
    }

    @Test func aRemoveDuringReloadKeepsTheNewerStatus() async throws {
        let dir = try TempDir()
        defer { withExtendedLifetime(dir) {} }
        let secrets = GatedSecrets()
        try await secrets.setGenericPassword(
            service: "svc", account: "DEVIN_API_KEY", value: "tok")
        let credentials = ServiceCredentials(
            secrets: secrets, service: "svc",
            metadataURL: dir.url.appending(path: "config/services.json"))
        let model = ServicesModel(
            credentials: credentials, transport: ProbeTransport(.success((Data(), 200))))
        // The reload parks reading the later field with the earlier
        // field's pre-remove status already collected.
        await secrets.hold("DEVIN_ORG_ID")
        let reload = Task { await model.reload() }
        #expect(await secrets.gate.arrived())
        await model.remove("DEVIN_API_KEY")
        await secrets.release(value: nil)
        await reload.value
        // The stale "isSet" the parked read collected must not come back.
        #expect(model.statuses["DEVIN_API_KEY"]?.isSet == false)
        #expect(model.canProbe(try devin()) == false)
    }

    @Test func anOlderReloadCannotOverwriteANewerOne() async throws {
        let dir = try TempDir()
        defer { withExtendedLifetime(dir) {} }
        let secrets = GatedSecrets()
        let credentials = ServiceCredentials(
            secrets: secrets, service: "svc",
            metadataURL: dir.url.appending(path: "config/services.json"))
        let model = ServicesModel(
            credentials: credentials, transport: ProbeTransport(.success((Data(), 200))))
        // Round 1 parks on the last field; round 2 finishes first.
        await secrets.hold("ARTIFICIAL_ANALYSIS_API_KEY")
        let old = Task { await model.reload() }
        #expect(await secrets.gate.arrived())
        // Written straight to the store, so no field stamp moves.
        try await secrets.setGenericPassword(
            service: "svc", account: "ARTIFICIAL_ANALYSIS_API_KEY", value: "k")
        await model.reload()
        #expect(model.statuses["ARTIFICIAL_ANALYSIS_API_KEY"]?.isSet == true)
        // The parked round now answers nil — discarded wholesale.
        await secrets.release(value: nil)
        await old.value
        #expect(model.statuses["ARTIFICIAL_ANALYSIS_API_KEY"]?.isSet == true)
    }
}

/// SWE's service keys write through `SecItemKeychain` in-process;
/// `KeychainCLI` no longer writes, so a value never sits on `security`'s
/// argv where `ps` could read it.
@Suite struct ServiceKeychainTests {
    /// Services moved under Integrations; an old saved section still
    /// lands there.
    @Test func servicesLiveUnderIntegrations() {
        #expect(SettingsSection(saved: "services") == .integrations)
    }
}
