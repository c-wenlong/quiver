import SwiftUI

/// P13: every work item the hub knows — the pinned repos' open issues
/// and PRs plus stored links (Linear and Notion too, when configured) —
/// grouped by repo or project, the same answer `GET /v1/tasks` gives.
/// A row tap opens the task's detail; a swipe or the menu's "New agent
/// session" opens the launch sheet, never a blind launch.
struct TasksView: View {
    @Environment(AppModel.self) private var app
    /// The tab's model, shared with the detail the stack pushes.
    let model: TasksModel
    /// The tab pushes the new session's chat once a launch lands.
    var onStarted: (String) -> Void

    @State private var filter: TaskFilter = .all
    @State private var swipes = SwipeCoordinator()
    /// The task a row's New-session swipe or context menu opened the
    /// sheet for.
    @State private var launching: TaskRow?
    @State private var startedKey: String?

    var body: some View {
        let sections = TaskGrouping.sections(of: model.tasks, filter: filter)
        PageScroll {
            if model.loaded, !model.tasks.isEmpty {
                filterBar
            }
            // A failed refresh keeps the rows it already had — the
            // notice says why they're stale rather than replacing them.
            if let error = model.error, !(model.tasks.isEmpty && model.failedRepos.isEmpty) {
                Label { Text(inlineCode: "Couldn't refresh: \(error)") } icon: { Image(systemName: "exclamationmark.triangle") }
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .lineLimit(2)
            }
            if !model.loaded {
                LoadingRow(text: "Loading tasks")
            } else if let error = model.error, model.tasks.isEmpty, model.failedRepos.isEmpty {
                StateCard.unreachable(error) { Task { await model.load(client: app.client, refresh: true) } }
            } else {
                // A blocked source leaves its stored rows on last-known
                // state: the banner says why, each of its rows is marked.
                if let reason = model.githubBanner {
                    blockedBanner(source: "GitHub", message: reason)
                }
                if let blocked = model.linearBlocked {
                    blockedBanner(source: "Linear", message: blocked)
                }
                if let blocked = model.notionBlocked {
                    blockedBanner(source: "Notion", message: blocked)
                }
                if model.tasks.isEmpty && model.failedRepos.isEmpty {
                    if let reason = model.envBlocked ?? model.githubFailed {
                        StateCard(icon: "exclamationmark.lock", title: "Can't reach GitHub",
                                  message: "On \(model.machine ?? app.hubName): \(reason)")
                    } else {
                        StateCard(icon: "checklist", title: emptyTitle, message: emptyMessage)
                    }
                } else if sections.isEmpty && model.failedRepos.isEmpty {
                    StateCard(icon: "checklist", title: emptyTitle, message: emptyMessage)
                } else {
                    ForEach(sections) { section in
                        if let rows = section.rows {
                            GroupLabel(text: section.name, trailing: "\(rows.count)")
                            // A failed repo keeps its stored rows under
                            // its error, in its own group.
                            if let failed = model.failedRepos.first(where: { $0.name == section.name }) {
                                StateCard(icon: "exclamationmark.triangle", title: "Can't list this repo's work",
                                          message: failed.error ?? failed.status ?? "unknown error", tone: .error)
                            }
                            CardList(data: rows) { task in
                                SwipeRow(key: task.id, leading: [.newSession], trailing: [],
                                         coordinator: swipes,
                                         perform: { _ in launching = task }) {
                                    NavigationLink(value: Route.task(task.id)) {
                                        TaskRowView(task: task, stale: model.stale(task))
                                    }
                                    .buttonStyle(RowButtonStyle())
                                    .contentShape(.contextMenuPreview, .rect(cornerRadius: Theme.blockRadius))
                                    .contextMenu {
                                        TaskRowMenu(task: task) { launching = task }
                                    }
                                }
                            }
                        }
                    }
                    // `gh` failed on these repos: their slots stay,
                    // dimmed, with why.
                    ForEach(model.failedRepos.filter { repo in !sections.contains { $0.name == repo.name } }) { repo in
                        GroupLabel(text: repo.name)
                        StateCard(icon: "exclamationmark.triangle", title: "Can't list this repo's work",
                                  message: repo.error ?? repo.status ?? "unknown error", tone: .error)
                    }
                }
            }
        }
        .modifier(CloseSwipesOnScroll(swipes: swipes))
        .refreshable { await model.load(client: app.client, refresh: true) }
        .navigationTitle("Tasks")
        // A plain launch's link can land after the sheet closed.
        .onReceive(NotificationCenter.default.publisher(for: .taskLinked)) { _ in
            Task { await model.load(client: app.client) }
        }
        .task(id: app.hub?.id) { await model.load(client: app.client) }
        // The chat opens after the sheet is gone, so the push isn't
        // lost under the dismiss animation. Any launch — landed or
        // still pending — can change the links and running flags, so
        // the list reloads either way.
        .sheet(item: $launching, onDismiss: {
            if let key = startedKey { onStarted(key) }
            startedKey = nil
            // The hub reads the store on every call; only gh's lists
            // are cached, and a launch doesn't change them.
            Task { await model.load(client: app.client) }
        }) { task in
            TaskSessionSheet(task: task, machineId: model.machineId) { startedKey = $0 }
        }
    }

    private var filterBar: some View {
        WrappingHStack {
            ForEach(TaskFilter.allCases) { f in
                FilterChip(title: f.title, count: TaskGrouping.count(model.tasks, filter: f), selected: filter == f) {
                    filter = f
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 4)
    }

    /// The amber card a blocked source puts over the list: its rows are
    /// what the store last knew, so the page says so instead of
    /// blanking out.
    private func blockedBanner(source: String, message: String) -> some View {
        Card {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.triangle")
                    .font(Theme.font(size: 14)).foregroundStyle(Theme.warningText)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Can't reach \(source)").font(Theme.control).foregroundStyle(Theme.warningText)
                    Text(inlineCode: "On \(model.machine ?? app.hubName): \(message)")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    // `fetched_at` is the earliest reply across sources,
                    // not this one's last sync — so no age is claimed.
                    Text("The \(source) rows are the last synced list.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var emptyTitle: String {
        if !model.tasks.isEmpty {
            switch filter {
            case .all: "No tasks"
            case .running: "No agents running"
            case .prOpen: "No open pull requests"
            case .done: "Nothing finished yet"
            }
        } else {
            "No work items"
        }
    }

    private var emptyMessage: String {
        if !model.tasks.isEmpty {
            return "No task matches this filter right now."
        }
        return "Issues and pull requests from the hub's pinned repos show up here, with the sessions and launches linked to them."
    }
}

/// A task row's context menu: the launch sheet the detail's button
/// opens, then the link actions — never a blind launch. Copy link
/// writes the app's `quiver://issue` deep link for an issue, the
/// source's own URL for anything else.
struct TaskRowMenu: View {
    @Environment(\.openURL) private var openURL
    let task: TaskRow
    let newSession: () -> Void

    var body: some View {
        Button(action: newSession) {
            Label("New agent session", systemImage: "sparkles")
        }
        Divider()
        if let link = task.copyLink {
            Button { UIPasteboard.general.url = link } label: {
                Label("Copy link", systemImage: "doc.on.doc")
            }
        }
        if let url = task.url.flatMap(URL.init) {
            Button { openURL(url) } label: {
                Label(task.openLabel, systemImage: "safari")
            }
            ShareLink(item: url) {
                Label("Share", systemImage: "square.and.arrow.up")
            }
        }
    }
}

/// The kind mark at the row's left, coloured as the pulls and issues
/// pages colour their glyphs: open states green, merged the label ink,
/// closed muted — the derived state, not the source's raw one.
struct TaskGlyph: View {
    let kind: String?
    let state: String?

    var body: some View {
        Image(systemName: symbol)
            .font(Theme.rowIcon)
            .foregroundStyle(color)
            .iconColumn(20)
            .accessibilityLabel(accessibility)
    }

    private var symbol: String {
        switch kind {
        case "pull":
            state == "merged" ? "arrow.triangle.merge" : "arrow.triangle.pull"
        case "card":
            "rectangle.on.rectangle"
        case "page":
            "doc"
        default:
            state == "merged" || state == "closed"
                ? "checkmark.circle" : "exclamationmark.circle"
        }
    }

    private var color: Color {
        switch state {
        case "merged": Theme.textLabel
        case "closed": Theme.textMuted
        default: Theme.accentFill
        }
    }

    private var accessibility: String {
        switch state {
        case "in_progress": "In progress"
        case "pr_open": "PR open"
        case "merged": "Merged"
        case "closed": "Closed"
        default: "Open"
        }
    }
}

/// One task row: the kind glyph, the title, then ref · state · the
/// "● Agent" mark while a linked session or launch is running, and a
/// stale badge when the row's source is the blocked one.
struct TaskRowView: View {
    let task: TaskRow
    var stale = false

    /// The title, or the ref when the store never learned one.
    private var title: String {
        let t = task.title ?? ""
        return t.isEmpty ? (task.refLabel.isEmpty ? "Untitled" : task.refLabel) : t
    }

    var body: some View {
        HStack(spacing: 12) {
            TaskGlyph(kind: task.kind, state: task.state)
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1, larger: 3).truncationMode(.tail)
                WrappingHStack(spacing: 6) {
                    if !task.refLabel.isEmpty {
                        Text(task.refLabel)
                    }
                    Text("· \(task.stateLabel)")
                    if task.agentRunning == true {
                        Text("· ● Agent").foregroundStyle(Theme.accentFill)
                    }
                    let when = RelativeTime.short(task.lastActiveAt)
                    if !when.isEmpty { Text("· \(when)") }
                }
                .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                if stale {
                    HStack(spacing: 4) { Badge("stale", tint: Theme.warningText) }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Image(systemName: "chevron.right").font(Theme.chevron).foregroundStyle(Theme.textMuted)
        }
        .padding(.vertical, Theme.rowVertical)
        .padding(.horizontal, Theme.rowHorizontal)
        .contentShape(.rect)
    }
}
