import Foundation
import Observation

/// Settings › Experimental's "Session search" row: the on/off switch for
/// the transcript index, the status line under it, and the Clear button.
///
/// Enabling means handing a `SearchIndexer` to the ledger (`setIndexer`):
/// every scan then feeds the FTS index off the same transcript read the
/// usage ledger already performs, so the feature rides the scan path the
/// app always had. While enabled a reconcile task re-runs that entry —
/// `overview.load(scan:recentLimit:)` capped to each source's newest
/// files — on a short cycle so sessions running now become searchable
/// within a minute; a poll task keeps the status line
/// honest — every couple of seconds while a scan runs, slower once it
/// settles — and reads the index only while the line is on screen.
@MainActor
@Observable
public final class SearchIndexModel {
    /// The UserDefaults key the switch writes. The CLI reads the same key
    /// for its own index mode.
    public static let defaultsKey = "searchIndexingEnabled"

    /// Files per source the reconcile tick scans — enough to cover the
    /// sessions a user has live, without paying for the backlog on every
    /// pass. The app's own refresh still runs the full sweep.
    static let recentPerAgent = 12

    /// The switch itself. Persisted; a flip applies at once.
    public var enabled: Bool {
        didSet {
            defaults.set(enabled, forKey: Self.defaultsKey)
            Task { await apply() }
        }
    }

    /// This build's SQLite can run the FTS5 index. When not, the row says so
    /// and the switch never attaches.
    public let supported: Bool

    /// The line under the switch: progress mid-scan, totals when settled,
    /// or why the index never attached.
    public private(set) var statusText = "Off."

    /// The last `store.status()` the poll read; nil until the first answer.
    public private(set) var lastStatus: SearchStatus?

    /// When a scan the model saw finished — enabling's own kick, a resync,
    /// or a poll that watched the ledger's scan settle. Nil until then, so
    /// a store that only ever read an old index shows no timestamp.
    public private(set) var lastSynced: Date?

    /// Whether Settings is showing the status line. The row sets it on
    /// appear and disappear; showing it reads the index at once.
    public var statusVisible = false {
        didSet {
            guard statusVisible, !oldValue else { return }
            Task { await refreshStatus() }
        }
    }

    /// A scan is running (the ledger's `isLoading`): the resync button
    /// dims while one is.
    public var isScanning: Bool { overview?.isLoading ?? false }

    /// Internal so tests can check what got attached under `@testable`.
    var store: SearchStore?
    var indexer: SearchIndexer?
    /// Index reads `refreshStatus` started, and poll ticks run, so tests
    /// can tell a hidden poll skipped the index.
    private(set) var statusReads = 0
    private(set) var pollTicks = 0
    /// A failure the status line shows instead of the counts.
    private var statusError: String?

    private let quiver: URL
    private let defaults: UserDefaults
    /// Nil when the ledger never opened (the app reports that separately):
    /// every attach then shows the reason instead of an index.
    private let ledger: Ledger?
    /// The scan entry every trigger uses — enabling, the reconcile tick and
    /// the app's own refresh all run the same `load(scan:)`.
    private let overview: OverviewModel?
    /// How often the index piggybacks a scan while enabled.
    private let reconcileInterval: Duration
    /// Poll cadence while a scan runs and once it settles.
    private let pollScanning: Duration
    private let pollSettled: Duration
    private var started = false
    /// What the last poll saw of the ledger's scan, for the settle that
    /// stamps `lastSynced`.
    private var wasScanning = false
    /// The apply currently running; a second call waits it out, then applies
    /// the newest value — the last toggle always wins.
    private var applying: Task<Void, Never>?
    private var polling: Task<Void, Never>?
    private var reconciling: Task<Void, Never>?

    public init(
        quiver: URL, ledger: Ledger?, overview: OverviewModel?,
        defaults: UserDefaults = .standard,
        supported: Bool = SearchStore.fts5Supported,
        reconcileInterval: Duration = .seconds(60),
        pollScanning: Duration = .seconds(2),
        pollSettled: Duration = .seconds(10)
    ) {
        self.quiver = quiver
        self.ledger = ledger
        self.overview = overview
        self.defaults = defaults
        self.supported = supported
        self.reconcileInterval = reconcileInterval
        self.pollScanning = pollScanning
        self.pollSettled = pollSettled
        enabled = defaults.bool(forKey: Self.defaultsKey)
    }

    /// Applies the persisted switch once the app is up. Called from
    /// `AppModel.start()`: like every other loader there, nothing runs
    /// before `start()`.
    public func start() {
        guard !started else { return }
        started = true
        Task { await apply() }
    }

    /// The current `enabled` made real. Single-flight: a call while one is
    /// in flight waits for it, then applies the newest value.
    func apply() async {
        while let applying {
            await applying.value
            // `await` on a finished task returns without suspending, and the
            // owner only clears `applying` after its own await resumes — so
            // yield here or this loop spins on the main actor and the owner
            // never runs.
            await Task.yield()
        }
        let task = Task { await self.applyOnce() }
        applying = task
        await task.value
        applying = nil
    }

    private func applyOnce() async {
        if enabled && supported { await attach() } else { await detach() }
    }

    private func attach() async {
        guard supported else { await detach(); return }
        guard let ledger else {
            statusError = "The usage ledger isn't open, so there is no scan to index with."
            statusText = statusError!
            return
        }
        if store == nil {
            do {
                store = try SearchStore(directory: SearchLocation.directory(under: quiver))
            } catch {
                // A store that cannot open or heal itself (the schema's
                // rebuild already ran) stays off: the app keeps working and
                // the row says why.
                statusError = "Couldn't open the search index: \(error.localizedDescription)"
                statusText = statusError!
                return
            }
            statusError = nil
            if store?.fts5Available == false {
                store?.clear()
                store = nil
                statusError = "This build's SQLite has no FTS5, so the index can't run."
                statusText = statusError!
                return
            }
        }
        if indexer == nil {
            let indexer = SearchIndexer(store: store!)
            self.indexer = indexer
            await ledger.setIndexer(indexer)
        }
        startPolling()
        startReconcile()
        await refreshStatus()
        await overview?.load(scan: true)
        lastSynced = Date()
    }

    private func detach() async {
        reconciling?.cancel()
        reconciling = nil
        polling?.cancel()
        polling = nil
        if indexer != nil {
            indexer = nil
            // The actor can still be inside an index call — its scan binds
            // the indexer across awaits — but the detached store stays open
            // under the indexer's hold, so late writes commit harmlessly to
            // the same file the next attach reopens. Tearing the file away
            // is `clearData`'s path, and the store's lock covers that.
            await ledger?.setIndexer(nil)
        }
        store = nil
        statusError = nil
        lastStatus = nil
        lastSynced = nil
        statusText = "Off."
    }

    /// Settings' "Clear search data": detaches, deletes the database and
    /// every journal file beside it, then — while still enabled — opens a
    /// fresh store, reattaches and kicks a scan, so the row climbs back
    /// from zero. Re-reading every transcript is the honest cost of
    /// clearing; the reconcile tick would do it anyway, the kick just
    /// doesn't wait a minute for it.
    ///
    /// The whole sequence runs inside the `applying` flight: a toggle that
    /// lands while `setIndexer(nil)` is still in the ledger's queue must not
    /// run `attach()` against the store this clear is about to delete.
    public func clearData() async {
        while let applying {
            await applying.value
            await Task.yield()
        }
        let task = Task { await self.clearOnce() }
        applying = task
        await task.value
        applying = nil
    }

    private func clearOnce() async {
        reconciling?.cancel()
        reconciling = nil
        polling?.cancel()
        polling = nil
        indexer = nil
        await ledger?.setIndexer(nil)
        if let store {
            store.clear()
            self.store = nil
        } else {
            // Never opened this run: the files may still be on disk from a
            // previous session, so remove them by name.
            SearchLocation.removeDatabase(
                at: SearchLocation.directory(under: quiver)
                    .appending(path: SearchLocation.fileName))
        }
        lastStatus = nil
        lastSynced = nil
        statusError = nil
        if enabled {
            // The body, not `apply()`: this already runs inside the
            // single-flight, so `apply()` would await its own task and
            // hang. A mid-clear toggle still serializes — its `apply()`
            // queues behind this flight and applies the newest value.
            await applyOnce()
        } else {
            statusText = "Off."
        }
    }

    /// One poll of `store.status()`. The query runs detached so a `count(*)`
    /// over a big index never holds the main thread, and the answer is
    /// dropped if the store moved on (toggle off, clear) while it ran. A
    /// failed read lands on the status line too — the app keeps running.
    func refreshStatus() async {
        guard let store else { return }
        statusReads += 1
        let polled: Result<SearchStatus, Error> = await Task
            .detached(priority: .utility) { [store] in
                Result(catching: { try store.status() })
            }.value
        guard self.store === store else { return }
        switch polled {
        case .success(let status):
            lastStatus = status
            let scanning = noteScan()
            statusText = statusError
                ?? Self.statusLine(status, scanning: scanning, synced: lastSynced)
        case .failure(let error):
            statusText = "Couldn't read the index status: \(error.localizedDescription)"
        }
    }

    /// Watches the ledger's scan without touching the index: a scan the
    /// model didn't kick (the app's own refresh) still counts as a sync
    /// once a poll sees it end. Returns whether one is running now.
    @discardableResult
    private func noteScan() -> Bool {
        let scanning = overview?.isLoading ?? false
        if wasScanning && !scanning { lastSynced = Date() }
        wasScanning = scanning
        return scanning
    }

    /// Settings' resync: the same full `load(scan:)` enabling kicks, then a
    /// status read so the line shows the fresh totals at once.
    public func resync() async {
        await overview?.load(scan: true)
        lastSynced = Date()
        await refreshStatus()
    }

    /// The status poll: every `pollScanning` while the ledger's scan is in
    /// flight (the `isLoading` signal), `pollSettled` once it is quiet.
    /// The index's `count(*)` reads only run while the row is on screen;
    /// on a large index they cost a few percent of a core every poll.
    /// Hidden, the poll only watches the scan, so `lastSynced` stays true.
    private func startPolling() {
        polling?.cancel()
        polling = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if statusVisible {
                    await refreshStatus()
                } else {
                    noteScan()
                }
                pollTicks += 1
                do {
                    try await Task.sleep(
                        for: overview?.isLoading ?? false ? pollScanning : pollSettled)
                } catch { return }
            }
        }
    }

    /// Keeps the index current: the app's `load(scan:)`, capped to each
    /// source's `recentPerAgent` newest files and run on a short cycle
    /// while enabled, so a session that ends now is searchable within a
    /// minute. The capped pass is not a census — the app's own refresh
    /// still sweeps the backlog whole.
    private func startReconcile() {
        reconciling?.cancel()
        reconciling = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                do { try await Task.sleep(for: reconcileInterval) } catch { return }
                await overview?.load(scan: true, recentLimit: Self.recentPerAgent)
            }
        }
    }

    /// The line under the switch. `scanning` is the ledger scan the index
    /// rides on (Overview's `isLoading`): while it runs the file count can
    /// only grow, so the line reports progress — a total mid-scan would be
    /// made up. `synced` is when the last scan finished; with one the
    /// settled line leads "Last synced …", without it falls back to
    /// "Synced:".
    static func statusLine(_ status: SearchStatus?, scanning: Bool, synced: Date? = nil) -> String {
        guard let status else { return "The index hasn't run yet." }
        var line: String
        if scanning {
            let files = status.filesByState.values.reduce(0, +)
            line = "Indexing: \(files.formatted()) \(plural("file", of: files)) read so far"
        } else {
            let totals = "\(status.sessionCount.formatted()) "
                + "\(plural("session", of: status.sessionCount)), "
                + "\(status.messageCount.formatted()) "
                + "\(plural("message", of: status.messageCount))"
            line = synced.map { "Last synced \(syncedText($0)) · \(totals)" }
                ?? "Synced: \(totals)"
        }
        let due = status.filesByState[.due] ?? 0
        let failed = status.filesByState[.failed] ?? 0
        if due > 0 { line += " · \(due.formatted()) \(plural("file", of: due)) behind" }
        if failed > 0 { line += " · \(failed.formatted()) \(plural("file", of: failed)) failed" }
        return line
    }

    /// "2:41 PM" today, "Oct 26, 2:41 PM" once it's yesterday — the point
    /// is when, so the date drops off when it's obviously today.
    static func syncedText(_ date: Date) -> String {
        Calendar.current.isDateInToday(date)
            ? date.formatted(.dateTime.hour().minute())
            : date.formatted(date: .abbreviated, time: .shortened)
    }

    private static func plural(_ noun: String, of count: Int) -> String {
        count == 1 ? noun : noun + "s"
    }
}
