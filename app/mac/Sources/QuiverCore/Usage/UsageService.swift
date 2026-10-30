import Foundation

/// Reads each account's subscription usage from its provider, with the
/// safeguards quiver's `rate_limits.py` learned the hard way:
///
/// - A reading under 5 minutes old is reused unless the caller forces a fetch.
/// - After a 429 nothing is sent until `Retry-After` has passed (15 minutes
///   when absent), even when forced. Hammering a throttled endpoint extends
///   the throttle.
/// - A failed or skipped fetch shows the last good reading marked stale, with
///   the time it was really taken, but never one older than a day.
/// - Readings and cooldowns belong to the login they were made with (a hash
///   of its token), so signing in again starts clean.
/// - Tokens are read for one request and dropped: never refreshed, written,
///   cached or put in a message.
public actor UsageService {
    /// How long a reading counts as current for a non-forced call.
    static let freshFor: TimeInterval = 5 * 60
    /// The oldest reading ever shown, stale or not.
    static let keptFor: TimeInterval = 24 * 60 * 60
    /// The cooldown after a 429 with no usable `Retry-After`.
    static let defaultCooldown: TimeInterval = 15 * 60
    /// Bounds on a server-given cooldown.
    static let cooldownRange: ClosedRange<TimeInterval> = 60...(24 * 60 * 60)

    private let transport: HTTPTransport
    private let secrets: SecretReading
    private let files: FileReading
    private let store: UsageCacheStore
    private let now: @Sendable () -> Date
    private struct Running {
        let id: UUID
        let force: Bool
        let task: Task<AccountUsage, Never>
    }
    private var inFlight: [AccountKey: Running] = [:]
    /// Called when a call finds another in flight for its account, before
    /// it waits; a test's signal that the two calls really overlapped.
    private var onFindsInFlight: (@Sendable () -> Void)?

    func setOnFindsInFlight(_ hook: (@Sendable () -> Void)?) { onFindsInFlight = hook }

    public init(
        transport: HTTPTransport, secrets: SecretReading, files: FileReading,
        cacheURL: URL, now: @escaping @Sendable () -> Date = Date.init
    ) {
        self.transport = transport
        self.secrets = secrets
        self.files = files
        self.store = UsageCacheStore(url: cacheURL, now: now)
        self.now = now
    }

    /// The account's usage. `force` skips the cached reuse, never a cooldown;
    /// `freshFor` says how long a good reading counts as fresh this call (the
    /// Settings refresh window); the default is the service's five minutes.
    /// Calls for an account already being fetched wait for that fetch,
    /// except that a forced call never takes a non-forced one's answer
    /// (which may be the cached reading): it waits for it to land, so two
    /// requests never overlap, then fetches.
    public func usage(for key: AccountKey, force: Bool,
                      freshFor: TimeInterval = 300) async -> AccountUsage {
        while let running = inFlight[key] {
            onFindsInFlight?()
            if running.force || !force { return await running.task.value }
            _ = await running.task.value
            if inFlight[key]?.id == running.id { inFlight[key] = nil }
        }
        let id = UUID()
        let task = Task { await self.resolve(key, force: force, freshFor: freshFor) }
        inFlight[key] = Running(id: id, force: force, task: task)
        let result = await task.value
        if inFlight[key]?.id == id { inFlight[key] = nil }
        return result
    }

    /// The last stored reading without a network call, or nil when none is
    /// kept. The credential is never looked up — a launch must not prompt —
    /// so the stored fingerprint goes unchecked; the reading is marked stale
    /// and carries the last failure, if one was recorded. For hydrating a
    /// card while the real fetch runs.
    public func cachedUsage(for key: AccountKey) -> AccountUsage? {
        guard let entry = store.entry(for: key) else { return nil }
        if let reading = entry.reading,
           now().timeIntervalSince(reading.fetchedAt) <= Self.keptFor {
            return AccountUsage(
                status: entry.lastFailure?.status ?? .ok,
                plan: reading.plan, email: reading.email,
                windows: reading.windows, fetchedAt: reading.fetchedAt,
                isStale: true)
        }
        // No reading worth showing, but the last failure is still what
        // the card should wear.
        if let failure = entry.lastFailure {
            return AccountUsage(status: failure.status, windows: [],
                                fetchedAt: nil, isStale: true)
        }
        return nil
    }

    private func resolve(_ key: AccountKey, force: Bool, freshFor: TimeInterval) async -> AccountUsage {
        let credential: UsageCredential
        switch await lookUpCredential(key) {
        case .found(let found): credential = found
        case .signedOut: return AccountUsage(status: .signedOut)
        case .unusable(let reason): return AccountUsage(status: .error(reason))
        }

        let started = now()
        var entry = store.entry(for: key)
        if let stored = entry, stored.fingerprint != credential.fingerprint {
            // A different login: the old cooldown and reading are not its own.
            entry = nil
            store.save(nil, for: key)
        }
        let reading = entry?.reading.flatMap { started.timeIntervalSince($0.fetchedAt) <= Self.keptFor ? $0 : nil }

        if let until = entry?.cooldownUntil, until > started {
            return result(.rateLimited(until: until), reading: reading, credential: credential)
        }
        // An expired sign-in outranks a fresh reading: Claude's OAuth
        // token lapses eight hours after issue, often inside the refresh
        // window, and only this check lets a refresh notice and renew it.
        if let expiresAt = credential.expiresAt, expiresAt <= started {
            store.save(StoredAccount(fingerprint: credential.fingerprint, reading: reading,
                                     cooldownUntil: entry?.cooldownUntil,
                                     lastFailure: StoredFailure(at: started, status: .expired)),
                       for: key)
            return result(.expired, reading: reading, credential: credential)
        }
        // A recorded failure means the last attempt failed: the entry's
        // reading may be within the freshness window, but it is stale
        // data on a red card, so the refresh must fetch rather than
        // serve it as a good one.
        if !force, entry?.lastFailure == nil, let reading {
            let age = started.timeIntervalSince(reading.fetchedAt)
            if age >= 0 && age < freshFor {
                return result(.ok, reading: reading, credential: credential, isStale: false)
            }
        }

        let request = key.provider == .claude ? ClaudeUsageAPI.request(credential) : CodexUsageAPI.request(credential)
        let response: (Data, HTTPURLResponse)
        do {
            response = try await transport.send(request)
        } catch {
            // The error is not quoted: some carry the request, header and all.
            let status = UsageStatus.error("could not reach the usage API")
            store.save(StoredAccount(fingerprint: credential.fingerprint, reading: reading,
                                     cooldownUntil: entry?.cooldownUntil,
                                     lastFailure: StoredFailure(at: started, status: status)),
                       for: key)
            return result(status, reading: reading, credential: credential)
        }
        let (data, http) = response
        let answered = now()

        /// The account's entry after a failed fetch: the kept reading and
        /// any cooldown untouched, the failure stamped for `cachedUsage`.
        func failed(_ status: UsageStatus, entry: StoredAccount?,
                    reading: StoredReading?, credential: UsageCredential,
                    at: Date) -> StoredAccount {
            StoredAccount(fingerprint: credential.fingerprint, reading: reading,
                          cooldownUntil: entry?.cooldownUntil,
                          lastFailure: StoredFailure(at: at, status: status))
        }

        switch http.statusCode {
        case 200..<300:
            guard let parsed = parse(data, key: key, credential: credential, now: answered) else {
                let status = UsageStatus.error("unexpected response from the usage API")
                store.save(failed(status, entry: entry, reading: reading,
                                  credential: credential, at: answered), for: key)
                return result(status, reading: reading, credential: credential)
            }
            let fresh = StoredReading(plan: parsed.plan, email: parsed.email, windows: parsed.windows, fetchedAt: answered)
            store.save(StoredAccount(fingerprint: credential.fingerprint, reading: fresh,
                                     cooldownUntil: nil, lastFailure: nil), for: key)
            return result(.ok, reading: fresh, credential: credential, isStale: false)
        case 401:
            store.save(failed(.expired, entry: entry, reading: reading,
                              credential: credential, at: answered), for: key)
            return result(.expired, reading: reading, credential: credential)
        case 429:
            let asked = RetryAfter.date(http.value(forHTTPHeaderField: "Retry-After"), now: answered)
            let wait = asked.map { $0.timeIntervalSince(answered) } ?? Self.defaultCooldown
            let until = answered.addingTimeInterval(min(max(wait, Self.cooldownRange.lowerBound), Self.cooldownRange.upperBound))
            store.save(StoredAccount(fingerprint: credential.fingerprint, reading: reading,
                                     cooldownUntil: until,
                                     lastFailure: StoredFailure(at: answered,
                                                                status: .rateLimited(until: until))),
                       for: key)
            return result(.rateLimited(until: until), reading: reading, credential: credential)
        default:
            let status = UsageStatus.error("usage API returned HTTP \(http.statusCode)")
            store.save(failed(status, entry: entry, reading: reading,
                              credential: credential, at: answered), for: key)
            return result(status, reading: reading, credential: credential)
        }
    }

    /// What to show: the reading if there is one, else just who is signed in.
    private func result(
        _ status: UsageStatus, reading: StoredReading?, credential: UsageCredential, isStale: Bool = true
    ) -> AccountUsage {
        guard let reading else {
            return AccountUsage(status: status, plan: credential.plan, email: credential.email,
                                signInExpiresAt: credential.expiresAt)
        }
        return AccountUsage(
            status: status, plan: reading.plan ?? credential.plan, email: reading.email ?? credential.email,
            windows: reading.windows, fetchedAt: reading.fetchedAt, isStale: isStale,
            signInExpiresAt: credential.expiresAt)
    }

    private func lookUpCredential(_ key: AccountKey) async -> CredentialLookup {
        switch key.provider {
        case .claude: await ClaudeUsageAPI.credential(key: key, secrets: secrets, files: files)
        case .codex: CodexUsageAPI.credential(home: key.configHome, files: files)
        }
    }

    private func parse(_ data: Data, key: AccountKey, credential: UsageCredential, now: Date) -> ParsedUsage? {
        switch key.provider {
        case .claude:
            guard var parsed = ClaudeUsageAPI.parse(data, credential: credential) else { return nil }
            parsed.email = ClaudeUsageAPI.email(key: key, files: files)
            return parsed
        case .codex:
            return CodexUsageAPI.parse(data, credential: credential, now: now)
        }
    }
}
