import Foundation
import Synchronization
import Testing
@testable import QuiverCore

/// Every key here is synthetic. Nothing in this file touches the
/// network: every check goes through a fake transport.
@Suite struct APIKeyProbeTableTests {
    /// Every environment variable name the probe table covers.
    let names = [
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY", "XAI_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY",
        "CEREBRAS_API_KEY", "TOGETHER_AI_API_KEY", "TOGETHER_API_KEY",
        "FIREWORKS_AI_API_KEY", "FIREWORKS_API_KEY", "OPENROUTER_API_KEY",
        "KIMI_API_KEY", "MOONSHOT_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY",
        "ZAI_API_KEY", "ZHIPU_API_KEY", "MINIMAX_API_KEY", "MIMO_API_KEY",
        "STEPFUN_API_KEY", "UPSTAGE_API_KEY", "OPENCODE_ZEN_API_KEY",
        "VERCEL_GATEWAY_API_KEY", "AI_GATEWAY_API_KEY", "NEBIUS_API_KEY",
        "FEATHERLESS_API_KEY", "ROUTING_RUN_API_KEY",
    ]

    @Test func everyTableNameResolvesToAProbe() {
        for name in names {
            let probe = APIKeyProbe.probe(forName: name)
            #expect(probe != nil, "\(name) has no probe")
            #expect(probe?.hosts.isEmpty == false, "\(name) has no hosts")
            #expect(probe?.hosts.allSatisfy { $0.scheme == "https" } == true,
                    "\(name) has a non-https host")
        }
    }

    @Test func namesAreCaseSensitive() {
        #expect(APIKeyProbe.probe(forName: "openai_api_key") == nil)
        #expect(APIKeyProbe.probe(forName: "Openai_Api_Key") == nil)
    }

    @Test func providersWithSeveralHostsKeepThemInOrder() {
        #expect(APIKeyProbe.probe(forName: "MOONSHOT_API_KEY")?.hosts.map(\.host) ==
                    ["api.moonshot.ai", "api.moonshot.cn"])
        #expect(APIKeyProbe.probe(forName: "MINIMAX_API_KEY")?.hosts.map(\.host) ==
                    ["api.minimax.io", "api.minimaxi.com"])
        #expect(APIKeyProbe.probe(forName: "NEBIUS_API_KEY")?.hosts.map(\.host) ==
                    ["api.tokenfactory.nebius.com", "api.studio.nebius.com"])
    }

    @Test func nvidiaHasNoProbeAndItsOwnReason() {
        #expect(APIKeyProbe.probe(forName: "NVIDIA_API_KEY") == nil)
        #expect(APIKeyProbe.noCheckReason(forName: "NVIDIA_API_KEY") ==
                    "NVIDIA's API doesn't check the key on a free request.")
    }

    @Test func anUnknownNameHasNoProbeAndTheGenericReason() {
        #expect(APIKeyProbe.probe(forName: "SWEBENCH_API_KEY") == nil)
        #expect(APIKeyProbe.noCheckReason(forName: "SWEBENCH_API_KEY") ==
                    "Quiver doesn't know how to check this key.")
    }
}

@Suite struct APIKeyCheckRequestTests {
    let key = "sk-fake-TEST-KEY-0001"

    @Test func aBearerProbeSendsAnAuthorizationHeader() async {
        let transport = FakeTransport()
        transport.reply(200)
        let probe = try! #require(APIKeyProbe.probe(forName: "OPENAI_API_KEY"))
        _ = await APIKeyChecker(transport: transport).check(key: key, probe: probe)
        let request = try! #require(transport.requests.first)
        #expect(request.httpMethod == "GET")
        #expect(request.url?.absoluteString == "https://api.openai.com/v1/models")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer \(key)")
    }

    @Test func anAnthropicProbeSendsXApiKeyAndTheVersionHeader() async {
        let transport = FakeTransport()
        transport.reply(200)
        let probe = try! #require(APIKeyProbe.probe(forName: "ANTHROPIC_API_KEY"))
        _ = await APIKeyChecker(transport: transport).check(key: key, probe: probe)
        let request = try! #require(transport.requests.first)
        #expect(request.value(forHTTPHeaderField: "x-api-key") == key)
        #expect(request.value(forHTTPHeaderField: "anthropic-version") == "2023-06-01")
    }

    @Test func aGoogleProbeSendsXGoogApiKey() async {
        let transport = FakeTransport()
        transport.reply(200)
        let probe = try! #require(APIKeyProbe.probe(forName: "GEMINI_API_KEY"))
        _ = await APIKeyChecker(transport: transport).check(key: key, probe: probe)
        let request = try! #require(transport.requests.first)
        #expect(request.value(forHTTPHeaderField: "x-goog-api-key") == key)
    }

    @Test func routingRunPostsAnEmptyJsonBody() async {
        let transport = FakeTransport()
        transport.reply(200)
        let probe = try! #require(APIKeyProbe.probe(forName: "ROUTING_RUN_API_KEY"))
        _ = await APIKeyChecker(transport: transport).check(key: key, probe: probe)
        let request = try! #require(transport.requests.first)
        #expect(request.httpMethod == "POST")
        #expect(request.url?.absoluteString == "https://api.routing.run/v1/chat/completions")
        #expect(request.httpBody == Data("{}".utf8))
        #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer \(key)")
    }

    @Test func requestsTimeOutAfterTenSeconds() async {
        let transport = FakeTransport()
        transport.reply(200)
        let probe = try! #require(APIKeyProbe.probe(forName: "OPENAI_API_KEY"))
        _ = await APIKeyChecker(transport: transport).check(key: key, probe: probe)
        #expect(transport.requests.first?.timeoutInterval == 10)
    }
}

@Suite struct APIKeyCheckClassifyTests {
    let key = "sk-fake-TEST-KEY-0002"

    func outcome(_ name: String, _ status: Int, body: String = "{}") async throws -> APIKeyCheckResult {
        let transport = FakeTransport()
        transport.reply(status, body: Data(body.utf8))
        let probe = try #require(APIKeyProbe.probe(forName: name))
        return await APIKeyChecker(transport: transport).check(key: key, probe: probe)
    }

    @Test func a200Works() async throws {
        #expect(try await outcome("OPENAI_API_KEY", 200) == .works)
    }

    @Test func a429WorksBecauseTheKeyWasRead() async throws {
        #expect(try await outcome("OPENAI_API_KEY", 429) == .works)
    }

    @Test func a402MeansNoCredit() async throws {
        #expect(try await outcome("OPENAI_API_KEY", 402) == .noCredit)
    }

    @Test func a401And403Reject() async throws {
        for status in [401, 403] {
            guard case .rejected(let got, _) = try await outcome("OPENAI_API_KEY", status) else {
                Issue.record("\(status) did not reject")
                return
            }
            #expect(got == status)
        }
    }

    @Test func geminisApiKeyNotValid400Rejects() async throws {
        let body = #"{"error":{"code":400,"message":"API key not valid. Please pass a valid API key."}}"#
        guard case .rejected(let status, let message) = try await outcome("GEMINI_API_KEY", 400, body: body)
        else { Issue.record("did not reject"); return }
        #expect(status == 400)
        #expect(message?.contains("API key not valid") == true)
    }

    @Test func xaisIncorrectApiKey400Rejects() async throws {
        let body = #"{"error":{"message":"Incorrect API key provided: sk-fake. You can find your API key at https://console.x.ai."}}"#
        guard case .rejected(let status, _) = try await outcome("XAI_API_KEY", 400, body: body)
        else { Issue.record("did not reject"); return }
        #expect(status == 400)
    }

    @Test func aGeneric400CouldNotCheck() async throws {
        guard case .couldNotCheck = try await outcome("OPENAI_API_KEY", 400) else {
            Issue.record("expected couldNotCheck")
            return
        }
    }

    @Test func routingRun400And422Work() async throws {
        #expect(try await outcome("ROUTING_RUN_API_KEY", 400) == .works)
        #expect(try await outcome("ROUTING_RUN_API_KEY", 422) == .works)
    }

    @Test func a500CouldNotCheck() async throws {
        guard case .couldNotCheck(let reason) = try await outcome("OPENAI_API_KEY", 500) else {
            Issue.record("expected couldNotCheck")
            return
        }
        #expect(reason.contains("HTTP 500"))
    }

    @Test func aTimeoutCouldNotCheck() async throws {
        let transport = FakeTransport()
        transport.fail(.timedOut)
        let probe = try #require(APIKeyProbe.probe(forName: "OPENAI_API_KEY"))
        guard case .couldNotCheck = await APIKeyChecker(transport: transport)
            .check(key: key, probe: probe)
        else { Issue.record("expected couldNotCheck"); return }
    }

    @Test func aRejectionCarriesTheErrorMessageButNeverTheKey() async throws {
        let body = #"{"error":{"message":"Invalid authentication credentials for sk-fake-TEST-KEY-0002 provided"}}"#
        guard case .rejected(_, let extracted) = try await outcome("OPENAI_API_KEY", 401, body: body)
        else { Issue.record("did not reject"); return }
        let message = try #require(extracted)
        #expect(!message.contains(key))
        #expect(message.contains("Invalid authentication credentials"))
    }

    @Test func aMessageInAnotherFieldIsFoundAndTrimmed() {
        let long = String(repeating: "x", count: 200)
        let body = Data(#"{"detail":"\#(long)"}"#.utf8)
        let message = try! #require(APIKeyProbe.errorMessage(in: body, key: key))
        #expect(message.count <= 121)
        #expect(message.hasSuffix("…"))
    }

    @Test func aMaskedEchoMayRemain() {
        let body = Data(#"{"message":"bad key ****0000"}"#.utf8)
        let message = APIKeyProbe.errorMessage(in: body, key: key)
        #expect(message == "bad key ****0000")
    }
}

@Suite struct APIKeyCheckHostTests {
    let key = "sk-fake-TEST-KEY-0003"

    @Test func theSecondHostIsTriedWhenTheFirstRejects() async throws {
        let transport = FakeTransport()
        transport.reply(401)
        transport.reply(200)
        let probe = try #require(APIKeyProbe.probe(forName: "MOONSHOT_API_KEY"))
        let result = await APIKeyChecker(transport: transport).check(key: key, probe: probe)
        #expect(result == .works)
        #expect(transport.requests.count == 2)
        #expect(transport.requests[1].url?.host == "api.moonshot.cn")
    }

    @Test func everyHostRejectingMeansRejected() async throws {
        let transport = FakeTransport()
        transport.reply(401)
        transport.reply(403)
        let probe = try #require(APIKeyProbe.probe(forName: "MOONSHOT_API_KEY"))
        let result = await APIKeyChecker(transport: transport).check(key: key, probe: probe)
        guard case .rejected = result else { Issue.record("expected rejected"); return }
        #expect(transport.requests.count == 2)
    }

    @Test func aRejectionPlusAFailedCheckCouldNotCheck() async throws {
        let transport = FakeTransport()
        transport.reply(401)
        transport.fail(.cannotConnectToHost)
        let probe = try #require(APIKeyProbe.probe(forName: "MOONSHOT_API_KEY"))
        let result = await APIKeyChecker(transport: transport).check(key: key, probe: probe)
        guard case .couldNotCheck = result else { Issue.record("expected couldNotCheck"); return }
        #expect(transport.requests.count == 2)
    }

    @Test func aWorkingFirstHostMeansTheSecondIsNotCalled() async throws {
        let transport = FakeTransport()
        transport.reply(200)
        let probe = try #require(APIKeyProbe.probe(forName: "MOONSHOT_API_KEY"))
        let result = await APIKeyChecker(transport: transport).check(key: key, probe: probe)
        #expect(result == .works)
        #expect(transport.requests.count == 1)
    }
}

/// Holds every request parked until released, so in-flight counts and
/// queueing behind the limiter are observable.
final class GateTransport: HTTPTransport {
    private let state = Mutex<(inFlight: Int, peak: Int, requests: [URLRequest],
                              waiters: [CheckedContinuation<Void, Never>])>((0, 0, [], []))

    var peak: Int { state.withLock { $0.peak } }
    var inFlight: Int { state.withLock { $0.inFlight } }
    var requestCount: Int { state.withLock { $0.requests.count } }

    /// Answer `n` parked requests, or all of them.
    func release(_ n: Int = Int.max) {
        let released = state.withLock { s -> [CheckedContinuation<Void, Never>] in
            let count = min(n, s.waiters.count)
            let first = Array(s.waiters.prefix(count))
            s.waiters.removeFirst(count)
            return first
        }
        for waiter in released { waiter.resume() }
    }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        // Count and park under one lock: a release between the two could
        // run before the waiter existed and leave this send parked.
        await withCheckedContinuation { waiter in
            state.withLock { s in
                s.inFlight += 1
                s.peak = max(s.peak, s.inFlight)
                s.requests.append(request)
                s.waiters.append(waiter)
            }
        }
        state.withLock { $0.inFlight -= 1 }
        return (Data(), HTTPURLResponse(url: request.url!, statusCode: 200,
                                        httpVersion: nil, headerFields: nil)!)
    }
}

/// Counts how many requests run at once.
final class CountingTransport: HTTPTransport {
    private let state = Mutex<(inFlight: Int, peak: Int, requests: [URLRequest])>((0, 0, []))

    var peak: Int { state.withLock { $0.peak } }
    var requests: [URLRequest] { state.withLock { $0.requests } }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        state.withLock { s in
            s.inFlight += 1
            s.peak = max(s.peak, s.inFlight)
            s.requests.append(request)
        }
        defer { state.withLock { $0.inFlight -= 1 } }
        try? await Task.sleep(for: .milliseconds(20))
        return (Data(), HTTPURLResponse(url: request.url!, statusCode: 200,
                                        httpVersion: nil, headerFields: nil)!)
    }
}

@MainActor
@Suite struct APIKeyCheckModelTests {
    func model(text: String, transport: HTTPTransport) throws -> APIKeysModel {
        let dir = FileManager.default.temporaryDirectory.appending(path: "keycheck-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appending(path: ".api_keys")
        try text.write(to: url, atomically: true, encoding: .utf8)
        let store = APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups"))
        return APIKeysModel(store: store, shell: FakeShell(environment: [:]),
                            checker: APIKeyChecker(transport: transport),
                            checkCache: APIKeyCheckStore(url: dir.appending(path: "checks.json")),
                            defaults: UserDefaults(suiteName: "keycheck-\(UUID().uuidString)")!)
    }

    @Test func checkAllSkipsEmptyValues() async throws {
        let transport = FakeTransport()
        let model = try model(text: """
        # XAI
        export XAI_API_KEY="sk-fake-TEST-KEY-0004"
        export OPENAI_API_KEY=""
        """, transport: transport)
        await model.load()
        transport.reply(200)
        await model.checkAll()
        #expect(transport.requests.count == 1)
        #expect(model.checks["XAI_API_KEY"]?.result == .works)
        #expect(model.checks["OPENAI_API_KEY"] == nil)
        #expect(!model.isChecking)
    }

    @Test func keysWithNoProbeAreMarkedWithoutTheNetwork() async throws {
        let transport = FakeTransport()
        let model = try model(text: """
        # NVIDIA
        export NVIDIA_API_KEY="nvapi-fake-TEST-0005"
        # SWE-bench
        export SWEBENCH_API_KEY="swe-fake-TEST-0005"
        """, transport: transport)
        await model.load()
        await model.checkAll()
        #expect(transport.requests.isEmpty)
        #expect(model.checks["NVIDIA_API_KEY"]?.result ==
                    .noCheck("NVIDIA's API doesn't check the key on a free request."))
        #expect(model.checks["SWEBENCH_API_KEY"]?.result ==
                    .noCheck("Quiver doesn't know how to check this key."))
    }

    @Test func atMostFourChecksRunAtOnce() async throws {
        let transport = CountingTransport()
        let model = try model(text: """
        export OPENAI_API_KEY="sk-fake-A"
        export ANTHROPIC_API_KEY="sk-fake-B"
        export GEMINI_API_KEY="AIza-fake-C"
        export XAI_API_KEY="sk-fake-D"
        export GROQ_API_KEY="gsk-fake-E"
        export MISTRAL_API_KEY="sk-fake-F"
        export DEEPSEEK_API_KEY="sk-fake-G"
        """, transport: transport)
        await model.load()
        await model.checkAll()
        #expect(transport.requests.count == 7)
        #expect(transport.peak <= 4)
        #expect(transport.peak > 1)
        #expect(model.rows.allSatisfy { model.check(for: $0)?.result == .works })
    }

    @Test func editingAValueOutdatesItsResult() async throws {
        let transport = FakeTransport()
        let model = try model(text: """
        export OPENAI_API_KEY="sk-fake-OLD-KEY"
        """, transport: transport)
        await model.load()
        transport.reply(200)
        await model.checkAll()
        let row = try #require(model.rows.first)
        #expect(model.check(for: row)?.result == .works)
        try await model.set(name: "OPENAI_API_KEY", value: "sk-fake-NEW-KEY", provider: "OPENAI")
        let updated = try #require(model.rows.first)
        #expect(model.check(for: updated) == nil)
        #expect(model.checks["OPENAI_API_KEY"]?.isCurrent(for: "sk-fake-NEW-KEY", salt: model.checkSalt) == false)
        #expect(model.checks["OPENAI_API_KEY"]?.isCurrent(for: "sk-fake-OLD-KEY", salt: model.checkSalt) == true)
    }

    @Test func aChipClickSharesTheSameLimit() async throws {
        let transport = GateTransport()
        let model = try model(text: """
        export OPENAI_API_KEY="sk-fake-A"
        export ANTHROPIC_API_KEY="sk-fake-B"
        export GEMINI_API_KEY="AIza-fake-C"
        export XAI_API_KEY="sk-fake-D"
        export GROQ_API_KEY="gsk-fake-E"
        export MISTRAL_API_KEY="sk-fake-F"
        """, transport: transport)
        await model.load()
        let all = Task { await model.checkAll() }
        while transport.inFlight < 4 { await Task.yield() }
        // Two answer; their slots hand over to the pair still queued.
        transport.release(2)
        while transport.requestCount < 6 || transport.inFlight < 4 { await Task.yield() }
        while model.checks.count < 2 { await Task.yield() }
        // Re-checking finished rows queues at the same limiter: no extra
        // request runs while four are parked.
        let finished = model.checks.keys.sorted().prefix(2)
        let again = finished.map { name in Task { await model.check(name: name) } }
        while !finished.allSatisfy({ model.checking.contains($0) }) { await Task.yield() }
        #expect(transport.inFlight == 4)
        #expect(transport.requestCount == 6)
        transport.release()
        while transport.requestCount < 8 { await Task.yield() }
        transport.release()
        await all.value
        for task in again { await task.value }
        #expect(transport.peak == 4)
        #expect(model.rows.allSatisfy { model.check(for: $0)?.result == .works })
        #expect(!model.isChecking)
    }

    @Test func checkingOneKeyOnlySendsItsOwnRequest() async throws {
        let transport = FakeTransport()
        let model = try model(text: """
        export OPENAI_API_KEY="sk-fake-TEST-KEY-0006"
        export XAI_API_KEY="sk-fake-TEST-KEY-0007"
        """, transport: transport)
        await model.load()
        transport.reply(401)
        await model.check(name: "XAI_API_KEY")
        #expect(transport.requests.count == 1)
        #expect(transport.requests.first?.url?.host == "api.x.ai")
        guard case .rejected = model.checks["XAI_API_KEY"]?.result else {
            Issue.record("expected rejected")
            return
        }
        #expect(model.checks["OPENAI_API_KEY"] == nil)
    }
}

/// The cache under ~/.quiver/cache and the salt the fingerprints hash against.
@Suite struct APIKeyCheckCacheTests {
    let salt = Data(repeating: 7, count: 32)

    @Test func aSavedResultComesBackFromDisk() throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: "keycache-\(UUID().uuidString)")
        let store = APIKeyCheckStore(url: dir.appending(path: "api-key-checks.json"))
        let check = APIKeyCheck(result: .rejected(status: 401, message: "bad key"),
                                checkedAt: Date(timeIntervalSince1970: 1_700_000_000),
                                value: "sk-fake-OLD", salt: salt)
        let keysURL = dir.appending(path: ".api_keys")
        store.save(["OPENAI_API_KEY": check], for: keysURL,
                   currentNames: ["OPENAI_API_KEY"])
        #expect(store.load(for: keysURL)["OPENAI_API_KEY"] == check)
        // Another keys file sees nothing: sections are per file.
        #expect(store.load(for: dir.appending(path: "other/.api_keys")) == [:])
        // The file holds the fingerprint, never the value.
        let text = try String(contentsOf: store.url, encoding: .utf8)
        #expect(!text.contains("sk-fake-OLD"))
        #expect(text.contains(check.fingerprint))
    }

    @Test func everyResultShapeSurvivesTheRoundTrip() throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: "keycache-\(UUID().uuidString)")
        let store = APIKeyCheckStore(url: dir.appending(path: "api-key-checks.json"))
        let checks: [String: APIKeyCheck] = [
            "A": APIKeyCheck(result: .works, checkedAt: .now, value: "a", salt: salt),
            "B": APIKeyCheck(result: .noCredit, checkedAt: .now, value: "b", salt: salt),
            "C": APIKeyCheck(result: .rejected(status: 403, message: nil),
                             checkedAt: .now, value: "c", salt: salt),
            "D": APIKeyCheck(result: .couldNotCheck("timed out"), checkedAt: .now,
                             value: "d", salt: salt),
            "E": APIKeyCheck(result: .noCheck("no probe"), checkedAt: .now,
                             value: "e", salt: salt),
        ]
        let keysURL = dir.appending(path: ".api_keys")
        store.save(checks, for: keysURL, currentNames: Set(checks.keys))
        #expect(store.load(for: keysURL) == checks)
    }

    @Test func junkOnDiskReadsAsNoResults() throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: "keycache-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appending(path: "api-key-checks.json")
        let keysURL = dir.appending(path: ".api_keys")
        try "this is not json".write(to: url, atomically: true, encoding: .utf8)
        #expect(APIKeyCheckStore(url: url).load(for: keysURL) == [:])
        try #"{"version": 99, "checks": {}}"#.write(to: url, atomically: true, encoding: .utf8)
        #expect(APIKeyCheckStore(url: url).load(for: keysURL) == [:])
        // A version 1 file is retired, not migrated.
        try #"{"version": 1, "checks": {"A": {}}}"#.write(to: url, atomically: true, encoding: .utf8)
        #expect(APIKeyCheckStore(url: url).load(for: keysURL) == [:])
    }

    @Test func theSaltIsMadeOnceAndReused() {
        let suite = "keysalt-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let first = APIKeyCheckStore.salt(from: defaults)
        #expect(first.count == 32)
        #expect(APIKeyCheckStore.salt(from: defaults) == first)
        let other = UserDefaults(suiteName: "keysalt-\(UUID().uuidString)")!
        #expect(APIKeyCheckStore.salt(from: other) != first)
    }

    @MainActor
    @Test func resultsSurviveANewModelAndStaleOnesStayHidden() async throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: "keycache-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let keysURL = dir.appending(path: ".api_keys")
        try """
        export OPENAI_API_KEY="sk-fake-OLD"
        export XAI_API_KEY="sk-fake-GONE"
        """.write(to: keysURL, atomically: true, encoding: .utf8)
        let cacheURL = dir.appending(path: "api-key-checks.json")
        let defaults = UserDefaults(suiteName: "keycache-\(UUID().uuidString)")!
        func make(_ transport: HTTPTransport) -> APIKeysModel {
            APIKeysModel(store: APIKeysStore(url: keysURL, backupDirectory: dir),
                         shell: FakeShell(environment: [:]),
                         checker: APIKeyChecker(transport: transport),
                         checkCache: APIKeyCheckStore(url: cacheURL), defaults: defaults)
        }
        let transport = FakeTransport()
        transport.reply(401)
        transport.reply(401)
        let first = make(transport)
        await first.load()
        await first.checkAll()

        // A second model over the same cache and salt shows the stored
        // results without asking the network again.
        let secondTransport = FakeTransport()
        let second = make(secondTransport)
        await second.load()
        #expect(second.check(for: second.rows[0])?.result == .rejected(status: 401, message: nil))
        #expect(second.check(for: second.rows[1])?.result == .rejected(status: 401, message: nil))
        // An edited value hides its stored result, and a name the file
        // dropped is pruned from the cache on the next save.
        try await second.set(name: "OPENAI_API_KEY", value: "sk-fake-NEW", provider: "OPENAI")
        #expect(second.check(for: second.rows[0]) == nil)
        try await second.remove(name: "XAI_API_KEY")
        secondTransport.reply(200)
        await second.check(name: "OPENAI_API_KEY")
        let stored = APIKeyCheckStore(url: cacheURL).load(for: keysURL)
        #expect(stored["XAI_API_KEY"] == nil)
        #expect(stored["OPENAI_API_KEY"]?.result == .works)
    }

    @MainActor
    @Test func aRunWithNothingToCheckStillSaves() async throws {
        // Every key lacks a probe, so no network task ever finishes; the
        // single save at the end must still land the "no check" answers.
        let dir = FileManager.default.temporaryDirectory.appending(path: "keycache-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let keysURL = dir.appending(path: ".api_keys")
        try """
        export MYSTERY_TOKEN="abc123"
        """.write(to: keysURL, atomically: true, encoding: .utf8)
        let cacheURL = dir.appending(path: "api-key-checks.json")
        let defaults = UserDefaults(suiteName: "keycache-\(UUID().uuidString)")!
        func make(_ transport: HTTPTransport) -> APIKeysModel {
            APIKeysModel(store: APIKeysStore(url: keysURL, backupDirectory: dir),
                         shell: FakeShell(environment: [:]),
                         checker: APIKeyChecker(transport: transport),
                         checkCache: APIKeyCheckStore(url: cacheURL), defaults: defaults)
        }
        let first = make(FakeTransport())
        await first.load()
        await first.checkAll()
        #expect(first.check(for: first.rows[0])?.result
                == .noCheck("Quiver doesn't know how to check this key."))

        let second = make(FakeTransport())
        await second.load()
        guard case .noCheck = second.check(for: second.rows[0])?.result else {
            Issue.record("the stored no-check result did not come back")
            return
        }
    }

    @MainActor
    @Test func finishedChecksSaveBeforeTheRunEnds() async throws {
        // Two of four checks land while the rest stay parked; a model
        // built over the same cache sees the finished pair already.
        let dir = FileManager.default.temporaryDirectory.appending(path: "keycache-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let keysURL = dir.appending(path: ".api_keys")
        try """
        export OPENAI_API_KEY="sk-1"
        export ANTHROPIC_API_KEY="sk-2"
        export GEMINI_API_KEY="sk-3"
        export XAI_API_KEY="sk-4"
        """.write(to: keysURL, atomically: true, encoding: .utf8)
        let cacheURL = dir.appending(path: "api-key-checks.json")
        let defaults = UserDefaults(suiteName: "keycache-\(UUID().uuidString)")!
        func make(_ transport: HTTPTransport) -> APIKeysModel {
            APIKeysModel(store: APIKeysStore(url: keysURL, backupDirectory: dir),
                         shell: FakeShell(environment: [:]),
                         checker: APIKeyChecker(transport: transport),
                         checkCache: APIKeyCheckStore(url: cacheURL), defaults: defaults)
        }
        let transport = GateTransport()
        let first = make(transport)
        await first.load()
        let run = Task { await first.checkAll() }
        for _ in 0..<2_000 where transport.requestCount < 4 {
            try await Task.sleep(for: .milliseconds(5))
        }
        #expect(transport.requestCount == 4)
        transport.release(2)
        for _ in 0..<2_000 where first.checks.count < 2 {
            try await Task.sleep(for: .milliseconds(5))
        }
        #expect(first.checks.count == 2)

        let second = make(FakeTransport())
        await second.load()
        #expect(second.rows.compactMap { second.check(for: $0) }.count == 2)
        transport.release()
        await run.value
        #expect(APIKeyCheckStore(url: cacheURL).load(for: keysURL).count == 4)
    }

    @MainActor
    @Test func twoKeysFilesShareTheCache() async throws {
        // One cache file, two keys files: each model writes only its own
        // section, so neither loses the other's results.
        let dir = FileManager.default.temporaryDirectory.appending(path: "keycache-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let keysA = dir.appending(path: "a/.api_keys")
        let keysB = dir.appending(path: "b/.api_keys")
        try FileManager.default.createDirectory(at: keysA.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: keysB.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "export OPENAI_API_KEY=\"sk-a\"".write(to: keysA, atomically: true, encoding: .utf8)
        try "export OPENAI_API_KEY=\"sk-b\"".write(to: keysB, atomically: true, encoding: .utf8)
        let cacheURL = dir.appending(path: "api-key-checks.json")
        let defaults = UserDefaults(suiteName: "keycache-\(UUID().uuidString)")!
        func make(_ keysURL: URL, _ transport: HTTPTransport) -> APIKeysModel {
            APIKeysModel(store: APIKeysStore(url: keysURL, backupDirectory: dir),
                         shell: FakeShell(environment: [:]),
                         checker: APIKeyChecker(transport: transport),
                         checkCache: APIKeyCheckStore(url: cacheURL), defaults: defaults)
        }
        let transportA = FakeTransport()
        transportA.reply(200)
        let modelA = make(keysA, transportA)
        await modelA.load()
        await modelA.checkAll()
        let transportB = FakeTransport()
        transportB.reply(401)
        let modelB = make(keysB, transportB)
        await modelB.load()
        await modelB.checkAll()

        let stored = APIKeyCheckStore(url: cacheURL)
        #expect(stored.load(for: keysA)["OPENAI_API_KEY"]?.result == .works)
        #expect(stored.load(for: keysB)["OPENAI_API_KEY"]?.result
                == .rejected(status: 401, message: nil))
    }

    @Test func aSaveKeepsTheNewerResultPerName() {
        // A writer holding an older snapshot merges by checkedAt: it can
        // replace only the names it checked, and only when it is newer.
        let dir = FileManager.default.temporaryDirectory.appending(path: "keycache-\(UUID().uuidString)")
        let store = APIKeyCheckStore(url: dir.appending(path: "api-key-checks.json"))
        let keysURL = dir.appending(path: ".api_keys")
        let newer = APIKeyCheck(result: .works,
                                checkedAt: Date(timeIntervalSince1970: 1_700_000_100),
                                value: "a", salt: salt)
        let older = APIKeyCheck(result: .rejected(status: 401, message: nil),
                                checkedAt: Date(timeIntervalSince1970: 1_700_000_000),
                                value: "a", salt: salt)
        let other = APIKeyCheck(result: .noCredit,
                                checkedAt: Date(timeIntervalSince1970: 1_700_000_050),
                                value: "b", salt: salt)
        store.save(["A": newer, "B": other], for: keysURL, currentNames: ["A", "B"])
        store.save(["A": older], for: keysURL, currentNames: ["A", "B"])
        let loaded = store.load(for: keysURL)
        #expect(loaded["A"] == newer)
        #expect(loaded["B"] == other)
        // Pruning is by the file's names: dropping B removes only B.
        store.save([:], for: keysURL, currentNames: ["A"])
        #expect(store.load(for: keysURL) == ["A": newer])
    }

    @Test func theLabelStyleFollowsTheResult() {
        #expect(APIKeyCheckStyle(.works) == .success)
        #expect(APIKeyCheckStyle(.noCredit) == .success)
        #expect(APIKeyCheckStyle(.rejected(status: 401, message: nil)) == .rejected)
        #expect(APIKeyCheckStyle(.couldNotCheck("timeout")) == .unchanged)
        #expect(APIKeyCheckStyle(.noCheck("no probe")) == .unchanged)
        #expect(APIKeyCheckStyle(nil) == .unchanged)
        #expect(APIKeyCheckResult.noCredit.words == "No credit on this account")
        #expect(APIKeyCheckResult.rejected(status: 401, message: "nope").words == "Rejected: HTTP 401: nope")
        #expect(APIKeyCheckResult.couldNotCheck("timed out").words == "Couldn't check: timed out")
        #expect(APIKeyCheckResult.noCheck("no probe").words == "No check: no probe")
    }
}
