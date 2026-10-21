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
        func loaded<T>(_ l: Loadable<T>) -> Bool {
            if case .loaded = l { return true }
            return false
        }
        return switch tab {
        case .pulls: loaded(pulls)
        case .issues: loaded(issues)
        case .actions: loaded(runs)
        case .pages: loaded(pages)
        case .releases: loaded(releases)
        case .tags: loaded(tags)
        case .branches: loaded(branches)
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
    /// The page's filter text: repo names, PR titles, branch names and
    /// the rest of the row text. Lives on the model so a banner's
    /// click-through can clear it and land on its repo's section.
    public var filter = ""

    public private(set) var boards: [String: RepoBoard] = [:]
    /// Which (repo, tab) is mid-fetch, so rows keep their last data
    /// while a spinner shows on the tab.
    public private(set) var loading: Set<BoardKey> = []
    /// A second request while one was already running — its reply
    /// predates the second call, so the surface refetches after it lands
    /// rather than dropping the refresh.
    private var queuedRefreshes: Set<BoardKey> = []

    public private(set) var inbox: Loadable<[GhNotification]> = .idle
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
                snapshotFile: URL? = nil) {
        self.now = now
        self.staleAfter = staleAfter
        snapshot = snapshotFile.map { SnapshotFile(url: $0) }
        restore()
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
        do {
            let overview = try await swe.ghOverview()
            env = overview.github
            probed = true
            probeError = nil
            pinsFetchedAt = now()
            pins = overview.repos.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
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

    /// Load whatever the page is looking at unless it was fetched recently.
    public func loadIfStale(swe: SweClient) async {
        // `probed` alone is not enough: the inbox can set it while the
        // pin list is still empty, and a failed overview must retry.
        if pinsFetchedAt == nil { await load(swe: swe) }
        guard let repo = selected else { return }
        await loadTabIfStale(tab, repo: repo, swe: swe)
    }

    /// `loadIfStale` for one repo: the page shows several boards at
    /// once, each with its own clock. First look this session hydrates
    /// every surface in one `swe gh board` call; a surface the board
    /// missed or failed falls through to its own per-tab fetch.
    public func loadTabIfStale(_ tab: RepoTab, repo: String, swe: SweClient) async {
        if !boardHydrated.contains(repo) { await loadBoard(repo, swe: swe) }
        let key = BoardKey(repo: repo, tab: tab)
        // The timestamp only skips a fetch when rows are actually there —
        // a cleared board (re-pinned repo) is never "fresh".
        if let at = fetched[key], now().timeIntervalSince(at) < staleAfter,
           board(for: repo).isLoaded(key.tab) { return }
        await loadTab(tab, repo: repo, swe: swe)
    }

    /// A new notification names a repo whose rows may have moved: drop
    /// its fetch times so the next look refetches, and refresh it now
    /// when it is the repo on screen.
    public func invalidate(repo: String, swe: SweClient) {
        for key in fetched.keys where key.repo == repo { fetched[key] = nil }
        if selected == repo {
            Task { await loadTab(tab, repo: repo, swe: swe) }
        }
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
            await loadBoard(repo, swe: swe)
            if board(for: repo).isLoaded(tab) { return }
        }
        await loadTab(tab, repo: repo, swe: swe)
    }

    /// Fetch every surface of one repo in a single `swe gh board` call.
    /// Runs once per repo per launch — `boardHydrated` is the attempt
    /// flag, so a failed board degrades to per-tab fetches rather than
    /// retrying the expensive call on every visit.
    private func loadBoard(_ repo: String, swe: SweClient) async {
        boardHydrated.insert(repo)
        let keys = RepoTab.allCases.map { BoardKey(repo: repo, tab: $0) }
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
            let reply = try await swe.ghBoard(repo: repo)
            env = reply.github
            probed = true
            guard let payload = reply.boards.first(where: { $0.repo == repo })
            else { return }
            var board = boards[repo] ?? RepoBoard()
            let stamp = now()
            let s = payload.surfaces
            fill(s.pullRequests, into: &board, \.pulls, repo: repo, tab: .pulls, stamp: stamp) { $0.pullRequests ?? [] }
            fill(s.issues, into: &board, \.issues, repo: repo, tab: .issues, stamp: stamp) { $0.issues ?? [] }
            fill(s.runs, into: &board, \.runs, repo: repo, tab: .actions, stamp: stamp) { $0.runs ?? [] }
            fill(s.pages, into: &board, \.pages, repo: repo, tab: .pages, stamp: stamp) { $0.pages }
            fill(s.releases, into: &board, \.releases, repo: repo, tab: .releases, stamp: stamp) { $0.releases ?? [] }
            fill(s.tags, into: &board, \.tags, repo: repo, tab: .tags, stamp: stamp) { $0.tags ?? [] }
            fill(s.branches, into: &board, \.branches, repo: repo, tab: .branches, stamp: stamp) { $0.branches ?? [] }
            boards[repo] = board
            scheduleSave()
        } catch is CancellationError {
        } catch {
            var board = boards[repo] ?? RepoBoard()
            board.fail(tab, message: error.localizedDescription)
            boards[repo] = board
        }
    }

    /// Write one board surface's entry into `path`: ok loads the rows and
    /// the fetched stamp, anything else is the entry's typed failure.
    private func fill<Entry: GhEntryFields, Row: Equatable & Sendable>(
        _ entry: Entry?, into board: inout RepoBoard,
        _ path: WritableKeyPath<RepoBoard, Loadable<Row>>,
        repo: String, tab: RepoTab, stamp: Date,
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
        if case .loaded = value { fetched[BoardKey(repo: repo, tab: tab)] = stamp }
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
                apply(try await swe.ghPullRequests(repo: repo), to: key, \.pulls) { $0.pullRequests ?? [] }
            case .issues:
                apply(try await swe.ghIssues(repo: repo), to: key, \.issues) { $0.issues ?? [] }
            case .actions:
                apply(try await swe.ghRuns(repo: repo), to: key, \.runs) { $0.runs ?? [] }
            case .pages:
                apply(try await swe.ghPages(repo: repo), to: key, \.pages) { $0.pages }
            case .releases:
                apply(try await swe.ghReleases(repo: repo), to: key, \.releases) { $0.releases ?? [] }
            case .tags:
                apply(try await swe.ghTags(repo: repo), to: key, \.tags) { $0.tags ?? [] }
            case .branches:
                apply(try await swe.ghBranches(repo: repo), to: key, \.branches) { $0.branches ?? [] }
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

    /// The GitHub notifications inbox; refetches when stale or forced.
    public func loadInbox(swe: SweClient, force: Bool = false) async {
        if case .loading = inbox { return }
        if !force, case .loaded = inbox,
           let at = inboxFetchedAt, now().timeIntervalSince(at) < staleAfter { return }
        // First read shows the spinner; later polls keep the rows.
        if case .idle = inbox { inbox = .loading }
        let next: Loadable<[GhNotification]>
        do {
            let reply = try await swe.ghInbox()
            env = reply.github
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
        inbox = next
        if case .loaded(let notes) = next {
            inboxFetchedAt = now()
            snapshotInbox = notes
            snapshotInboxLoaded = true
        }
        scheduleSave()
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
            pins = reply.pinned.sorted().map { PinnedRepo(name: $0, addedAt: now()) }
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
            pins = reply.pinned.sorted().map { PinnedRepo(name: $0, addedAt: now()) }
            boards[name] = nil
            boardHydrated.remove(name)
            for key in fetched.keys where key.repo == name { fetched[key] = nil }
            if selected == name { selected = pins.first?.name }
            pinError = nil
            scheduleSave()
            return true
        } catch {
            pinError = error.localizedDescription
            return false
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

    // MARK: - internals

    /// Pull the entry for the repo out of a surface reply and write it to
    /// the board slot `path`. `.ok` loads the rows; any other status —
    /// or no entry at all — is a failure carrying swe's `error`.
    private func apply<Entry: GhEntryFields, Row: Equatable & Sendable>(
        _ reply: GhResults<Entry>, to key: BoardKey,
        _ path: WritableKeyPath<RepoBoard, Loadable<Row>>,
        rows: (Entry) -> Row
    ) {
        env = reply.github
        probed = true
        let value: Loadable<Row>
        if let entry = reply.results.first(where: { $0.repo == key.repo }) {
            value = entry.status == .ok
                ? .loaded(rows(entry))
                : .failed(entry.error ?? entry.status.rawValue)
        } else {
            value = .failed("swe returned no result for \(key.repo)")
        }
        var board = boards[key.repo] ?? RepoBoard()
        board[keyPath: path] = value
        boards[key.repo] = board
        if case .loaded = value { fetched[key] = now() }
        scheduleSave()
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
        boards = snap.boards
        snapshotInbox = snap.inbox
        snapshotInboxLoaded = snap.inboxLoaded
        inbox = snap.inboxLoaded ? .loaded(snap.inbox) : .idle
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
            boards: boards, inbox: snapshotInbox,
            inboxLoaded: snapshotInboxLoaded))
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
