import Foundation

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

/// The real file system.
public struct LocalFiles: FileReading {
    public init() {}

    public func read(_ path: String) -> Data? {
        FileManager.default.contents(atPath: path)
    }
}
