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

/// The Security calls behind `SecItemKeychain`, as a seam tests fake. C
/// functions are not `Sendable`-friendly to store directly, so each is a
/// closure over the real call.
struct SecItemCalls: Sendable {
    var copyMatching: @Sendable (CFDictionary, UnsafeMutablePointer<CFTypeRef?>) -> OSStatus
    var update: @Sendable (CFDictionary, CFDictionary) -> OSStatus
    var add: @Sendable (CFDictionary, UnsafeMutablePointer<CFTypeRef?>?) -> OSStatus
    var delete: @Sendable (CFDictionary) -> OSStatus
    /// Runs one call with the keychain's consent prompts allowed or not.
    var interaction: @Sendable (_ allowed: Bool, _ call: () -> OSStatus) -> OSStatus

    static let live = SecItemCalls(
        copyMatching: { SecItemCopyMatching($0, $1) },
        update: { SecItemUpdate($0, $1) },
        add: { SecItemAdd($0, $1) },
        delete: { SecItemDelete($0) },
        interaction: { LegacyKeychainPrompts.run(allowed: $0, $1) })
}

/// The legacy login keychain's switch for consent prompts.
///
/// SWE's items live in the legacy (file-based) keychain: the Data
/// Protection keychain needs a `keychain-access-groups` entitlement that
/// SWE's builds do not carry, and `SecItemAdd` there answers
/// errSecMissingEntitlement. On a legacy item neither
/// `kSecUseAuthenticationUIFail` nor an `LAContext` with
/// `interactionNotAllowed` stops the "wants to use your confidential
/// information" dialog; Apple's SecItem shim applies both to Data
/// Protection items only. Checked on macOS 26.5: both reads hung on the
/// dialog. With this switch off, the same read fails at once with
/// errSecAuthFailed.
///
/// `SecKeychainSetUserInteractionAllowed` is deprecated with the rest of
/// SecKeychain but is still the only switch for that keychain, so it is
/// looked up at run time instead of called, which keeps the build free of
/// deprecation warnings. It is process-wide, so every call runs under one
/// lock: a Save the person just clicked must not land while a read has
/// prompts off, and must not turn them back on under a read.
enum LegacyKeychainPrompts {
    private typealias Get = @convention(c) (UnsafeMutablePointer<DarwinBoolean>) -> OSStatus
    private typealias Set = @convention(c) (DarwinBoolean) -> OSStatus

    private struct Calls {
        var get: Get
        var set: Set
    }

    /// Resolved once; nil when this macOS no longer has the pair. Security
    /// is already loaded, so the dlopen only takes a handle.
    private static let calls: Calls? = {
        guard let handle = dlopen("/System/Library/Frameworks/Security.framework/Security", RTLD_LAZY),
              let get = dlsym(handle, "SecKeychainGetUserInteractionAllowed"),
              let set = dlsym(handle, "SecKeychainSetUserInteractionAllowed")
        else { return nil }
        return Calls(get: unsafeBitCast(get, to: Get.self), set: unsafeBitCast(set, to: Set.self))
    }()

    /// Held across a call that may prompt, so a lock that sleeps rather
    /// than spins.
    private static let lock = NSLock()

    /// Runs `call` with prompts allowed or not, then puts the switch back.
    /// Without the switch (a future macOS) the call runs as it is.
    static func run(allowed: Bool, _ call: () -> OSStatus) -> OSStatus {
        lock.lock()
        defer { lock.unlock() }
        guard !allowed, let calls else { return call() }
        var before = DarwinBoolean(true)
        guard calls.get(&before) == errSecSuccess else { return call() }
        _ = calls.set(false)
        defer { _ = calls.set(before) }
        return call()
    }

    /// The switch's current state; nil without it. For tests.
    static func isAllowed() -> Bool? {
        lock.lock()
        defer { lock.unlock() }
        var state = DarwinBoolean(true)
        guard let calls, calls.get(&state) == errSecSuccess else { return nil }
        return state.boolValue
    }
}

/// SWE's own service keys, through Security.framework.
///
/// Writing through `security` (`add-generic-password -w`) would put the
/// value on argv, where `ps` can see it; SecItem calls carry it in
/// memory. The reverse of KeychainCLI's read caveat applies too: items
/// the app writes here read back in-process without a prompt, for as
/// long as the app keeps the same signing identity.
///
/// Reads run with `LegacyKeychainPrompts` off. An item SWE is not on
/// the access list of (one written by `security` before SWE moved to
/// SecItem, or by an ad-hoc build whose signature has since changed), or
/// a locked keychain, would otherwise put up a consent dialog that takes
/// focus and can wait forever under a page load. Such a read answers nil.
///
/// Writes and deletes are explicit user actions — the Save or Remove a
/// person just clicked — so they may prompt. One consent puts SWE on
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
        let status = calls.interaction(false) { calls.copyMatching(query as CFDictionary, &item) }
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    /// Upserts the item: update first, add when it is not there yet. The
    /// value rides in the query dictionary, never on a tool's argv.
    public func setGenericPassword(service: String, account: String, value: String) async throws {
        // Explicit save: the consent prompt is acceptable here, and one
        // consent on an item written by `security` grants SWE access.
        var query = Self.query(service: service, account: account)
        let data = Data(value.utf8)
        switch calls.interaction(true, {
            calls.update(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        }) {
        case errSecSuccess:
            return
        case errSecItemNotFound:
            query[kSecValueData as String] = data
            try Self.check(calls.interaction(true) { calls.add(query as CFDictionary, nil) })
        case let status:
            throw Self.error(status)
        }
    }

    /// Removes the item; one that was never there is already removed.
    public func deleteGenericPassword(service: String, account: String) async throws {
        let query = Self.query(service: service, account: account)
        let status = calls.interaction(true) { calls.delete(query as CFDictionary) }
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
