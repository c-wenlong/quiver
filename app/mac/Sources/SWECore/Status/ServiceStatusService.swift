import Foundation

/// Reads the status pages politely: each page at most every five minutes
/// (one minute for a manual refresh), conditional requests so an unchanged
/// page costs a 304, and a provider's `Retry-After` honoured. Readings are
/// kept in a cache file under ~/.swe/cache so a relaunch does not poll
/// again, and a reading older than an hour is never shown as current.
public actor ServiceStatusService {
    /// The answer to "can SWE read this page": the page URL it finally
    /// answers at, the feed URL a check reads, and its current reading —
    /// or why not.
    public enum ProbeResult: Sendable, Equatable {
        case ok(pageURL: URL, feedURL: URL, report: StatusReport)
        case failure(String)
    }

    private let transport: HTTPTransport
    private let cacheURL: URL
    private let now: @Sendable () -> Date
    private let interval: TimeInterval
    private let manualFloor: TimeInterval
    private let staleAfter: TimeInterval
    private let redirectResolver: @Sendable (URL) async -> URL?

    public init(
        transport: HTTPTransport, cacheURL: URL, now: @escaping @Sendable () -> Date = Date.init,
        interval: TimeInterval = 300, manualFloor: TimeInterval = 60, staleAfter: TimeInterval = 3600,
        redirectResolver: @escaping @Sendable (URL) async -> URL? = ServiceStatusService.resolveRedirects
    ) {
        self.transport = transport
        self.cacheURL = cacheURL
        self.now = now
        self.interval = interval
        self.manualFloor = manualFloor
        self.staleAfter = staleAfter
        self.redirectResolver = redirectResolver
    }

    /// What one page last said, and when to ask again.
    struct Entry: Codable, Equatable {
        var report: StatusReport?
        /// When the page last answered with a reading (200 or 304).
        var checkedAt: Date?
        /// When a request was last sent, answered or not.
        var attemptedAt: Date?
        var etag: String?
        var lastModified: String?
        var cooldownUntil: Date?
        var problem: String?
    }

    private struct File: Codable {
        var version = 1
        var services: [String: Entry]
    }

    /// Every entry this process has seen, so the polling limits hold even
    /// when the cache file cannot be written.
    private var memory: [String: Entry] = [:]
    /// Requests under way. An overlapping call (the page opening during a
    /// background poll) waits for these instead of sending its own.
    private var inFlight: [String: Task<Entry, Never>] = [:]

    /// The last readings, without touching the network.
    public func cached(for services: [StatusService]) -> [ServiceStatus] {
        let entries = current()
        let now = now()
        return services.map { status(for: $0, entries[$0.id], now: now) }
    }

    /// Every service's status, asking the pages that are due. `force` cuts
    /// the five-minute wait to one minute but never skips a cooldown.
    public func statuses(for services: [StatusService], force: Bool) async -> [ServiceStatus] {
        let entries = current()
        let start = now()
        let floor = force ? manualFloor : interval
        let transport = self.transport
        let clock = self.now
        var tasks: [String: Task<Entry, Never>] = [:]
        for service in services {
            if let running = inFlight[service.id] {
                tasks[service.id] = running
                continue
            }
            let entry = entries[service.id]
            if let until = entry?.cooldownUntil, until > start { continue }
            if let attempted = entry?.attemptedAt, start.timeIntervalSince(attempted) < floor { continue }
            var attempt = entry ?? Entry()
            attempt.attemptedAt = start
            // Recorded before the request goes out, so the next call sees it.
            memory[service.id] = attempt
            let task = Task { await Self.check(service, attempt, transport: transport, clock: clock) }
            inFlight[service.id] = task
            tasks[service.id] = task
        }

        var updated: [String: Entry] = [:]
        for (id, task) in tasks {
            let entry = await task.value
            // A forget while the request was out (a remove, or an edit
            // that moved the feed) discards its answer, or the old page's
            // reading comes back and holds the new feed off for minutes.
            if inFlight[id] == task {
                inFlight[id] = nil
                updated[id] = entry
            }
        }
        if !updated.isEmpty { save(updated) }
        let merged = current()
        let now = now()
        return services.map { status(for: $0, merged[$0.id], now: now) }
    }

    /// Whether an address serves a readable status feed, for the "Track a
    /// status page" sheet's Check button. The address is normalised to
    /// `scheme://host/` (Statuspage and incident.io serve `summary.json`
    /// at the root), then redirects are followed once through
    /// `redirectResolver`, since the polling transport follows none. The
    /// probe touches neither the cache nor the poll counters.
    public func probe(_ input: String) async -> ProbeResult {
        var text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.contains("://") { text = "https://" + text }
        guard let entered = URL(string: text), entered.scheme?.lowercased() == "https",
              entered.host(percentEncoded: false) != nil
        else { return .failure("Use the https address of the status page.") }

        var base = Self.base(entered)
        if let final = await redirectResolver(base),
           final.scheme?.lowercased() == "https", final.host(percentEncoded: false) != nil {
            base = Self.base(final)
        }
        let host = base.host() ?? base.absoluteString

        // Atlassian Statuspage and incident.io serve summary.json under
        // /api/v2; some compatible pages (status.x.ai) file it under
        // /api/compat instead. Try each in turn.
        for feedURL in Self.feedPaths.map({ base.appending(path: $0) }) {
            var request = URLRequest(url: feedURL)
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            request.setValue("SWE (macOS)", forHTTPHeaderField: "User-Agent")
            let data: Data
            let response: HTTPURLResponse
            do {
                (data, response) = try await transport.send(request)
            } catch {
                return .failure("Couldn't reach \(host).")
            }
            if response.statusCode == 200, let report = StatusParser.statuspage(data, now: now()) {
                return .ok(pageURL: base, feedURL: feedURL, report: report)
            }
        }
        return .failure("SWE can read Atlassian Statuspage and incident.io pages, which serve a JSON feed under /api. \(host) doesn't.")
    }

    /// The feed paths `probe` tries at a page's root, in order.
    private static let feedPaths = ["api/v2/summary.json", "api/compat/summary.json"]

    /// `scheme://host/` for a URL, the port kept and the path and query
    /// dropped: Statuspage and incident.io serve `summary.json` at the root.
    private static func base(_ url: URL) -> URL {
        var parts = URLComponents()
        parts.scheme = url.scheme?.lowercased()
        parts.host = url.host(percentEncoded: false)
        parts.port = url.port
        parts.path = "/"
        return parts.url ?? url
    }

    /// The real `redirectResolver`: one HEAD request that follows redirects
    /// and answers with the URL the page finally responds at, nil on failure.
    public static func resolveRedirects(to url: URL) async -> URL? {
        var request = URLRequest(url: url, timeoutInterval: 10)
        request.httpMethod = "HEAD"
        let session = URLSession(configuration: .ephemeral)
        defer { session.finishTasksAndInvalidate() }
        return (try? await session.data(for: request))?.1.url
    }

    /// Drop everything kept for an id: this process's memory, a request in
    /// flight and the cache file. Called when a custom page is removed, so
    /// a same-named page added later cannot inherit its reading.
    public func forget(id: String) {
        memory.removeValue(forKey: id)
        inFlight.removeValue(forKey: id)?.cancel()
        write { entries in entries.removeValue(forKey: id) }
    }

    /// Of two entries for one page, the one from the later attempt: another
    /// running copy may have saved a newer reading or cooldown.
    static func newer(_ mine: Entry?, _ theirs: Entry?) -> Entry? {
        guard let mine else { return theirs }
        guard let theirs else { return mine }
        return (theirs.attemptedAt ?? .distantPast) > (mine.attemptedAt ?? .distantPast) ? theirs : mine
    }

    /// The disk and this process's memory, newest per page.
    private func current() -> [String: Entry] {
        load().merging(memory) { disk, mine in Self.newer(mine, disk)! }
    }

    private func status(for service: StatusService, _ entry: Entry?, now: Date) -> ServiceStatus {
        guard let entry else {
            return ServiceStatus(service: service, report: nil, checkedAt: nil, problem: nil, isStale: false)
        }
        let fresh = entry.checkedAt.map { now.timeIntervalSince($0) <= staleAfter } ?? false
        let report = fresh ? entry.report : nil
        return ServiceStatus(service: service, report: report, checkedAt: entry.checkedAt,
                             problem: entry.problem, isStale: report != nil && entry.problem != nil)
    }

    /// One request, folded into the page's entry.
    private static func check(
        _ service: StatusService, _ entry: Entry, transport: HTTPTransport, clock: @Sendable () -> Date
    ) async -> Entry {
        var entry = entry
        var request = URLRequest(url: service.feedURL)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("SWE (macOS)", forHTTPHeaderField: "User-Agent")
        // Validators only with a reading to fall back on, or a 304 leaves nothing.
        if entry.report != nil {
            if let etag = entry.etag { request.setValue(etag, forHTTPHeaderField: "If-None-Match") }
            if let modified = entry.lastModified { request.setValue(modified, forHTTPHeaderField: "If-Modified-Since") }
        }

        let data: Data
        let response: HTTPURLResponse
        do {
            (data, response) = try await transport.send(request)
        } catch {
            entry.problem = "Couldn't reach \(service.host)"
            return entry
        }
        // Read after the answer: a Retry-After counts from when it arrived,
        // and a slow response must not shorten the wait.
        let now = clock()

        switch response.statusCode {
        case 200..<300:
            let report: StatusReport? = switch service.feed {
            case .statuspage: StatusParser.statuspage(data, now: now)
            case .googleCloud(_, let products): StatusParser.googleCloud(data, products: products, now: now)
            }
            guard let report else {
                entry.problem = "Couldn't read \(service.host)"
                entry.etag = nil
                entry.lastModified = nil
                return entry
            }
            entry.report = report
            entry.checkedAt = now
            entry.etag = response.value(forHTTPHeaderField: "ETag")
            entry.lastModified = response.value(forHTTPHeaderField: "Last-Modified")
            entry.cooldownUntil = nil
            entry.problem = nil
        case 304 where entry.report != nil:
            entry.checkedAt = now
            entry.cooldownUntil = nil
            entry.problem = nil
        case 429, 503:
            // Without a Retry-After, back off for a quarter of an hour; with
            // one, never longer than six hours.
            let asked = RetryAfter.date(response.value(forHTTPHeaderField: "Retry-After"), now: now)
            let until = min(asked ?? now.addingTimeInterval(900), now.addingTimeInterval(6 * 3600))
            entry.cooldownUntil = until
            entry.problem = "\(service.host) asked us to wait until \(until.formatted(date: .omitted, time: .shortened))"
        default:
            entry.problem = "\(service.host) answered \(response.statusCode)"
        }
        return entry
    }

    // MARK: Cache file

    private func load() -> [String: Entry] {
        guard let data = try? Data(contentsOf: cacheURL) else { return [:] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .deferredToDate
        return (try? decoder.decode(File.self, from: data))?.services ?? [:]
    }

    /// Folds `updated` into memory and into what is on disk now, under a
    /// lock shared with any other running copy. A failed write loses
    /// nothing for this process: memory still has every entry.
    private func save(_ updated: [String: Entry]) {
        for (id, entry) in updated { memory[id] = Self.newer(entry, memory[id]) }
        write { entries in
            for (id, entry) in updated { entries[id] = Self.newer(entry, entries[id]) }
        }
    }

    /// Reads the file under the lock, runs `change` and writes it back,
    /// the same way `save` does: directory created, locked, atomically
    /// replaced.
    private func write(_ change: (inout [String: Entry]) -> Void) {
        let manager = FileManager.default
        try? manager.createDirectory(at: cacheURL.deletingLastPathComponent(), withIntermediateDirectories: true,
                                     attributes: [.posixPermissions: 0o700])
        // Without the lock another copy could overwrite a newer entry.
        guard let lock = try? FileLock(at: cacheURL.appendingPathExtension("lock")) else { return }
        defer { lock.release() }
        var entries = load()
        change(&entries)
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .deferredToDate
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let data = try? encoder.encode(File(services: entries)) {
            try? data.write(to: cacheURL, options: .atomic)
        }
    }
}
