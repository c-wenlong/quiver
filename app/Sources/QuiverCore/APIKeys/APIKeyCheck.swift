import CryptoKit
import Foundation

/// Whether one key still works, asked of its own provider with the
/// cheapest request that checks auth. Each probe is one HTTPS request to
/// the provider's API only, and the transport never follows redirects, so
/// a key can only reach a host the table lists.
public struct APIKeyProbe: Equatable, Sendable {
    public enum Auth: Sendable, Equatable {
        /// `Authorization: Bearer <key>`.
        case bearer
        /// `x-api-key: <key>` plus `anthropic-version`.
        case xApiKey
        /// `x-goog-api-key: <key>`.
        case googApiKey
    }

    /// Hosts to try in order; the first that accepts the key wins.
    public let hosts: [URL]
    public let method: String
    public let body: Data?
    public let auth: Auth
    /// A provider that answers a working key with a request error:
    /// routing.run refuses the probe body (no model) before anything runs,
    /// so its 400 and 422 mean works.
    public let worksOnBadRequest: Bool

    public init(hosts: [URL], method: String, body: Data?, auth: Auth,
                worksOnBadRequest: Bool) {
        self.hosts = hosts
        self.method = method
        self.body = body
        self.auth = auth
        self.worksOnBadRequest = worksOnBadRequest
    }

    private static func get(_ auth: Auth, _ hosts: String...) -> APIKeyProbe {
        APIKeyProbe(hosts: hosts.map { URL(string: $0)! }, method: "GET", body: nil,
                    auth: auth, worksOnBadRequest: false)
    }

    /// The settled table, verified by sending a fake key to every host:
    /// each one rejects it as listed, so an accept means the key works.
    /// OpenRouter's /models is public, so the probe hits /key instead.
    static let byName: [String: APIKeyProbe] = {
        var table: [String: APIKeyProbe] = [:]
        func put(_ probe: APIKeyProbe, _ names: String...) {
            for name in names { table[name] = probe }
        }
        put(get(.bearer, "https://api.openai.com/v1/models"), "OPENAI_API_KEY")
        put(get(.xApiKey, "https://api.anthropic.com/v1/models"), "ANTHROPIC_API_KEY")
        put(get(.googApiKey, "https://generativelanguage.googleapis.com/v1beta/models"),
            "GEMINI_API_KEY", "GOOGLE_API_KEY")
        put(get(.bearer, "https://api.deepseek.com/models"), "DEEPSEEK_API_KEY")
        put(get(.bearer, "https://api.x.ai/v1/models"), "XAI_API_KEY")
        put(get(.bearer, "https://api.groq.com/openai/v1/models"), "GROQ_API_KEY")
        put(get(.bearer, "https://api.mistral.ai/v1/models"), "MISTRAL_API_KEY")
        put(get(.bearer, "https://api.cerebras.ai/v1/models"), "CEREBRAS_API_KEY")
        put(get(.bearer, "https://api.together.xyz/v1/models"),
            "TOGETHER_AI_API_KEY", "TOGETHER_API_KEY")
        put(get(.bearer, "https://api.fireworks.ai/inference/v1/models"),
            "FIREWORKS_AI_API_KEY", "FIREWORKS_API_KEY")
        put(get(.bearer, "https://openrouter.ai/api/v1/key"), "OPENROUTER_API_KEY")
        put(get(.bearer, "https://api.moonshot.ai/v1/models",
                "https://api.moonshot.cn/v1/models"),
            "KIMI_API_KEY", "MOONSHOT_API_KEY")
        put(get(.bearer, "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models",
                "https://dashscope.aliyuncs.com/compatible-mode/v1/models"),
            "QWEN_API_KEY", "DASHSCOPE_API_KEY")
        put(get(.bearer, "https://api.z.ai/api/paas/v4/models",
                "https://open.bigmodel.cn/api/paas/v4/models"),
            "ZAI_API_KEY", "ZHIPU_API_KEY")
        put(get(.bearer, "https://api.minimax.io/v1/models",
                "https://api.minimaxi.com/v1/models"), "MINIMAX_API_KEY")
        put(get(.bearer, "https://api.xiaomimimo.com/v1/models"), "MIMO_API_KEY")
        put(get(.bearer, "https://api.stepfun.ai/v1/models",
                "https://api.stepfun.com/v1/models"), "STEPFUN_API_KEY")
        put(get(.bearer, "https://api.upstage.ai/v1/models"), "UPSTAGE_API_KEY")
        put(get(.bearer, "https://opencode.ai/zen/v1/models"), "OPENCODE_ZEN_API_KEY")
        put(get(.bearer, "https://ai-gateway.vercel.sh/v1/models"),
            "VERCEL_GATEWAY_API_KEY", "AI_GATEWAY_API_KEY")
        put(get(.bearer, "https://api.tokenfactory.nebius.com/v1/models",
                "https://api.studio.nebius.com/v1/models"), "NEBIUS_API_KEY")
        put(get(.bearer, "https://api.featherless.ai/v1/models"), "FEATHERLESS_API_KEY")
        put(APIKeyProbe(hosts: [URL(string: "https://api.routing.run/v1/chat/completions")!],
                        method: "POST", body: Data("{}".utf8), auth: .bearer,
                        worksOnBadRequest: true),
            "ROUTING_RUN_API_KEY")
        return table
    }()

    public static func probe(forName name: String) -> APIKeyProbe? { byName[name] }

    /// Why a key can't be checked: NVIDIA's API answers a free request
    /// without reading the key, or Quiver doesn't know the provider.
    public static func noCheckReason(forName name: String) -> String {
        if name == "NVIDIA_API_KEY" {
            return "NVIDIA's API doesn't check the key on a free request."
        }
        return "Quiver doesn't know how to check this key."
    }
}

/// What one check learned about a key.
public enum APIKeyCheckResult: Sendable, Equatable {
    /// The provider accepted the key.
    case works
    /// The key is valid but the account has no balance.
    case noCredit
    /// The provider refused the key.
    case rejected(status: Int, message: String?)
    /// The answer didn't prove anything about the key.
    case couldNotCheck(String)
    /// Quiver has no check for this provider.
    case noCheck(String)
}

/// A check's answer, when it ran, and what it ran against. In memory
/// only: a key's health is never written anywhere.
public struct APIKeyCheck: Sendable, Equatable {
    public var result: APIKeyCheckResult
    public var checkedAt: Date
    /// SHA-256 of the value checked, so an edit makes the result old.
    var fingerprint: String

    public init(result: APIKeyCheckResult, checkedAt: Date, value: String) {
        self.result = result
        self.checkedAt = checkedAt
        fingerprint = Self.fingerprint(of: value)
    }

    /// The result still describes this value.
    public func isCurrent(for value: String) -> Bool {
        fingerprint == Self.fingerprint(of: value)
    }

    static func fingerprint(of value: String) -> String {
        SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

extension APIKeyProbe {
    /// One answer, per the settled rules: 200 and 429 (rate limited, so
    /// the key was read) work; 402 works but has no credit; 401 and 403
    /// reject; a 400 whose body names the key rejects (Gemini's "API key
    /// not valid", xAI's "Incorrect API key"); routing.run's 400 or 422
    /// works; anything else couldn't be checked.
    static func classify(status: Int, body: Data, probe: APIKeyProbe,
                         key: String) -> APIKeyCheckResult {
        switch status {
        case 200, 429:
            return .works
        case 402:
            return .noCredit
        case 401, 403:
            return .rejected(status: status, message: errorMessage(in: body, key: key))
        case 400 where probe.worksOnBadRequest, 422 where probe.worksOnBadRequest:
            return .works
        case 400 where isKeyError(body):
            return .rejected(status: status, message: errorMessage(in: body, key: key))
        default:
            let detail = errorMessage(in: body, key: key)
            return .couldNotCheck("HTTP \(status)" + (detail.map { ": \($0)" } ?? ""))
        }
    }

    /// A 400 that names the key, the way Gemini says "API key not valid"
    /// and xAI says "Incorrect API key".
    private static func isKeyError(_ body: Data) -> Bool {
        let text = String(decoding: body, as: UTF8.self)
        return text.contains("API key not valid") || text.contains("Incorrect API key")
    }

    /// Hosts tried in order stop at the first accepted answer; the key is
    /// rejected only when every host rejected it, and any mix of
    /// rejections and couldn't-check is couldn't-check (a region host
    /// being down is not proof the key is dead).
    static func combine(_ outcomes: [APIKeyCheckResult]) -> APIKeyCheckResult {
        var rejected: APIKeyCheckResult?
        var failed: APIKeyCheckResult?
        for outcome in outcomes {
            switch outcome {
            case .works, .noCredit:
                return outcome
            case .rejected:
                rejected = rejected ?? outcome
            case .couldNotCheck:
                failed = failed ?? outcome
            case .noCheck:
                break
            }
        }
        if let failed { return failed }
        return rejected ?? .couldNotCheck("no answer")
    }

    /// The provider's error text from `error.message`, `message` or
    /// `detail`, with any echo of the key cut out and the rest cut to 120
    /// characters. A masked echo like `****0000` survives.
    static func errorMessage(in body: Data, key: String) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any]
        else { return nil }
        var candidates: [String?] = []
        if let error = object["error"] as? [String: Any] {
            candidates.append(error["message"] as? String)
        }
        candidates.append(object["message"] as? String)
        candidates.append(object["detail"] as? String)
        candidates.append(object["error"] as? String)
        for candidate in candidates {
            guard var text = candidate?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !text.isEmpty else { continue }
            if !key.isEmpty { text = text.replacingOccurrences(of: key, with: "…") }
            if text.count > 120 { text = String(text.prefix(120)) + "…" }
            return text
        }
        return nil
    }
}

/// Sends each key only to its own provider's hosts, through the shared
/// transport so tests inject a fake.
public struct APIKeyChecker: Sendable {
    public var transport: HTTPTransport
    /// Per request.
    public var timeout: TimeInterval

    public init(transport: HTTPTransport, timeout: TimeInterval = 10) {
        self.transport = transport
        self.timeout = timeout
    }

    /// One request per host until one accepts the key, then the combined
    /// answer.
    public func check(key: String, probe: APIKeyProbe) async -> APIKeyCheckResult {
        var outcomes: [APIKeyCheckResult] = []
        for host in probe.hosts {
            let outcome = await request(host, key: key, probe: probe)
            outcomes.append(outcome)
            if case .works = outcome { break }
            if case .noCredit = outcome { break }
        }
        return APIKeyProbe.combine(outcomes)
    }

    private func request(_ host: URL, key: String, probe: APIKeyProbe) async -> APIKeyCheckResult {
        var request = URLRequest(url: host)
        request.httpMethod = probe.method
        request.httpBody = probe.body
        request.timeoutInterval = timeout
        if probe.body != nil {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        switch probe.auth {
        case .bearer:
            request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        case .xApiKey:
            request.setValue(key, forHTTPHeaderField: "x-api-key")
            request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        case .googApiKey:
            request.setValue(key, forHTTPHeaderField: "x-goog-api-key")
        }
        do {
            let (body, response) = try await transport.send(request)
            return APIKeyProbe.classify(status: response.statusCode, body: body,
                                        probe: probe, key: key)
        } catch {
            var reason = error.localizedDescription
            if !key.isEmpty { reason = reason.replacingOccurrences(of: key, with: "…") }
            if reason.count > 120 { reason = String(reason.prefix(120)) + "…" }
            return .couldNotCheck(reason)
        }
    }
}
