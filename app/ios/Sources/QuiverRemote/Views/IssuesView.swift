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

    @State private var model = IssuesModel()
    @State private var filter: IssueFilter = .all
    @State private var swipes = SwipeCoordinator()
    /// The issue a row's Resolve swipe or context menu opened the sheet for.
    @State private var resolving: IssueRef?
    @State private var startedKey: String?

    var body: some View {
        let sections = IssueGrouping.sections(of: model.repos, filter: filter)
        PageScroll {
            if model.loaded, model.envBlocked == nil, !model.repos.isEmpty {
                filterBar
            }
            // A failed refresh keeps the rows it already had — the
            // notice says why they're stale rather than replacing them.
            if let error = model.error, !model.repos.isEmpty {
                Label("Couldn't refresh: \(error)", systemImage: "exclamationmark.triangle")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .lineLimit(2)
            }
            if !model.loaded {
                LoadingRow(text: "Loading issues")
            } else if let error = model.error, model.repos.isEmpty {
                StateCard(icon: "wifi.exclamationmark", title: "Can't reach the hub", message: error, tone: .error,
                          actionTitle: "Try again") { Task { await model.load(client: app.client, refresh: true) } }
            } else if let blocked = model.envBlocked {
                StateCard(icon: "exclamationmark.lock", title: "Can't reach GitHub",
                          message: "On \(model.machine ?? app.hubName): \(blocked)")
            } else if model.repos.isEmpty {
                StateCard(icon: "exclamationmark.circle", title: "No pinned repos",
                          message: "Pin repositories on the hub with `swe gh repo add` and their open issues show up here.")
            } else if sections.isEmpty {
                StateCard(icon: "exclamationmark.circle", title: filter == .all ? "No open issues" : "Nothing here",
                          message: filter == .all ? "Open an issue on GitHub and it shows up here."
                                                : "No open issue matches this filter right now.")
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
                                    IssueRowMenu(issue: issue) {
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
        .refreshable { await model.load(client: app.client, refresh: true) }
        .navigationTitle("Issues")
        .task(id: app.hub?.id) { await model.load(client: app.client) }
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
        HStack(spacing: 6) {
            ForEach(IssueFilter.allCases) { f in
                FilterChip(title: f.title, count: IssueGrouping.count(model.repos, filter: f), selected: filter == f) {
                    filter = f
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.top, 4)
    }
}

/// The repo+number pair a row's Resolve opens the sheet for.
private struct IssueRef: Identifiable {
    let repo: String
    let number: Int
    var id: String { "\(repo)#\(number)" }
}

/// An issue row's context menu: the same resolve sheet the detail's
/// button opens, then the link actions — never a blind launch.
struct IssueRowMenu: View {
    @Environment(\.openURL) private var openURL
    let issue: IssueRow
    let resolve: () -> Void

    var body: some View {
        Button(action: resolve) {
            Label("Resolve with agent", systemImage: "sparkles")
        }
        if let url = issue.url.flatMap(URL.init) {
            Divider()
            Button { openURL(url) } label: {
                Label("Open on GitHub", systemImage: "safari")
            }
            Button { UIPasteboard.general.string = issue.url ?? "" } label: {
                Label("Copy link", systemImage: "doc.on.doc")
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
            .font(.system(size: 15))
            .foregroundStyle(state == "open" ? Theme.accentFill : Theme.textMuted)
            .frame(width: 20)
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
                    .lineLimit(1).truncationMode(.tail)
                HStack(spacing: 6) {
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
                    HStack(spacing: 4) {
                        ForEach(badges, id: \.self) { Badge($0) }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Image(systemName: "chevron.right").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textMuted)
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
