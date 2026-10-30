import QuiverCore
import SwiftUI

/// The single-repo focus page: Repositories zoomed to one pin, swapped
/// in over the dashboard. Same seven tabs — pull requests, issues,
/// actions, Pages, releases, tags, branches — but every surface fetches
/// on open and pulls/issues come back in every state, so the page
/// answers "what happened here lately": open work plus what landed.
///
/// Back is the header's Repositories button, ⌘[, or picking the
/// Repositories sidebar item again. The visible tab polls on a minute
/// timer like the dashboard; the board lives in the model's focus
/// store so its every-state rows never bleed into the dashboard's
/// open-only sections.
struct RepoDetailPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    var repo: String
    var onBack: () -> Void

    /// The PR whose detail sheet is open, if any.
    @State private var detail: PrSelection?
    /// The issue whose detail or resolve sheet is open, if any.
    @State private var issue: IssueSelection?
    /// The row filter — this page's own, not the dashboard's.
    @State private var filter = ""
    /// All / Mine / Review requested — this page's own too.
    @State private var pullFilter: PullFilter = .all

    private var model: GitHubModel { app.github }
    private var query: String { filter.trimmingCharacters(in: .whitespaces) }
    private var pullFilters: [PullFilter] {
        PullFilter.available(login: model.env?.login, requests: [model.reviewRequested(repo)])
    }
    private var activePullFilter: PullFilter {
        PullFilter.effective(pullFilter, available: pullFilters)
    }
    /// Rows under `filter` on this page's every-state list, after the search.
    private func pullCount(_ filter: PullFilter) -> Int {
        guard case .loaded(let prs) = model.focusBoard(for: repo).pulls else { return 0 }
        let login = model.env?.login
        let requested = model.reviewRequested(repo)
        return prs.count { pr in
            filter.keeps(pr, login: login, requested: requested)
                && (query.isEmpty || pr.searchText.contains { $0?.localizedCaseInsensitiveContains(query) ?? false })
        }
    }

    var body: some View {
        @Bindable var model = model
        Page(title: repo,
             subtitle: "Pull requests, issues, CI and refs — every state, most recent first.") {
            Button { onBack() } label: {
                Label("Repositories", systemImage: "chevron.left")
            }
            .secondaryButton()
            .keyboardShortcut("[", modifiers: .command)
            .help("Back to all repositories (⌘[)")
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task {
                    await model.refreshFocus(repo: repo, swe: app.swe)
                    if model.focusTab == .pulls {
                        await model.loadReviewRequests(repo: repo, swe: app.swe, force: true)
                    }
                }
            }
            .iconButton()
            .keyboardShortcut("r", modifiers: .command)
            .help("Fetch the visible tab again (⌘R)")
            Button {
                if let url = URL(string: "https://github.com/\(repo)") { openURL(url) }
            } label: {
                Image(systemName: "arrow.up.right.square")
            }
            .iconButton()
            .help("Open on GitHub")
        } content: {
            if let setup = model.envSetup {
                GhSetupCard(status: setup) {
                    Task { await model.loadFocus(repo: repo, swe: app.swe) }
                }
            } else if let blocked = model.envBlocked {
                ErrorCard(message: blocked, actionTitle: "Refresh",
                          actionShortcut: KeyboardShortcut("r", modifiers: .command)) {
                    Task { await model.loadFocus(repo: repo, swe: app.swe) }
                }
            } else {
                HStack(alignment: .bottom, spacing: 16) {
                    // Same layout as the dashboard: the tabs expand for
                    // the underline, the search rides the right end.
                    Tabs(options: RepoTab.allCases.map { ($0, $0.title) },
                         selection: $model.focusTab, style: .underline)
                        .layoutPriority(1)
                    if model.focusLoading.contains(.init(repo: repo, tab: model.focusTab)) {
                        ProgressView().controlSize(.small)
                            .padding(.bottom, 4)
                    }
                    // The tabs take their room first, so the field sits at
                    // its minimum: wide enough for the whole prompt, which
                    // at 100pt read "Filter by t".
                    SearchField(prompt: "Filter by title, branch, tag…", text: $filter)
                        .frame(minWidth: 220, maxWidth: 280)
                        .padding(.bottom, 4)
                }
                if model.focusTab == .pulls, !pullFilters.isEmpty {
                    PullFilterPicker(options: pullFilters,
                                     count: pullCount,
                                     selection: Binding(get: { activePullFilter },
                                                        set: { pullFilter = $0 }))
                }
                if let error = model.pinError ?? model.actionError {
                    ErrorCard(message: error)
                }
                if let error = model.sessionError {
                    ErrorCard(message: error)
                }
                // The callout's row treatment without the callout: the
                // surface bleeds to the card's edges like a dashboard
                // section minus its header.
                Card {
                    RepoSurfaces(repo: repo,
                                 board: model.focusBoard(for: repo),
                                 tab: model.focusTab,
                                 query: query,
                                 keepsPull: { [filter = activePullFilter, login = model.env?.login,
                                               requested = model.reviewRequested(repo)] in
                                     filter.keeps($0, login: login, requested: requested)
                                 },
                                 scrollFetch: true,
                                 openPr: { detail = PrSelection(repo: repo, number: $0) },
                                 openIssue: { issue = $0 })
                    .padding(.horizontal, -Theme.cardPadding)
                    .padding(.bottom, -Theme.cardPadding)
                }
                .clipShape(.rect(cornerRadius: Theme.cardRadius))
            }
        }
        .task(id: Focus(repo: repo, tab: model.focusTab, swe: app.swePath)) {
            await model.loadPinsIfNeeded(swe: app.swe)
            await model.loadSessionHarnesses(swe: app.swe)
            // The page's reason for being: every surface fetches up
            // front, so no tab waits on its own first call.
            await model.loadFocus(repo: repo, swe: app.swe)
            if model.focusTab == .pulls {
                await model.loadReviewRequests(repo: repo, swe: app.swe)
            }
            // Same poll cadence as the dashboard: the visible tab
            // refetches every minute while the page is open.
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(60))
                await model.loadFocusTab(model.focusTab, repo: repo, swe: app.swe)
                if model.focusTab == .pulls {
                    await model.loadReviewRequests(repo: repo, swe: app.swe)
                }
            }
        }
        .quiverSheet(item: $detail) { selection in
            PrDetailSheet(selection: selection)
        }
        .quiverSheet(item: $issue) { selection in
            IssueSheet(selection: selection) { issue = $0 }
        }
        // Reselecting the Repositories sidebar item goes back, the same
        // route the other detail pages take.
        .onChange(of: app.navReselects) { onBack() }
    }

    /// Identity for the visible surface: switching tab or swe re-runs
    /// the open fetch (fresh surfaces skip themselves) and restarts the
    /// poll on the new tab.
    private struct Focus: Hashable {
        var repo: String
        var tab: RepoTab
        var swe: String
    }
}
