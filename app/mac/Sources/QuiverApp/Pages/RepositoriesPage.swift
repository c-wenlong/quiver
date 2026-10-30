import AppKit
import QuiverCore
import SwiftUI

/// The GitHub dashboard: one list of every pinned repo's rows, grouped
/// under a plain header per repo, beside a rail of the repos by owner.
/// The rail narrows the list to one repo (which unlocks Pages,
/// releases, tags and branches), folds repos with nothing to show, and
/// is where a drag reorders the pins; the list follows its order. The
/// filter narrows by repo name (a name-matched repo shows its whole
/// board) and by row text, state words included. Rows link
/// straight to GitHub. Every visible repo's tab refetches on a minute
/// timer while the page is open.
struct RepositoriesPage: View {
    @Environment(AppModel.self) private var app
    @State private var adding = false
    /// The PR whose detail sheet is open, if any.
    @State private var detail: PrSelection?
    /// The issue whose detail or resolve sheet is open, if any.
    @State private var issue: IssueSelection?

    private var model: GitHubModel { app.github }

    var body: some View {
        @Bindable var model = model
        if let repo = model.focused {
            RepoDetailPage(repo: repo) { model.focused = nil }
        } else {
            dashboard
        }
    }

    /// The page's own frame rather than `Page`: the rail runs the full
    /// height beside the title, and the header buttons live in the
    /// rail (gear, Refresh, Add repository) and on the tab row (search).
    /// Same padding and width cap as `Page`.
    private var dashboard: some View {
        HStack(alignment: .top, spacing: 24) {
            VStack(alignment: .leading, spacing: 24) {
                PageHeader(title: "Repositories",
                           subtitle: "Your pinned repos' pull requests, issues and CI, from swe gh.") {
                    EmptyView()
                }
                content
                    .textSelection(.enabled)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if showsRail {
                RepoRail(add: { adding = true }, refresh: refresh)
                    .frame(width: 240)
            }
        }
        .frame(maxHeight: .infinity, alignment: .top)
        .padding(.horizontal, 40)
        .padding(.vertical, 36)
        .frame(maxWidth: 1280, alignment: .leading)
        .frame(maxWidth: .infinity)
        .task(id: Visible(tab: model.tab, swe: app.swePath,
                         filter: model.filter,
                         repos: Set(model.pins.map(\.name)))) {
            // Pins first, then one board call hydrates every visible
            // repo's surfaces at once — the CLI fans the list out itself.
            await model.loadPinsIfNeeded(swe: app.swe)
            await model.loadSessionHarnesses(swe: app.swe)
            await model.loadBoards(visibleRepos.map(\.name), swe: app.swe)
            // What the board missed or failed falls back to per-tab
            // fetches — overlapped, since each await releases the actor.
            let model = model
            let swe = app.swe
            await withTaskGroup(of: Void.self) { group in
                for repo in visibleRepos {
                    group.addTask {
                        await model.loadTabIfStale(model.tab, repo: repo.name,
                                                   swe: swe)
                    }
                    // The Review requested filter's flags ride along
                    // with the pull requests they filter.
                    if model.tab == .pulls {
                        group.addTask {
                            await model.loadReviewRequests(repo: repo.name, swe: swe)
                        }
                    }
                }
            }
            // Poll while the page is open so merged PRs and finished CI
            // land without a manual refresh. The runs ride along on
            // every tab: the rail's CI dots read them.
            let tabs: [RepoTab] = model.tab == .actions ? [.actions] : [model.tab, .actions]
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(60))
                await withTaskGroup(of: Void.self) { group in
                    for repo in visibleRepos {
                        for tab in tabs {
                            group.addTask {
                                await model.loadTab(tab, repo: repo.name, swe: swe)
                            }
                        }
                        if model.tab == .pulls {
                            group.addTask {
                                await model.loadReviewRequests(repo: repo.name, swe: swe)
                            }
                        }
                    }
                }
            }
        }
        .quiverSheet(isPresented: $adding) {
            AddRepoSheet()
        }
        .quiverSheet(item: $detail) { selection in
            PrDetailSheet(selection: selection)
        }
        .quiverSheet(item: $issue) { selection in
            IssueSheet(selection: selection) { issue = $0 }
        }
    }

    /// The rail shows once there are pins to list.
    private var showsRail: Bool {
        model.envSetup == nil && model.envBlocked == nil && model.probed && !model.pins.isEmpty
    }

    /// Refresh (⌘R, in the rail): every visible repo's tab, plus its
    /// runs for the rail's CI dot.
    private func refresh() {
        Task {
            // A board call covers repos not yet hydrated; the per-repo
            // refreshes overlap after it.
            await model.loadBoards(visibleRepos.map(\.name), swe: app.swe)
            let model = model
            let swe = app.swe
            await withTaskGroup(of: Void.self) { group in
                for repo in visibleRepos {
                    group.addTask {
                        await model.refresh(repo: repo.name, swe: swe)
                    }
                    if model.tab != .actions {
                        group.addTask {
                            await model.loadTab(.actions, repo: repo.name, swe: swe)
                        }
                    }
                    if model.tab == .pulls {
                        group.addTask {
                            await model.loadReviewRequests(repo: repo.name, swe: swe, force: true)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder private var content: some View {
        @Bindable var model = model
        if let setup = model.envSetup {
            GhSetupCard(status: setup) {
                Task { await model.refresh(swe: app.swe) }
            }
        } else if let blocked = model.envBlocked {
            ErrorCard(message: blocked, actionTitle: "Refresh",
                      actionShortcut: KeyboardShortcut("r", modifiers: .command)) {
                Task { await model.refresh(swe: app.swe) }
            }
        } else if !model.probed {
            LoadingCard(text: "Checking gh and the pinned repos…")
        } else if model.pins.isEmpty {
            EmptyCard(text: "Pin a repository to see its pull requests, issues and CI here.",
                      icon: "shippingbox", actionTitle: "Add repository") { adding = true }
        } else {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .bottom, spacing: 12) {
                    // Tabs expands for the full-width underline; the
                    // search rides the right end of the same row. It
                    // matches repo names, row text and state words
                    // ("failing", "draft", "conflict", "bot").
                    Tabs(options: GitHubModel.tabs(oneRepo: model.shownRepo != nil)
                            .map { ($0, $0.title) },
                         selection: $model.tab, style: .underline)
                        .layoutPriority(1)
                    SearchField(prompt: "Search", text: $model.filter,
                                focusShortcut: KeyboardShortcut("k", modifiers: .command))
                        .frame(minWidth: 120, maxWidth: 260)
                        .help("Search titles, branches, authors and states like failing or draft (⌘K)")
                        .padding(.bottom, 4)
                }
                if model.tab == .pulls, !model.pullFilters.isEmpty {
                    PullFilterPicker(options: model.pullFilters,
                                     count: { model.pullCount($0) },
                                     selection: Binding(get: { model.activePullFilter },
                                                        set: { model.pullFilter = $0 }))
                }
                if let error = model.pinError ?? model.actionError {
                    ErrorCard(message: error)
                }
                if let error = model.sessionError {
                    ErrorCard(message: error)
                }
                ScrollViewReader { proxy in
                    ScrollView {
                        list.padding(.bottom, 24)
                    }
                    .scrollContentBackground(.hidden)
                    .onChange(of: model.scrollTarget) { _, target in
                        // Banner click-through: the rail already
                        // narrowed to the repo; bring it up.
                        guard let target else { return }
                        model.scrollTarget = nil
                        withAnimation { proxy.scrollTo(target, anchor: .top) }
                    }
                }
            }
            .frame(maxHeight: .infinity, alignment: .top)
        }
    }

    /// Identity for the visible surface: switching tab, swe, the
    /// filter or the pin list (add/remove) re-runs the load task and
    /// its poll loop. The pins are a set: a drag reorder changes only
    /// their order, and re-running on it refetched every repo once the
    /// boards were a minute old, spinning beside each name mid-drop.
    /// The rail's pick is left out too: every visible repo stays loaded
    /// so the rail's counts hold while the list shows one.
    private struct Visible: Hashable {
        var tab: RepoTab
        var swe: String
        var filter: String
        var repos: Set<String>
    }

    /// The repos the page loads: every one the filter leaves, so the
    /// rail can count them.
    private var visibleRepos: [PinnedRepo] { model.visibleRepos }

    /// One box of rows: a plain header per repo, its rows under it.
    /// Repos with nothing on this tab are left out (the rail folds them
    /// as quiet); one still loading or failed keeps its header so the
    /// state shows.
    @ViewBuilder private var list: some View {
        let repos = model.listedRepos.filter { model.rowCount($0.name, tab: model.tab) != 0 }
        if repos.isEmpty {
            EmptyCard(text: emptyText, icon: "checkmark.circle")
        } else {
            VStack(spacing: 0) {
                ForEach(Array(repos.enumerated()), id: \.element.id) { index, pin in
                    if index > 0 { Divider().overlay(Theme.border) }
                    RepoGroupHeader(repo: pin.name, isFirst: index == 0,
                                    loading: model.loading.contains(.init(repo: pin.name, tab: model.tab)))
                        .id(pin.name)
                    Divider().overlay(Theme.rowDivider)
                    RepoSurfaces(repo: pin.name, board: model.board(for: pin.name),
                                 tab: model.tab, query: model.query,
                                 unfiltered: model.showsAllRows(pin.name),
                                 keepsPull: { model.keepsPull($0, repo: pin.name) },
                                 preview: 5,
                                 expandEdge: index == repos.count - 1 ? .cardBottom : .inset,
                                 openPr: { detail = PrSelection(repo: pin.name, number: $0) },
                                 openIssue: { issue = $0 })
                }
            }
            .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
            .clipShape(.rect(cornerRadius: Theme.cardRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius)
                .strokeBorder(Theme.border, lineWidth: Theme.hairline))
        }
    }

    private var emptyText: String {
        if !model.query.isEmpty {
            return "Nothing matches \"\(model.query)\"."
        }
        let place = model.shownRepo ?? "your pinned repos"
        return switch model.tab {
        case .pulls: model.activePullFilter == .all
            ? "No open pull requests in \(place)."
            : "No open pull requests in \(place) match \(model.activePullFilter.title)."
        case .issues: "No open issues in \(place)."
        case .actions: "No workflow runs in \(place)."
        case .pages: "\(place) has no GitHub Pages site."
        case .releases: "No releases in \(place)."
        case .tags: "No tags in \(place)."
        case .branches: "No branches in \(place)."
        }
    }
}

/// A repo's header row inside the list: `owner /` muted, the name, a
/// spinner while its tab fetches, and on hover the links out.
private struct RepoGroupHeader: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    var repo: String
    /// The top header takes the box's rounded top corners; the rest are
    /// plain strips across it.
    var isFirst: Bool
    var loading: Bool
    @State private var hovering = false

    var body: some View {
        let parts = repo.split(separator: "/", maxSplits: 1).map(String.init)
        HStack(spacing: 6) {
            if parts.count == 2 {
                Text("\(parts[0]) /").foregroundStyle(Theme.textMuted)
                Text(parts[1]).foregroundStyle(Theme.textPrimary)
            } else {
                Text(repo).foregroundStyle(Theme.textPrimary)
            }
            if loading { ProgressView().controlSize(.mini) }
            Spacer()
            Group {
                Button {
                    if let url = URL(string: "https://github.com/\(repo)") { openURL(url) }
                } label: {
                    Image(systemName: "arrow.up.right.square")
                }
                .plainButton()
                .help("Open on GitHub")
                Button { app.github.focused = repo } label: {
                    Image(systemName: "arrow.up.left.and.arrow.down.right")
                }
                .plainButton()
                .help("Open \(repo) in full")
            }
            .font(Theme.font(size: 12)).foregroundStyle(Theme.textMuted)
            .opacity(hovering ? 1 : 0)
        }
        .font(Theme.control)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(Theme.subtle, in: isFirst
            ? AnyShape(UnevenRoundedRectangle(topLeadingRadius: Theme.cardRadius,
                                              topTrailingRadius: Theme.cardRadius))
            : AnyShape(Rectangle()))
        .onHover { hovering = $0 }
    }
}

// MARK: - Repo rail

/// The page's right-hand rail, full height: the filter with the
/// manage gear and Refresh, "All repos", the pins by owner, then "Add
/// repository". Each repo row carries a CI dot (the default branch's
/// newest run) and the tab's row count; repos with nothing on the tab
/// fold under "N quiet". A tap narrows the list to that repo; a drag
/// reorders the pins within the row's group.
private struct RepoRail: View {
    @Environment(AppModel.self) private var app
    var add: () -> Void
    var refresh: () -> Void
    /// Owners whose quiet repos are unfolded.
    @State private var openQuiet: Set<String> = []
    @State private var managing = false

    private var model: GitHubModel { app.github }

    private struct Group: Identifiable {
        var owner: String
        var active: [PinnedRepo]
        var quiet: [PinnedRepo]
        var id: String { owner }
    }

    /// Owners in order of their first pin; repos in pin order.
    private var groups: [Group] {
        var order: [String] = []
        var byOwner: [String: [PinnedRepo]] = [:]
        for pin in model.visibleRepos {
            let owner = String(pin.name.split(separator: "/", maxSplits: 1).first ?? "")
            if byOwner[owner] == nil { order.append(owner) }
            byOwner[owner, default: []].append(pin)
        }
        return order.map { owner in
            let pins = byOwner[owner] ?? []
            return Group(owner: owner,
                         active: pins.filter { model.rowCount($0.name, tab: model.tab) != 0 },
                         quiet: pins.filter { model.rowCount($0.name, tab: model.tab) == 0 })
        }
    }

    private var total: Int {
        model.visibleRepos.reduce(0) { $0 + (model.rowCount($1.name, tab: model.tab) ?? 0) }
    }

    var body: some View {
        @Bindable var model = model
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 4) {
                Text("Pinned repos")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .padding(.leading, 8)
                Spacer(minLength: 0)
                RailIconButton(symbol: "gearshape", help: "Manage pinned repositories") {
                    managing = true
                }
                .popover(isPresented: $managing, arrowEdge: .bottom) {
                    RepoManager {
                        managing = false
                        // The sheet attaches once the popover is gone.
                        DispatchQueue.main.async { add() }
                    }
                }
                RailIconButton(symbol: "arrow.clockwise",
                               help: "Fetch the visible tab again (⌘R)", action: refresh)
                    .keyboardShortcut("r", modifiers: .command)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    RailRow(selected: model.shownRepo == nil,
                            select: { model.shownRepo = nil }) {
                        Image(systemName: "square.stack.3d.up")
                            .font(Theme.font(size: 12))
                        Text("All repos").font(Theme.nav)
                        Spacer(minLength: 6)
                        Text("\(total)").font(Theme.caption.monospacedDigit())
                            .foregroundStyle(Theme.textMuted)
                    }
                    ForEach(groups) { group in
                        Text(group.owner)
                            .font(Theme.caption).foregroundStyle(Theme.textMuted)
                            .lineLimit(1).truncationMode(.tail)
                            .padding(.horizontal, 8)
                            .padding(.top, 14).padding(.bottom, 4)
                        rows(group.active)
                        if !group.quiet.isEmpty {
                            let open = openQuiet.contains(group.owner)
                            Button {
                                if open { openQuiet.remove(group.owner) } else { openQuiet.insert(group.owner) }
                            } label: {
                                HStack(spacing: 6) {
                                    Image(systemName: "chevron.right")
                                        .font(Theme.font(size: 9, weight: .semibold))
                                        .rotationEffect(open ? .degrees(90) : .zero)
                                    Text("\(group.quiet.count) quiet").font(Theme.nav)
                                    Spacer()
                                }
                                .foregroundStyle(Theme.textMuted)
                                .padding(.horizontal, 8).padding(.vertical, 5)
                                .contentShape(Rectangle())
                            }
                            .plainButton()
                            .help(open ? "Hide repos with nothing on this tab"
                                       : "Show repos with nothing on this tab")
                            if open { rows(group.quiet) }
                        }
                    }
                    // A real Button: outside the drag rows, so it can
                    // take keyboard focus.
                    Button(action: add) {
                        HStack(spacing: 8) {
                            Image(systemName: "plus").font(Theme.font(size: 12))
                            Text("Add repository").font(Theme.nav)
                            Spacer(minLength: 0)
                        }
                        .foregroundStyle(Theme.textMuted)
                        .padding(.horizontal, 8)
                        .frame(height: Theme.navRowHeight)
                        .contentShape(.rect)
                    }
                    .plainButton(cornerRadius: Theme.controlRadius)
                    .padding(.top, 14)
                }
            }
            .scrollContentBackground(.hidden)
        }
        .padding(8)
        .background(Theme.panel, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius)
            .strokeBorder(Theme.border, lineWidth: Theme.hairline))
    }

    /// One reorderable run of repo rows. A drop moves the repo within
    /// this run only; every other pin keeps its slot.
    private func rows(_ pins: [PinnedRepo]) -> some View {
        ReorderableRows(pins) { id, before in
            let order = PinOrder.move(id, before: before, in: pins.map(\.name),
                                      pins: model.pins.map(\.name))
            Task { await model.reorderPins(order, swe: app.swe) }
        } row: { pin in
            RailRow(selected: model.shownRepo == pin.name,
                    select: { model.shownRepo = pin.name }) {
                Circle().fill(dotColor(model.ciState(pin.name)))
                    .frame(width: Theme.statusDot, height: Theme.statusDot)
                Text(pin.name.split(separator: "/").last.map(String.init) ?? pin.name)
                    .font(Theme.nav)
                    .lineLimit(1).truncationMode(.middle)
                Spacer(minLength: 6)
                if model.loading.contains(.init(repo: pin.name, tab: model.tab)) {
                    ProgressView().controlSize(.mini)
                } else if let count = model.rowCount(pin.name, tab: model.tab), count > 0 {
                    Text("\(count)").font(Theme.caption.monospacedDigit())
                        .foregroundStyle(Theme.textMuted)
                }
            }
            .accessibilityHint("Drag to reorder")
        }
    }

    private func dotColor(_ state: GhPr.Checks?) -> Color {
        switch state {
        case .failing: Theme.danger
        case .passing: Theme.success
        case .running: Theme.warning
        default: Theme.borderStrong
        }
    }
}

// MARK: - Pull request filter

/// All / Mine / Review requested above a pull request list. Only the
/// filters the loaded flags can answer show; the caller hides the
/// picker when none can.
struct PullFilterPicker: View {
    var options: [PullFilter]
    /// The rows each filter would list, shown after its title.
    var count: (PullFilter) -> Int
    @Binding var selection: PullFilter

    var body: some View {
        Tabs(options: options.map { ($0, "\($0.title) \(count($0))") }, selection: $selection)
            .hoverTip("Mine is pull requests you opened; Review requested is open ones waiting on your review, from gh pr status")
    }
}

// MARK: - Repo surfaces

/// One repo's tab surfaces — pull requests, issues, workflow runs,
/// Pages, releases, tags and branches — shared verbatim between the
/// dashboard's repo callouts and the single-repo focus page. What
/// differs is the board they read (the dashboard's open-state store vs
/// the focus page's every-state one) and the row filter.
struct RepoSurfaces: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    var repo: String
    var board: RepoBoard
    var tab: RepoTab
    /// The row-filter text; empty keeps every row.
    var query = ""
    /// Skip row filtering — a dashboard section the query named keeps
    /// its whole board.
    var unfiltered = false
    /// The Mine / Review requested filter on pull requests; applies
    /// even where the search is skipped.
    var keepsPull: (GhPr) -> Bool = { _ in true }
    /// The focus page's scroll loading: lists reveal `batch` rows at a
    /// time and the end sentinel grows the fetch window. Off here, the
    /// dashboard's 3-row collapse preview stays.
    var scrollFetch = false
    /// Rows a list shows before its "Show N more" row.
    var preview = 3
    /// Where that row sits: the bottom of a card, or mid-list.
    var expandEdge: ExpandRow.Edge = .cardBottom
    /// A PR row tap opens the page's detail sheet.
    var openPr: (Int) -> Void
    /// An issue row opens its detail sheet; its Resolve button and menu
    /// item open the resolve sheet.
    var openIssue: (IssueSelection) -> Void = { _ in }
    /// "repo:surface" keys opened past their 3-row preview — this
    /// view's own set, so expanding one repo never unfolds another's.
    @State private var expanded: Set<String> = []
    /// Rows revealed per surface under `scrollFetch`, keyed "repo:tab"
    /// so switching tabs (or repos) never hands one list another's
    /// position.
    @State private var shown: [String: Int] = [:]

    private var model: GitHubModel { app.github }

    /// Rows each scroll-load fill reveals — the first screenful, then
    /// every completed ring.
    private static let batch = 10

    /// The tab's list window: `nil` keeps the callout's collapse
    /// preview; under `scrollFetch` the sentinel reveals `batch` more
    /// while rows remain, then asks the model for the next page.
    private func window(for tab: RepoTab, rows: Int) -> ListWindow? {
        guard scrollFetch, tab != .pages else { return nil }
        let key = GitHubModel.BoardKey(repo: repo, tab: tab)
        return ListWindow(
            shown: shown["\(repo):\(tab)"] ?? Self.batch,
            fetching: model.focusLoading.contains(key),
            canGrow: model.focusCanGrow(key),
            advance: { advance(tab, filtered: rows) })
    }

    /// One filled ring: show another `batch` of the rows already
    /// fetched, or — at the end of them — fetch the next page.
    private func advance(_ tab: RepoTab, filtered rows: Int) {
        let key = "\(repo):\(tab)"
        let now = shown[key] ?? Self.batch
        if now < rows {
            shown[key] = now + Self.batch
        } else {
            Task { await model.loadMoreFocus(tab, repo: repo, swe: app.swe) }
        }
    }

    /// A loaded surface's row count for the window's reveal math;
    /// anything not `.loaded` reports 0 and the sentinel stays hidden.
    private func count<Row>(_ state: Loadable<[Row]>) -> Int {
        if case .loaded(let rows) = state { return rows.count }
        return 0
    }

    var body: some View {
        switch tab {
        case .pulls: pulls()
        case .issues: issues()
        case .actions: actions()
        case .pages: pages()
        case .releases: releases()
        case .tags: tags()
        case .branches: branches()
        }
    }

    /// The rows, filtered when `query` is on and the surface is not
    /// exempt — a match on any of the row's fields keeps it. An empty
    /// query exempts too: `contains("")` is false on this Foundation,
    /// so filtering with no query would drop every row.
    static func filtered<Row>(_ state: Loadable<[Row]>,
                              _ parts: @escaping (Row) -> [String?],
                              query: String, unfiltered: Bool) -> Loadable<[Row]> {
        guard case .loaded(let rows) = state, !unfiltered, !query.isEmpty
        else { return state }
        return .loaded(rows.filter { row in
            parts(row).contains { $0?.localizedCaseInsensitiveContains(query) ?? false }
        })
    }

    private func filtered<Row>(_ state: Loadable<[Row]>,
                               _ parts: @escaping (Row) -> [String?]) -> Loadable<[Row]> {
        Self.filtered(state, parts, query: query, unfiltered: unfiltered)
    }

    /// Under a row filter an empty list means "nothing matched", not
    /// "nothing exists" — an unfiltered surface keeps the base text.
    private func emptyText(_ base: String) -> String {
        unfiltered || query.isEmpty ? base : "Nothing matches \"\(query)\"."
    }

    /// What the row filter searches per surface — shared by the list's
    /// filtered state and the callout's `surfaceEmpty`, so they can
    /// never disagree.
    static let prParts: (GhPr) -> [String?] = \.searchText
    static let issueParts: (GhIssue) -> [String?] = \.searchText
    static let runParts: (GhRun) -> [String?] = \.searchText
    static let releaseParts: (GhRelease) -> [String?] = \.searchText
    static let tagParts: (GhTag) -> [String?] = \.searchText
    static let branchParts: (GhBranch) -> [String?] = \.searchText

    /// The tab's rows after the search filter.
    private func pullsState() -> Loadable<[GhPr]> {
        var state = board.pulls
        if case .loaded(let rows) = state { state = .loaded(rows.filter(keepsPull)) }
        return filtered(state, Self.prParts)
    }

    @ViewBuilder
    private func pulls() -> some View {
        let state = pullsState()
        SurfaceList(state,
                    empty: emptyText("No pull requests."),
                    expandKey: "\(repo):pulls", expanded: $expanded,
                    preview: preview, edge: expandEdge,
                    window: window(for: .pulls, rows: count(state))) { pr in
            // Centred: the state icons sit mid-row; the PR icon stays
            // on the title line.
            OpenRow(alignment: .center, act: { openPr(pr.number) }) {
                HStack(alignment: .top, spacing: 12) {
                    PrIcon(state: pr.state, draft: pr.draft)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 8) {
                            Text(pr.title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                                .lineLimit(1).truncationMode(.tail)
                            ForEach(pr.labels.prefix(3), id: \.self) { Badge($0) }
                        }
                        Text("#\(pr.number) · \(pr.author) · \(pr.branch) → \(pr.base)"
                             + (pr.updatedAt.date.map { " · \(UsageFormat.ago($0, now: .now))" } ?? ""))
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .lineLimit(1).truncationMode(.middle)
                    }
                }
                Spacer(minLength: 12)
                PrStateIcons(pr: pr)
                if model.worktreeBusy.contains("\(repo)#\(pr.number)") {
                    ProgressView().controlSize(.small)
                }
            }
            .contextMenu {
                if !model.sessionHarnesses.isEmpty {
                    Menu {
                        ForEach(model.sessionHarnesses) { harness in
                            Button(harness.title) {
                                Task {
                                    await app.openPrSession(repo: repo,
                                                            number: pr.number,
                                                            harness: harness)
                                }
                            }
                        }
                    } label: {
                        Label("Open in session", systemImage: "terminal")
                    }
                    Divider()
                }
                Button("Open on GitHub") {
                    if let url = URL(string: pr.url), url.isWebURL { openURL(url) }
                }
            }
        }
    }

    private func issuesState() -> Loadable<[GhIssue]> {
        filtered(board.issues, Self.issueParts)
    }

    @ViewBuilder
    private func issues() -> some View {
        let state = issuesState()
        SurfaceList(state,
                    empty: emptyText("No issues."),
                    expandKey: "\(repo):issues", expanded: $expanded,
                    preview: preview, edge: expandEdge,
                    window: window(for: .issues, rows: count(state))) { issue in
            IssueRow(open: { openIssue(IssueSelection(repo: repo, number: issue.number, mode: .detail)) },
                     resolve: { openIssue(IssueSelection(repo: repo, number: issue.number, mode: .resolve)) }) { resolveShown in
                Image(systemName: issue.state == .open ? "exclamationmark.circle" : "checkmark.circle")
                    .font(Theme.font(size: 15))
                    .foregroundStyle(issue.state == .open ? Theme.success : Theme.textMuted)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 8) {
                        Text(issue.title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                            .lineLimit(1).truncationMode(.tail)
                        ForEach(issue.labels.prefix(3), id: \.self) { Badge($0) }
                    }
                    Text("#\(issue.number) · \(issue.author)"
                         + (issue.comments > 0 ? " · \(issue.comments) comments" : "")
                         + (issue.updatedAt.date.map { " · \(UsageFormat.ago($0, now: .now))" } ?? ""))
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 12)
                // The Resolve button takes the badges' place while it shows.
                if !resolveShown {
                    ForEach(issue.assignees.prefix(2), id: \.self) {
                        Badge("@\($0)")
                    }
                }
            }
            .contextMenu {
                Button("Resolve with agent") {
                    openIssue(IssueSelection(repo: repo, number: issue.number, mode: .resolve))
                }
                Divider()
                Button("Open on GitHub") {
                    if let url = URL(string: issue.url), url.isWebURL { openURL(url) }
                }
                Button("Copy link") { copyToPasteboard(issue.url) }
            }
        }
    }

    private func actionsState() -> Loadable<[GhRun]> {
        filtered(board.runs, Self.runParts)
    }

    @ViewBuilder
    private func actions() -> some View {
        let state = actionsState()
        SurfaceList(state,
                    empty: emptyText("No workflow runs."),
                    expandKey: "\(repo):actions", expanded: $expanded,
                    preview: preview, edge: expandEdge,
                    window: window(for: .actions, rows: count(state))) { run in
            OpenRow(url: run.url, open: openURL.callAsFunction) {
                RunIcon(status: run.status, conclusion: run.conclusion)
                VStack(alignment: .leading, spacing: 3) {
                    Text(run.title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                        .lineLimit(1).truncationMode(.tail)
                    Text("\(run.workflow) · \(run.branch) · \(run.event)"
                         + (run.createdAt.date.map { " · \(UsageFormat.ago($0, now: .now))" } ?? ""))
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 12)
                if model.actionBusy.contains("rerun:\(repo)#\(run.id)") {
                    ProgressView().controlSize(.small)
                } else if let conclusion = run.conclusion {
                    Badge(conclusion == "success" ? "Passed" : conclusion.capitalized)
                }
            }
            .contextMenu {
                if run.conclusion == "failure" {
                    Button("Re-run failed jobs") {
                        Task { await model.rerunRun(repo: repo, run: run.id,
                                                    failedOnly: true, swe: app.swe) }
                    }
                }
                Button("Re-run all jobs") {
                    Task { await model.rerunRun(repo: repo, run: run.id,
                                                failedOnly: false, swe: app.swe) }
                }
                Divider()
                Button("Open on GitHub") {
                    if let url = URL(string: run.url), url.isWebURL { openURL(url) }
                }
            }
        }
    }

    /// Pages is a single facts panel, not a row list — inside the repo
    /// callout it keeps the card's inset rather than bleeding.
    @ViewBuilder
    private func pages() -> some View {
        Group {
            switch board.pages {
            case .idle, .loading:
                LoadingCard(text: "Reading the Pages settings…", framed: false)
            case .failed(let message):
                ErrorCard(message: message, framed: false)
            case .loaded(let pages):
                if let pages {
                VStack(alignment: .leading, spacing: 10) {
                    Text(pages.cname ?? pages.url ?? "GitHub Pages")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    if let url = pages.url, let link = URL(string: url), link.isWebURL {
                        Button { openURL(link) } label: {
                            Label(url, systemImage: "arrow.up.right.square")
                                .font(Theme.bodySmall).foregroundStyle(Theme.link)
                        }
                        .plainButton(cornerRadius: Theme.controlRadius)
                    }
                    if let branch = pages.sourceBranch {
                        Label("Built from \(branch)", systemImage: "arrow.triangle.branch")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                    if let build = pages.latestBuild {
                        HStack(spacing: 8) {
                            BuildMark(status: build.status)
                            Text("Last build \(build.status)")
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            if let commit = build.commit {
                                Text(commit.prefix(7))
                                    .font(Theme.font(size: 12, design: .monospaced))
                                    .foregroundStyle(Theme.textMuted)
                            }
                            if let at = build.updatedAt?.date {
                                Text(UsageFormat.ago(at, now: .now))
                                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                            }
                        }
                        if let error = build.error {
                            Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                                .lineLimit(3)
                        }
                    }
                }
                .padding(.vertical, 6)
                } else {
                    EmptyCard(text: "This repo has no GitHub Pages site.",
                              icon: "globe", framed: false)
                }
            }
        }
        // Counteracts the callout's row bleed on every branch: the
        // panel keeps the inset the rows gave up.
        .padding([.horizontal, .bottom], Theme.cardPadding)
    }

    private func releasesState() -> Loadable<[GhRelease]> {
        filtered(board.releases, Self.releaseParts)
    }

    @ViewBuilder
    private func releases() -> some View {
        let state = releasesState()
        SurfaceList(state,
                    empty: emptyText("No releases."),
                    expandKey: "\(repo):releases", expanded: $expanded,
                    preview: preview, edge: expandEdge,
                    window: window(for: .releases, rows: count(state))) { release in
            OpenRow(url: release.url, open: openURL.callAsFunction) {
                Image(systemName: "tag").font(Theme.font(size: 14))
                    .foregroundStyle(Theme.accent)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 8) {
                        Text(release.name ?? release.tag)
                            .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                            .lineLimit(1).truncationMode(.tail)
                        if release.latest { Badge("Latest") }
                        if release.draft { Badge("Draft") }
                        if release.prerelease { Badge("Pre-release") }
                    }
                    Text(release.tag
                         + (release.publishedAt?.date.map { " · \(UsageFormat.ago($0, now: .now))" } ?? ""))
                        .font(Theme.font(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                }
            }
        }
    }

    private func tagsState() -> Loadable<[GhTag]> {
        filtered(board.tags, Self.tagParts)
    }

    @ViewBuilder
    private func tags() -> some View {
        let state = tagsState()
        SurfaceList(state,
                    empty: emptyText("No tags."),
                    expandKey: "\(repo):tags", expanded: $expanded,
                    preview: preview, edge: expandEdge,
                    window: window(for: .tags, rows: count(state))) { tag in
            OpenRow(url: tag.url, open: openURL.callAsFunction) {
                Image(systemName: "tag").font(Theme.font(size: 14))
                    .foregroundStyle(Theme.textSecondary)
                Text(tag.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                Spacer(minLength: 12)
                Text(tag.sha).font(Theme.font(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.textMuted)
            }
        }
    }

    private func branchesState() -> Loadable<[GhBranch]> {
        filtered(board.branches, Self.branchParts)
    }

    @ViewBuilder
    private func branches() -> some View {
        let state = branchesState()
        SurfaceList(state,
                    empty: emptyText("No branches."),
                    expandKey: "\(repo):branches", expanded: $expanded,
                    preview: preview, edge: expandEdge,
                    window: window(for: .branches, rows: count(state))) { branch in
            OpenRow(url: branch.url, open: openURL.callAsFunction) {
                Image(systemName: "arrow.triangle.branch").font(Theme.font(size: 14))
                    .foregroundStyle(branch.default ? Theme.accent : Theme.textSecondary)
                Text(branch.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1).truncationMode(.middle)
                if branch.default { Badge("Default") }
                if branch.protected { Badge("Protected") }
                Spacer(minLength: 12)
                Text(branch.sha).font(Theme.font(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.textMuted)
            }
        }
    }
}

/// A surface's rows inside the repo callout: the spinner on first
/// load, the error card on failure, the empty line on `.loaded([])`,
/// else a preview of `preview` rows plus a `.cardBottom` expander that
/// sits flush on the callout's bottom edge when more exist.
/// A scroll-load window for a list: `shown` rows on screen, a sentinel
/// at the end whose ring fills to `advance` — revealing the next rows,
/// or fetching the next page once the local ones run out.
struct ListWindow {
    var shown: Int
    /// A fetch for more rows is already in flight — the sentinel shows
    /// a spinner instead of a fresh ring.
    var fetching: Bool
    /// The remote may hold more rows; false ends the sentinel for good.
    var canGrow: Bool
    /// One fill's work: reveal more rows, or ask the model for another
    /// page when the local ones are all out.
    var advance: () -> Void
}

private struct SurfaceList<Row: Identifiable & Equatable & Sendable, Content: View>: View {
    var state: Loadable<[Row]>
    var empty: String
    /// "repo:surface" key into `expanded`; nil disables the preview cap.
    var expandKey: String?
    @Binding var expanded: Set<String>
    var preview = 3
    var edge: ExpandRow.Edge = .cardBottom
    /// Scroll loading (the focus page): replaces the collapse preview
    /// with reveal-while-scrolling. nil keeps the 3-row preview.
    var window: ListWindow?
    @ViewBuilder var row: (Row) -> Content

    init(_ state: Loadable<[Row]>, empty: String,
         expandKey: String? = nil, expanded: Binding<Set<String>>? = nil,
         preview: Int = 3, edge: ExpandRow.Edge = .cardBottom,
         window: ListWindow? = nil,
         @ViewBuilder row: @escaping (Row) -> Content) {
        self.state = state
        self.empty = empty
        self.expandKey = expandKey
        self._expanded = expanded ?? .constant([])
        self.preview = preview
        self.edge = edge
        self.window = window
        self.row = row
    }

    private var isOpen: Bool {
        expandKey.map { expanded.contains($0) } ?? true
    }

    var body: some View {
        switch state {
        case .idle, .loading:
            LoadingCard(text: "Fetching…", framed: false)
                .padding(statePadding)
        case .failed(let message):
            ErrorCard(message: message, framed: false)
                .padding(statePadding)
        case .loaded(let rows) where rows.isEmpty:
            EmptyCard(text: empty, icon: "checkmark.circle", framed: false)
                .padding(statePadding)
        case .loaded(let rows):
            content(rows)
        }
    }

    /// The bare state rows share the row content's inset inside the
    /// bled callout — without it their text and buttons touch the edge.
    private var statePadding: EdgeInsets {
        EdgeInsets(top: 10, leading: 14, bottom: 10, trailing: 14)
    }

    @ViewBuilder private func content(_ rows: [Row]) -> some View {
        if let window {
            windowed(rows, window)
        } else {
            collapsible(rows)
        }
    }

    /// The dashboard callout's list: three rows, then a row that opens
    /// the rest on click.
    @ViewBuilder private func collapsible(_ rows: [Row]) -> some View {
        VStack(spacing: 0) {
            let visible = isOpen ? rows : Array(rows.prefix(preview))
            let hasMore = expandKey != nil && rows.count > preview
            ForEach(Array(visible.enumerated()), id: \.element.id) { index, item in
                row(item)
                if index < visible.count - 1 || hasMore {
                    Divider().overlay(Theme.rowDivider)
                }
            }
            if let expandKey, hasMore {
                ExpandRow(isExpanded: isOpen, hidden: rows.count - preview,
                          help: (expand: "Show every row",
                                 collapse: "Show only the first \(preview)"),
                          edge: edge) {
                    if isOpen { expanded.remove(expandKey) }
                    else { expanded.insert(expandKey) }
                }
            }
        }
    }

    /// The focus page's list: `window.shown` rows, then a sentinel
    /// whose ring fills as it stays in view — a full ring reveals the
    /// next slice or fetches the next page.
    @ViewBuilder private func windowed(_ rows: [Row], _ window: ListWindow) -> some View {
        VStack(spacing: 0) {
            let visible = Array(rows.prefix(window.shown))
            let pending = window.fetching || rows.count > window.shown || window.canGrow
            ForEach(Array(visible.enumerated()), id: \.element.id) { index, item in
                row(item)
                if index < visible.count - 1 || pending {
                    Divider().overlay(Theme.rowDivider)
                }
            }
            if pending {
                ScrollSentinel(fetching: window.fetching,
                               advance: window.advance)
                    .id("sentinel:\(window.shown):\(rows.count):\(window.fetching)")
            }
        }
    }
}

/// The load-more trigger at the end of a windowed list: a ring that
/// fills while it stays in the scroll viewport (~650ms), then runs
/// `advance` once — scroll away mid-fill and the ring resets instead
/// of firing. Its `.id` rides on the revealed count, so each reveal
/// re-arms it: parked at the end, fills keep coming until the surface
/// runs out of local rows and the remote page is in.
private struct ScrollSentinel: View {
    var fetching: Bool
    var advance: () -> Void
    @State private var fill = 0.0
    /// Half the row must be inside the scroll viewport to count — a
    /// peek of a pixel at the fold shouldn't start the fill.
    @State private var visible = false

    var body: some View {
        HStack(spacing: 10) {
            if fetching {
                ProgressView().controlSize(.small)
            } else {
                ZStack {
                    Circle().stroke(Theme.rowDivider, lineWidth: 2)
                    Circle()
                        .trim(from: 0, to: fill)
                        .stroke(Theme.accent,
                                style: StrokeStyle(lineWidth: 2, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                }
                .frame(width: 14, height: 14)
            }
            Text(fetching ? "Fetching more…" : "Keep scrolling for more")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
            Spacer(minLength: 0)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 14)
        .onScrollVisibilityChange(threshold: 0.5) { seen in
            visible = seen
            if !seen { fill = 0 }
        }
        .task(id: "\(visible)-\(fetching)") {
            guard visible, !fetching else { return }
            withAnimation(.linear(duration: 0.6)) { fill = 1 }
            try? await Task.sleep(for: .milliseconds(650))
            guard !Task.isCancelled else { return }
            advance()
        }
    }
}

/// A row with one click action: hover fill, link pointer, plain style.
/// `url:` rows open a GitHub page; a custom action opens the in-app
/// workbench instead. Rows bleed to the callout's edges, so the hover
/// fill runs wall to wall and the content carries the inset.
private struct OpenRow<Content: View>: View {
    var alignment: VerticalAlignment = .top
    var act: () -> Void
    @ViewBuilder var content: Content
    @State private var hovering = false

    init(url: String, open: @escaping (URL) -> Void,
         @ViewBuilder content: () -> Content) {
        act = { if let link = URL(string: url), link.isWebURL { open(link) } }
        self.content = content()
    }

    init(alignment: VerticalAlignment = .top, act: @escaping () -> Void,
         @ViewBuilder content: () -> Content) {
        self.alignment = alignment
        self.act = act
        self.content = content()
    }

    var body: some View {
        Button(action: act) {
            HStack(alignment: alignment, spacing: 12) { content }
                .padding(.vertical, 10)
                .padding(.horizontal, 14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(hovering ? Theme.rowHover : .clear,
                            in: .rect(cornerRadius: Theme.controlRadius))
                .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))
        .onHover { hovering = $0 }
    }
}

/// An issue row: a click opens the issue sheet, like `OpenRow`, and a
/// Resolve button shows at its trailing end while the pointer is over
/// the row or the keyboard is on it. The button sits over the row, not
/// inside its label, so it takes its own clicks; `content` hears whether
/// it shows, to give up the space it covers.
private struct IssueRow<Content: View>: View {
    var open: () -> Void
    var resolve: () -> Void
    @ViewBuilder var content: (Bool) -> Content
    @State private var hovering = false
    @FocusState private var focus: Part?

    private enum Part: Hashable { case row, resolve }

    private var resolveShown: Bool { hovering || focus != nil }

    var body: some View {
        Button(action: open) {
            HStack(alignment: .top, spacing: 12) { content(resolveShown) }
                .padding(.vertical, 10)
                .padding(.leading, 14)
                .padding(.trailing, resolveShown ? 112 : 14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(hovering ? Theme.rowHover : .clear,
                            in: .rect(cornerRadius: Theme.controlRadius))
                .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))
        .focused($focus, equals: .row)
        .focusRing(focus == .row, cornerRadius: Theme.controlRadius)
        .overlay(alignment: .trailing) {
            if resolveShown {
                Button(action: resolve) {
                    Label("Resolve", systemImage: "sparkles")
                }
                .buttonStyle(SecondaryButtonStyle())
                .fixedSize()
                .focused($focus, equals: .resolve)
                .focusRing(focus == .resolve, cornerRadius: Theme.controlRadius)
                .padding(.trailing, 14)
                .accessibilityLabel("Resolve with agent")
            }
        }
        .onHover { hovering = $0 }
    }
}

/// The header gear's popover: every pin with an open and an unpin
/// control, and the add path — all repo management in one place.
private struct RepoManager: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    var onAdd: () -> Void
    /// The pin whose Unpin waits on the confirmation.
    @State private var unpinning: String?

    private var model: GitHubModel { app.github }

    /// Pins grouped by their `owner/` — the header only earns its row
    /// when more than one owner exists.
    private var groups: [(owner: String, pins: [PinnedRepo])] {
        Dictionary(grouping: model.pins) { pin in
            String(pin.name.split(separator: "/", maxSplits: 1).first ?? "")
        }
        .sorted { $0.key < $1.key }
        .map { ($0.key, $0.value.sorted { $0.name < $1.name }) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Pinned repositories")
                .font(Theme.control).foregroundStyle(Theme.textSecondary)
                .padding(.bottom, 4)
            if model.pins.isEmpty {
                Text("Nothing pinned yet.")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .padding(.vertical, 6)
            }
            ForEach(groups, id: \.owner) { owner, pins in
                if groups.count > 1 {
                    Text(owner)
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                        .padding(.top, 4)
                }
                ForEach(pins) { pin in
                    HStack(spacing: 8) {
                        Text(groups.count > 1
                             ? String(pin.name.split(separator: "/").last ?? "")
                             : pin.name)
                            .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                            .lineLimit(1).truncationMode(.middle)
                        Spacer(minLength: 8)
                        Button {
                            if let url = URL(string: "https://github.com/\(pin.name)") { openURL(url) }
                        } label: {
                            Image(systemName: "arrow.up.right.square")
                                .font(Theme.font(size: 13)).foregroundStyle(Theme.textSecondary)
                        }
                        .plainButton()
                        .help("Open on GitHub")
                        Button {
                            unpinning = pin.name
                        } label: {
                            Image(systemName: "minus.circle")
                                .font(Theme.font(size: 13)).foregroundStyle(Theme.dangerText)
                        }
                        .plainButton()
                        .help("Unpin \(pin.name)")
                    }
                    .padding(.vertical, 3)
                }
            }
            Divider().overlay(Theme.rowDivider).padding(.vertical, 6)
            Button(action: onAdd) {
                Label("Add repository…", systemImage: "plus")
                    .font(Theme.bodySmall).foregroundStyle(Theme.link)
            }
            .plainButton()
        }
        .padding(14)
        .frame(width: 300)
        // One click on the small minus took the repo off every board.
        .confirmationDialog("Unpin \(unpinning ?? "")?", isPresented: .init(
            get: { unpinning != nil }, set: { if !$0 { unpinning = nil } })) {
            Button("Unpin", role: .destructive) {
                if let name = unpinning { Task { await model.removeRepo(name, swe: app.swe) } }
                unpinning = nil
            }
            Button("Cancel", role: .cancel) { unpinning = nil }
        } message: {
            Text("Its boards leave Repositories. Pin it again any time.")
        }
    }
}

// MARK: - State marks

/// GitHub's purple for merged/closed-done states.
let ghPurple = Color(light: "#8250df", dark: "#a371f7")

/// The PR's state as GitHub's own octicon — pull request open, merge
/// node merged, crossed pull request closed, dashed pull request draft.
/// Tinted like the platform: green, purple, red, muted.
struct PrIcon: View {
    var state: GhPr.State
    var draft: Bool
    var body: some View {
        OcticonView(icon: icon, size: 16)
            .foregroundStyle(color)
    }
    private var icon: Octicon {
        if draft { return .pullRequestDraft }
        return switch state {
        case .open: .pullRequest
        case .merged: .merge
        case .closed: .pullRequestClosed
        }
    }
    private var color: Color {
        if draft { return Theme.textMuted }
        return switch state {
        case .open: Theme.success
        case .merged: ghPurple
        case .closed: Theme.danger
        }
    }
}

/// The folded statusCheckRollup as one coloured mark.
struct ChecksMark: View {
    var checks: GhPr.Checks?
    var body: some View {
        Group {
            switch checks {
            case .passing:
                Label("Passing", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(Theme.success)
            case .failing:
                Label("Failing", systemImage: "xmark.circle.fill")
                    .foregroundStyle(Theme.danger)
            case .running:
                Label("Running", systemImage: "arrow.triangle.2.circlepath")
                    .foregroundStyle(Theme.warning)
            case .unknown:
                Label("CI unknown", systemImage: "questionmark.circle")
                    .foregroundStyle(Theme.textMuted)
            case .none?, nil:
                EmptyView()
            }
        }
        .font(Theme.caption)
    }
}

/// The review decision, shown only when GitHub tracks one.
struct ReviewMark: View {
    var decision: GhPr.Review?
    var body: some View {
        Group {
            switch decision {
            case .approved:
                Label("Approved", systemImage: "checkmark.seal.fill").foregroundStyle(Theme.success)
            case .changesRequested:
                Label("Changes", systemImage: "pencil.circle.fill").foregroundStyle(Theme.danger)
            case .reviewRequired:
                Label("Review needed", systemImage: "eye.circle.fill").foregroundStyle(Theme.warning)
            case .none?, nil:
                EmptyView()
            }
        }
        .font(Theme.caption)
    }
}

/// The list row's PR state as icons only, a little larger than the
/// labelled marks: conflicts, review, then CI. Running CI spins. The
/// words come back on hover.
struct PrStateIcons: View {
    var pr: GhPr

    var body: some View {
        HStack(spacing: 10) {
            if pr.mergeable == .conflicting {
                OcticonView(icon: .alert, size: 16)
                    .foregroundStyle(Theme.warningText)
                    .hoverTip("This branch has merge conflicts to resolve.", title: "Merge conflicts")
            }
            switch pr.reviewDecision {
            case .approved:
                icon("checkmark.seal.fill", Theme.success)
                    .hoverTip("A reviewer approved this pull request.", title: "Approved")
            case .changesRequested:
                icon("pencil.circle.fill", Theme.danger)
                    .hoverTip("A reviewer asked for changes.", title: "Changes requested")
            case .reviewRequired:
                icon("eye.circle.fill", Theme.warning)
                    .hoverTip("Needs an approving review before it can merge.", title: "Review needed")
            case .none?, nil:
                EmptyView()
            }
            switch pr.checks {
            case .passing:
                icon("checkmark.circle.fill", Theme.success)
                    .hoverTip(readyToMerge ? "Checks passed and nothing blocks the merge."
                                           : "Checks passed.",
                              title: readyToMerge ? "Ready to merge" : "Checks passed")
            case .failing:
                icon("xmark.circle.fill", Theme.danger)
                    .hoverTip("At least one check failed.", title: "Checks failing")
            case .running:
                icon("arrow.triangle.2.circlepath", Theme.warning)
                    .symbolEffect(.rotate, options: .repeating)
                    .hoverTip("Checks are still running.", title: "Running")
            case .unknown:
                icon("questionmark.circle", Theme.textMuted)
                    .hoverTip("GitHub hasn't reported a CI state.", title: "CI unknown")
            case .none?, nil:
                EmptyView()
            }
        }
    }

    /// Passing checks with nothing else in the way: GitHub confirmed
    /// no conflicts (unknown doesn't count), no outstanding review,
    /// not a draft.
    private var readyToMerge: Bool {
        !pr.draft && pr.mergeable == .mergeable
            && pr.reviewDecision != .changesRequested && pr.reviewDecision != .reviewRequired
    }

    private func icon(_ symbol: String, _ color: Color) -> some View {
        Image(systemName: symbol)
            .font(Theme.font(size: 17))
            .foregroundStyle(color)
    }
}

/// A workflow run's status: running marks spin yellow, finished runs
/// take their conclusion's colour.
private struct RunIcon: View {
    var status: String?
    var conclusion: String?
    private var running: Bool { status == "in_progress" || status == "queued" }
    var body: some View {
        Image(systemName: symbol).font(Theme.font(size: 15)).foregroundStyle(color)
            .symbolEffect(.rotate, options: .repeating, isActive: running)
    }
    private var symbol: String {
        if running { return "arrow.triangle.2.circlepath" }
        switch conclusion {
        case "success": return "checkmark.circle.fill"
        case "failure": return "xmark.circle.fill"
        case "cancelled": return "slash.circle"
        case "skipped": return "minus.circle"
        default: return "questionmark.circle"
        }
    }
    private var color: Color {
        if status == "in_progress" || status == "queued" { return Theme.warning }
        return switch conclusion {
        case "success": Theme.success
        case "failure": Theme.danger
        case "cancelled", "skipped": Theme.textMuted
        default: Theme.textMuted
        }
    }
}

/// A Pages build's status mark.
private struct BuildMark: View {
    var status: String
    var body: some View {
        Image(systemName: symbol).font(Theme.font(size: 14)).foregroundStyle(color)
    }
    private var symbol: String {
        switch status {
        case "built": "checkmark.circle.fill"
        case "building": "arrow.triangle.2.circlepath"
        case "errored": "xmark.circle.fill"
        default: "questionmark.circle"
        }
    }
    private var color: Color {
        switch status {
        case "built": return Theme.success
        case "building": return Theme.warning
        case "errored": return Theme.danger
        default: return Theme.textMuted
        }
    }
}
