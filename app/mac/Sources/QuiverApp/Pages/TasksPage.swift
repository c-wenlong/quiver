import QuiverCore
import SwiftUI

/// The Tasks page: the work items this Mac's hub knows its agents are
/// on — the pinned repos' open issues and PRs plus stored links — as
/// `GET /v1/app/tasks` reports them, the same payload the web app's
/// `tasks.js` reads. Laid out like Repositories: one box of groups (a
/// repo or project each, five rows then "Show N more") beside a rail
/// of the scopes by owner or source, where a tap narrows the list to
/// one, unfolded. All / Running / PR open / Done chips filter both. A task's
/// detail shows the state ladder, its linked sessions (each opening
/// the chat), the linked PR, and the two actions the web and iOS
/// clients share: start a new agent on it (issues/resolve or
/// pulls/open, whose launch origin links the session on adoption) and
/// link a session already running.
struct TasksPage: View {
    @Environment(AppModel.self) private var app
    @State private var selectedId: String?
    /// "scope:filter" keys opened past their five-row preview.
    @State private var expanded: Set<String> = []

    private var model: TasksModel { app.tasks }

    /// Rows a group shows before its "Show N more" row, as on
    /// Repositories.
    private static let preview = 5

    var body: some View {
        Group {
            if let selectedId {
                TaskDetailPage(taskId: selectedId, model: model) {
                    self.selectedId = nil
                }
            } else {
                dashboard
            }
        }
        .task { await model.loadUntilHubAnswers(hub: app.hub) }
        .onChange(of: app.navReselects) { selectedId = nil }
    }

    // MARK: - list

    /// The page's own frame rather than `Page`, as on Repositories: the
    /// rail runs the full height beside the title and carries Refresh;
    /// the list scrolls under the chips. Same padding and width cap.
    private var dashboard: some View {
        HStack(alignment: .top, spacing: 24) {
            VStack(alignment: .leading, spacing: 24) {
                PageHeader(title: "Tasks",
                           subtitle: "The issues and pull requests your agents are on.") {
                    if !showsRail {
                        Button("Refresh", systemImage: "arrow.clockwise", action: refresh)
                            .iconButton()
                            .keyboardShortcut("r", modifiers: .command)
                            .help("Fetch again (⌘R)")
                    }
                }
                content
                    .textSelection(.enabled)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if showsRail {
                TaskRail(groups: TaskGrouping.rail(of: model.tasks, failed: model.failedRepos,
                                                   filter: model.filter),
                         total: TaskGrouping.count(model.tasks, filter: model.filter),
                         shown: shownScope,
                         select: { model.shownScope = $0 },
                         refresh: refresh)
                    .frame(width: 240)
            }
        }
        .frame(maxHeight: .infinity, alignment: .top)
        .padding(.horizontal, 40)
        .padding(.vertical, 36)
        .frame(maxWidth: 1280, alignment: .leading)
        .frame(maxWidth: .infinity)
    }

    /// The rail shows once there are tasks to list.
    private var showsRail: Bool { model.loaded && !model.tasks.isEmpty }

    /// The rail's pick while it still names a scope or a failed repo; a
    /// refresh that drops it falls back to every task.
    private var shownScope: String? {
        guard let scope = model.shownScope,
              model.tasks.contains(where: { TaskGrouping.sectionName($0) == scope })
                || model.failedRepos.contains(where: { $0.name == scope })
        else { return nil }
        return scope
    }

    private func refresh() {
        Task { await model.load(hub: app.hub, refresh: true) }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 12) {
            chips
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    states
                }
                .padding(.bottom, 24)
            }
            .scrollContentBackground(.hidden)
        }
        .frame(maxHeight: .infinity, alignment: .top)
    }

    @ViewBuilder private var states: some View {
        if let setup = model.githubSetup {
            // gh missing or signed out on the hub gets the setup card, like
            // the other GitHub pages; an upstream-error probe is not a
            // setup ask.
            GhSetupCard(status: setup, refresh: refresh)
        }
        if let failed = model.githubFailed {
            blockedBanner(source: "GitHub", message: failed)
        }
        if let blocked = model.linearBlocked {
            blockedBanner(source: "Linear", message: blocked)
        }
        if let blocked = model.notionBlocked {
            blockedBanner(source: "Notion", message: blocked)
        }
        if let error = model.error, model.tasks.isEmpty, model.failedRepos.isEmpty {
            // `error` is set only when the hub request itself failed, so
            // it shows even beside a source banner kept from an earlier
            // reply: that banner can't say why this refresh failed.
            ErrorCard(message: error, actionTitle: "Refresh", action: refresh)
        } else if !model.loaded {
            SkeletonRows(rows: 6, shape: .task, label: "Reading the task list…")
        } else {
            if let error = model.error {
                // A failed refresh keeps the rows it already had —
                // the notice says why they're stale rather than
                // replacing them.
                Card {
                    Label("Couldn't refresh: \(error)", systemImage: "exclamationmark.triangle")
                        .font(Theme.caption).foregroundStyle(Theme.warningText)
                }
            }
            listContent
        }
    }

    /// The filter chips, counting within the rail's pick.
    private var chips: some View {
        HStack(spacing: 6) {
            ForEach(TaskFilter.allCases) { filter in
                FilterChip(title: filter.title,
                           count: TaskGrouping.count(model.tasks, filter: filter,
                                                     scope: shownScope),
                           selected: model.filter == filter) {
                    model.filter = filter
                }
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder private var listContent: some View {
        if model.tasks.isEmpty, model.failedRepos.isEmpty {
            if model.envBlocked == nil, model.githubFailed == nil {
                Card {
                    VStack(alignment: .leading, spacing: 10) {
                        Label("No tasks yet.", systemImage: "tray")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        Text("Tasks are the issues and pull requests your agents touch — resolve an issue or open a PR from Repositories, or link a running session to one.")
                            .font(Theme.caption).foregroundStyle(Theme.textMuted)
                            .fixedSize(horizontal: false, vertical: true)
                        Button("Repositories") { app.selection = .repositories }
                            .secondaryButton()
                    }
                }
            }
        } else {
            if model.envBlocked != nil || model.githubFailed != nil
                || model.linearBlocked != nil || model.notionBlocked != nil
            {
                Label("Last known state — a source isn't answering, so this is what the store remembers.",
                      systemImage: "clock.arrow.circlepath")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            let groups = groups
            if groups.isEmpty {
                EmptyCard(text: model.filter.emptyText, icon: "checklist")
            } else {
                box(groups)
            }
        }
    }

    private var groups: [TaskSection] {
        TaskGrouping.sections(of: model.tasks, failed: model.failedRepos,
                              filter: model.filter, scope: shownScope)
    }

    /// One box of rows, as Repositories draws its repos: a plain header
    /// per group, then five rows and a "Show N more" row that opens the
    /// rest.
    private func box(_ groups: [TaskSection]) -> some View {
        VStack(spacing: 0) {
            ForEach(Array(groups.enumerated()), id: \.element.id) { index, section in
                if index > 0 { Divider().overlay(Theme.border) }
                TaskGroupHeader(section: section, isFirst: index == 0)
                Divider().overlay(Theme.rowDivider)
                if let error = section.error {
                    ErrorCard(message: error, framed: false)
                        .padding(EdgeInsets(top: 10, leading: 14, bottom: 10, trailing: 14))
                    if !(section.rows ?? []).isEmpty {
                        Divider().overlay(Theme.rowDivider)
                    }
                }
                rows(section, edge: index == groups.count - 1 ? .cardBottom : .inset)
            }
        }
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .clipShape(.rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius)
            .strokeBorder(Theme.border, lineWidth: Theme.hairline))
    }

    /// A group the rail narrowed to shows every row: the pick is the
    /// ask to see it all.
    @ViewBuilder private func rows(_ section: TaskSection, edge: ExpandRow.Edge) -> some View {
        let rows = section.rows ?? []
        let key = "\(section.name):\(model.filter.rawValue)"
        let open = shownScope != nil || expanded.contains(key)
        let visible = open ? rows : Array(rows.prefix(Self.preview))
        let hasMore = shownScope == nil && rows.count > Self.preview
        ForEach(Array(visible.enumerated()), id: \.element.id) { index, task in
            TaskRowView(task: task, stale: model.stale(task)) {
                selectedId = task.id
            }
            if index < visible.count - 1 || hasMore {
                Divider().overlay(Theme.rowDivider)
            }
        }
        if hasMore {
            ExpandRow(isExpanded: open, hidden: rows.count - Self.preview,
                      help: (expand: "Show every task",
                             collapse: "Show only the first \(Self.preview)"),
                      edge: edge) {
                if open { expanded.remove(key) } else { expanded.insert(key) }
            }
        }
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
                    Text("On \(model.machine ?? "the hub"): \(message)")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("The \(source) rows are the last synced list.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                Spacer(minLength: 8)
            }
        }
    }
}

/// A group's header strip inside the box, as Repositories heads a repo:
/// `owner /` muted then the name (`Linear /` and the project for
/// another source), and on hover the link out to GitHub. The rail
/// carries the counts.
private struct TaskGroupHeader: View {
    @Environment(\.openURL) private var openURL
    var section: TaskSection
    /// The top header takes the box's rounded top corners; the rest are
    /// plain strips across it.
    var isFirst: Bool
    @State private var hovering = false

    private var parts: (prefix: String?, name: String) {
        if let repo = section.repo {
            let split = repo.split(separator: "/", maxSplits: 1).map(String.init)
            return split.count == 2 ? (split[0], split[1]) : (nil, repo)
        }
        let source = section.rows?.first?.source.map { $0.prefix(1).uppercased() + $0.dropFirst() }
        return (source, section.name)
    }

    var body: some View {
        HStack(spacing: 6) {
            if let prefix = parts.prefix {
                Text("\(prefix) /").foregroundStyle(Theme.textMuted)
            }
            Text(parts.name).foregroundStyle(Theme.textPrimary)
            Spacer()
            if let repo = section.repo {
                Button {
                    if let url = URL(string: "https://github.com/\(repo)"), url.isWebURL {
                        openURL(url)
                    }
                } label: {
                    Image(systemName: "arrow.up.right.square")
                }
                .plainButton()
                .help("Open \(repo) on GitHub")
                .font(Theme.font(size: 12)).foregroundStyle(Theme.textMuted)
                .opacity(hovering ? 1 : 0)
            }
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

// MARK: - rail

/// The page's right-hand rail, after the Repositories one: Refresh,
/// "All tasks", then the scopes under their owner or source by name,
/// each with
/// a green dot while an agent is on one of its tasks and the chip
/// filter's count. Scopes with nothing under the chip fold under "N
/// quiet". A tap narrows the list to that scope.
private struct TaskRail: View {
    var groups: [TaskRailGroup]
    var total: Int
    var shown: String?
    var select: (String?) -> Void
    var refresh: () -> Void
    /// Groups whose quiet scopes are unfolded.
    @State private var openQuiet: Set<String> = []

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 4) {
                Text("Sources")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .padding(.leading, 8)
                Spacer(minLength: 0)
                RailIconButton(symbol: "arrow.clockwise",
                               help: "Fetch again (⌘R)", action: refresh)
                    .keyboardShortcut("r", modifiers: .command)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    RailRow(selected: shown == nil, select: { select(nil) }) {
                        Image(systemName: "square.stack.3d.up")
                            .font(Theme.font(size: 12))
                        Text("All tasks").font(Theme.nav)
                        Spacer(minLength: 6)
                        Text("\(total)").font(Theme.caption.monospacedDigit())
                            .foregroundStyle(Theme.textMuted)
                    }
                    ForEach(groups) { group in
                        let active = group.scopes.filter { $0.count > 0 || $0.name == shown }
                        let quiet = group.scopes.filter { $0.count == 0 && $0.name != shown }
                        Text(group.title)
                            .font(Theme.caption).foregroundStyle(Theme.textMuted)
                            .lineLimit(1).truncationMode(.tail)
                            .padding(.horizontal, 8)
                            .padding(.top, 14).padding(.bottom, 4)
                        ForEach(active) { row($0) }
                        if !quiet.isEmpty {
                            let open = openQuiet.contains(group.id)
                            Button {
                                if open { openQuiet.remove(group.id) } else { openQuiet.insert(group.id) }
                            } label: {
                                HStack(spacing: 6) {
                                    Image(systemName: "chevron.right")
                                        .font(Theme.font(size: 9, weight: .semibold))
                                        .rotationEffect(open ? .degrees(90) : .zero)
                                    Text("\(quiet.count) quiet").font(Theme.nav)
                                    Spacer()
                                }
                                .foregroundStyle(Theme.textMuted)
                                .padding(.horizontal, 8).padding(.vertical, 5)
                                .contentShape(Rectangle())
                            }
                            .plainButton()
                            .help(open ? "Hide sources with nothing under this filter"
                                       : "Show sources with nothing under this filter")
                            if open { ForEach(quiet) { row($0) } }
                        }
                    }
                }
            }
            .scrollContentBackground(.hidden)
        }
        .padding(8)
        .background(Theme.panel, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius)
            .strokeBorder(Theme.border, lineWidth: Theme.hairline))
    }

    private func row(_ scope: TaskRailScope) -> some View {
        RailRow(selected: shown == scope.name, select: { select(scope.name) }) {
            Circle().fill(scope.running ? Theme.success : Theme.borderStrong)
                .frame(width: Theme.statusDot, height: Theme.statusDot)
            Text(scope.label)
                .font(Theme.nav)
                .lineLimit(1).truncationMode(.middle)
            Spacer(minLength: 6)
            if scope.count > 0 {
                Text("\(scope.count)").font(Theme.caption.monospacedDigit())
                    .foregroundStyle(Theme.textMuted)
            }
        }
        .help(scope.running ? "\(scope.name) · an agent is on it" : scope.name)
    }
}

/// A filter chip with its count: All / Running / PR open / Done.
private struct FilterChip: View {
    var title: String
    var count: Int
    var selected: Bool
    var action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Text(title)
                Text("\(count)").monospacedDigit()
                    .foregroundStyle(selected ? Theme.textSecondary : Theme.textMuted)
            }
            .font(Theme.control)
            .foregroundStyle(selected || hovering ? Theme.textPrimary : Theme.textTab)
            .padding(.horizontal, 12)
            .frame(height: Theme.controlHeight)
            .background(selected ? Theme.segmentSelected : hovering ? Theme.tabHover : .clear,
                        in: .rect(cornerRadius: Theme.controlRadius))
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        .plainButton(cornerRadius: Theme.controlRadius)
        .onHover { hovering = $0 }
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// The kind mark at the row's left, coloured as the pulls and issues
/// pages colour their glyphs: open green, merged GitHub purple, closed
/// red for pulls and muted elsewhere.
private struct TaskGlyph: View {
    var task: HubTaskRow

    var body: some View {
        mark
            .frame(width: 20)
            .accessibilityLabel(task.stateLabel)
    }

    @ViewBuilder private var mark: some View {
        if task.kind == "pull" {
            if task.pr?.draft == true, !task.isDone {
                OcticonView(icon: .pullRequestDraft, size: 15).foregroundStyle(Theme.textMuted)
            } else if task.state == "merged" {
                OcticonView(icon: .merge, size: 15).foregroundStyle(ghPurple)
            } else if task.state == "closed" {
                OcticonView(icon: .pullRequestClosed, size: 15).foregroundStyle(Theme.danger)
            } else {
                OcticonView(icon: .pullRequest, size: 15).foregroundStyle(Theme.success)
            }
        } else {
            switch task.kind {
            case "card":
                Image(systemName: "rectangle.on.rectangle").font(Theme.font(size: 14))
                    .foregroundStyle(color)
            case "page":
                Image(systemName: "doc").font(Theme.font(size: 14))
                    .foregroundStyle(color)
            default:
                Image(systemName: task.isDone ? "checkmark.circle" : "exclamationmark.circle")
                    .font(Theme.font(size: 15))
                    .foregroundStyle(color)
            }
        }
    }

    private var color: Color {
        switch task.state {
        case "merged": ghPurple
        case "closed": Theme.textMuted
        default: Theme.success
        }
    }
}

/// One task row: the kind glyph, the title and its PR badge, ref ·
/// state · age, then the "Agent running" mark and session count.
private struct TaskRowView: View {
    var task: HubTaskRow
    var stale = false
    var open: () -> Void
    @State private var hovering = false

    private var title: String {
        let t = task.title ?? ""
        return t.isEmpty ? (task.refLabel.isEmpty ? "Untitled" : task.refLabel) : t
    }

    var body: some View {
        Button(action: open) {
            HStack(alignment: .top, spacing: 12) {
                TaskGlyph(task: task)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 8) {
                        Text(title)
                            .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                            .lineLimit(1).truncationMode(.tail)
                        if let pr = task.pr, let number = pr.number {
                            Badge("PR #\(number)")
                                .hoverTip(pr.title ?? "Pull request #\(number)")
                        }
                        if stale { Badge("stale") }
                    }
                    Text(subtitle)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 12)
                if task.agentRunning == true {
                    Label("Agent running", systemImage: "circle.fill")
                        .font(Theme.caption).foregroundStyle(Theme.success)
                        .symbolRenderingMode(.hierarchical)
                }
                let sessions = (task.sessions ?? []).count
                if sessions > 0 { Badge("\(sessions)") }
                Image(systemName: "chevron.right")
                    .font(Theme.font(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.textMuted)
            }
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

    private var subtitle: String {
        ([task.refLabel, task.stateLabel]
            .filter { !$0.isEmpty }
            + [task.lastActiveDate.map { UsageFormat.ago($0, now: .now) }].compactMap { $0 })
            .joined(separator: " · ")
    }
}
