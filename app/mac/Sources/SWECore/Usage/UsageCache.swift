import Foundation

/// The last good reading for one account. Holds no credential.
struct StoredReading: Codable, Equatable {
    var plan: String?
    var email: String?
    var windows: [UsageWindow]
    /// When the provider answered, kept as is when the reading is reused.
    var fetchedAt: Date
}

/// The last failed fetch, kept so a launch can show "couldn't refresh" and
/// its status without asking the network or the keychain.
struct StoredFailure: Codable, Equatable {
    /// When the provider (or the credential check) last said no.
    var at: Date
    /// `expired`, `rateLimited` or `error`: the status the card should wear.
    var status: UsageStatus
}

/// What is remembered about one account between calls and launches.
struct StoredAccount: Codable, Equatable {
    /// The `UsageCredential.fingerprint` the reading and cooldown belong to.
    /// A new login has a new token, so neither carries over to it.
    var fingerprint: String
    var reading: StoredReading?
    /// No request is sent for this account before this time.
    var cooldownUntil: Date?
    /// The last failed fetch; cleared by the next success. Missing in files
    /// written before this field existed.
    var lastFailure: StoredFailure?
}

/// The usage state file. Belongs under ~/.swe/cache/ (gitignored there), for
/// example ~/.swe/cache/app-usage.json. Read on every call and
/// rewritten per change, so two running copies of the app share one cooldown.
struct UsageCacheStore {
    let url: URL
    /// The owning service's clock, so its idea of an active cooldown and
    /// this file's agree.
    var now: @Sendable () -> Date = Date.init

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
        // Read, change and write under one lock shared by every process: an
        // atomic rename alone lets a second running copy write back the file
        // it read before this one saved, erasing this account's cooldown.
        withFileLock { write(entry, for: key) }
    }

    private func write(_ entry: StoredAccount?, for key: AccountKey) {
        var accounts = loadAll()
        var entry = entry
        // Another running copy may have saved a cooldown for this same login
        // after this one started its request. A success saved now must not
        // erase it, or the next refresh would ignore Retry-After.
        if var next = entry,
           let current = accounts[Self.id(key)], current.fingerprint == next.fingerprint,
           let until = current.cooldownUntil, until > now(),
           until > (next.cooldownUntil ?? .distantPast) {
            // Keep the later of the two: a copy that saved an older (or no)
            // cooldown must not shorten one another copy just recorded.
            next.cooldownUntil = until
            entry = next
        }
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

    /// Runs `body` holding the lock file beside the cache. If the lock
    /// cannot be taken the write still happens, unlocked, rather than
    /// dropping a cooldown.
    private func withFileLock(_ body: () -> Void) {
        FileLock.whileHeld(beside: url, body)
    }

    /// Everything on disk, or nothing when the file is missing or unreadable.
    private func loadAll() -> [String: StoredAccount] {
        guard let data = try? Data(contentsOf: url) else { return [:] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .deferredToDate
        return (try? decoder.decode(File.self, from: data))?.accounts ?? [:]
    }
}
