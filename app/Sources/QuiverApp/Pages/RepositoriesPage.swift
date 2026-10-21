import AppKit
import QuiverCore
import SwiftUI

/// The GitHub dashboard: a section per pinned repo under a filter bar,
/// then pull requests, issues, workflow runs, Pages site, releases,
/// tags and branches in shared tabs. The filter narrows by repo name
/// (a name-matched repo shows its whole board) and by row text — PR
/// title, branch, author, labels, tag, sha. Rows carry their state
/// (draft, conflicts, review, CI rollup) as icons and colour, and link
/// straight to the GitHub page. Every visible repo's tab refetches on
/// a minute timer while the page is open.
struct RepositoriesPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    @State private var adding = false
    @State private var managing = false
    @State private var jumping = false
    /// Repo names whose section is folded to just the header — keyed by
    /// repo alone, so a collapse follows the repo across every tab.
    @State private var collapsed: Set<String> = []
    /// "repo:surface" keys whose row list was opened past the preview.
    @State private var expanded: Set<String> = []
    /// The PR whose detail sheet is open, if any.
    @State private var detail: PrSelection?

    private var model: GitHubModel { app.github }

    var body: some View {
        @Bindable var model = model
        Page(title: "Repositories",
             subtitle: "Your pinned repos' pull requests, issues and CI, from swe gh.") {
            if model.envBlocked == nil {
                Button("Add repository", systemImage: "plus") { adding = true }
                    .primaryButton()
                Button { managing = true } label: {
                    Image(systemName: "gearshape")
                }
                .iconButton()
                .help("Manage pinned repositories")
                .popover(isPresented: $managing, arrowEdge: .bottom) {
                    RepoManager {
                        managing = false
                        // The sheet attaches once the popover is gone.
                        DispatchQueue.main.async { adding = true }
                    }
                }
                Button { jumping = true } label: {
                    Image(systemName: "magnifyingglass")
                }
                .iconButton()
                .keyboardShortcut("k", modifiers: .command)
                .help("Jump to a repository (⌘K)")
                Button("Refresh", systemImage: "arrow.clockwise") {
                    Task {
                        // A board call covers repos not yet hydrated;
                        // the per-repo refreshes overlap after it.
                        await model.loadBoards(visibleRepos.map(\.name),
                                               swe: app.swe)
                        let model = model
                        let swe = app.swe
                        await withTaskGroup(of: Void.self) { group in
                            for repo in visibleRepos {
                                group.addTask {
                                    await model.refresh(repo: repo.name,
                                                        swe: swe)
                                }
                            }
                        }
                    }
                }
                .iconButton()
                .keyboardShortcut("r", modifiers: .command)
                .help("Fetch the visible tab again (⌘R)")
            }
        } content: {
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
                HStack(alignment: .bottom, spacing: 16) {
                    // Tabs expands for the full-width underline; the
                    // search rides the right end of the same row.
                    Tabs(options: RepoTab.allCases.map { ($0, $0.title) },
                         selection: $model.tab, style: .underline)
                        .layoutPriority(1)
                    SearchField(prompt: "Filter by repo, title, branch…",
                                text: $model.filter)
                        .frame(minWidth: 100, maxWidth: 280)
                        .padding(.bottom, 4)
                }
                if let error = model.pinError ?? model.actionError {
                    ErrorCard(message: error)
                }
                if let error = model.sessionError {
                    ErrorCard(message: error)
                }
                ScrollViewReader { proxy in
                    ReorderableRows(visibleRepos) { id, before in
                        var order = model.pins.map(\.name)
                        order.removeAll { $0 == id }
                        if let before, let idx = order.firstIndex(of: before) {
                            order.insert(id, at: idx)
                        } else {
                            order.append(id)
                        }
                        Task { await model.reorderPins(order, swe: app.swe) }
                    } row: { pin in
                        repoSection(pin.name)
                            .id(pin.name)
                            .padding(.bottom, 10)
                    }
                    .onChange(of: model.scrollTarget) { _, target in
                        // Banner click-through: the filter is already
                        // cleared so the section exists — scroll to it.
                        guard let target else { return }
                        model.scrollTarget = nil
                        collapsed.remove(target)
                        withAnimation { proxy.scrollTo(target, anchor: .top) }
                    }
                }
            }
        }
        .task(id: Visible(tab: model.tab, swe: app.swePath,
                         filter: model.filter,
                         repos: model.pins.map(\.name))) {
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
                }
            }
            // Poll while the page is open so merged PRs and finished CI
            // land without a manual refresh.
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(60))
                await withTaskGroup(of: Void.self) { group in
                    for repo in visibleRepos {
                        group.addTask {
                            await model.loadTab(model.tab, repo: repo.name,
                                                swe: swe)
                        }
                    }
                }
            }
        }
        .sheet(isPresented: $adding) {
            AddRepoSheet()
        }
        .sheet(isPresented: $jumping) {
            RepoJumper()
        }
        .sheet(item: $detail) { selection in
            PrDetailSheet(selection: selection)
        }
    }

    /// Identity for the visible surface: switching tab, swe, the
    /// filter or the pin list (add/remove) re-runs the load task and
    /// its poll loop.
    private struct Visible: Hashable {
        var tab: RepoTab
        var swe: String
        var filter: String
        var repos: [String]
    }

    /// The repos whose sections show — the model's filter logic.
    private var visibleRepos: [PinnedRepo] { model.visibleRepos }

    /// The board's rows, filtered when this repo is shown by fallback
    /// rather than by name.
    private func filtered<Row>(_ state: Loadable<[Row]>, repo: String,
                               _ parts: @escaping (Row) -> [String?]) -> Loadable<[Row]> {
        model.filtered(state, repo: repo, parts)
    }

    /// One pinned repo's section: a callout holding its header (grip +
    /// chevron + name, a link out) and, unless folded or empty, the
    /// active tab's surface. The section is one ReorderableRows item —
    /// a vertical drag on it reorders the pin list.
    @ViewBuilder private func repoSection(_ repo: String) -> some View {
        let board = model.board(for: repo)
        let empty = surfaceEmpty(repo, board: board)
        Card {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    GripDots()
                    if empty {
                        // Nothing inside to fold — no chevron, no body.
                        Text(repo).font(Theme.cardTitle)
                            .foregroundStyle(Theme.textPrimary)
                    } else {
                        Button {
                            if collapsed.contains(repo) {
                                collapsed.remove(repo)
                            } else {
                                collapsed.insert(repo)
                            }
                        } label: {
                            HStack(spacing: 8) {
                                Image(systemName: "chevron.right")
                                    .font(.system(size: 10, weight: .semibold))
                                    .foregroundStyle(Theme.textMuted)
                                    .rotationEffect(collapsed.contains(repo)
                                                    ? .zero : .degrees(90))
                                Text(repo).font(Theme.cardTitle)
                                    .foregroundStyle(Theme.textPrimary)
                            }
                            .contentShape(Rectangle())
                        }
                        .plainButton()
                        .help(collapsed.contains(repo) ? "Expand" : "Collapse")
                    }
                    Spacer()
                    Button {
                        if let url = URL(string: "https://github.com/\(repo)") { openURL(url) }
                    } label: {
                        Image(systemName: "arrow.up.right.square")
                            .font(.system(size: 12)).foregroundStyle(Theme.textMuted)
                    }
                    .plainButton()
                    .help("Open on GitHub")
                }
                .padding(.bottom, empty || collapsed.contains(repo) ? 0 : 10)
                if !empty, !collapsed.contains(repo) {
                    // Rows bleed to the callout's edges, GitHub-style:
                    // the negative pads undo the card's, and the clip
                    // keeps hover fills inside the rounded corners.
                    VStack(spacing: 0) {
                        Divider().overlay(Theme.rowDivider)
                            .padding(.bottom, 4)
                        switch model.tab {
                        case .pulls: pulls(repo: repo, board: board)
                        case .issues: issues(repo: repo, board: board)
                        case .actions: actions(repo: repo, board: board)
                        case .pages: pages(repo: repo, board: board)
                        case .releases: releases(repo: repo, board: board)
                        case .tags: tags(repo: repo, board: board)
                        case .branches: branches(repo: repo, board: board)
                        }
                    }
                    .padding(.horizontal, -Theme.cardPadding)
                    .padding(.bottom, -Theme.cardPadding)
                }
            }
        }
        .clipShape(.rect(cornerRadius: Theme.cardRadius))
    }

    /// Whether the active tab's surface came back empty — the callout
    /// then shows just the repo title, without a fold control or an
    /// empty-state row. `hasRows` only checks presence (first match
    /// exits) so the list's own filter pass isn't duplicated. Loading,
    /// errors and unfetched surfaces keep the card so state stays
    /// visible.
    private func surfaceEmpty(_ repo: String, board: RepoBoard) -> Bool {
        switch model.tab {
        case .pulls: return !model.hasRows(board.pulls, repo: repo, Self.prParts)
        case .issues: return !model.hasRows(board.issues, repo: repo, Self.issueParts)
        case .actions: return !model.hasRows(board.runs, repo: repo, Self.runParts)
        case .releases: return !model.hasRows(board.releases, repo: repo, Self.releaseParts)
        case .tags: return !model.hasRows(board.tags, repo: repo, Self.tagParts)
        case .branches: return !model.hasRows(board.branches, repo: repo, Self.branchParts)
        case .pages:
            if case .loaded(let site) = board.pages { return site == nil }
            return false
        }
    }

    /// The surface's empty text: under a row filter an empty list means
    /// "nothing matched", not "nothing exists" — but a repo the query
    /// named keeps the normal text, since its rows were never filtered.
    private func emptyText(_ base: String, repo: String) -> String {
        model.showsAllRows(repo) ? base : "Nothing matches \"\(model.query)\"."
    }

    // MARK: - Surfaces

    /// What the row filter searches per surface — shared by the list's
    /// filtered state and `surfaceEmpty`, so they can never disagree.
    private static let prParts: (GhPr) -> [String?] = {
        [$0.title, "\($0.number)", $0.branch, $0.base, $0.author] + $0.labels }
    private static let issueParts: (GhIssue) -> [String?] = {
        [$0.title, "\($0.number)", $0.author] + $0.labels + $0.assignees }
    private static let runParts: (GhRun) -> [String?] = {
        [$0.title, $0.workflow, $0.branch, $0.event] }
    private static let releaseParts: (GhRelease) -> [String?] = { [$0.name, $0.tag] }
    private static let tagParts: (GhTag) -> [String?] = { [$0.name, $0.sha] }
    private static let branchParts: (GhBranch) -> [String?] = { [$0.name, $0.sha] }

    /// The tab's rows after the search filter.
    private func pullsState(_ repo: String, board: RepoBoard) -> Loadable<[GhPr]> {
        filtered(board.pulls, repo: repo, Self.prParts)
    }

    @ViewBuilder
    private func pulls(repo: String, board: RepoBoard) -> some View {
        SurfaceList(pullsState(repo, board: board),
                    empty: emptyText("No open pull requests.", repo: repo),
                    loading: model.loading.contains(.init(repo: repo, tab: .pulls)),
                    expandKey: "\(repo):pulls", expanded: $expanded) { pr in
            OpenRow(act: { detail = PrSelection(repo: repo, number: pr.number) }) {
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
                Spacer(minLength: 12)
                if pr.mergeable == .conflicting {
                    Label {
                        Text("Conflicts")
                    } icon: {
                        OcticonView(icon: .alert, size: 12)
                    }
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                }
                ReviewMark(decision: pr.reviewDecision)
                ChecksMark(checks: pr.checks)
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
                    if let url = URL(string: pr.url) { openURL(url) }
                }
            }
        }
    }

    private func issuesState(_ repo: String, board: RepoBoard) -> Loadable<[GhIssue]> {
        filtered(board.issues, repo: repo, Self.issueParts)
    }

    @ViewBuilder
    private func issues(repo: String, board: RepoBoard) -> some View {
        SurfaceList(issuesState(repo, board: board),
                    empty: emptyText("No open issues.", repo: repo),
                    loading: model.loading.contains(.init(repo: repo, tab: .issues)),
                    expandKey: "\(repo):issues", expanded: $expanded) { issue in
            OpenRow(url: issue.url, open: openURL.callAsFunction) {
                Image(systemName: issue.state == .open ? "exclamationmark.circle" : "checkmark.circle")
                    .font(.system(size: 15))
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
                ForEach(issue.assignees.prefix(2), id: \.self) {
                    Badge("@\($0)")
                }
            }
        }
    }

    private func actionsState(_ repo: String, board: RepoBoard) -> Loadable<[GhRun]> {
        filtered(board.runs, repo: repo, Self.runParts)
    }

    @ViewBuilder
    private func actions(repo: String, board: RepoBoard) -> some View {
        SurfaceList(actionsState(repo, board: board),
                    empty: emptyText("No workflow runs.", repo: repo),
                    loading: model.loading.contains(.init(repo: repo, tab: .actions)),
                    expandKey: "\(repo):actions", expanded: $expanded) { run in
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
                    if let url = URL(string: run.url) { openURL(url) }
                }
            }
        }
    }

    /// Pages is a single facts panel, not a row list — inside the repo
    /// callout it keeps the card's inset rather than bleeding.
    @ViewBuilder
    private func pages(repo: String, board: RepoBoard) -> some View {
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
                    if let url = pages.url, let link = URL(string: url) {
                        Button { openURL(link) } label: {
                            Label(url, systemImage: "arrow.up.right.square")
                                .font(Theme.bodySmall).foregroundStyle(Theme.accent)
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
                                    .font(.system(size: 12, design: .monospaced))
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

    private func releasesState(_ repo: String, board: RepoBoard) -> Loadable<[GhRelease]> {
        filtered(board.releases, repo: repo, Self.releaseParts)
    }

    @ViewBuilder
    private func releases(repo: String, board: RepoBoard) -> some View {
        SurfaceList(releasesState(repo, board: board),
                    empty: emptyText("No releases.", repo: repo),
                    loading: model.loading.contains(.init(repo: repo, tab: .releases)),
                    expandKey: "\(repo):releases", expanded: $expanded) { release in
            OpenRow(url: release.url, open: openURL.callAsFunction) {
                Image(systemName: "tag").font(.system(size: 14))
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
                        .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                }
            }
        }
    }

    private func tagsState(_ repo: String, board: RepoBoard) -> Loadable<[GhTag]> {
        filtered(board.tags, repo: repo, Self.tagParts)
    }

    @ViewBuilder
    private func tags(repo: String, board: RepoBoard) -> some View {
        SurfaceList(tagsState(repo, board: board),
                    empty: emptyText("No tags.", repo: repo),
                    loading: model.loading.contains(.init(repo: repo, tab: .tags)),
                    expandKey: "\(repo):tags", expanded: $expanded) { tag in
            OpenRow(url: tag.url, open: openURL.callAsFunction) {
                Image(systemName: "tag").font(.system(size: 14))
                    .foregroundStyle(Theme.textSecondary)
                Text(tag.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                Spacer(minLength: 12)
                Text(tag.sha).font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.textMuted)
            }
        }
    }

    private func branchesState(_ repo: String, board: RepoBoard) -> Loadable<[GhBranch]> {
        filtered(board.branches, repo: repo, Self.branchParts)
    }

    @ViewBuilder
    private func branches(repo: String, board: RepoBoard) -> some View {
        SurfaceList(branchesState(repo, board: board),
                    empty: emptyText("No branches.", repo: repo),
                    loading: model.loading.contains(.init(repo: repo, tab: .branches)),
                    expandKey: "\(repo):branches", expanded: $expanded) { branch in
            OpenRow(url: branch.url, open: openURL.callAsFunction) {
                Image(systemName: "arrow.triangle.branch").font(.system(size: 14))
                    .foregroundStyle(branch.default ? Theme.accent : Theme.textSecondary)
                Text(branch.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1).truncationMode(.middle)
                if branch.default { Badge("Default") }
                if branch.protected { Badge("Protected") }
                Spacer(minLength: 12)
                Text(branch.sha).font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.textMuted)
            }
        }
    }
}

/// A surface's rows inside the repo callout: the spinner on first
/// load, the error card on failure, the empty line on `.loaded([])`,
/// else a preview of `preview` rows plus a `.cardBottom` expander that
/// sits flush on the callout's bottom edge when more exist.
private struct SurfaceList<Row: Identifiable & Equatable & Sendable, Content: View>: View {
    var state: Loadable<[Row]>
    var empty: String
    var loading: Bool
    /// "repo:surface" key into `expanded`; nil disables the preview cap.
    var expandKey: String?
    @Binding var expanded: Set<String>
    var preview = 3
    @ViewBuilder var row: (Row) -> Content

    init(_ state: Loadable<[Row]>, empty: String, loading: Bool,
         expandKey: String? = nil, expanded: Binding<Set<String>>? = nil,
         @ViewBuilder row: @escaping (Row) -> Content) {
        self.state = state
        self.empty = empty
        self.loading = loading
        self.expandKey = expandKey
        self._expanded = expanded ?? .constant([])
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
                          edge: .cardBottom) {
                    if isOpen { expanded.remove(expandKey) }
                    else { expanded.insert(expandKey) }
                }
            }
        }
        if loading {
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("Refreshing…").font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            .padding(.horizontal, Theme.cardPadding)
        }
    }
}

/// A row with one click action: hover fill, link pointer, plain style.
/// `url:` rows open a GitHub page; a custom action opens the in-app
/// workbench instead. Rows bleed to the callout's edges, so the hover
/// fill runs wall to wall and the content carries the inset.
private struct OpenRow<Content: View>: View {
    var act: () -> Void
    @ViewBuilder var content: Content
    @State private var hovering = false

    init(url: String, open: @escaping (URL) -> Void,
         @ViewBuilder content: () -> Content) {
        act = { if let link = URL(string: url) { open(link) } }
        self.content = content()
    }

    init(act: @escaping () -> Void, @ViewBuilder content: () -> Content) {
        self.act = act
        self.content = content()
    }

    var body: some View {
        Button(action: act) {
            HStack(alignment: .top, spacing: 12) { content }
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

/// The header gear's popover: every pin with an open and an unpin
/// control, and the add path — all repo management in one place.
private struct RepoManager: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    var onAdd: () -> Void

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
                                .font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                        }
                        .plainButton()
                        .help("Open on GitHub")
                        Button {
                            Task { await model.removeRepo(pin.name, swe: app.swe) }
                        } label: {
                            Image(systemName: "minus.circle")
                                .font(.system(size: 13)).foregroundStyle(Theme.dangerText)
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
                    .font(Theme.bodySmall).foregroundStyle(Theme.accent)
            }
            .plainButton()
        }
        .padding(14)
        .frame(width: 300)
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

/// A workflow run's status: running marks spin yellow, finished runs
/// take their conclusion's colour.
private struct RunIcon: View {
    var status: String?
    var conclusion: String?
    var body: some View {
        Image(systemName: symbol).font(.system(size: 15)).foregroundStyle(color)
    }
    private var symbol: String {
        if status == "in_progress" || status == "queued" { return "arrow.triangle.2.circlepath" }
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
        Image(systemName: symbol).font(.system(size: 14)).foregroundStyle(color)
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
