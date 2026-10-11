import Foundation

/// The last good reading for one account. Holds no credential.
struct StoredReading: Codable, Equatable {
    var plan: String?
    var email: String?
    var windows: [UsageWindow]
    /// When the provider answered, kept as is when the reading is reused.
    var fetchedAt: Date
}

/// What is remembered about one account between calls and launches.
struct StoredAccount: Codable, Equatable {
    /// The `UsageCredential.fingerprint` the reading and cooldown belong to.
    /// A new login has a new token, so neither carries over to it.
    var fingerprint: String
    var reading: StoredReading?
    /// No request is sent for this account before this time.
    var cooldownUntil: Date?
}

/// The usage state file. Belongs under ~/.quiver/cache/ (gitignored there), for
/// example ~/.quiver/cache/app-usage.json. Read on every call and
/// rewritten per change, so two running copies of the app share one cooldown.
struct UsageCacheStore {
    let url: URL

    private struct File: Codable {
        var version = 1
        var accounts: [String: StoredAccount]
    }

    static func id(_ key: AccountKey) -> String {
        "\(key.provider.rawValue)|\(key.isDefault ? "default" : "custom")|\(key.configHome)"
    }

    func entry(for key: AccountKey) -> StoredAccount? {
        loadAll()[Self.id(key)]
    }

    /// Replaces one account's entry, leaving the others as they are on disk.
    func save(_ entry: StoredAccount?, for key: AccountKey) {
        var accounts = loadAll()
        accounts[Self.id(key)] = entry
        let encoder = JSONEncoder()
        // Reference-date seconds, so a date comes back bit-for-bit equal.
        encoder.dateEncodingStrategy = .deferredToDate
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(File(accounts: accounts)) else { return }
        let manager = FileManager.default
        try? manager.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
        guard (try? data.write(to: url, options: .atomic)) != nil else { return }
        try? manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    /// Everything on disk, or nothing when the file is missing or unreadable.
    private func loadAll() -> [String: StoredAccount] {
        guard let data = try? Data(contentsOf: url) else { return [:] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .deferredToDate
        return (try? decoder.decode(File.self, from: data))?.accounts ?? [:]
    }
}
