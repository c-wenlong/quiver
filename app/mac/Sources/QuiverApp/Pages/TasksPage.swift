import QuiverCore
import SwiftUI

/// The Tasks page: the work items this Mac's hub knows its agents are
/// on — the pinned repos' open issues and PRs plus stored links — as
/// `GET /v1/app/tasks` reports them, the same payload the web app's
/// `tasks.js` reads. The list groups by repository under All / Running
/// / PR open / Done chips; a task's detail shows the state ladder, its
/// linked sessions (each opening the chat), the linked PR, and the two
/// actions the web and iOS clients share: start a new agent on it
/// (issues/resolve or pulls/open, whose launch origin links the session
/// on adoption) and link a session already running.
struct TasksPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    @State private var selectedId: String?

    private var model: TasksModel { app.tasks }

    var body: some View {
        Group {
            if let selectedId {
                TaskDetailPage(taskId: selectedId, model: model) {
                    self.selectedId = nil
                }
            } else {
                list
            }
        }
        .task { await model.load(hub: app.hub) }
        .onChange(of: app.navReselects) { selectedId = nil }
    }

    // MARK: - list

    private var list: some View {
        Page(title: "Tasks",
             subtitle: "The issues and pull requests your agents are on.") {
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await model.load(hub: app.hub, refresh: true) }
            }
            .iconButton()
            .keyboardShortcut("r", modifiers: .command)
            .help("Fetch again (⌘R)")
        } content: {
            chips
            if let setup = model.githubSetup {
                GhSetupCard(status: setup) {
                    Task { await model.load(hub: app.hub, refresh: true) }
                }
            }
            if let failed = model.githubFailed {
                blockedBanner(source: "GitHub", message: failed)
            }
            if let blocked = model.linearBlocked {
                blockedBanner(source: "Linear", message: blocked)
            }
            if let error = model.error, model.tasks.isEmpty, model.failedRepos.isEmpty {
                if model.githubSetup == nil, model.linearBlocked == nil,
                   model.githubFailed == nil
                {
                    ErrorCard(message: error, actionTitle: "Refresh") {
                        Task { await model.load(hub: app.hub, refresh: true) }
                    }
                }
            } else if !model.loaded {
                LoadingCard(text: "Reading the task list…")
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
    }

    /// gh missing or signed out on the hub gets the setup card, like the
    /// other GitHub pages; an upstream-error probe is not a setup ask.
    private var chips: some View {
        HStack(spacing: 6) {
            ForEach(TaskFilter.allCases) { filter in
                FilterChip(title: filter.title,
                           count: TaskGrouping.count(model.tasks, filter: filter),
                           selected: model.filter == filter) {
                    model.filter = filter
                }
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder private var listContent: some View {
        let sections = TaskGrouping.sections(of: model.tasks, filter: model.filter)
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
                || model.linearBlocked != nil
            {
                Label("Last known state — a source isn't answering, so this is what the store remembers.",
                      systemImage: "clock.arrow.circlepath")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            if sections.isEmpty, model.failedRepos.isEmpty {
                EmptyCard(text: model.filter.emptyText, icon: "checklist")
            } else {
                ForEach(sections) { section in
                    groupSection(section)
                }
                ForEach(model.failedRepos) { repo in
                    failedSection(repo)
                }
            }
        }
    }

    /// A repo (or project) and its task rows under it. The header links
    /// out to the repo on GitHub and in to Repositories, like the web
    /// page's group heads.
    private func groupSection(_ section: TaskSection) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                groupTitle(section)
                Text("\(section.rows?.count ?? 0)")
                    .font(Theme.caption).monospacedDigit()
                    .foregroundStyle(Theme.textMuted)
                Spacer(minLength: 8)
                if let repo = section.repo {
                    Button {
                        if let url = URL(string: "https://github.com/\(repo)"), url.isWebURL {
                            openURL(url)
                        }
                    } label: {
                        Image(systemName: "arrow.up.right.square")
                            .font(Theme.font(size: 13))
                            .foregroundStyle(Theme.textSecondary)
                            .frame(width: 24, height: 20)
                            .contentShape(.rect)
                    }
                    .plainButton()
                    .hoverTip("Open \(repo) on GitHub")
                }
            }
            .padding(.horizontal, 4)
            Card {
                VStack(spacing: 0) {
                    ForEach(Array((section.rows ?? []).enumerated()), id: \.element.id) { index, task in
                        TaskRowView(task: task, stale: model.stale(task)) {
                            selectedId = task.id
                        }
                        if index < (section.rows?.count ?? 0) - 1 {
                            Divider().overlay(Theme.rowDivider)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder private func groupTitle(_ section: TaskSection) -> some View {
        if let repo = section.repo {
            let parts = repo.split(separator: "/", maxSplits: 1)
            HStack(spacing: 4) {
                if parts.count == 2 {
                    Text("\(parts[0]) /").font(Theme.caption).foregroundStyle(Theme.textMuted)
                    Text(String(parts[1])).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                } else {
                    Text(repo).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                }
            }
        } else {
            Text(section.name).font(Theme.rowTitle).foregroundStyle(Theme.textMuted)
        }
    }

    /// `gh` failed on this repo: its slot stays with why.
    private func failedSection(_ repo: HubTaskRepoFailure) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Text(repo.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                Spacer(minLength: 8)
            }
            .padding(.horizontal, 4)
            ErrorCard(message: repo.error ?? repo.status ?? "Can't list this repo's work.")
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
            .padding(.horizontal, 6)
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
