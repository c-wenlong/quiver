import Foundation
import Synchronization
import Testing
@testable import QuiverCore

/// 2026-10-11T12:00:00Z, the moment every usage test starts at.
let usageEpoch = Date(timeIntervalSince1970: 1_791_720_000)

/// A clock the test moves by hand.
final class TestClock: Sendable {
    private let current: Mutex<Date>
    init(_ start: Date = usageEpoch) { current = Mutex(start) }
    var now: Date { current.withLock { $0 } }
    func advance(_ seconds: TimeInterval) { current.withLock { $0 += seconds } }
}

/// Stands in for the network: records every request and answers from a queue.
final class FakeTransport: HTTPTransport {
    enum Reply: Sendable {
        case http(Int, [String: String], Data)
        case failure(URLError.Code)
    }

    private let state = Mutex<(replies: [Reply], requests: [URLRequest])>(([], []))

    var requests: [URLRequest] { state.withLock { $0.requests } }

    func reply(_ status: Int, headers: [String: String] = [:], body: Data = Data("{}".utf8)) {
        state.withLock { $0.replies.append(.http(status, headers, body)) }
    }

    func fail(_ code: URLError.Code = .notConnectedToInternet) {
        state.withLock { $0.replies.append(.failure(code)) }
    }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let next: Reply? = state.withLock { s in
            s.requests.append(request)
            return s.replies.isEmpty ? nil : s.replies.removeFirst()
        }
        switch next {
        case .http(let status, let headers, let body):
            let response = HTTPURLResponse(
                url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
            return (body, response)
        case .failure(let code):
            throw URLError(code)
        case nil:
            Issue.record("unexpected request to \(request.url?.absoluteString ?? "?")")
            throw URLError(.cannotConnectToHost)
        }
    }
}

/// Stands in for the login keychain.
final class FakeSecrets: SecretReading {
    private let state = Mutex<(items: [String: String], lookups: [String])>(([:], []))

    /// Every `service|account` asked for, in order.
    var lookups: [String] { state.withLock { $0.lookups } }

    func set(service: String, account: String, _ value: String) {
        state.withLock { $0.items["\(service)|\(account)"] = value }
    }

    func genericPassword(service: String, account: String) async -> String? {
        state.withLock { s in
            s.lookups.append("\(service)|\(account)")
            return s.items["\(service)|\(account)"]
        }
    }
}

/// Stands in for the file system the credentials live on.
final class FakeFiles: FileReading {
    private let files = Mutex<[String: Data]>([:])

    func set(_ path: String, _ contents: String) {
        files.withLock { $0[path] = Data(contents.utf8) }
    }

    func remove(_ path: String) {
        _ = files.withLock { $0.removeValue(forKey: path) }
    }

    func read(_ path: String) -> Data? { files.withLock { $0[path] } }
}

/// One service plus its fakes, sharing a cache file in a fresh temp folder.
struct UsageHarness {
    let transport = FakeTransport()
    let secrets = FakeSecrets()
    let files = FakeFiles()
    let clock = TestClock()
    /// Nested a level down so the service has to create the parent itself.
    let cacheURL = usageScratch
        .appendingPathComponent("usage-\(UUID().uuidString)")
        .appendingPathComponent("state")
        .appendingPathComponent("usage.json")

    func makeService() -> UsageService {
        let clock = self.clock
        return UsageService(
            transport: transport, secrets: secrets, files: files,
            cacheURL: cacheURL, now: { clock.now })
    }

    var cacheText: String {
        (try? String(contentsOf: cacheURL, encoding: .utf8)) ?? ""
    }
}

let codexHome = "/Users/test/.codex"
let codexKey = AccountKey(provider: .codex, configHome: codexHome, isDefault: true)
let claudeHome = "/Users/test/.claude"
let claudeKey = AccountKey(provider: .claude, configHome: claudeHome, isDefault: true)

/// A JWT whose payload is `claims`. The header and signature are junk on
/// purpose: nothing should check them.
func makeJWT(_ claims: [String: Any]) -> String {
    let payload = try! JSONSerialization.data(withJSONObject: claims, options: [.sortedKeys])
    let encoded = payload.base64EncodedString()
        .replacingOccurrences(of: "+", with: "-")
        .replacingOccurrences(of: "/", with: "_")
        .replacingOccurrences(of: "=", with: "")
    return "eyJhbGciOiJub25lIn0.\(encoded).c2ln"
}

func codexAuthJSON(
    accessToken: String = "codex-access-SECRET-1",
    accountID: String? = "acct-123",
    email: String = "dev@example.com",
    plan: String = "plus"
) -> String {
    var tokens: [String: Any] = [
        "access_token": accessToken,
        "refresh_token": "codex-refresh-SECRET",
        "id_token": makeJWT([
            "email": email,
            "https://api.openai.com/auth": ["chatgpt_plan_type": plan, "chatgpt_account_id": "acct-from-jwt"],
        ]),
    ]
    if let accountID { tokens["account_id"] = accountID }
    let object: [String: Any] = ["auth_mode": "chatgpt", "tokens": tokens, "last_refresh": "2026-10-10T00:00:00Z"]
    return String(decoding: try! JSONSerialization.data(withJSONObject: object), as: UTF8.self)
}

func claudeCredentialsJSON(
    accessToken: String = "claude-access-SECRET-1",
    expiresAt: Date = usageEpoch.addingTimeInterval(3600),
    subscriptionType: String = "max",
    rateLimitTier: String = "default_claude_max_20x"
) -> String {
    let object: [String: Any] = [
        "claudeAiOauth": [
            "accessToken": accessToken,
            "refreshToken": "claude-refresh-SECRET",
            "expiresAt": Int64(expiresAt.timeIntervalSince1970 * 1000),
            "scopes": ["user:inference", "user:profile"],
            "subscriptionType": subscriptionType,
            "rateLimitTier": rateLimitTier,
        ],
    ]
    return String(decoding: try! JSONSerialization.data(withJSONObject: object), as: UTF8.self)
}

/// One folder per test process for the per-test files below, removed when
/// the process exits, as `TestDefaults.folder` is. These helpers hand out
/// a bare URL that the code under test writes to whenever it likes, often
/// after the test's last use of the URL, so nothing owned by one test
/// could remove its file without racing that write.
let usageScratch: URL = {
    let folder = FileManager.default.temporaryDirectory
        .appending(path: "quiver-usage-tests-\(getpid())", directoryHint: .isDirectory)
    atexit { try? FileManager.default.removeItem(at: usageScratch) }
    return folder
}()

/// A per-test file URL for `OtherLimitsModel`'s snapshot, so no test ever
/// touches the real `~/.swe` cache.
func tempSnapshotFile() -> URL {
    usageScratch
        .appendingPathComponent("limits-\(UUID().uuidString)")
        .appendingPathComponent("app-other-limits.json")
}

/// Same for `HarnessAccountNameStore`: a per-test file URL, so the real
/// `~/.swe/accounts/harness-names.json` is never read or written.
func tempNamesStore() -> HarnessAccountNameStore {
    HarnessAccountNameStore(file: usageScratch
        .appendingPathComponent("names-\(UUID().uuidString)")
        .appendingPathComponent("harness-names.json"))
}
