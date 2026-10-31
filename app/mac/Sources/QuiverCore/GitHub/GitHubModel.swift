import Foundation
import Observation

/// Which surface of the selected repo the Repositories page shows.
public enum RepoTab: String, CaseIterable, Identifiable, Sendable {
    case pulls, issues, actions, pages, releases, tags, branches
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .pulls: "Pull requests"
        case .issues: "Issues"
        case .actions: "Actions"
        case .pages: "Pages"
        case .releases: "Releases"
        case .tags: "Tags"
        case .branches: "Branches"
        }
    }
}

/// All seven surfaces' state for one pinned repo. Each loads on first
/// visit and is re-fetched by refresh and the page's poll loop; a surface
/// that was never asked for stays `.idle` rather than reading as empty.
public struct RepoBoard: Codable, Equatable, Sendable {
    public var pulls: Loadable<[GhPr]> = .idle
    public var issues: Loadable<[GhIssue]> = .idle
    public var runs: Loadable<[GhRun]> = .idle
    /// nil inside `.loaded` means the repo has no Pages site.
    public var pages: Loadable<GhPages?> = .idle
    public var releases: Loadable<[GhRelease]> = .idle
    public var tags: Loadable<[GhTag]> = .idle
    /// Branches plus the default-branch name `swe gh branches` reports.
    public var branches: Loadable<[GhBranch]> = .idle

    /// Mark the tab's surface failed — used when the `swe` call itself
    /// threw, before any per-repo entry could be read.
    mutating func fail(_ tab: RepoTab, message: String) {
        switch tab {
        case .pulls: pulls = .failed(message)
        case .issues: issues = .failed(message)
        case .actions: runs = .failed(message)
        case .pages: pages = .failed(message)
        case .releases: releases = .failed(message)
        case .tags: tags = .failed(message)
        case .branches: branches = .failed(message)
        }
    }

    /// Whether the tab's surface holds rows — `.idle` and `.failed`
    /// both want a fetch regardless of the timestamp.
    func isLoaded(_ tab: RepoTab) -> Bool {
        rowCount(tab) != nil
    }

    /// The tab's loaded row count; nil for anything not `.loaded`.
    /// A count below the requested limit means the listing ended —
    /// the scroll loader uses it to stop growing the window.
    func rowCount(_ tab: RepoTab) -> Int? {
        func count<T>(_ l: Loadable<[T]>) -> Int? {
            if case .loaded(let rows) = l { return rows.count }
            return nil
        }
        return switch tab {
        case .pulls: count(pulls)
        case .issues: count(issues)
        case .actions: count(runs)
        case .pages: nil
        case .releases: count(releases)
        case .tags: count(tags)
        case .branches: count(branches)
        }
    }
}

/// The GitHub pages' store: the `gh` probe, the pin list, one board per
/// repo, and the notifications inbox. Everything goes through `swe gh`;
/// GitHub failures arrive as typed statuses, not thrown errors, so a
/// dead `gh` leaves the page showing the last good rows it had.
@MainActor
@Observable
public final class GitHubModel {
    /// The `gh` probe from the last overview or surface read. nil until
    /// the first reply lands; `.unavailable`/`.authRequired` mean the
    /// pages show a setup card instead of empty lists.
    public private(set) var env: GhEnv?
    /// A reply was received at all — distinguishes "not asked yet" from
    /// "asked, gh is broken".
    public private(set) var probed = false
    /// env reads throw only on a broken swe; the message for that case.
    public private(set) var probeError: String?

    public private(set) var pins: [PinnedRepo] = []
    /// The repo the Repositories page shows, a pin name.
    public var selected: String?
    public var tab: RepoTab = .pulls
    /// The repo open on the focus page — the single-repo view that
    /// swaps in over the dashboard. nil means the dashboard shows.
    /// Picking a different repo restarts the page on pull requests.
    public var focused: String? {
        didSet {
            if focused != nil, focused != oldValue { focusTab = .pulls }
        }
    }
    /// The focus page's tab; the dashboard's `tab` is left alone so a
    /// trip through a repo never moves it.
    public var focusTab: RepoTab = .pulls
    /// The dashboard's pull request filter (All, Mine, Review
    /// requested). `activePullFilter` falls back to All while the flags
    /// it needs are unknown.
    public var pullFilter: PullFilter = .all
    /// Per repo, the open PRs `gh pr status` says ask for the login's
    /// review (`loadReviewRequests`). A repo missing here is unknown.
    public internal(set) var reviewRequests: [String: PrReviewRequests] = [:]
    /// The page's filter text: repo names, PR titles, branch names and
    /// the rest of the row text. Lives on the model so a banner's
    /// click-through can clear it and land on its repo's section.
    public var filter = ""
    /// A repo the page should scroll into view — set by banner
    /// click-through; the page scrolls and clears it.
    public var scrollTarget: String?
    /// The repo the page's rail narrowed the list to; nil lists every
    /// pin. Going back to all repos drops a one-repo-only tab.
    public var shownRepo: String? {
        didSet {
            if !Self.tabs(oneRepo: shownRepo != nil).contains(tab) { tab = .pulls }
        }
    }

    public private(set) var boards: [String: RepoBoard] = [:]
    /// Which (repo, tab) is mid-fetch, so rows keep their last data
    /// while a spinner shows on the tab.
    public private(set) var loading: Set<BoardKey> = []
    /// A second request while one was already running — its reply
    /// predates the second call, so the surface refetches after it lands
    /// rather than dropping the refresh.
    private var queuedRefreshes: Set<BoardKey> = []

    /// The focus page's boards, loading set and clocks — its own store.
    /// Pulls and issues fetch every state, since the page exists to
    /// survey a repo's recent work and open-only would hide what
    /// landed; the rows can't share the dashboard's open-only board.
    public private(set) var focusBoards: [String: RepoBoard] = [:]
    public private(set) var focusLoading: Set<BoardKey> = []
    private var focusFetched: [BoardKey: Date] = [:]
    /// `queuedRefreshes` for the focus store: a request during a fetch
    /// reruns once the older reply lands.
    private var focusQueued: Set<BoardKey> = []
    /// How many rows each focus surface asks for — grows by
    /// `focusStep` every time the scroll loader reaches the end.
    private var focusLimits: [BoardKey: Int] = [:]
    /// Focus surfaces whose last reply came back short of the limit —
    /// the remote has no more, so the scroll loader stops asking.
    public private(set) var focusExhausted: Set<BoardKey> = []
    /// Every row the focus page has ever fetched, per repo. Seeding
    /// shows it before the network answers; each ok reply merges in by
    /// number, so the listing accumulates past the fetch window rather
    /// than resetting to it. The dictionary is the in-session cache;
    /// `historyStore` is what survives the quit — a repo's rows load
    /// from SQLite on first touch.
    private var history: [String: RepoHistory] = [:]
    /// The SQLite store history persists through; nil in tests or when
    /// the file cannot open — the in-memory dictionary still covers
    /// the session either way.
    private let historyStore: GitHubHistory?
    /// Snapshot-era rows not yet confirmed inside SQLite. `saveNow`
    /// keeps writing them into the JSON file until `importAll` commits —
    /// dropping them earlier would erase the only copy when the
    /// database cannot open.
    private var legacyHistory: [String: RepoHistory]?
    /// SQLite writes ride a serial queue so a fetch reply never blocks
    /// the main actor on a busy or slow disk; order is preserved, so a
    /// `removeRepo` still wins over the remembers queued before it.
    let historyWriteQueue = DispatchQueue(label: "quiver.gh-history")
    /// Set once the store has provably taken the snapshot's boards —
    /// `saveNow` drops the file's `boards` field only then, so an
    /// unopened database or a failed import never erases the last copy.
    private var storeOwnsBoards = false

    public private(set) var inbox: Loadable<[GhNotification]> = .idle
    /// An inbox read is in flight; a second caller waits for it rather
    /// than racing it.
    private var inboxReading = false
    /// Callers waiting for the read in flight to land.
    private var inboxWaiters: [CheckedContinuation<Void, Never>] = []
    /// How many callers are waiting on the read in flight, so a test can
    /// wait for an overlapping caller to queue instead of yielding.
    var inboxWaiterCount: Int { inboxWaiters.count }
    /// How many reads have started, so a forced caller can tell a read
    /// begun after it asked (which answers it) from an older one.
    private var inboxReadsStarted = 0
    /// Bumped by each read and by marking threads read: only the newest
    /// read's reply, from after the last mark, lands.
    private var inboxGeneration = 0
    /// Unread lines in the loaded inbox — the sidebar badge and the
    /// Dock tile both read this. 0 until the first fetch lands and
    /// through `.idle`/`.failed`, which read as "nothing known" rather
    /// than "inbox zero".
    public var unreadCount: Int {
        guard case .loaded(let notes) = inbox else { return 0 }
        return notes.filter(\.unread).count
    }
    /// Newest inbox fetch time; the page polls on a timer and shows
    /// "updated Ns ago" from it.
    public private(set) var inboxFetchedAt: Date?
    /// A pin search in the Add sheet, newest query wins.
    public private(set) var searchResults: [RepoHit] = []
    public private(set) var searching = false
    public private(set) var searchError: String?

    /// The synced repo catalog `repo search` reads — nil until the
    /// first `swe gh sync` has run. The Settings card owns resyncs.
    public private(set) var catalog: GhCatalog?
    public private(set) var syncing = false
    /// The last sync's failure, or which owners it skipped — shown as a
    /// warning on the Settings card, cleared on a clean resync.
    public private(set) var syncError: String?
    /// Bumped every time `syncGitHub` writes a fresh catalog — a `load`
    /// reply that started before that write must not overwrite it.
    private var syncGeneration = 0

    /// A mutation in flight — add or remove — keyed by repo name so two
    /// taps on the same repo can't run concurrently.
    public private(set) var mutating: Set<String> = []
    public private(set) var pinError: String?

    public struct BoardKey: Hashable, Sendable {
        public var repo: String
        public var tab: RepoTab
        public init(repo: String, tab: RepoTab) {
            self.repo = repo
            self.tab = tab
        }
    }

    /// When each board field was last filled; a surface older than
    /// `staleAfter` refetches when it becomes visible.
    private var fetched: [BoardKey: Date] = [:]
    /// Repos whose surfaces were hydrated by `swe gh board` this launch —
    /// the attempt flag, so the board runs once per repo per session and
    /// failures degrade to per-tab fetches.
    private var boardHydrated: Set<String> = []
    /// When the pin list last loaded. Separate from `probed` because
    /// `loadInbox` sets `probed` without fetching pins — gating on
    /// `probed` alone would leave Repositories stuck on the empty card.
    private var pinsFetchedAt: Date?
    private var generation = 0
    private let now: () -> Date
    private let staleAfter: TimeInterval
    /// Where the warm-start snapshot lives; nil (tests) disables disk.
    private let snapshot: SnapshotFile<GitHubSnapshot>?
    private var snapshotTask: Task<Void, Never>?
    /// The last successfully loaded inbox — a failed refresh must not
    /// erase what a relaunch would restore. `snapshotInboxLoaded`
    /// records that a load happened at all: without it a never-fetched
    /// inbox would restore as `.loaded([])` and show "no unread" while
    /// its first request runs.
    private var snapshotInbox: [GhNotification] = []
    private var snapshotInboxLoaded = false

    public init(now: @escaping () -> Date = Date.init, staleAfter: TimeInterval = 60,
                snapshotFile: URL? = nil, historyFile: URL? = nil) {
        self.now = now
        self.staleAfter = staleAfter
        snapshot = snapshotFile.map { SnapshotFile(url: $0) }
        historyStore = historyFile.map { GitHubHistory(url: $0) }
        restore()
    }

    /// Takes a reply's `gh` probe. Only `swe gh`'s overview runs `gh auth
    /// status` when the reads all came back ok; a surface reply (board,
    /// pulls, issues…) then says ok with no login. Taking that as is
    /// dropped the login, and with it the Mine filter, a second after
    /// the dashboard showed it. While gh still answers ok, the login the
    /// overview found stays.
    private func adopt(_ new: GhEnv) {
        if new.status == .ok, new.login == nil, let old = env, old.status == .ok, let login = old.login {
            env = GhEnv(status: .ok, login: login, error: new.error)
        } else {
            env = new
        }
    }

    /// The blocking env state the pages' setup card covers — gh missing
    /// or signed out. nil while probing, ok, or upstream-error (the
    /// probe failed, not the login — pages stay up).
    public var envSetup: GhEnv.Status? {
        switch env?.status {
        case .unavailable, .authRequired: env?.status
        default: nil
        }
    }

    /// The env is unusable when the probe says so — surface reads can
    /// still succeed after a probe `upstream-error` (the probe failed,
    /// not the login), so only the two certain states gate.
    public var envBlocked: String? {
        guard let env else { return probeError }
        switch env.status {
        case .ok: return nil
        case .unavailable:
            return "GitHub CLI (gh) isn't installed. Install it, then run gh auth login."
        case .authRequired:
            return "Not signed in to GitHub. Run gh auth login, then refresh."
        case .upstreamError:
            return nil
        }
    }

    /// First load: probe + pins. Keeps the selection if it is still pinned.
    public func load(swe: SweClient) async {
        let syncGen = syncGeneration
        do {
            let overview = try await swe.ghOverview()
            adopt(overview.github)
            // A reply older than the last sync's write would paint over
            // the fresher count the sync just reported.
            if syncGeneration == syncGen { catalog = overview.catalog }
            probed = true
            probeError = nil
            pinsFetchedAt = now()
            pins = overview.repos
            if selected == nil || !pins.contains(where: { $0.name == selected }) {
                selected = pins.first?.name
            }
            scheduleSave()
        } catch is CancellationError {
        } catch {
            probed = true
            probeError = error.localizedDescription
        }
    }

    /// Fetch the pin list on first use. The surfaces themselves are
    /// per-repo (`loadTabIfStale`), so a filtered page never waits on a
    /// board it isn't showing.
    public func loadPinsIfNeeded(swe: SweClient) async {
        // `probed` alone is not enough: the inbox can set it while the
        // pin list is still empty, and a failed overview must retry.
        if pinsFetchedAt == nil { await load(swe: swe) }
    }

    /// Load whatever the page is looking at unless it was fetched recently.
    public func loadIfStale(swe: SweClient) async {
        await loadPinsIfNeeded(swe: swe)
        guard let repo = selected else { return }
        await loadTabIfStale(tab, repo: repo, swe: swe)
    }

    /// `loadIfStale` for one repo: the page shows several boards at
    /// once, each with its own clock. First look this session hydrates
    /// every surface in one `swe gh board` call; a surface the board
    /// missed or failed falls through to its own per-tab fetch.
    public func loadTabIfStale(_ tab: RepoTab, repo: String, swe: SweClient) async {
        if !boardHydrated.contains(repo) { await loadBoards([repo], swe: swe) }
        let key = BoardKey(repo: repo, tab: tab)
        // The timestamp only skips a fetch when rows are actually there —
        // a cleared board (re-pinned repo) is never "fresh".
        if let at = fetched[key], now().timeIntervalSince(at) < staleAfter,
           board(for: repo).isLoaded(key.tab) { return }
        await loadTab(tab, repo: repo, swe: swe)
    }

    /// A new notification names a repo whose rows may have moved: drop
    /// its fetch times so the next look refetches, and refresh it now
    /// when it is the repo on screen. Returns the refreshes it started
    /// (none when the repo is off screen), so a caller can wait for them.
    @discardableResult
    public func invalidate(repo: String, swe: SweClient) -> [Task<Void, Never>] {
        for key in fetched.keys where key.repo == repo { fetched[key] = nil }
        for key in focusFetched.keys where key.repo == repo { focusFetched[key] = nil }
        var refreshes: [Task<Void, Never>] = []
        if selected == repo {
            refreshes.append(Task { await loadTab(tab, repo: repo, swe: swe) })
        }
        if focused == repo {
            refreshes.append(Task { await loadFocusTab(focusTab, repo: repo, swe: swe) })
        }
        return refreshes
    }

    /// Manual refresh always fetches, even inside the stale window.
    public func refresh(swe: SweClient) async {
        if pinsFetchedAt == nil { await load(swe: swe) }
        guard let repo = selected else { return }
        await refresh(repo: repo, swe: swe)
    }

    /// `refresh` for one repo — the page's Refresh covers every repo on
    /// screen, not only the selected one.
    public func refresh(repo: String, swe: SweClient) async {
        if !boardHydrated.contains(repo) {
            await loadBoards([repo], swe: swe)
            if board(for: repo).isLoaded(tab) { return }
        }
        await loadTab(tab, repo: repo, swe: swe)
    }

    /// Fetch every surface of every repo in a single `swe gh board` call —
    /// the CLI fans the repo list out internally, so the page hydrates in
    /// one subprocess instead of pins × subprocesses in sequence.
    /// `boardHydrated` is the once-per-launch attempt flag per repo, so a
    /// failed board degrades to per-tab fetches rather than retrying the
    /// expensive call on every visit.
    public func loadBoards(_ repos: [String], swe: SweClient) async {
        let pending = repos.filter { !boardHydrated.contains($0) }
        guard !pending.isEmpty else { return }
        boardHydrated.formUnion(pending)
        let keys = pending.flatMap { repo in
            RepoTab.allCases.map { BoardKey(repo: repo, tab: $0) }
        }
        for key in keys { loading.insert(key) }
        defer {
            for key in keys {
                loading.remove(key)
                if queuedRefreshes.remove(key) != nil {
                    Task { await loadTab(key.tab, repo: key.repo, swe: swe) }
                }
            }
        }
        do {
            let reply = try await swe.ghBoard(repos: pending)
            adopt(reply.github)
            probed = true
            let stamp = now()
            for repo in pending {
                // A repo absent from the reply keeps its prior state —
                // the surfaces were never fetched, so per-tab loads
                // pick it up on demand.
                guard let payload = reply.boards.first(where: { $0.repo == repo })
                else { continue }
                var board = boards[repo] ?? RepoBoard()
                let s = payload.surfaces
                fill(s.pullRequests, into: &board, \.pulls, repo: repo, tab: .pulls, stamp: stamp,
                     persist: persist(repo: repo, surface: .pulls)) { $0.pullRequests ?? [] }
                fill(s.issues, into: &board, \.issues, repo: repo, tab: .issues, stamp: stamp,
                     persist: persist(repo: repo, surface: .issues)) { $0.issues ?? [] }
                fill(s.runs, into: &board, \.runs, repo: repo, tab: .actions, stamp: stamp,
                     persist: persist(repo: repo, surface: .runs)) { $0.runs ?? [] }
                fill(s.pages, into: &board, \.pages, repo: repo, tab: .pages, stamp: stamp,
                     persist: { [historyStore] site in
                         historyStore?.replace(repo: repo, surface: .pages,
                                               rows: site.map { [$0] } ?? []) { _ in "site" }
                     }) { $0.pages }
                fill(s.releases, into: &board, \.releases, repo: repo, tab: .releases, stamp: stamp,
                     persist: persist(repo: repo, surface: .releases)) { $0.releases ?? [] }
                fill(s.tags, into: &board, \.tags, repo: repo, tab: .tags, stamp: stamp,
                     persist: persist(repo: repo, surface: .tags)) { $0.tags ?? [] }
                fill(s.branches, into: &board, \.branches, repo: repo, tab: .branches, stamp: stamp,
                     persist: persist(repo: repo, surface: .branches)) { $0.branches ?? [] }
                boards[repo] = board
            }
            scheduleSave()
        } catch is CancellationError {
        } catch {
            for repo in pending {
                var board = boards[repo] ?? RepoBoard()
                board.fail(tab, message: error.localizedDescription)
                boards[repo] = board
            }
        }
    }

    /// Write one board surface's entry into `path`: ok loads the rows and
    /// the fetched stamp, anything else is the entry's typed failure. A
    /// loaded value also lands in SQLite through `persist`, called on the
    /// write queue so the reply never blocks on disk. `persist` must be
    /// `@Sendable`: a plain closure formed on this actor inherits
    /// `@MainActor` isolation and traps when the write queue runs it.
    private func fill<Entry: GhEntryFields, Row: Equatable & Sendable>(
        _ entry: Entry?, into board: inout RepoBoard,
        _ path: WritableKeyPath<RepoBoard, Loadable<Row>>,
        repo: String, tab: RepoTab, stamp: Date,
        persist: (@Sendable (Row) -> Void)? = nil,
        rows: (Entry) -> Row
    ) {
        let value: Loadable<Row>
        if let entry {
            value = entry.status == .ok
                ? .loaded(rows(entry))
                : .failed(entry.error ?? entry.status.rawValue)
        } else {
            value = .failed("swe board returned no \(tab.rawValue) entry")
        }
        board[keyPath: path] = value
        if case .loaded(let r) = value {
            fetched[BoardKey(repo: repo, tab: tab)] = stamp
            if let persist { historyWriteQueue.async { persist(r) } }
        }
    }

    /// Fetch one surface of one repo into its board slot. The reply's
    /// entry for `repo` decides ok/failed; a missing entry is a failure,
    /// not an empty list.
    public func loadTab(_ tab: RepoTab, repo: String, swe: SweClient) async {
        let key = BoardKey(repo: repo, tab: tab)
        guard !loading.contains(key) else {
            queuedRefreshes.insert(key)
            return
        }
        loading.insert(key)
        defer {
            loading.remove(key)
            if queuedRefreshes.remove(key) != nil {
                Task { await loadTab(tab, repo: repo, swe: swe) }
            }
        }
        do {
            switch tab {
            case .pulls:
                apply(try await swe.ghPullRequests(repo: repo), to: key, \.pulls,
                      persist: persist(repo: repo, surface: .pulls)) { $0.pullRequests ?? [] }
            case .issues:
                apply(try await swe.ghIssues(repo: repo), to: key, \.issues,
                      persist: persist(repo: repo, surface: .issues)) { $0.issues ?? [] }
            case .actions:
                apply(try await swe.ghRuns(repo: repo), to: key, \.runs,
                      persist: persist(repo: repo, surface: .runs)) { $0.runs ?? [] }
            case .pages:
                apply(try await swe.ghPages(repo: repo), to: key, \.pages,
                      persist: { [historyStore] site in
                          historyStore?.replace(repo: repo, surface: .pages,
                                                rows: site.map { [$0] } ?? []) { _ in "site" }
                      }) { $0.pages }
            case .releases:
                apply(try await swe.ghReleases(repo: repo), to: key, \.releases,
                      persist: persist(repo: repo, surface: .releases)) { $0.releases ?? [] }
            case .tags:
                apply(try await swe.ghTags(repo: repo), to: key, \.tags,
                      persist: persist(repo: repo, surface: .tags)) { $0.tags ?? [] }
            case .branches:
                apply(try await swe.ghBranches(repo: repo), to: key, \.branches,
                      persist: persist(repo: repo, surface: .branches)) { $0.branches ?? [] }
            }
        } catch is CancellationError {
        } catch {
            var board = boards[repo] ?? RepoBoard()
            board.fail(tab, message: error.localizedDescription)
            boards[repo] = board
        }
    }

    /// The board for `repo`, always non-nil so views can read directly.
    public func board(for repo: String) -> RepoBoard {
        boards[repo] ?? RepoBoard()
    }

    /// A persist closure for `apply` — the queue hop happens there, so
    /// the closure talks to the store directly. The surface picks the
    /// write mode: pulls, issues and runs accumulate past the window;
    /// releases, tags and branches are list state and rewrite whole.
    /// The `@Sendable` return is load-bearing: it keeps the closure
    /// nonisolated, so the write queue can call it.
    private func persist<Row: Codable & Identifiable & Sendable>(
        repo: String, surface: GitHubHistory.Surface
    ) -> @Sendable ([Row]) -> Void {
        { [historyStore] rows in
            switch surface {
            case .pulls, .issues, .runs:
                historyStore?.remember(repo: repo, surface: surface, rows: rows)
            case .releases, .tags, .branches:
                historyStore?.replace(repo: repo, surface: surface, rows: rows)
            case .pages:
                break // pages persists through its own closure — GhPages has no id
            }
        }
    }

    // MARK: - focus page (one repo in full)

    /// The focus board for `repo`, always non-nil so the page can read
    /// directly.
    public func focusBoard(for repo: String) -> RepoBoard {
        focusBoards[repo] ?? RepoBoard()
    }

    /// The focus page's opening fetch: all seven surfaces together,
    /// each fresh-skipped, so the initial view fills at the price of
    /// the slowest surface and a quick out-and-back costs nothing.
    /// Remembered rows go up first — the page shows what it knows
    /// while the network answers.
    public func loadFocus(repo: String, swe: SweClient) async {
        seedFocus(repo: repo)
        await withTaskGroup(of: Void.self) { group in
            for tab in RepoTab.allCases {
                group.addTask { await self.loadFocusTabIfStale(tab, repo: repo, swe: swe) }
            }
        }
    }

    /// Put the repo's remembered rows on the board before any reply —
    /// an already-loaded surface is left alone, so a re-focus does not
    /// blink fresh rows back to older ones. The store's board covers
    /// the state surfaces; the accumulating three merge their session
    /// dictionary over it.
    private func seedFocus(repo: String) {
        let h = history(repo: repo)
        var board = focusBoards[repo] ?? historyStore?.board(repo: repo) ?? RepoBoard()
        if !h.pulls.isEmpty, !board.isLoaded(.pulls) { board.pulls = .loaded(h.pulls) }
        if !h.issues.isEmpty, !board.isLoaded(.issues) { board.issues = .loaded(h.issues) }
        if !h.runs.isEmpty, !board.isLoaded(.actions) { board.runs = .loaded(h.runs) }
        focusBoards[repo] = board
    }

    /// The repo's remembered accumulating rows: the session dictionary
    /// first, then the SQLite store — one read per repo, cached either
    /// way. Reads sort newest-first, the invariant `mergeHistory` keeps
    /// — stored positions can drift when imports meet live writes.
    private func history(repo: String) -> RepoHistory {
        if let h = history[repo] { return h }
        let pulls: [GhPr] = historyStore?.rows(repo: repo, surface: .pulls) ?? []
        let issues: [GhIssue] = historyStore?.rows(repo: repo, surface: .issues) ?? []
        let runs: [GhRun] = historyStore?.rows(repo: repo, surface: .runs) ?? []
        let h = RepoHistory(
            pulls: pulls.sorted { $0.id > $1.id },
            issues: issues.sorted { $0.id > $1.id },
            runs: runs.sorted { $0.id > $1.id })
        history[repo] = h
        return h
    }

    /// `loadTabIfStale` against the focus store: a surface never fetched
    /// (or whose repo was re-pinned and cleared) is never "fresh".
    public func loadFocusTabIfStale(_ tab: RepoTab, repo: String, swe: SweClient) async {
        let key = BoardKey(repo: repo, tab: tab)
        if let at = focusFetched[key], now().timeIntervalSince(at) < staleAfter,
           focusBoard(for: repo).isLoaded(tab) { return }
        await loadFocusTab(tab, repo: repo, swe: swe)
    }

    /// Fetch one surface into the focus store. Pulls and issues go out
    /// with `--state=all`: the page is the "what happened here lately"
    /// view, so merged and closed rows are the content, not noise.
    public func loadFocusTab(_ tab: RepoTab, repo: String, swe: SweClient) async {
        let key = BoardKey(repo: repo, tab: tab)
        guard !focusLoading.contains(key) else {
            focusQueued.insert(key)
            return
        }
        focusLoading.insert(key)
        defer {
            focusLoading.remove(key)
            if focusQueued.remove(key) != nil {
                Task { await loadFocusTab(tab, repo: repo, swe: swe) }
            }
        }
        let limit = focusLimit(for: key)
        // The reply's own row count — exhaustion is measured on what
        // arrived, not on the merged listing history makes bigger.
        var fetched: Int? = nil
        do {
            switch tab {
            case .pulls:
                apply(try await swe.ghPullRequests(repo: repo, state: "all", limit: limit),
                      to: key, \.pulls, store: .focus) {
                    fetched = ($0.pullRequests ?? []).count
                    return $0.pullRequests ?? []
                }
            case .issues:
                apply(try await swe.ghIssues(repo: repo, state: "all", limit: limit),
                      to: key, \.issues, store: .focus) {
                    fetched = ($0.issues ?? []).count
                    return $0.issues ?? []
                }
            case .actions:
                apply(try await swe.ghRuns(repo: repo, limit: limit),
                      to: key, \.runs, store: .focus) { $0.runs ?? [] }
            case .pages:
                apply(try await swe.ghPages(repo: repo),
                      to: key, \.pages, store: .focus,
                      persist: { [historyStore] site in
                          historyStore?.replace(repo: repo, surface: .pages,
                                                rows: site.map { [$0] } ?? []) { _ in "site" }
                      }) { $0.pages }
            case .releases:
                apply(try await swe.ghReleases(repo: repo, limit: limit),
                      to: key, \.releases, store: .focus,
                      persist: persist(repo: repo, surface: .releases)) { $0.releases ?? [] }
            case .tags:
                apply(try await swe.ghTags(repo: repo, limit: limit),
                      to: key, \.tags, store: .focus,
                      persist: persist(repo: repo, surface: .tags)) { $0.tags ?? [] }
            case .branches:
                apply(try await swe.ghBranches(repo: repo, limit: limit),
                      to: key, \.branches, store: .focus,
                      persist: persist(repo: repo, surface: .branches)) { $0.branches ?? [] }
            }
            remember(tab, repo: repo)
            // A short page means the listing ended — the scroll loader
            // stops growing the window on this surface.
            if let fetched, fetched < limit {
                focusExhausted.insert(key)
            }
        } catch is CancellationError {
        } catch {
            var board = focusBoards[repo] ?? RepoBoard()
            board.fail(tab, message: error.localizedDescription)
            focusBoards[repo] = board
            // Remembered rows stand in for the error — a blip must not
            // blank a listing the app already knows.
            remember(tab, repo: repo)
        }
    }

    /// Fold the surface's reply into the repo's history, or put the
    /// remembered rows back when the reply failed. On `.loaded` the
    /// fetched rows win on number — their state may have moved on —
    /// and cached rows beyond the fetch window stay, so the board's
    /// listing grows instead of capping at the window.
    private func remember(_ tab: RepoTab, repo: String) {
        switch tab {
        case .pulls:
            mergeHistory(repo: repo, board: \.pulls, into: \.pulls) { rows in
                historyWriteQueue.async { [historyStore] in
                    historyStore?.remember(repo: repo, surface: .pulls, rows: rows)
                }
            }
        case .issues:
            mergeHistory(repo: repo, board: \.issues, into: \.issues) { rows in
                historyWriteQueue.async { [historyStore] in
                    historyStore?.remember(repo: repo, surface: .issues, rows: rows)
                }
            }
        case .actions:
            mergeHistory(repo: repo, board: \.runs, into: \.runs) { rows in
                historyWriteQueue.async { [historyStore] in
                    historyStore?.remember(repo: repo, surface: .runs, rows: rows)
                }
            }
        case .releases:
            restoreRemembered(repo: repo, surface: .releases, into: \.releases)
        case .tags:
            restoreRemembered(repo: repo, surface: .tags, into: \.tags)
        case .branches:
            restoreRemembered(repo: repo, surface: .branches, into: \.branches)
        case .pages:
            // Only a failed read looks backwards — a live reply is
            // fresher than anything the store holds, and its own
            // persist may not have run yet.
            guard var board = focusBoards[repo],
                  case .failed = board.pages,
                  let seeded = historyStore?.board(repo: repo),
                  case .loaded = seeded.pages else { return }
            board.pages = seeded.pages
            focusBoards[repo] = board
        }
    }

    /// A state-list surface whose reply failed shows what the store
    /// remembered instead of the error — same rule as the accumulating
    /// surfaces, just without a merge to run.
    private func restoreRemembered<Row: Codable & Equatable & Sendable>(
        repo: String, surface: GitHubHistory.Surface,
        into path: WritableKeyPath<RepoBoard, Loadable<[Row]>>
    ) {
        guard var board = focusBoards[repo],
              case .failed = board[keyPath: path],
              // The marker is the answer, not the row count — a surface
              // that fetched empty restores .loaded([]), not the error.
              historyStore?.fetched(repo: repo, surface: surface) == true
        else { return }
        let rows: [Row] = historyStore?.rows(repo: repo, surface: surface) ?? []
        board[keyPath: path] = .loaded(rows)
        focusBoards[repo] = board
    }

    /// Rows a repo remembers past a single fetch — the file stays small
    /// and even heavy repos' recent work fits well under it.
    private static let historyCap = 500

    private func mergeHistory<Row: Identifiable>(
        repo: String,
        board boardPath: WritableKeyPath<RepoBoard, Loadable<[Row]>>,
        into historyPath: WritableKeyPath<RepoHistory, [Row]>,
        persist: ([Row]) -> Void
    ) where Row.ID: Comparable & Hashable {
        var h = history(repo: repo)
        var board = focusBoards[repo] ?? RepoBoard()
        switch board[keyPath: boardPath] {
        case .loaded(let fetched):
            let seen = Set(fetched.map(\.id))
            // Number order is creation order — gh's list order — and
            // cached rows are all older than the window's first row.
            let rows = (fetched + h[keyPath: historyPath]
                .filter { !seen.contains($0.id) }).sorted { $0.id > $1.id }
            // The board holds the whole merged listing — a cap here
            // would stop the scroll loader short of real history —
            // while what persists keeps its bound.
            board[keyPath: boardPath] = .loaded(rows)
            h[keyPath: historyPath] = Array(rows.prefix(Self.historyCap))
            persist(h[keyPath: historyPath])
        default:
            if h[keyPath: historyPath].isEmpty { return }
            board[keyPath: boardPath] = .loaded(h[keyPath: historyPath])
        }
        history[repo] = h
        focusBoards[repo] = board
    }

    /// The scroll loader's fetch: grow the surface's window by
    /// `focusStep` and refetch at the new limit. Rows already shown
    /// stay put — the reply is a superset, so nothing moves.
    public func loadMoreFocus(_ tab: RepoTab, repo: String, swe: SweClient) async {
        guard tab != .pages else { return }
        let key = BoardKey(repo: repo, tab: tab)
        guard !focusExhausted.contains(key), !focusLoading.contains(key) else { return }
        focusLimits[key] = focusLimit(for: key) + Self.focusStep
        await loadFocusTab(tab, repo: repo, swe: swe)
    }

    /// A surface's fetch size — the opening defaults below, grown by
    /// `focusStep` every time `loadMoreFocus` runs.
    private func focusLimit(for key: BoardKey) -> Int {
        focusLimits[key] ?? Self.focusDefaultLimit(key.tab)
    }

    /// How much `loadMoreFocus` widens a surface each pass.
    private static let focusStep = 50

    /// The opening fetch sizes: ≥ the "past 20" the page promises on
    /// every list surface. Pages is a single settings card, not a list.
    private static func focusDefaultLimit(_ tab: RepoTab) -> Int {
        switch tab {
        case .pulls, .issues, .branches: 50
        case .actions, .tags: 30
        case .releases: 20
        case .pages: 0
        }
    }

    /// Whether the scroll loader may still find older rows — the
    /// surface fetched its full window last time, so more could exist.
    public func focusCanGrow(_ key: BoardKey) -> Bool {
        guard key.tab != .pages, !focusExhausted.contains(key) else { return false }
        return focusBoard(for: key.repo).rowCount(key.tab) ?? 0 >= focusLimit(for: key)
    }

    /// The focus page's Refresh — the visible surface only, matching
    /// the dashboard's manual refresh.
    public func refreshFocus(repo: String, swe: SweClient) async {
        await loadFocusTab(focusTab, repo: repo, swe: swe)
    }

    /// The GitHub notifications inbox; refetches when stale or forced.
    public func loadInbox(swe: SweClient, force: Bool = false) async {
        // One read at a time, polls included: two in flight could land out
        // of order and an older list overwrite a newer one. A caller that
        // finds a read running waits for it, so it returns with fresh rows
        // (the notifier reads them right after). A forced caller then reads
        // again with its own client, unless a read begun after it asked
        // already answered.
        let askedAt = inboxReadsStarted
        while inboxReading {
            let running = inboxReadsStarted
            await withCheckedContinuation { inboxWaiters.append($0) }
            if !force || running > askedAt { return }
        }
        if !force, case .loaded = inbox,
           let at = inboxFetchedAt, now().timeIntervalSince(at) < staleAfter { return }
        inboxReading = true
        inboxReadsStarted += 1
        inboxGeneration += 1
        let mine = inboxGeneration
        defer {
            inboxReading = false
            let waiting = inboxWaiters
            inboxWaiters = []
            waiting.forEach { $0.resume() }
        }
        // First read shows the spinner; later polls keep the rows.
        if case .idle = inbox { inbox = .loading }
        let next: Loadable<[GhNotification]>
        do {
            let reply = try await swe.ghInbox()
            adopt(reply.github)
            probed = true
            next = reply.status == "ok"
                ? .loaded(reply.notifications)
                : .failed("GitHub returned \(reply.status)")
        } catch is CancellationError {
            // A cancelled first read must not strand `.loading`: the
            // top guard treats it as "request in flight" forever.
            if case .loading = inbox { inbox = .idle }
            return
        } catch {
            next = .failed(error.localizedDescription)
        }
        // Marking threads read while this ran: the reply predates it and
        // would bring the read threads back as unread.
        guard mine == inboxGeneration else { return }
        inbox = next
        if case .loaded(let notes) = next {
            inboxFetchedAt = now()
            snapshotInbox = notes
            snapshotInboxLoaded = true
        }
        scheduleSave()
    }

    /// `swe gh sync` — refresh the repo catalog the search reads. The
    /// Settings card owns this; nothing else writes the store.
    public func syncGitHub(swe: SweClient) async {
        guard !syncing else { return }
        syncing = true
        defer { syncing = false }
        do {
            let reply = try await swe.ghSync()
            syncGeneration += 1
            catalog = GhCatalog(repos: reply.repos, syncedAt: reply.syncedAt,
                                owners: reply.owners)
            syncError = reply.failed.isEmpty ? nil
                : "Skipped \(reply.failed.map(\.owner).joined(separator: ", ")) — their old repos stayed."
        } catch is CancellationError {
        } catch {
            syncError = error.localizedDescription
        }
    }

    /// Search repos to pin. A new query supersedes the one in flight.
    public func search(_ query: String, swe: SweClient) async {
        generation += 1
        let mine = generation
        let trimmed = query.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else {
            searchResults = []
            searchError = nil
            // The superseded request's defer can't clear this — `mine`
            // no longer matches `generation` — so it is cleared here.
            searching = false
            return
        }
        searching = true
        defer { if mine == generation { searching = false } }
        do {
            let reply = try await swe.ghSearchRepos(trimmed)
            if mine == generation { searchResults = reply.results; searchError = nil }
        } catch is CancellationError {
        } catch {
            if mine == generation { searchError = error.localizedDescription }
        }
    }

    /// Pin a repo. `swe gh repo add` verifies it exists first; the reply's
    /// `pinned` list is authoritative. Returns the repo's normalised
    /// `owner/name` on success so the caller can select it.
    ///
    /// Mutations are serialized (not just per-name): every reply carries
    /// the whole pin list, so two overlapping mutations could land an
    /// older list last and resurrect a removed pin.
    @discardableResult
    public func addRepo(_ input: String, swe: SweClient) async -> String? {
        let name = input.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty, mutating.isEmpty else { return nil }
        mutating.insert(name)
        defer { mutating.remove(name) }
        do {
            let reply = try await swe.ghAddRepo(name)
            pins = reply.pinned.map { PinnedRepo(name: $0, addedAt: now()) }
            pinError = nil
            return reply.repo
        } catch {
            pinError = error.localizedDescription
            return nil
        }
    }

    /// Unpin a repo; its board is dropped with it.
    public func removeRepo(_ name: String, swe: SweClient) async -> Bool {
        guard mutating.isEmpty else { return false }
        mutating.insert(name)
        defer { mutating.remove(name) }
        do {
            let reply = try await swe.ghRemoveRepo(name)
            pins = reply.pinned.map { PinnedRepo(name: $0, addedAt: now()) }
            boards[name] = nil
            boardHydrated.remove(name)
            for key in fetched.keys where key.repo == name { fetched[key] = nil }
            focusBoards[name] = nil
            for key in focusFetched.keys where key.repo == name { focusFetched[key] = nil }
            for key in focusLimits.keys where key.repo == name { focusLimits[key] = nil }
            focusExhausted = focusExhausted.filter { $0.repo != name }
            // An unpinned repo forgets its rows too — a re-pin starts
            // from a fetch, not from what it used to know.
            history[name] = nil
            legacyHistory?[name] = nil
            historyWriteQueue.async { [historyStore] in
                historyStore?.remove(repo: name)
            }
            if selected == name { selected = pins.first?.name }
            if shownRepo == name { shownRepo = nil }
            // The focus page dies with its pin — the dashboard takes over.
            if focused == name { focused = nil }
            pinError = nil
            scheduleSave()
            return true
        } catch {
            pinError = error.localizedDescription
            return false
        }
    }

    /// The newest order stashed while a reorder is still saving — the
    /// running call picks it up when the first one settles, so a drop
    /// mid-save lands instead of being discarded.
    private var queuedReorder: [String]?

    /// Persist a drag-reordered pin list. Applies the new order
    /// optimistically, then lets the CLI rewrite `github.json`; on
    /// failure the reply's authoritative list (or a reload) restores
    /// whatever the store actually holds.
    public func reorderPins(_ order: [String], swe: SweClient) async {
        if mutating.contains("<reorder>") {
            queuedReorder = order
            let lookup = Dictionary(uniqueKeysWithValues: pins.map { ($0.name, $0) })
            pins = order.compactMap { lookup[$0] }
            return
        }
        guard mutating.isEmpty else { return }
        mutating.insert("<reorder>")
        defer { mutating.remove("<reorder>") }
        var next = order
        while true {
            // The pre-apply order is this attempt's restore point — a
            // failed save puts back what the list showed before it.
            let current = pins
            let lookup = Dictionary(uniqueKeysWithValues: current.map { ($0.name, $0) })
            pins = next.compactMap { lookup[$0] }
            do {
                let reply = try await swe.ghReorderRepos(next)
                pins = reply.pinned.map { lookup[$0] ?? PinnedRepo(name: $0, addedAt: now()) }
                pinError = nil
            } catch {
                pins = current
                pinError = error.localizedDescription
            }
            scheduleSave()
            guard let queued = queuedReorder else { return }
            queuedReorder = nil
            next = queued
        }
    }

    // MARK: - PR detail + write actions

    /// A PR detail keyed `owner/name#number`, filled on the sheet's open
    /// and stale-while-revalidate on later opens — the last good read
    /// stays on screen while a fresh one lands.
    public private(set) var prDetails: [String: Loadable<GhPrDetail>] = [:]
    /// A merge, rerun or mark-read in flight — keyed so the same
    /// action on the same object can't fire twice.
    public private(set) var actionBusy: Set<String> = []
    /// The last action's failure, for surfaces (context menus) with no
    /// natural place to carry a returned error.
    public private(set) var actionError: String?

    /// The sheet's read — always fetches fresh (a PR's state moves);
    /// `.loaded` rows stay on screen while the new reply lands.
    public func loadPrDetail(repo: String, number: Int, swe: SweClient) async {
        let key = "\(repo)#\(number)"
        if case .loading = prDetails[key] { return }
        if prDetails[key] == nil { prDetails[key] = .loading }
        do {
            let reply = try await swe.ghPrView(repo: repo, ref: "\(number)")
            adopt(reply.github)
            probed = true
            prDetails[key] = reply.pr.map { .loaded($0) }
                ?? .failed(reply.error ?? "GitHub returned \(reply.status)")
        } catch is CancellationError {
            // Same rule as the inbox: a cancelled first read must not
            // strand `.loading` forever.
            if case .loading = prDetails[key] { prDetails[key] = nil }
        } catch {
            prDetails[key] = .failed(error.localizedDescription)
        }
    }

    // MARK: - Issue detail + comments

    /// An issue detail keyed `owner/name#number`, read the same way as
    /// `prDetails`: fresh on every open, the last good read on screen
    /// while the new one lands.
    public private(set) var issueDetails: [String: Loadable<GhIssueDetail>] = [:]
    /// The read in flight per issue, owned by the model so a sheet that
    /// closes mid-read can't strand the slot, and another sheet joins it.
    private var issueReads: [String: (generation: Int, task: Task<Void, Never>)] = [:]
    /// The newest read started per issue; an older reply that lands
    /// later is dropped.
    private var issueGenerations: [String: Int] = [:]

    /// Reads the issue, or waits for the read already running. `fresh`
    /// starts a new read regardless (after a write), and the one it
    /// overtakes no longer writes the slot.
    public func loadIssueDetail(repo: String, number: Int, swe: SweClient,
                                fresh: Bool = false) async {
        let key = "\(repo)#\(number)"
        if !fresh, let running = issueReads[key] {
            await running.task.value
            return
        }
        let generation = (issueGenerations[key] ?? 0) + 1
        issueGenerations[key] = generation
        if issueDetails[key] == nil { issueDetails[key] = .loading }
        let task = Task { await self.readIssue(repo: repo, number: number, key: key,
                                               generation: generation, swe: swe) }
        issueReads[key] = (generation, task)
        await task.value
        if issueReads[key]?.generation == generation { issueReads[key] = nil }
    }

    private func readIssue(repo: String, number: Int, key: String, generation: Int,
                           swe: SweClient) async {
        let next: Loadable<GhIssueDetail>
        do {
            let reply = try await swe.ghIssueView(repo: repo, number: number)
            guard issueGenerations[key] == generation else { return }
            if let github = reply.github {
                adopt(github)
                probed = true
            }
            next = reply.issue.map { .loaded($0) }
                ?? .failed(reply.error ?? "GitHub returned \(reply.status)")
        } catch {
            next = .failed(error.localizedDescription)
        }
        guard issueGenerations[key] == generation else { return }
        issueDetails[key] = next
    }

    /// Whether a comment on this issue is being posted.
    public func commenting(repo: String, number: Int) -> Bool {
        actionBusy.contains("comment:\(repo)#\(number)")
    }

    /// Post `body` as a comment through `gh` on this Mac, then read the
    /// issue again so the sheet lists it. Returns the failure, or nil.
    @discardableResult
    public func commentOnIssue(repo: String, number: Int, body: String,
                               swe: SweClient) async -> String? {
        let text = body.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        let key = "comment:\(repo)#\(number)"
        guard actionBusy.insert(key).inserted else { return "A comment is already posting." }
        defer { actionBusy.remove(key) }
        do {
            let reply = try await swe.ghIssueComment(repo: repo, number: number, body: text)
            if let github = reply.github { adopt(github) }
            if let error = reply.error { return error }
            guard reply.comment != nil else {
                return "GitHub returned \(reply.status ?? "no comment")."
            }
        } catch is CancellationError {
            return "Cancelled."
        } catch {
            return error.localizedDescription
        }
        // The issues surface's comment count moved too.
        fetched[BoardKey(repo: repo, tab: .issues)] = nil
        focusFetched[BoardKey(repo: repo, tab: .issues)] = nil
        // Fresh: a read started before the post would not list it.
        await loadIssueDetail(repo: repo, number: number, swe: swe, fresh: true)
        return nil
    }

    /// Merge a PR, or arm auto-merge — the reply's `error` is the
    /// verdict, so a blocked env reads the same as a declined merge.
    /// On success the pulls surface forgets its fetch time and the
    /// row drops out optimistically; the next poll confirms it.
    @discardableResult
    public func mergePr(repo: String, ref: String, method: String, auto: Bool,
                        swe: SweClient) async -> String? {
        let key = "merge:\(repo)#\(ref)"
        guard actionBusy.insert(key).inserted else { return nil }
        defer { actionBusy.remove(key) }
        do {
            let reply = try await swe.ghPrMerge(repo: repo, ref: ref,
                                                method: method, auto: auto)
            adopt(reply.github)
            if let error = reply.error { actionError = error; return error }
            actionError = nil
            for k in fetched.keys where k.repo == repo { fetched[k] = nil }
            for k in focusFetched.keys where k.repo == repo { focusFetched[k] = nil }
            var board = boards[repo] ?? RepoBoard()
            if reply.merged, case .loaded(let prs) = board.pulls,
               let number = Int(ref) {
                board.pulls = .loaded(prs.filter { $0.number != number })
                boards[repo] = board
                scheduleSave()
            }
            return nil
        } catch is CancellationError {
            return "Cancelled."
        } catch {
            actionError = error.localizedDescription
            return actionError
        }
    }

    /// Re-run a workflow run — all jobs or only the failed ones.
    /// Success just drops the runs surface's freshness so the next
    /// look refetches; the run row itself flips on GitHub's schedule.
    @discardableResult
    public func rerunRun(repo: String, run: Int64, failedOnly: Bool,
                         swe: SweClient) async -> String? {
        let key = "rerun:\(repo)#\(run)"
        guard actionBusy.insert(key).inserted else { return nil }
        defer { actionBusy.remove(key) }
        do {
            let reply = try await swe.ghRunRerun(repo: repo, run: run,
                                                 failedOnly: failedOnly)
            adopt(reply.github)
            if let error = reply.error { actionError = error; return error }
            actionError = nil
            fetched[BoardKey(repo: repo, tab: .actions)] = nil
            focusFetched[BoardKey(repo: repo, tab: .actions)] = nil
            return nil
        } catch is CancellationError {
            return "Cancelled."
        } catch {
            actionError = error.localizedDescription
            return actionError
        }
    }

    /// Mark notification threads read — per id, or the whole inbox.
    /// The local unread flags flip immediately; `unreadCount` (and so
    /// the badges) moves without waiting on the next poll.
    @discardableResult
    public func markInboxRead(_ ids: [String], all: Bool = false,
                              swe: SweClient) async -> String? {
        guard all || !ids.isEmpty else { return nil }
        let key = all ? "inbox:all" : "inbox:\(ids.sorted().joined(separator: ","))"
        guard actionBusy.insert(key).inserted else { return nil }
        defer { actionBusy.remove(key) }
        do {
            let reply = try await swe.ghInboxRead(ids: ids, all: all)
            adopt(reply.github)
            if let error = reply.error { actionError = error; return error }
            let missed = (reply.failed ?? []).map { "\($0.id): \($0.error)" }
            if !missed.isEmpty {
                actionError = missed.joined(separator: " · ")
                return actionError
            }
            actionError = nil
            if case .loaded(let notes) = inbox {
                let done = Set(all ? notes.map(\.id) : reply.marked)
                inbox = .loaded(notes.map {
                    done.contains($0.id)
                        ? GhNotification(id: $0.id, repo: $0.repo, title: $0.title,
                                         type: $0.type, reason: $0.reason,
                                         unread: false, updatedAt: $0.updatedAt,
                                         url: $0.url)
                        : $0
                })
                if case .loaded(let now) = inbox {
                    snapshotInbox = now
                }
                // A poll in flight answers from before this mark.
                inboxGeneration += 1
                scheduleSave()
            }
            return nil
        } catch is CancellationError {
            return "Cancelled."
        } catch {
            actionError = error.localizedDescription
            return actionError
        }
    }

    // MARK: - filter

    /// What the filter matches against: the trimmed query.
    public var query: String {
        filter.trimmingCharacters(in: .whitespaces)
    }

    /// The repos whose sections show. A query matching repo names
    /// narrows to those; one matching none is a pure row search across
    /// every pin, so a branch name still finds its board.
    public var visibleRepos: [PinnedRepo] {
        guard !query.isEmpty else { return pins }
        let named = pins.filter { $0.name.localizedCaseInsensitiveContains(query) }
        return named.isEmpty ? pins : named
    }

    /// A repo that matched the query by name shows its whole board;
    /// only repos shown by fallback filter their rows.
    public func showsAllRows(_ repo: String) -> Bool {
        query.isEmpty || repo.localizedCaseInsensitiveContains(query)
    }

    /// Whether the row's text fields contain the query — repo names,
    /// PR titles, branch names, numbers, labels, authors, tags, shas.
    public func matches(_ parts: [String?]) -> Bool {
        query.isEmpty || parts.contains { $0?.localizedCaseInsensitiveContains(query) ?? false }
    }

    /// The board's rows, filtered when this repo is shown by fallback
    /// rather than by name.
    public func filtered<Row>(_ state: Loadable<[Row]>, repo: String,
                             _ parts: @escaping (Row) -> [String?]) -> Loadable<[Row]> {
        guard case .loaded(let rows) = state, !showsAllRows(repo) else { return state }
        return .loaded(rows.filter { matches(parts($0)) })
    }

    /// Whether `filtered` would leave any rows — for callers that only
    /// need presence, like the repo card's fold control. Exits on the
    /// first match instead of building a filtered array, and treats
    /// unloaded or failed surfaces as non-empty so their state shows.
    public func hasRows<Row>(_ state: Loadable<[Row]>, repo: String,
                            _ parts: @escaping (Row) -> [String?]) -> Bool {
        guard case .loaded(let rows) = state else { return true }
        guard !showsAllRows(repo) else { return !rows.isEmpty }
        return rows.contains { matches(parts($0)) }
    }

    // MARK: - session bridge

    /// Installed harness CLIs for the "Open in session" picker —
    /// `swe list`, loaded once and archived entries dropped.
    public private(set) var sessionHarnesses: [Harness] = []
    private var harnessesLoaded = false
    /// `owner/name#N` while its PR worktree is being prepared.
    public private(set) var worktreeBusy = Set<String>()
    /// The last worktree or launch failure, shown on the page.
    public private(set) var sessionError: String?

    public func loadSessionHarnesses(swe: SweClient) async {
        guard !harnessesLoaded else { return }
        harnessesLoaded = true
        do {
            sessionHarnesses = try await swe.harnesses(withUsage: false)
                .filter { ($0.installed ?? false) && $0.state != .archived }
                .sorted { $0.title < $1.title }
        } catch {
            // An empty picker is the failure mode — the menu hides.
        }
    }

    /// `swe gh pr worktree`: fetch the PR head into the shared mirror
    /// and keep a detached checkout for it. Returns the checkout path,
    /// or nil with `sessionError` set.
    public func prWorktree(repo: String, pr: Int, swe: SweClient) async -> String? {
        let key = "\(repo)#\(pr)"
        guard !worktreeBusy.contains(key) else { return nil }
        worktreeBusy.insert(key)
        defer { worktreeBusy.remove(key) }
        do {
            let reply = try await swe.ghPrWorktree(repo: repo, pr: pr)
            if let path = reply.path {
                sessionError = nil
                return path
            }
            sessionError = reply.error ?? "swe returned no worktree path."
        } catch {
            sessionError = error.localizedDescription
        }
        return nil
    }

    // MARK: - internals

    /// Which board map a surface reply lands in — the dashboard's
    /// `boards` or the focus page's `focusBoards`.
    private enum Store { case dashboard, focus }

    /// Pull the entry for the repo out of a surface reply and write it to
    /// the board slot `path`. `.ok` loads the rows; any other status —
    /// or no entry at all — is a failure carrying swe's `error`. A loaded
    /// value also lands in SQLite through `persist`, called on the write
    /// queue so the reply never blocks on disk. `persist` must be
    /// `@Sendable`: a plain closure formed on this actor inherits
    /// `@MainActor` isolation and traps when the write queue runs it.
    private func apply<Entry: GhEntryFields, Row: Equatable & Sendable>(
        _ reply: GhResults<Entry>, to key: BoardKey,
        _ path: WritableKeyPath<RepoBoard, Loadable<Row>>,
        store: Store = .dashboard,
        persist: (@Sendable (Row) -> Void)? = nil,
        rows: (Entry) -> Row
    ) {
        adopt(reply.github)
        probed = true
        let value: Loadable<Row>
        if let entry = reply.results.first(where: { $0.repo == key.repo }) {
            value = entry.status == .ok
                ? .loaded(rows(entry))
                : .failed(entry.error ?? entry.status.rawValue)
        } else {
            value = .failed("swe returned no result for \(key.repo)")
        }
        if case .loaded(let r) = value, let persist {
            historyWriteQueue.async { persist(r) }
        }
        switch store {
        case .dashboard:
            var board = boards[key.repo] ?? RepoBoard()
            board[keyPath: path] = value
            boards[key.repo] = board
            if case .loaded = value { fetched[key] = now() }
            scheduleSave()
        case .focus:
            var board = focusBoards[key.repo] ?? RepoBoard()
            board[keyPath: path] = value
            focusBoards[key.repo] = board
            if case .loaded = value { focusFetched[key] = now() }
        }
    }

    // MARK: - warm-start snapshot

    /// Hydrate pins, selection, boards and the inbox from the last
    /// quit's file. Rows come back but fetch times do not: everything
    /// restored is cold, so the stale machinery refetches it on the
    /// first look — this only decides what the first frame shows.
    private func restore() {
        guard let snap = snapshot?.load() else { return }
        pins = snap.pins
        selected = snap.selected.flatMap { name in
            snap.pins.contains { $0.name == name } ? name : nil
        } ?? snap.pins.first?.name
        snapshotInbox = snap.inbox
        snapshotInboxLoaded = snap.inboxLoaded
        inbox = snap.inboxLoaded ? .loaded(snap.inbox) : .idle
        // Boards seed from the file first so a store that cannot take
        // them yet never blanks the warm start; the store's own copy
        // overlays per repo once it is known good. `legacyHistory`
        // holds the field until the import commits — `saveNow` keeps
        // writing it, and keeps writing boards while the store does
        // not own them, so a failed import never erases the only copy.
        boards = snap.boards
        legacyHistory = snap.history
        if let store = historyStore, store.openError == nil {
            if store.importedLegacy, store.boardsImported {
                legacyHistory = nil
                storeOwnsBoards = true
            } else if store.importAll(legacyHistory ?? [:], boards: snap.boards) {
                legacyHistory = nil
                storeOwnsBoards = true
            }
            for name in snap.pins.map(\.name) {
                var seeded = store.board(repo: name)
                // The store also keeps the focus page's every-state
                // history; the dashboard lists open work only, so a
                // closed or merged row there must not seed it as open.
                if case .loaded(let rows) = seeded.pulls {
                    seeded.pulls = .loaded(rows.filter { $0.state == .open })
                }
                if case .loaded(let rows) = seeded.issues {
                    seeded.issues = .loaded(rows.filter { $0.state == .open })
                }
                if seeded != RepoBoard() { boards[name] = seeded }
            }
        } else if let legacy = legacyHistory {
            // No usable database at all — the rows still cover this session.
            history.merge(legacy) { _, old in old }
        }
        // A snapshot exists only because replies were received before.
        probed = true
    }

    /// Coalesce bursts — a tab switch can fill several surfaces in one
    /// second — into one atomic write.
    private func scheduleSave() {
        guard snapshot != nil else { return }
        snapshotTask?.cancel()
        snapshotTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 400_000_000)
            guard !Task.isCancelled else { return }
            self?.saveNow()
        }
    }

    /// Write the current state to disk, synchronously. Called from the
    /// debounce and from `applicationWillTerminate`.
    public func saveNow() {
        snapshot?.saveNow(GitHubSnapshot(
            savedAt: now(), pins: pins, selected: selected,
            // The file keeps carrying boards until the store owns
            // them — open plus a confirmed import — so a bad database
            // never costs the last copy.
            boards: storeOwnsBoards ? [:] : boards,
            inbox: snapshotInbox,
            inboxLoaded: snapshotInboxLoaded, history: legacyHistory))
    }
}

/// What every per-repo results entry shares; lets `apply` treat the
/// seven entry types uniformly.
public protocol GhEntryFields {
    var repo: String { get }
    var status: GhEntryStatus { get }
    var error: String? { get }
}
extension GhPrEntry: GhEntryFields {}
extension GhIssueEntry: GhEntryFields {}
extension GhRunEntry: GhEntryFields {}
extension GhPagesEntry: GhEntryFields {}
extension GhReleaseEntry: GhEntryFields {}
extension GhTagEntry: GhEntryFields {}
extension GhBranchEntry: GhEntryFields {}
extension GhPrStatusEntry: GhEntryFields {}
