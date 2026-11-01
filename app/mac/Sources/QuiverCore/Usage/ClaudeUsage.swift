import Foundation

/// Where Claude Code keeps its OAuth credential in the login keychain.
///
/// Matches Claude Code 2.1.296: the service is "Claude Code-credentials",
/// plus "-" and the first 8 hex digits of sha256 of the config dir when
/// CLAUDE_CONFIG_DIR is set. The dir is hashed as given, NFC-normalized,
/// with no tilde expansion or trailing-slash cleanup. The account is $USER,
/// or "claude-code-user" when that name has characters outside [A-Za-z0-9._-].
public enum ClaudeKeychain {
    public static func service(configHome: String, isDefault: Bool) -> String {
        if isDefault { return "Claude Code-credentials" }
        let hash = UsageHashing.sha256Hex(configHome.precomposedStringWithCanonicalMapping)
        return "Claude Code-credentials-\(hash.prefix(8))"
    }

    public static func account(environmentUser: String?, systemUser: String) -> String {
        let name = (environmentUser?.isEmpty == false ? environmentUser : nil) ?? systemUser
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
        guard !name.isEmpty, name.unicodeScalars.allSatisfy(allowed.contains) else { return "claude-code-user" }
        return name
    }

    public static var currentAccount: String {
        account(environmentUser: ProcessInfo.processInfo.environment["USER"], systemUser: NSUserName())
    }
}

/// Claude: Claude Code's OAuth credential and the endpoint its `/usage`
/// screen reads. Tokens are never refreshed or written by Quiver itself:
/// a manual Refresh runs Claude Code, which renews its own credential.
enum ClaudeUsageAPI {
    static let url = URL(string: "https://api.anthropic.com/api/oauth/usage")!

    static func credential(key: AccountKey, secrets: SecretReading, files: FileReading) async -> CredentialLookup {
        let service = ClaudeKeychain.service(configHome: key.configHome, isDefault: key.isDefault)
        var fromKeychain = await secrets.genericPassword(service: service, account: ClaudeKeychain.currentAccount)
        if fromKeychain == nil, !key.isDefault,
           key.configHome.contains("/.swe/") {
            // Sign-ins made before the ~/.quiver -> ~/.swe move live under
            // the hash of the old spelling; the service name is derived
            // from the path verbatim, so check both.
            let legacy = ClaudeKeychain.service(
                configHome: key.configHome.replacingOccurrences(of: "/.swe/", with: "/.quiver/"),
                isDefault: key.isDefault)
            fromKeychain = await secrets.genericPassword(service: legacy, account: ClaudeKeychain.currentAccount)
        }
        let fromFile = files.read(key.configHome + "/.credentials.json")
        // Both can exist, and either can be the stale one: the later expiry
        // is the newer login, as quiver's own Claude fetcher decides.
        let candidates = [
            fromKeychain.flatMap { parse(credentials: $0) },
            fromFile.flatMap { parse(credentials: String(decoding: $0, as: UTF8.self)) },
        ].compactMap { $0 }
        if let newest = candidates.max(by: { ($0.expiresAt ?? .distantPast) < ($1.expiresAt ?? .distantPast) }) {
            return .found(newest)
        }
        if fromKeychain != nil || fromFile != nil {
            return .unusable("Claude Code's credentials could not be read")
        }
        return .signedOut
    }

    private static func parse(credentials text: String) -> UsageCredential? {
        guard let root = UsageJSON.objectAllowingHex(text) else { return nil }
        let oauth = root["claudeAiOauth"] as? [String: Any] ?? root
        guard let token = UsageJSON.string(oauth["accessToken"]) else { return nil }
        return UsageCredential(
            token: token,
            accountID: nil,
            expiresAt: UsageJSON.number(oauth["expiresAt"]).flatMap { $0 > 0 ? UsageJSON.date($0) : nil },
            plan: PlanNames.name(.claude, UsageJSON.string(oauth["subscriptionType"]), UsageJSON.string(oauth["rateLimitTier"])),
            email: nil)
    }

    /// Claude Code's global config, which names the signed-in account. Without
    /// an override it sits beside the home (`~/.claude.json`), with one inside it.
    static func email(key: AccountKey, files: FileReading) -> String? {
        let base = key.isDefault ? (key.configHome as NSString).deletingLastPathComponent : key.configHome
        guard let data = files.read(base + "/.claude.json"),
              let config = UsageJSON.object(data),
              let account = config["oauthAccount"] as? [String: Any]
        else { return nil }
        return UsageJSON.string(account["emailAddress"])
    }

    static func request(_ credential: UsageCredential) -> URLRequest {
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: 15)
        request.httpMethod = "GET"
        request.setValue("Bearer \(credential.token)", forHTTPHeaderField: "Authorization")
        request.setValue("oauth-2025-04-20", forHTTPHeaderField: "anthropic-beta")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Quiver", forHTTPHeaderField: "User-Agent")
        return request
    }

    static func parse(_ data: Data, credential: UsageCredential) -> ParsedUsage? {
        guard let body = UsageJSON.object(data) else { return nil }
        var windows: [UsageWindow] = []
        func add(_ label: String, _ used: Double?, _ resets: Any?) {
            guard let used, !windows.contains(where: { $0.label == label }) else { return }
            windows.append(UsageWindow(label: label, usedPercent: min(100, max(0, used)), resetsAt: UsageJSON.date(resets)))
        }

        let scopedKeys = body.keys.filter { $0.hasPrefix("seven_day_") }.sorted { lhs, rhs in
            // Opus and Sonnet first, the rest alphabetically.
            let rank = { (key: String) in ["seven_day_opus": 0, "seven_day_sonnet": 1][key] ?? 2 }
            return (rank(lhs), lhs) < (rank(rhs), rhs)
        }
        for (key, label) in [("five_hour", "5h"), ("seven_day", "7d")] + scopedKeys.map({ ($0, "7d " + readable($0.dropFirst("seven_day_".count))) }) {
            guard let window = body[key] as? [String: Any] else { continue }
            add(label, UsageJSON.number(window["utilization"]), window["resets_at"])
        }

        // Newer responses also list limits, with per-model weekly caps only here.
        for case let limit as [String: Any] in body["limits"] as? [Any] ?? [] {
            let kind = UsageJSON.string(limit["kind"]) ?? "limit"
            let scope = limit["scope"] as? [String: Any]
            let model = scope?["model"] as? [String: Any]
            let scopeName = UsageJSON.string(model?["display_name"]) ?? UsageJSON.string(scope?["surface"])
            let label: String
            switch kind {
            case "session": label = "5h"
            case "weekly_all": label = "7d"
            default:
                let group = UsageJSON.string(limit["group"]) == "session" ? "5h" : "7d"
                label = "\(group) \(scopeName ?? readable(Substring(kind)))"
            }
            add(label, UsageJSON.number(limit["percent"]), limit["resets_at"])
        }

        let known = ["five_hour", "seven_day", "limits"].contains { body[$0] != nil }
        guard known else { return nil }
        return ParsedUsage(plan: credential.plan, email: nil, windows: windows)
    }

    /// "oauth_apps" reads as "Oauth Apps".
    private static func readable(_ slug: Substring) -> String {
        slug.split(separator: "_").map { $0.prefix(1).uppercased() + $0.dropFirst() }.joined(separator: " ")
    }
}
