import SwiftUI

/// P12: every open issue across the hub's pinned repos, grouped by
/// repo — the same answer `GET /v1/issues` gives, so the phone and the
/// web app's Repositories page agree. A row tap opens the detail; a
/// swipe or long press offers Resolve, which opens the same sheet the
/// detail's button does — never a blind launch.
struct IssuesView: View {
    @Environment(AppModel.self) private var app
    /// The tab pushes the new session's chat once a launch lands.
    var onStarted: (String) -> Void
    /// Owned by the tab bar, so switching the GitHub tab between Issues
    /// and Pull requests keeps what each already loaded.
    let model: IssuesModel

    /// Kept on the model, so it survives the GitHub tab's switch to
    /// Pull requests and back.
    private var filter: IssueFilter {
        get { model.filter }
        nonmutating set { model.filter = newValue }
    }
    @State private var swipes = SwipeCoordinator()
    /// The issue a row's Resolve swipe or context menu opened the sheet for.
    @State private var resolving: IssueRef?
    @State private var startedKey: String?

    var body: some View {
        let closed = filter == .closed
        let listed = closed ? model.closedRepos : model.repos
        // The failure of the list on screen; the other chip's is its own.
        let listError = closed ? model.closedError : model.error
        let sections = IssueGrouping.sections(of: listed, filter: filter)
        PageScroll {
            if model.loaded, model.envBlocked == nil,
               !model.repos.isEmpty || !model.closedRepos.isEmpty {
                filterBar
            }
            // A failed refresh keeps the rows it already had — the
            // notice says why they're stale rather than replacing them.
            if let error = listError, !listed.isEmpty {
                Label { Text(inlineCode: "Couldn't refresh: \(error)") } icon: { Image(systemName: "exclamationmark.triangle") }
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .lineLimit(2)
            }
            if !model.loaded {
                LoadingRow(text: "Loading issues")
            } else if closed, model.closedLoaded, model.closedRepos.isEmpty,
                      let error = model.closedError {
                // A Closed fetch that failed with an empty closed list
                // must say so — otherwise it reads as "No closed issues".
                StateCard(icon: "wifi.exclamationmark", title: "Can't load closed issues",
                          message: error, tone: .error,
                          actionTitle: "Try again") { Task { await model.loadClosed(client: app.client, refresh: true) } }
            } else if let error = listError, model.repos.isEmpty, model.closedRepos.isEmpty {
                StateCard.unreachable(error) {
                    Task {
                        if closed {
                            await model.loadClosed(client: app.client, refresh: true)
                        } else {
                            await model.load(client: app.client, refresh: true)
                        }
                    }
                }
            } else if let blocked = model.envBlocked {
                StateCard(icon: "exclamationmark.lock", title: "Can't reach GitHub",
                          message: "On \(model.machine ?? app.hubName): \(blocked)")
            } else if model.repos.isEmpty && model.closedRepos.isEmpty {
                StateCard(icon: "exclamationmark.circle", title: "No pinned repos",
                          message: "Pin repositories on the hub with `swe gh repo add` and their issues show up here.")
            } else if closed && !model.closedLoaded {
                LoadingRow(text: "Loading closed issues")
            } else if sections.isEmpty {
                StateCard(icon: "exclamationmark.circle", title: emptyTitle, message: emptyMessage)
            } else {
                ForEach(sections) { section in
                    if let rows = section.rows {
                        GroupLabel(text: section.name, trailing: "\(rows.count)")
                        CardList(data: rows) { issue in
                            SwipeRow(key: "\(section.name)#\(issue.number)", leading: [.resolve], trailing: [],
                                     coordinator: swipes,
                                     perform: { _ in resolving = IssueRef(repo: section.name, number: issue.number) }) {
                                NavigationLink(value: Route.issue(repo: section.name, number: issue.number)) {
                                    IssueRowView(issue: issue)
                                }
                                .buttonStyle(RowButtonStyle())
                                .contentShape(.contextMenuPreview, .rect(cornerRadius: Theme.blockRadius))
                                .contextMenu {
                                    IssueRowMenu(issue: issue, repo: section.name) {
                                        resolving = IssueRef(repo: section.name, number: issue.number)
                                    }
                                }
                            }
                        }
                    } else {
                        // `gh` failed on this repo: its slot stays,
                        // dimmed, with why.
                        GroupLabel(text: section.name)
                        StateCard(icon: "exclamationmark.triangle", title: "Can't list this repo's issues",
                                  message: section.error, tone: .error)
                    }
                }
            }
        }
        .modifier(CloseSwipesOnScroll(swipes: swipes))
        .refreshable {
            if filter == .closed {
                await model.loadClosed(client: app.client, refresh: true)
            } else {
                await model.load(client: app.client, refresh: true)
            }
        }
        .navigationTitle("Issues")
        .task(id: app.hub?.id) { await model.load(client: app.client) }
        // The closed list arrives on the chip's first pick.
        .onChange(of: filter) { _, new in
            if new == .closed, !model.closedLoaded {
                Task { await model.loadClosed(client: app.client) }
            }
        }
        // The chat opens after the sheet is gone, so the push isn't
        // lost under the dismiss animation.
        .sheet(item: $resolving, onDismiss: {
            if let key = startedKey { onStarted(key) }
            startedKey = nil
        }) { ref in
            ResolveIssueSheet(repo: ref.repo, number: ref.number, detail: nil,
                              machineId: model.machineId) { startedKey = $0 }
        }
    }

    private var filterBar: some View {
        WrappingHStack {
            ForEach(IssueFilter.allCases) { f in
                FilterChip(title: f.title, count: count(f), selected: filter == f) {
                    filter = f
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 4)
    }

    /// The chip's count: the closed list isn't fetched until its chip
    /// is picked, so until then there's no number to draw.
    private func count(_ f: IssueFilter) -> Int? {
        if f == .closed {
            return model.closedLoaded
                ? IssueGrouping.count(model.closedRepos, filter: f) : nil
        }
        return IssueGrouping.count(model.repos, filter: f)
    }

    private var emptyTitle: String {
        switch filter {
        case .all: "No open issues"
        case .mine: "Nothing here"
        case .closed: "No closed issues"
        }
    }

    private var emptyMessage: String {
        switch filter {
        case .all: "Open an issue on GitHub and it shows up here."
        case .mine: "No open issue matches this filter right now."
        case .closed: "Issues closed in your pinned repos show up here."
        }
    }
}

/// The repo+number pair a row's Resolve opens the sheet for.
private struct IssueRef: Identifiable {
    let repo: String
    let number: Int
    var id: String { "\(repo)#\(number)" }
}

/// An issue row's context menu: the same resolve sheet the detail's
/// button opens, then the link actions — never a blind launch. Copy
/// link writes the app's own `quiver://issue` deep link; the share
/// sheet wants the GitHub one.
struct IssueRowMenu: View {
    @Environment(\.openURL) private var openURL
    let issue: IssueRow
    /// `owner/repo`, the section the row sits in — the deep link's path.
    let repo: String
    let resolve: () -> Void

    var body: some View {
        Button(action: resolve) {
            Label("Resolve with agent", systemImage: "sparkles")
        }
        Divider()
        Button {
            UIPasteboard.general.url = IssueLink(repo: repo, number: issue.number).url
        } label: {
            Label("Copy link", systemImage: "doc.on.doc")
        }
        if let url = issue.url.flatMap(URL.init) {
            Button { openURL(url) } label: {
                Label("Open on GitHub", systemImage: "safari")
            }
            ShareLink(item: url) {
                Label("Share on GitHub", systemImage: "square.and.arrow.up")
            }
        }
    }
}

/// The state mark at the row's left, as the Mac app's issue row draws
/// it: open green, closed muted.
struct IssueGlyph: View {
    let state: String?

    var body: some View {
        Image(systemName: state == "open" ? "exclamationmark.circle" : "checkmark.circle")
            .font(Theme.rowIcon)
            .foregroundStyle(state == "open" ? Theme.accentFill : Theme.textMuted)
            .iconColumn(20)
            .accessibilityLabel(state == "open" ? "Open" : "Closed")
    }
}

extension IssueRow {
    /// `updated_at` parsed for `RelativeTime`.
    var updatedDate: Date? { GitHubTime.date(updatedAt) }
}

struct IssueRowView: View {
    let issue: IssueRow

    var body: some View {
        HStack(spacing: 12) {
            IssueGlyph(state: issue.state)
            VStack(alignment: .leading, spacing: 3) {
                Text(issue.title ?? "Untitled")
                    .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1, larger: 3).truncationMode(.tail)
                WrappingHStack(spacing: 6) {
                    Text("#\(issue.number)")
                    if let author = issue.author, !author.isEmpty, author != "-" {
                        Text("· \(author)").lineLimit(1)
                    }
                    if let comments = issue.comments, comments > 0 {
                        Text("· \(comments) comment\(comments == 1 ? "" : "s")")
                    }
                    let when = RelativeTime.short(issue.updatedDate?.timeIntervalSince1970)
                    if !when.isEmpty { Text("· \(when)") }
                }
                .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                if let badges = badges, !badges.isEmpty {
                    WrappingHStack(spacing: 4) {
                        ForEach(Array(badges.enumerated()), id: \.offset) { Badge($0.element) }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Image(systemName: "chevron.right").font(Theme.chevron).foregroundStyle(Theme.textMuted)
        }
        .padding(.vertical, Theme.rowVertical)
        .padding(.horizontal, Theme.rowHorizontal)
        .contentShape(.rect)
    }

    /// "agent running" and "mine" first, then up to three labels and
    /// two assignees — the rest stay on the detail page.
    private var badges: [String]? {
        var out: [String] = []
        if issue.agentRunning == true { out.append("agent running") }
        if issue.mine == true { out.append("mine") }
        out.append(contentsOf: (issue.labels ?? []).prefix(3))
        out.append(contentsOf: (issue.assignees ?? []).prefix(2).map { "@\($0)" })
        return out.isEmpty ? nil : out
    }
}
