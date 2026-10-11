import Foundation
import Observation

/// One page's value from swe: shows the last saved one at once, fetches
/// behind it, and skips swe while what is on show is fresh.
@MainActor
@Observable
public final class SnapshotLoader<Value: SnapshotValue> {
    public private(set) var state: Loadable<Value> = .idle
    public var isLoading: Bool { inflight != nil }

    private let snapshots: (any SnapshotStoring<Snapshot<Value>>)?
    private let maxAge: TimeInterval
    private let timeout: Duration
    private let now: @Sendable () -> Date
    private let saving: @Sendable (Value) -> Value
    /// When the value on show was fetched and from which swe; nil until one is.
    private var loadedAt: Date?
    private var loadedFrom: String?
    /// The swe most recently asked for, which a load with no source uses.
    private var requested: String?
    /// The fetch in flight and the swe it asks. It runs in a task of its
    /// own, so a page that closes doesn't cancel it, and a page that
    /// reopens waits for it instead of starting another.
    private var inflight: (source: String, task: Task<Bool, Never>)?
    /// Only the newest load writes the value.
    private var generation = 0

    /// - Parameters:
    ///   - snapshots: where the last value is kept between launches.
    ///   - maxAge: how long a value counts as fresh, so opening the page
    ///     again soon after doesn't ask swe again.
    ///   - timeout: how long swe may take before the fetch is stopped and
    ///     counts as failed, so a hung swe can't block the next try.
    ///   - saving: what of a value goes to disk, for a value holding
    ///     anything that must stay in memory.
    public init(snapshots: (any SnapshotStoring<Snapshot<Value>>)?, maxAge: TimeInterval = 60,
                timeout: Duration = .seconds(60), now: @escaping @Sendable () -> Date = Date.init,
                saving: @escaping @Sendable (Value) -> Value = { $0 }) {
        self.snapshots = snapshots
        self.maxAge = maxAge
        self.timeout = timeout
        self.now = now
        self.saving = saving
    }

    /// Shows the last saved value until swe answers. Does nothing once a
    /// load has finished, so a slow read never replaces a newer value or
    /// hides a failure.
    public func restore() async {
        guard loadedAt == nil, let snapshot = await snapshots?.load(), loadedAt == nil else { return }
        if case .failed = state { return }
        state = .loaded(snapshot.value)
        loadedAt = snapshot.savedAt
        loadedFrom = snapshot.source
    }

    /// Fetches again unless this swe's value is under `maxAge` old. Joins a
    /// fetch already in flight from the same swe rather than starting
    /// another. Refresh calls `load` instead, which always fetches.
    public func loadIfStale(from source: String, fetch: @escaping @Sendable () async throws -> Value) async {
        requested = source
        if let inflight, inflight.source == source {
            _ = await inflight.task.value
            return
        }
        if case .loaded = state, loadedFrom == source, let loadedAt, now().timeIntervalSince(loadedAt) < maxAge {
            return
        }
        await load(from: source, fetch: fetch)
    }

    /// Fetches again after a change made through `source`'s swe, unless
    /// another swe has been asked for since: its value is the one to keep.
    /// - Returns: false when another swe has been asked for, before or
    ///   during the reload, so the value on show is no longer `source`'s.
    ///   A newer load from the same swe still counts: it is as fresh.
    @discardableResult
    public func reload(from source: String, fetch: @escaping @Sendable () async throws -> Value) async -> Bool {
        guard requested == nil || requested == source else { return false }
        await load(from: source, fetch: fetch)
        return requested == nil || requested == source
    }

    /// Fetches from swe and shows the result.
    /// - Parameters:
    ///   - source: the swe asked, as set in Settings; nil keeps the last one.
    ///   - timeout: overrides the loader's for a fetch known to be slow.
    /// - Returns: whether this load's result is the one on show, false when
    ///   a newer load started meanwhile.
    @discardableResult
    public func load(from source: String?, timeout: Duration? = nil,
                     fetch: @escaping @Sendable () async throws -> Value) async -> Bool {
        generation += 1
        let mine = generation
        if let source { requested = source }
        let key = source ?? requested ?? loadedFrom ?? ""
        if case .loaded = state {} else { state = .loading }
        let limit = timeout ?? self.timeout
        let task = Task { await self.run(fetch, from: key, within: limit, generation: mine) }
        inflight = (key, task)
        return await task.value
    }

    private func run(_ fetch: @escaping @Sendable () async throws -> Value, from key: String,
                     within limit: Duration, generation mine: Int) async -> Bool {
        defer { if mine == generation { inflight = nil } }
        let next: Loadable<Value>
        do {
            next = .loaded(try await withDeadline(limit, onTimeout: { TimedOut(timeout: limit) }, fetch))
        } catch {
            next = .failed(error.localizedDescription)
        }
        guard mine == generation else { return false }
        state = next
        guard case .loaded(let value) = next else { return true }
        let at = now()
        loadedAt = at
        loadedFrom = key
        await snapshots?.save(Snapshot(source: key, savedAt: at, value: saving(value)))
        return true
    }

    struct TimedOut: LocalizedError {
        var timeout: Duration
        var errorDescription: String? {
            "swe didn't answer within \(timeout.components.seconds) seconds. Refresh to try again."
        }
    }
}
