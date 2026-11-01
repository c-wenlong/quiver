import Foundation

/// A service SWE itself calls, with the keys it needs and a help line
/// saying where they come from. More services join the list here.
public struct KnownService: Sendable, Equatable, Identifiable {
    /// How the "Test" button proves a key: one request the key pays for.
    public struct Probe: Sendable, Equatable {
        /// The field whose value becomes the request's credential.
        public var field: String
        public var label: String
        public var url: String
        /// The header the credential goes in ("Authorization" or "x-api-key").
        public var header: String
        /// When true the header value is "Bearer <token>", else the token itself.
        public var bearer: Bool
    }

    public var id: String
    public var name: String
    public var help: String
    /// The Keychain account names this service needs, in display order.
    public var fields: [String]
    public var probe: Probe?
    /// Where the user gets a key for it.
    public var keyURL: URL
}

/// SWE's own service credentials, kept in the login keychain under a
/// service of their own — apart from the API keys file, which is for the
/// harnesses. Only dates live in the metadata file; values never leave
/// the keychain or appear in errors.
public actor ServiceCredentials {
    /// The login-keychain service every SWE-owned key shares; the
    /// account is the key's name.
    public static let keychainService = "com.cwenlong.swe.services"
    /// Where Quiver.app kept the same keys. A read that misses the new
    /// service tries it, and a hit is copied over and removed: the keys
    /// migrate one at a time, as they are used.
    public static let legacyKeychainService = "com.cwenlong.swe.services"

    /// What one field looks like in Settings.
    public struct Status: Sendable, Equatable {
        public var isSet: Bool
        public var added: Date?
        public var lastUsed: Date?

        public init(isSet: Bool, added: Date?, lastUsed: Date?) {
            self.isSet = isSet
            self.added = added
            self.lastUsed = lastUsed
        }
    }

    /// What the "Test" button reports.
    public enum ProbeResult: Sendable, Equatable {
        case works
        case httpError(Int)
        /// A missing key or a request that never got an HTTP answer. The
        /// message describes the failure and never carries the value.
        case unavailable(String)
    }

    public static let services: [KnownService] = [
        KnownService(
            id: "devin", name: "Devin",
            help: "Imports your Devin cloud sessions into Sessions and the calendar. Personal access token from app.devin.ai › Settings › Devin API › PATs; the org ID at the top of that page is optional.",
            fields: ["DEVIN_API_KEY", "DEVIN_ORG_ID"],
            probe: .init(field: "DEVIN_API_KEY", label: "Test",
                         url: "https://api.devin.ai/v3/self",
                         header: "Authorization", bearer: true),
            keyURL: URL(string: "https://app.devin.ai/settings/api")!),
        KnownService(
            id: "artificial-analysis", name: "Artificial Analysis",
            help: "From artificialanalysis.ai › Insights Platform › API keys. Free tier: 100 requests a day.",
            fields: ["ARTIFICIAL_ANALYSIS_API_KEY"],
            probe: .init(field: "ARTIFICIAL_ANALYSIS_API_KEY",
                         label: "Test (uses 1 of 100 daily requests)",
                         url: "https://artificialanalysis.ai/api/v2/language/models/free",
                         header: "x-api-key", bearer: false),
            keyURL: URL(string: "https://artificialanalysis.ai/data-api/docs")!),
    ]

    /// `services.json` under ~/.swe/config: per-key dates, never values.
    private struct Metadata: Codable {
        var added: TimeInterval
        var lastUsed: TimeInterval?
    }

    private let secrets: any SecretStoring
    private let service: String
    private let legacyService: String
    private let metadataURL: URL
    private let now: @Sendable () -> Date

    public init(secrets: any SecretStoring,
                service: String = ServiceCredentials.keychainService,
                legacyService: String? = nil,
                metadataURL: URL,
                now: @escaping @Sendable () -> Date = Date.init) {
        self.secrets = secrets
        self.service = service
        // The legacy store only ever feeds the real service: a scratch
        // service (tests, SWE_SERVICES_KEYCHAIN) must not read, copy or
        // delete real keys. Matching the two names disables the
        // fallback below.
        self.legacyService = legacyService
            ?? (service == ServiceCredentials.keychainService
                ? ServiceCredentials.legacyKeychainService : service)
        self.metadataURL = metadataURL
        self.now = now
    }

    /// The key's value, recording that it was used. Values are never
    /// logged, written to the metadata file, or put in an error.
    public func value(for name: String) async -> String? {
        var value = await secrets.genericPassword(service: service, account: name)
        if value == nil, legacyService != service,
           let legacy = await secrets.genericPassword(service: legacyService, account: name) {
            // First use since the rename: carry the key to the new
            // service. The old item goes only once the copy exists —
            // a failed save must not drop the one stored copy.
            do {
                try await secrets.setGenericPassword(service: service, account: name, value: legacy)
                try? await secrets.deleteGenericPassword(service: legacyService, account: name)
            } catch {}
            value = legacy
        }
        guard let value else { return nil }
        var metadata = load()
        if var entry = metadata[name] {
            entry.lastUsed = now().timeIntervalSince1970
            metadata[name] = entry
        } else {
            // Written by hand or an older build: it exists, so note when.
            metadata[name] = Metadata(added: now().timeIntervalSince1970,
                                      lastUsed: now().timeIntervalSince1970)
        }
        save(metadata)
        return value
    }

    /// Stores or replaces the key's value. `added` is the first set, so a
    /// Replace keeps the original date.
    public func set(_ name: String, value: String) async throws {
        try await secrets.setGenericPassword(service: service, account: name, value: value)
        var metadata = load()
        if metadata[name] == nil {
            metadata[name] = Metadata(added: now().timeIntervalSince1970)
        }
        save(metadata)
    }

    /// Deletes the value and its bookkeeping. A legacy-only key whose
    /// delete fails keeps its metadata instead of looking removed while
    /// the key itself survives.
    public func remove(_ name: String) async throws {
        try await secrets.deleteGenericPassword(service: service, account: name)
        if legacyService != service {
            try await secrets.deleteGenericPassword(service: legacyService, account: name)
        }
        var metadata = load()
        metadata.removeValue(forKey: name)
        save(metadata)
    }

    /// Whether the key is in the keychain, plus its dates when they were
    /// recorded. A key written without metadata still reports isSet.
    public func status(for name: String) async -> Status {
        let entry = load()[name]
        var isSet = await secrets.genericPassword(service: service, account: name) != nil
        if !isSet, legacyService != service {
            isSet = await secrets.genericPassword(service: legacyService, account: name) != nil
        }
        return Status(isSet: isSet,
                      added: entry.map { Date(timeIntervalSince1970: $0.added) },
                      lastUsed: entry?.lastUsed.map { Date(timeIntervalSince1970: $0) })
    }

    /// Runs the service's probe request with its stored key. The reply
    /// is not kept; only the status reaches the caller.
    public func probe(_ service: KnownService, transport: any HTTPTransport) async -> ProbeResult {
        guard let probe = service.probe else { return .unavailable("Nothing to test.") }
        guard let token = await value(for: probe.field) else {
            return .unavailable("\(probe.field) is not saved.")
        }
        guard let url = URL(string: probe.url) else { return .unavailable("Bad probe URL.") }
        var request = URLRequest(url: url)
        request.setValue(probe.bearer ? "Bearer \(token)" : token, forHTTPHeaderField: probe.header)
        do {
            let (_, response) = try await transport.send(request)
            return (200..<300).contains(response.statusCode)
                ? .works : .httpError(response.statusCode)
        } catch {
            return .unavailable(error.localizedDescription)
        }
    }

    private func load() -> [String: Metadata] {
        guard let data = FileManager.default.contents(atPath: metadataURL.path),
              let decoded = try? JSONDecoder().decode([String: Metadata].self, from: data) else {
            return [:]
        }
        return decoded
    }

    private func save(_ metadata: [String: Metadata]) {
        guard let data = try? JSONEncoder().encode(metadata) else { return }
        try? FileManager.default.createDirectory(
            at: metadataURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: metadataURL, options: .atomic)
    }
}

/// Whether the Sessions page shows the Devin-key hint. Unknown status
/// stays quiet, so statuses loading never flashes it.
public enum DevinKeyHint {
    /// A session row that means "this user has Devin": the CLI's own
    /// sessions or the cloud ones imported with the key.
    public static func isDevin(harness: String) -> Bool {
        harness == "devin" || harness == DevinCloud.harness
    }

    public static func shows(hasDevin: Bool, keySet: Bool?, dismissed: Bool) -> Bool {
        hasDevin && !dismissed && keySet == false
    }
}
