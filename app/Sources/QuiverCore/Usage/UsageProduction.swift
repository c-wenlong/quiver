import Foundation
import LocalAuthentication
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
        runner: (any CommandRunning)? = nil,
        timeout: Duration = .seconds(5)
    ) {
        self.runner = runner ?? ProcessRunner(
            executable: KeychainCLI.securityPath,
            environment: ProcessInfo.processInfo.environment,
            defaultTimeout: timeout)
        self.timeout = timeout
    }

    /// The item's password, or nil when it is missing, the keychain refused,
    /// or `security` did not answer in time (a locked keychain can prompt).
    /// The group bound stays even when a swapped-in runner ignores the
    /// deadline.
    public func genericPassword(service: String, account: String) async -> String? {
        let runner = self.runner
        let timeout = self.timeout
        let arguments = ["find-generic-password", "-s", service, "-a", account, "-w"]
        let result: CommandResult? = await withTaskGroup(of: CommandResult?.self) { group in
            group.addTask { try? await runner.run(arguments, input: nil, timeout: timeout) }
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

/// Quiver's own keychain items, written and read in-process through the
/// Security framework.
///
/// `security add-generic-password` can take the value only through `-w`
/// on its argv, where `ps` can see it — so writes happen here, never
/// through `KeychainCLI`. For Quiver-owned items the in-process API is
/// also the quiet one: an item this process wrote reads back without a
/// prompt, while asking `security` for it may prompt. Claude Code's
/// credentials stay on `KeychainCLI` for the inverse reason — `security`
/// wrote them, so SecItem would prompt on every read.
/// The three Security calls behind `SecItemKeychain`, as a seam tests
/// fake. C functions are not `Sendable`-friendly to store directly, so
/// each is a closure over the real call.
struct SecItemCalls: Sendable {
    var copyMatching: @Sendable (CFDictionary, UnsafeMutablePointer<CFTypeRef?>) -> OSStatus
    var update: @Sendable (CFDictionary, CFDictionary) -> OSStatus
    var add: @Sendable (CFDictionary, UnsafeMutablePointer<CFTypeRef?>?) -> OSStatus
    var delete: @Sendable (CFDictionary) -> OSStatus

    static let live = SecItemCalls(
        copyMatching: { SecItemCopyMatching($0, $1) },
        update: { SecItemUpdate($0, $1) },
        add: { SecItemAdd($0, $1) },
        delete: { SecItemDelete($0) })
}

/// Quiver's own service keys, through Security.framework.
///
/// Writing through `security` (`add-generic-password -w`) would put the
/// value on argv, where `ps` can see it; SecItem calls carry it in
/// memory. The reverse of KeychainCLI's read caveat applies too: items
/// the app writes here read back in-process without a prompt.
///
/// Reads pass an `LAContext` with `interactionNotAllowed`: an item written by
/// `security` before the switch (or a locked keychain) can wait forever
/// on a consent prompt, and a page load must not hang on it. Such a read
/// answers nil.
///
/// Writes and deletes are explicit user actions — the Save or Remove a
/// person just clicked — so they may prompt. One consent puts Quiver on
/// the item's access list, which is how a key saved before the switch
/// becomes readable again: the first save after the upgrade prompts
/// once, and reads stop returning nil for it from then on.
public struct SecItemKeychain: SecretStoring {
    private let calls: SecItemCalls

    public init() { calls = .live }
    init(calls: SecItemCalls) { self.calls = calls }

    /// The item's password, or nil when it is missing, the keychain
    /// refused, the read would have prompted, or the value is not UTF-8.
    public func genericPassword(service: String, account: String) async -> String? {
        var item: CFTypeRef?
        var query = Self.query(service: service, account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        let status = calls.copyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    /// Upserts the item: update first, add when it is not there yet. The
    /// value rides in the query dictionary, never on a tool's argv.
    public func setGenericPassword(service: String, account: String, value: String) async throws {
        // Explicit save: the consent prompt is acceptable here, and one
        // consent on an item written by `security` grants Quiver access.
        var query = Self.query(service: service, account: account, interactive: true)
        let data = Data(value.utf8)
        switch calls.update(query as CFDictionary,
                            [kSecValueData as String: data] as CFDictionary) {
        case errSecSuccess:
            return
        case errSecItemNotFound:
            query[kSecValueData as String] = data
            try Self.check(calls.add(query as CFDictionary, nil))
        case let status:
            throw Self.error(status)
        }
    }

    /// Removes the item; one that was never there is already removed.
    public func deleteGenericPassword(service: String, account: String) async throws {
        let status = calls.delete(Self.query(service: service, account: account,
                                             interactive: true) as CFDictionary)
        if status != errSecItemNotFound {
            try Self.check(status)
        }
    }

    private static func query(service: String, account: String,
                              interactive: Bool = false) -> [String: Any] {
        var query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: account]
        if !interactive {
            // Never put up a consent prompt on a read: it can wait
            // forever while a page is trying to load. A context that may
            // not interact is Apple's replacement for the deprecated
            // kSecUseAuthenticationUIFail; the read fails with
            // errSecInteractionNotAllowed instead of asking.
            let context = LAContext()
            context.interactionNotAllowed = true
            query[kSecUseAuthenticationContext as String] = context
        }
        return query
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
