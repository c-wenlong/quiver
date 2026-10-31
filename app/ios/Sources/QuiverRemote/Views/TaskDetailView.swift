import SwiftUI

/// P13 detail: one task's ref and title, the state stepper, the sessions
/// and PR linked to it, then New agent session and Open on GitHub. The
/// page reads the row off the list's `GET /v1/tasks` reply — sessions
/// join to `/v1/sessions` rows client-side, and the GitHub cards push
/// the issue and pull detail pages rather than re-fetching.
struct TaskDetailView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    let taskId: String
    /// The list's model: the row stays live as it refreshes.
    let model: TasksModel
    /// Pushes a route on the Tasks stack (a session's chat, the linked
    /// issue or pull request).
    var onRoute: (Route) -> Void

    @State private var launching: TaskRow?
    @State private var linking: TaskRow?
    @State private var startedKey: String?
    @State private var linkError: String?

    private var task: TaskRow? { model.task(taskId) }

    var body: some View {
        PageScroll {
            if let task {
                header(task)
                actions(task)
                sessionsSection(task)
                githubSection(task)
            } else if !model.loaded {
                LoadingRow(text: "Loading task")
            } else {
                StateCard(icon: "checklist", title: "This task is gone",
                          message: "It left the sources' lists and nothing links to it any more.")
            }
        }
        .refreshable { await model.load(client: app.client, refresh: true) }
        .navigationTitle(task.map { $0.refLabel.isEmpty ? "Task" : $0.refLabel } ?? "Task")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let task {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button { linking = task } label: {
                            Label("Link a session", systemImage: "link")
                        }
                        if let link = task.copyLink {
                            Button { UIPasteboard.general.url = link } label: {
                                Label("Copy link", systemImage: "doc.on.doc")
                            }
                        }
                        if let url = task.url.flatMap(URL.init) {
                            ShareLink(item: url) {
                                Label("Share", systemImage: "square.and.arrow.up")
                            }
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
        }
        .task { if !model.loaded { await model.load(client: app.client) } }
        // The chat opens after the sheet is gone, so the push isn't
        // lost under the dismiss animation. Any launch — landed or
        // still pending — can change the links and running flags, so
        // the task reloads either way.
        .sheet(item: $launching, onDismiss: {
            if let key = startedKey { onRoute(.chat(key)) }
            startedKey = nil
            Task { await model.load(client: app.client) }
        }) { task in
            TaskSessionSheet(task: task, machineId: model.machineId) { startedKey = $0 }
        }
        .sheet(item: $linking) { task in
            LinkSessionSheet(task: task) {
                Task { await model.load(client: app.client) }
            }
        }
    }

    // MARK: Sections

    private func header(_ task: TaskRow) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(task.sourceLine)
                .font(Theme.caption).foregroundStyle(Theme.textMuted).lineLimit(1)
            Text(task.title.flatMap { $0.isEmpty ? nil : $0 } ?? task.refLabel)
                .font(Theme.sectionTitle).foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            stepper(TaskStepper.steps(for: task), live: task.state != "closed")
            if model.stale(task) {
                Label("Last-known state — the source can't be reached", systemImage: "exclamationmark.triangle")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// The open → in progress → PR open → merged ladder; a pull's own
    /// lifecycle skips the PR rung, `closed` ends it muted.
    private func stepper(_ steps: [TaskStepper.Step], live: Bool) -> some View {
        let accent = live ? Theme.accentFill : Theme.textMuted
        return HStack(alignment: .top, spacing: 0) {
            ForEach(Array(steps.enumerated()), id: \.offset) { i, step in
                VStack(spacing: 5) {
                    HStack(spacing: 0) {
                        Rectangle()
                            .fill(i > 0 && step.reached ? accent : Theme.border)
                            .frame(height: 1.5)
                            .opacity(i > 0 ? 1 : 0)
                        Circle()
                            .fill(step.reached ? accent : Theme.card)
                            .overlay(Circle().strokeBorder(step.reached ? accent : Theme.borderStrong, lineWidth: 1.5))
                            .frame(width: 10, height: 10)
                            .overlay {
                                if step.current {
                                    Circle().strokeBorder(accent, lineWidth: 1.5)
                                        .frame(width: 18, height: 18)
                                }
                            }
                        Rectangle()
                            .fill(i < steps.count - 1 && steps[i + 1].reached ? accent : Theme.border)
                            .frame(height: 1.5)
                            .opacity(i < steps.count - 1 ? 1 : 0)
                    }
                    Text(step.title)
                        .font(Theme.small)
                        .foregroundStyle(step.current ? Theme.textPrimary : Theme.textMuted)
                        .lineLimit(1, larger: 2)
                        .centredWhenWrapped()
                        .minimumScaleFactor(0.8)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .padding(.top, 4)
    }

    private func actions(_ task: TaskRow) -> some View {
        VStack(spacing: 8) {
            Button { launching = task } label: {
                Label("New agent session", systemImage: "sparkles")
            }
            .primaryButton()
            if let url = task.url.flatMap(URL.init) {
                Button { openURL(url) } label: {
                    Label(task.openLabel, systemImage: "safari")
                }
                .secondaryButton()
            }
        }
        .padding(.top, 4)
    }

    /// The sessions linked to the task, joined to the catalog's rows:
    /// a tap pushes the chat, a long press offers Unlink. A key the
    /// catalog no longer holds stays listed — the link is real even
    /// when the row is gone — dimmed, with Unlink still offered.
    @ViewBuilder private func sessionsSection(_ task: TaskRow) -> some View {
        let keys = task.sessions ?? []
        if !keys.isEmpty || task.linked == true {
            GroupLabel(text: "Sessions", trailing: keys.isEmpty ? nil : "\(keys.count)")
            if keys.isEmpty {
                StateCard(icon: "link", title: "No sessions linked",
                          message: "A launch is still preparing, or its session ended.")
            } else {
                CardList(data: keys.map(TaskSessionKey.init)) { slot in
                    if let row = app.session(slot.key) {
                        NavigationLink(value: Route.chat(row.key)) {
                            SessionRowView(row: row)
                        }
                        .buttonStyle(RowButtonStyle())
                        .contextMenu {
                            Button(role: .destructive) { unlink(slot.key) } label: {
                                Label("Unlink from this task", systemImage: "link.badge.minus")
                            }
                        }
                    } else {
                        HStack(spacing: 12) {
                            Image(systemName: "questionmark.circle")
                                .font(Theme.rowIcon).foregroundStyle(Theme.textMuted).iconColumn(20)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(shortKey(slot.key)).font(Theme.rowTitle).foregroundStyle(Theme.textMuted).lineLimit(1)
                                Text("No longer in sessions").font(Theme.caption).foregroundStyle(Theme.textMuted).lineLimit(1)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .padding(.vertical, Theme.rowVertical)
                        .padding(.horizontal, Theme.rowHorizontal)
                        .contextMenu {
                            Button(role: .destructive) { unlink(slot.key) } label: {
                                Label("Unlink from this task", systemImage: "link.badge.minus")
                            }
                        }
                    }
                }
            }
            if let linkError {
                Text(linkError).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
            }
        }
    }

    /// The GitHub card: the issue or pull the task is, pushed onto the
    /// same detail pages the Issues and Pull requests tabs use — plus
    /// the PR an issue's `quiver/issue-<n>` branch produced.
    @ViewBuilder private func githubSection(_ task: TaskRow) -> some View {
        if task.source == "github", let repo = task.repo, let number = task.number {
            GroupLabel(text: "GitHub")
            CardList(data: githubLinks(task, repo: repo, number: number)) { link in
                NavigationLink(value: link.route) {
                    HStack(spacing: 12) {
                        link.glyph
                        VStack(alignment: .leading, spacing: 3) {
                            Text(link.title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                            if let detail = link.detail {
                                Text(detail).font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        Image(systemName: "chevron.right").font(Theme.chevron).foregroundStyle(Theme.textMuted)
                    }
                    .padding(.vertical, Theme.rowVertical)
                    .padding(.horizontal, Theme.rowHorizontal)
                    .contentShape(.rect)
                }
                .buttonStyle(RowButtonStyle())
            }
        }
    }

    /// The detail routes the GitHub card pushes: the task's own issue
    /// or pull, then the branch's PR when it isn't the task itself.
    private func githubLinks(_ task: TaskRow, repo: String, number: Int) -> [GitHubLink] {
        var out: [GitHubLink] = []
        switch task.kind {
        case "pull":
            out.append(GitHubLink(title: "\(repo)#\(number)",
                                  detail: task.stateLabel,
                                  route: .pull(repo: repo, number: number),
                                  pull: true, state: task.state, draft: task.pr?.draft == true))
        default:
            out.append(GitHubLink(title: "\(repo)#\(number)",
                                  detail: "Issue · \(task.stateLabel)",
                                  route: .issue(repo: repo, number: number),
                                  pull: false,
                                  state: task.isDone ? "closed" : "open",
                                  draft: false))
            if let pr = task.pr, let n = pr.number {
                out.append(GitHubLink(title: "\(repo)#\(n)",
                                      detail: "Pull request · \(pr.state ?? "open")\(pr.draft == true ? " · draft" : "")",
                                      route: .pull(repo: repo, number: n),
                                      pull: true, state: pr.state, draft: pr.draft == true))
            }
        }
        return out
    }

    /// A session key trimmed for the "no longer in sessions" row:
    /// `claude@/folder:abc123` → `abc123`.
    private func shortKey(_ key: String) -> String {
        key.split(separator: ":").last.map(String.init) ?? key
    }

    private func unlink(_ key: String) {
        guard let task, let client = app.client else { return }
        linkError = nil
        Task {
            do {
                try await client.unlinkTask(key, task: task)
                await model.load(client: client)
            } catch {
                linkError = error.localizedDescription
            }
        }
    }
}

/// One row of the detail's GitHub card: a route plus what draws it.
private struct GitHubLink: Identifiable {
    let title: String
    let detail: String?
    let route: Route
    let pull: Bool
    let state: String?
    let draft: Bool
    var id: String { title + (detail ?? "") }

    var glyph: some View {
        pull ? AnyView(PullGlyph(state: state, draft: draft))
             : AnyView(IssueGlyph(state: state ?? "open"))
    }
}

/// A session key as an Identifiable for the Sessions card list.
private struct TaskSessionKey: Identifiable {
    let key: String
    var id: String { key }
}
