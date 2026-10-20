import Foundation
import Security

/// The network, through an ephemeral session that keeps nothing: no cookies,
/// no response cache, no stored credentials, and no redirects followed, so a
/// bearer token is only ever sent to the host it was meant for.
public struct URLSessionTransport: HTTPTransport {
    private let session: URLSession

    public init(configuration: URLSessionConfiguration = URLSessionTransport.defaultConfiguration()) {
        session = URLSession(configuration: configuration, delegate: RedirectRefuser(), delegateQueue: nil)
    }

    public static func defaultConfiguration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.httpCookieAcceptPolicy = .never
        configuration.urlCredentialStorage = nil
        configuration.timeoutIntervalForRequest = 15
        configuration.timeoutIntervalForResource = 30
        return configuration
    }

    public func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        return (data, http)
    }
}

/// Hands a redirect back to the caller as the response instead of following it.
private final class RedirectRefuser: NSObject, URLSessionTaskDelegate, Sendable {
    func urlSession(
        _ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest
    ) async -> URLRequest? {
        nil
    }
}

/// The login keychain, read through Apple's `security` tool.
///
/// Not `SecItemCopyMatching` on purpose: Claude Code writes its item with
/// `security`, which leaves it readable by `security` without a prompt. An
/// app reading it in-process gets a password dialog, and gets it again every
/// time Claude Code refreshes its token and rewrites the item.
public struct KeychainCLI: SecretReading {
    public static let securityPath = "/usr/bin/security"

    private let runner: CommandRunning
    private let timeout: Duration

    public init(
        runner: CommandRunning = ProcessRunner(
            executable: KeychainCLI.securityPath, environment: ProcessInfo.processInfo.environment),
        timeout: Duration = .seconds(5)
    ) {
        self.runner = runner
        self.timeout = timeout
    }

    /// The item's password, or nil when it is missing, the keychain refused,
    /// or `security` did not answer in time (a locked keychain can prompt).
    public func genericPassword(service: String, account: String) async -> String? {
        let runner = self.runner
        let timeout = self.timeout
        let arguments = ["find-generic-password", "-s", service, "-a", account, "-w"]
        let result: CommandResult? = await withTaskGroup(of: CommandResult?.self) { group in
            group.addTask { try? await runner.run(arguments) }
            group.addTask {
                try? await Task.sleep(for: timeout)
                return nil
            }
            let first = await group.next() ?? nil
            group.cancelAll()
            return first
        }
        guard let result, result.status == 0 else { return nil }
        let value = String(decoding: result.stdout, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }
}

extension KeychainCLI: SecretStoring {
    /// `security`'s exit code when the item is not in the keychain.
    static let itemNotFoundStatus: Int32 = 44

    /// Upserts the item. `security` takes the value only through `-w` on
    /// argv; the arguments go nowhere near a log.
    public func setGenericPassword(service: String, account: String, value: String) async throws {
        let result = try await runner.run([
            "add-generic-password", "-U", "-s", service, "-a", account,
            "-w", value, "-T", Self.securityPath])
        guard result.status == 0 else {
            throw SweError.commandFailed(status: result.status, message: result.stderr.trimmingCharacters(in: .whitespacesAndNewlines))
        }
    }

    /// Removes the item; one that was never there is already removed.
    public func deleteGenericPassword(service: String, account: String) async throws {
        let result = try await runner.run(["delete-generic-password", "-s", service, "-a", account])
        guard result.status == 0 || result.status == Self.itemNotFoundStatus else {
            throw SweError.commandFailed(status: result.status, message: result.stderr.trimmingCharacters(in: .whitespacesAndNewlines))
        }
    }
}

/// Quiver's own keychain items, written and read in-process through the
/// Security framework.
///
/// `KeychainCLI.setGenericPassword` can take the value only on
/// `security`'s argv, where `ps` can see it. For Quiver-owned items the
/// in-process API is also the quiet one: an item this process wrote reads
/// back without a prompt, while asking `security` for it may prompt.
/// Claude Code's credentials stay on `KeychainCLI` for the inverse
/// reason — `security` wrote them, so SecItem would prompt on every read.
public struct SecItemKeychain: SecretStoring {
    public init() {}

    /// The item's password, or nil when it is missing, the keychain
    /// refused, or the value is not UTF-8 text.
    public func genericPassword(service: String, account: String) async -> String? {
        var item: CFTypeRef?
        var query = Self.query(service: service, account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    /// Upserts the item: update first, add when it is not there yet. The
    /// value rides in the query dictionary, never on a tool's argv.
    public func setGenericPassword(service: String, account: String, value: String) async throws {
        var query = Self.query(service: service, account: account)
        let data = Data(value.utf8)
        switch SecItemUpdate(query as CFDictionary,
                             [kSecValueData as String: data] as CFDictionary) {
        case errSecSuccess:
            return
        case errSecItemNotFound:
            query[kSecValueData as String] = data
            try Self.check(SecItemAdd(query as CFDictionary, nil))
        case let status:
            throw Self.error(status)
        }
    }

    /// Removes the item; one that was never there is already removed.
    public func deleteGenericPassword(service: String, account: String) async throws {
        let status = SecItemDelete(Self.query(service: service, account: account) as CFDictionary)
        if status != errSecItemNotFound {
            try Self.check(status)
        }
    }

    private static func query(service: String, account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    /// What a failed SecItem call throws: `commandFailed` with Security's
    /// own message for the status, which never contains the value. The
    /// seam tests exercise without touching a keychain.
    static func error(_ status: OSStatus) -> SweError {
        let message = SecCopyErrorMessageString(status, nil) as? String
            ?? "The keychain answered with an error."
        return .commandFailed(status: status, message: message)
    }

    private static func check(_ status: OSStatus) throws {
        if status != errSecSuccess { throw error(status) }
    }
}

/// The real file system.
public struct LocalFiles: FileReading {
    public init() {}

    public func read(_ path: String) -> Data? {
        FileManager.default.contents(atPath: path)
    }
}
