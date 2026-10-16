import Foundation
import Testing
@testable import QuiverCore

/// An in-memory keychain; records the operation, never the value.
actor StubSecrets: SecretStoring {
    var items: [String: String] = [:]
    private(set) var calls: [[String]] = []
    private var nextError: Error?

    func setNextError(_ error: Error) { nextError = error }

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
    private func make(_ secrets: StubSecrets)
        throws -> (TempDir, ServiceCredentials) {
        let dir = try TempDir()
        return (dir, ServiceCredentials(
            secrets: secrets, service: "com.cwenlong.quiver.services.test",
            metadataURL: dir.url.appending(path: "config/services.json"),
            now: { Date(timeIntervalSince1970: 1_791_000_000) }))
    }

    @Test func aSavedKeyReadsBackWithItsDates() async throws {
        let secrets = StubSecrets()
        let added = Date(timeIntervalSince1970: 1_791_000_000)
        let dir = try TempDir()
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
        let (_, credentials) = try make(secrets)
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
        let (_, credentials) = try make(secrets)
        await secrets.setNextError(SweError.commandFailed(status: 1, message: "keychain locked"))
        do {
            try await credentials.set("DEVIN_API_KEY", value: "secret-token")
            Issue.record("the write should have thrown")
        } catch {
            #expect(!error.localizedDescription.contains("secret-token"))
        }
    }

    @Test func probeCallsDevinSelfWithTheBearerToken() async throws {
        let secrets = StubSecrets()
        let (_, credentials) = try make(secrets)
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
        let (_, credentials) = try make(secrets)
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
        let (_, credentials) = try make(secrets)
        let devin = try #require(ServiceCredentials.services.first { $0.id == "devin" })
        #expect(await credentials.probe(devin, transport: ProbeTransport(.success((Data(), 200))))
                == .unavailable("DEVIN_API_KEY is not saved."))
    }

    @Test func artificialAnalysisSendsItsKeyHeader() async throws {
        let secrets = StubSecrets()
        let (_, credentials) = try make(secrets)
        try await credentials.set("ARTIFICIAL_ANALYSIS_API_KEY", value: "aa-key")
        let transport = ProbeTransport(.success((Data(), 200)))
        let service = try #require(ServiceCredentials.services.first { $0.id == "artificial-analysis" })
        #expect(service.probe?.label == "Test (uses 1 of 1,000 daily requests)")
        #expect(await credentials.probe(service, transport: transport) == .works)
        let request = try #require(transport.requests.first)
        #expect(request.url?.absoluteString == "https://artificialanalysis.ai/api/v2/data/llms/models")
        #expect(request.value(forHTTPHeaderField: "x-api-key") == "aa-key")
    }
}

/// KeychainCLI's argv: writes upsert with `-w` under Quiver's service,
/// deletes tolerate a missing item, and no path logs the value.
@Suite struct ServiceKeychainTests {
    @Test func setUpsertsAGenericPassword() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: Data(), stderr: ""))
        try await KeychainCLI(runner: runner).setGenericPassword(
            service: "com.cwenlong.quiver.services.test", account: "DEVIN_API_KEY", value: "v")
        #expect(runner.calls == [["add-generic-password", "-U",
                                  "-s", "com.cwenlong.quiver.services.test",
                                  "-a", "DEVIN_API_KEY", "-w", "v",
                                  "-T", "/usr/bin/security"]])
    }

    @Test func deleteOnAMissingItemIsNotAnError() async throws {
        let runner = FakeRunner(.init(status: 44, stdout: Data(), stderr: "not found"))
        try await KeychainCLI(runner: runner).deleteGenericPassword(
            service: "com.cwenlong.quiver.services.test", account: "DEVIN_API_KEY")
        #expect(runner.calls == [["delete-generic-password",
                                  "-s", "com.cwenlong.quiver.services.test",
                                  "-a", "DEVIN_API_KEY"]])
    }

    @Test func aFailedWriteThrowsTheStderrNotTheValue() async throws {
        let runner = FakeRunner(.init(status: 1, stdout: Data(), stderr: "keychain locked"))
        do {
            try await KeychainCLI(runner: runner).setGenericPassword(
                service: "s", account: "DEVIN_API_KEY", value: "secret-value")
            Issue.record("the write should have thrown")
        } catch {
            #expect(!error.localizedDescription.contains("secret-value"))
        }
    }

    @Test func servicesIsASettingsSection() {
        #expect(SettingsGroup.order.contains(.services))
        #expect(!SettingsSection.services.title.isEmpty)
        #expect(!SettingsSection.services.detail.isEmpty)
    }
}
