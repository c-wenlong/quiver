import QuiverCore
import SwiftUI

/// A task's detail: the state ladder, its linked sessions (each one a
/// chat), its linked PR, and the two actions the web page draws in the
/// same places — "New agent session" where the hub has a launch route
/// (a GitHub issue resolves, a pull opens; both stamp the launch's
/// origin so the session links itself here) and "Link session" for a
/// session already running.
struct TaskDetailPage: View {
    var taskId: String
    @Bindable var model: TasksModel
    var onBack: () -> Void

    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL

    /// At most one of the detail's two panels is open at a time.
    private enum Panel { case new, link }
    @State private var panel: Panel?

    // The New agent session panel.
    /// What `?harnesses=1` says the hub can start; nil while asking.
    @State private var harnesses: [HubTaskHarness]?
    @State private var harnessesError: String?
    @State private var harness: String?
    @State private var prompt = ""
    /// The launch `model` menu's state; a harness change or a panel
    /// re-open refits it (`ModelPick.refit`).
    @State private var modelPick = ModelPick()
    @State private var flow = IssueLaunchFlow()
    /// The running launch, so going back stops it before it can open a
    /// chat under a view that is gone.
    @State private var launchTask: Task<Void, Never>?

    // The Link session panel.
    /// `GET /v1/sessions` rows for the picker; nil while asking.
    @State private var linkSessions: [HubSessionRow]?
    @State private var linkFilter = ""
    @State private var linkError: String?

    private var task: HubTaskRow? { model.task(taskId) }

    var body: some View {
        Page(title: task?.title ?? task?.refLabel ?? "Task",
             subtitle: subtitle,
             back: ("Back to all tasks", { launchTask?.cancel(); onBack() })) {
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await model.load(hub: app.hub, refresh: true) }
            }
            .iconButton()
            .keyboardShortcut("r", modifiers: .command)
            .help("Fetch again (⌘R)")
            if let url = task?.url.flatMap(URL.init(string:)), url.isWebURL {
                Button {
                    openURL(url)
                } label: {
                    Image(systemName: "arrow.up.right.square")
                }
                .iconButton()
                .hoverTip(task?.openLabel ?? "Open on GitHub")
            }
        } content: {
            if let error = model.actionError {
                ErrorCard(message: error)
            }
            if let task {
                StepperCard(task: task)
                sessionsCard(task)
                if let pr = task.pr, pr.number != nil {
                    prCard(task: task, pr: pr)
                }
            } else if model.loaded {
                Card {
                    VStack(alignment: .leading, spacing: 10) {
                        Label("This task isn't listed any more — it may have closed or left the pinned repos.",
                              systemImage: "questionmark.circle")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Button("All tasks", action: onBack)
                            .secondaryButton()
                    }
                }
            } else {
                SkeletonRows(rows: 6, label: "Reading the task list…")
            }
        }
        .onDisappear { launchTask?.cancel() }
    }

    private var subtitle: String {
        guard let task else { return "" }
        return [task.refLabel, task.repo ?? task.project ?? "", task.stateLabel]
            .filter { !$0.isEmpty }.joined(separator: " · ")
    }

    // MARK: - sessions card

    private func sessionsCard(_ task: HubTaskRow) -> some View {
        Card(title: "Sessions") {
            VStack(alignment: .leading, spacing: 8) {
                launchRows(task)
                sessionRows(task)
                if panel == .new { newPanel(task) }
                if panel == .link { linkPanel(task) }
                actionButtons(task)
            }
        }
    }

    /// A launch still on its way shows as its own row, so "Starting…"
    /// is visible before adoption lands. A `started` launch whose key
    /// is already a session row is that row.
    @ViewBuilder private func launchRows(_ task: HubTaskRow) -> some View {
        ForEach(task.launchRows ?? []) { launch in
            switch launch.state {
            case "pending":
                NoteRow(icon: { ProgressView().controlSize(.small).frame(width: 16, height: 16) },
                        title: "Starting \(harnessName(launch.harness))…",
                        subtitle: launch.note ?? "preparing")
            case "failed":
                NoteRow(icon: { Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(Theme.danger).frame(width: 16, height: 16) },
                        title: "The session didn't start",
                        subtitle: launch.note ?? "launch failed")
            case "untracked":
                NoteRow(icon: { Image(systemName: "questionmark.circle")
                        .foregroundStyle(Theme.textMuted).frame(width: 16, height: 16) },
                        title: "\(harnessName(launch.harness)) started; Quiver can't read its sessions",
                        subtitle: "It keeps running under tmux on the hub.")
            default:
                EmptyView()
            }
        }
    }

    private func harnessName(_ id: String?) -> String {
        guard let id, !id.isEmpty else { return "an agent" }
        return UsageFormat.harness(id)
    }

    /// The task's linked sessions: a chat on click, an unlink button at
    /// the trailing end. A key the catalog no longer holds stays listed
    /// — the link is real even when the row is gone — dimmed, with
    /// Unlink still offered.
    @ViewBuilder private func sessionRows(_ task: HubTaskRow) -> some View {
        let rows = task.sessionRows ?? []
        if rows.isEmpty, (task.launchRows ?? []).isEmpty {
            Text("No session is linked to this task yet.")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
        }
        ForEach(rows, id: \.key) { session in
            if session.gone == true {
                SessionRow(icon: { Image(systemName: "terminal")
                        .foregroundStyle(Theme.textMuted).frame(width: 16, height: 16) },
                           title: session.key, titleMuted: true,
                           subtitle: "No longer in the session catalog.",
                           busy: model.busy.contains("unlink:\(session.key)"),
                           action: nil,
                           unlink: { unlink(task, key: session.key) })
            } else {
                SessionRow(icon: { HarnessIcon(harness: session.harness ?? "", size: 16)
                        .frame(width: 16, height: 16) },
                           title: session.title ?? session.key,
                           subtitle: sessionSubtitle(session),
                           busy: model.busy.contains("unlink:\(session.key)"),
                           action: { app.openSessionChat(key: session.key) },
                           unlink: { unlink(task, key: session.key) })
            }
        }
    }

    private func sessionSubtitle(_ s: HubTaskSessionRow) -> String {
        let name = s.harness.map(UsageFormat.harness) ?? s.agent ?? "Session"
        var parts = [name, s.status ?? ""]
        if s.remote == true { parts.append(s.machine ?? "remote") }
        if let at = s.lastActiveAt {
            parts.append(UsageFormat.ago(Date(timeIntervalSince1970: at), now: .now))
        }
        return parts.filter { !$0.isEmpty }.joined(separator: " · ")
    }

    private func unlink(_ task: HubTaskRow, key: String) {
        Task { await model.unlink(task: task, key: key, hub: app.hub) }
    }

    @ViewBuilder private func actionButtons(_ task: HubTaskRow) -> some View {
        if panel == nil {
            HStack(spacing: 8) {
                if task.canLaunch {
                    Button {
                        openNewPanel()
                    } label: {
                        Label("New agent session", systemImage: "terminal")
                    }
                    .primaryButton()
                    .hoverTip("Start an agent on this \(task.kind == "pull" ? "pull request" : "issue") — the session links itself here when it lands")
                }
                Button {
                    openLinkPanel()
                } label: {
                    Label("Link session", systemImage: "plus")
                }
                .secondaryButton()
                .hoverTip("Link a session already running — it counts as work on this task until you unlink it")
            }
        }
    }

    // MARK: - new agent session panel

    /// The panel's confirm box: which harness, the first prompt, and
    /// the hub's steps while it works.
    @ViewBuilder private func newPanel(_ task: HubTaskRow) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            let what = "\(task.kind == "pull" ? "PR" : "issue") \(task.refLabel)"
            Text("Start an agent on **\(what)** on \(model.machine ?? "the hub") — the session links itself here when it lands.")
                .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            if let harnessesError {
                Label(harnessesError, systemImage: "exclamationmark.triangle")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let harnesses {
                if harnesses.isEmpty {
                    Text("No harness can start on the hub: install one there, or turn it on in Settings › Providers.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    SelectMenu(options: harnesses.map { ($0.name, $0.displayName ?? $0.name) },
                               selection: Binding(get: { harness ?? "" },
                                                  set: { harness = $0.isEmpty ? nil : $0 }),
                               width: 220,
                               icons: Dictionary(uniqueKeysWithValues: harnesses.map { ($0.name, $0.name) }))
                        .disabled(flow.busy)
                    if harnessUntracked(harnesses) {
                        Text("Quiver can't read this harness's sessions yet: it starts, but no chat opens.")
                            .font(Theme.caption).foregroundStyle(Theme.warningText)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            } else if harnessesError == nil {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Reading what the hub can start…")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            // The Model row (#914): `launch_model` absent means a hub
            // too old to apply a pick, so the row hides rather than
            // send one it would drop; `false` disables with a note.
            if let picked = pickedHarness, picked.launchModel != nil {
                ModelMenu(recent: LaunchOptions.recentModels(of: picked), pick: $modelPick,
                          supported: picked.launchModel == true, label: "Model")
                    .disabled(flow.busy)
            }
            TextField("Its first message — leave blank for the hub's built prompt",
                      text: $prompt, axis: .vertical)
                .lineLimit(3...8)
                .quiverField(multiline: true)
                .disabled(flow.busy)
            Text("It runs under tmux in a worktree the hub prepares. The chat opens once the session shows up.")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
            launchStatus
            HStack(spacing: 8) {
                Button(flow.cancelTitle) {
                    launchTask?.cancel()
                    panel = nil
                }
                .secondaryButton()
                .keyboardShortcut(.cancelAction)
                Button {
                    launchTask = Task { await start(task) }
                } label: {
                    HStack(spacing: 6) {
                        if flow.busy {
                            ProgressView().controlSize(.small).tint(Theme.primaryForeground)
                        }
                        Text(flow.busy ? "Starting" : "Start")
                    }
                }
                .primaryButton()
                .disabled(harness == nil || flow.busy)
            }
        }
        .padding(12)
        .background(Theme.subtle, in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
            .strokeBorder(Theme.borderStrong, lineWidth: 1))
        .onChange(of: harness) { prefillModel() }
    }

    private func harnessUntracked(_ offered: [HubTaskHarness]) -> Bool {
        guard let harness else { return false }
        return offered.first { $0.name == harness }?.tracked == false
    }

    /// The picked harness's `?harnesses=1` row, whose `launch_model`
    /// and `last_model` drive the Model row.
    private var pickedHarness: HubTaskHarness? {
        harnesses?.first { $0.name == harness }
    }

    /// Refits the Model menu to the picked harness: an untouched menu
    /// takes its newest model; a pick survives as `ModelPick.refit` says.
    private func prefillModel() {
        modelPick.refit(LaunchOptions.recentModels(of: pickedHarness))
    }

    /// The hub's step while the launch works, then why it stopped.
    @ViewBuilder private var launchStatus: some View {
        switch flow.phase {
        case .failed(let message):
            Text(message).font(Theme.caption).foregroundStyle(Theme.dangerText)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
        case .unconfirmed(let message):
            Text(message).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        default:
            if let note = flow.note {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(note).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(2)
                }
            }
        }
    }

    private func openNewPanel() {
        panel = .new
        linkError = nil
        // A nil harnesses means unanswered — clear the last failure so
        // this retry draws the spinner instead of the stale error. A
        // cached list still refetches: a re-open would otherwise keep
        // suggesting a last-used model another launch already moved on
        // from.
        if harnesses == nil { harnessesError = nil }
        Task {
            do {
                let (offered, _) = try await app.hub.withClient { try await $0.taskHarnesses() }
                harnesses = offered
                harnessesError = nil
                if harness == nil || !offered.contains(where: { $0.name == harness }) {
                    harness = offered.first?.name
                }
                // Explicit: a re-open keeping the same harness fires no
                // onChange, but its last-used model may have moved on.
                prefillModel()
            } catch is CancellationError {
            } catch {
                // A refresh that fails while cached rows are up keeps
                // them: the stale list beats no panel. Only an
                // unanswered ask reports the failure — and reopening
                // the panel must be allowed to retry.
                if harnesses == nil {
                    harnessesError = (error as? HubError)?.message ?? error.localizedDescription
                }
            }
        }
    }

    /// Resolve an issue or open a pull on the hub: both routes stamp
    /// the launch's `origin`, so the adopted session links itself to
    /// this task. A started key swaps the page for the chat; any other
    /// outcome reloads so the launch rows catch up.
    private func start(_ task: HubTaskRow) async {
        guard let harness, let repo = task.repo, let number = task.number else { return }
        let hub = app.hub
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        // A pick goes only for a harness that can honour it.
        let launchModel = modelPick.model(supported: pickedHarness?.launchModel == true)
        let key = await flow.run(
            post: {
                try await hub.withClient { client in
                    if task.kind == "pull" {
                        let parts = repo.split(separator: "/", maxSplits: 1).map(String.init)
                        guard parts.count == 2 else {
                            throw HubError(status: 0, code: "bad_repo", message: "\(repo) isn't an owner/repo name.")
                        }
                        return try await client.openPull(owner: parts[0], repo: parts[1],
                                                         number: number, harness: harness,
                                                         prompt: text.isEmpty ? nil : text,
                                                         model: launchModel)
                    }
                    return try await client.resolveIssue(repo: repo, number: number, harness: harness,
                                                         prompt: text.isEmpty ? nil : text, context: nil,
                                                         model: launchModel)
                }
            },
            fetch: { id in try await hub.withClient { try await $0.launch(id) } })
        guard !Task.isCancelled else { return }
        if let key {
            app.openSessionChat(key: key)
        }
        await model.load(hub: hub, refresh: true)
    }

    // MARK: - link session panel

    /// Every hub session not already linked, filtered by the box.
    private var linkFound: [HubSessionRow] {
        let linked = Set(task?.sessions ?? [])
        let query = linkFilter.trimmingCharacters(in: .whitespaces).lowercased()
        return (linkSessions ?? []).filter { session in
            guard !linked.contains(session.key) else { return false }
            guard !query.isEmpty else { return true }
            return [session.title, session.key, session.status, session.harness,
                    session.agent, session.machine, session.cwd]
                .contains { $0?.lowercased().contains(query) ?? false }
        }
    }

    @ViewBuilder private func linkPanel(_ task: HubTaskRow) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Link a session to this task — it counts as work on it until you unlink it.")
                .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            SearchField(prompt: "Filter sessions", text: $linkFilter)
            if linkSessions == nil {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Reading sessions…").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            } else if linkFound.isEmpty {
                Text(linkFilter.isEmpty
                     ? ((task.sessions ?? []).isEmpty ? "No session is running on the hub." : "Every session is already linked here.")
                     : "Nothing matches \"\(linkFilter)\".")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(linkFound.prefix(30).enumerated()), id: \.element.key) { index, session in
                        LinkRow(session: session,
                                busy: model.busy.contains("link:\(session.key)")) {
                            Task {
                                if await model.link(task: task, key: session.key, hub: app.hub) == nil {
                                    panel = nil
                                }
                            }
                        }
                        if index < min(linkFound.count, 30) - 1 { Divider().overlay(Theme.rowDivider) }
                    }
                }
            }
            if let linkError {
                Text(linkError).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 8) {
                Button("Cancel") { panel = nil }
                    .secondaryButton()
                    .keyboardShortcut(.cancelAction)
            }
        }
        .padding(12)
        .background(Theme.subtle, in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
            .strokeBorder(Theme.borderStrong, lineWidth: 1))
    }

    private func openLinkPanel() {
        panel = .link
        linkSessions = nil
        linkError = nil
        linkFilter = ""
        Task {
            do {
                let sessions = try await app.hub.withClient { try await $0.sessions() }
                linkSessions = sessions.sorted { a, b in
                    let active = (a.status == "active") != (b.status == "active")
                    if active { return a.status == "active" }
                    return (a.lastActiveAt ?? 0) > (b.lastActiveAt ?? 0)
                }
            } catch is CancellationError {
            } catch {
                linkSessions = []
                linkError = (error as? HubError)?.message ?? error.localizedDescription
            }
        }
    }

    // MARK: - PR card

    /// The linked PR: an issue's `quiver/issue-<n>` PR, or a pull task
    /// itself.
    private func prCard(task: HubTaskRow, pr: HubTaskPr) -> some View {
        Card(title: task.kind == "pull" ? "Pull request" : "Linked pull request") {
            HStack(spacing: 12) {
                prIcon(pr)
                VStack(alignment: .leading, spacing: 3) {
                    Text("#\(pr.number ?? 0) \(pr.title ?? "")")
                        .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                        .lineLimit(1).truncationMode(.tail)
                    Text([pr.draft == true ? "Draft" : nil, pr.stateLabel]
                            .compactMap { $0 }.joined(separator: " · "))
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                Spacer(minLength: 12)
                if let url = pr.url.flatMap(URL.init(string:)), url.isWebURL {
                    Button { openURL(url) } label: {
                        Image(systemName: "arrow.up.right.square")
                    }
                    .iconButton()
                    .hoverTip("Open on GitHub")
                }
            }
        }
    }

    @ViewBuilder private func prIcon(_ pr: HubTaskPr) -> some View {
        if pr.draft == true, pr.state == "open" {
            OcticonView(icon: .pullRequestDraft, size: 16).foregroundStyle(Theme.textMuted)
        } else {
            switch pr.state {
            case "merged": OcticonView(icon: .merge, size: 16).foregroundStyle(ghPurple)
            case "closed": OcticonView(icon: .pullRequestClosed, size: 16).foregroundStyle(Theme.danger)
            default: OcticonView(icon: .pullRequest, size: 16).foregroundStyle(Theme.success)
            }
        }
    }
}

/// The state ladder as rungs: open → in progress → (PR open) →
/// merged|closed. Reached rungs check, the current one names it.
private struct StepperCard: View {
    var task: HubTaskRow

    var body: some View {
        Card {
            HStack(alignment: .top, spacing: 0) {
                ForEach(Array(TaskStepper.steps(for: task).enumerated()), id: \.element.id) { index, step in
                    if index > 0 {
                        Rectangle()
                            .fill(step.reached ? Theme.interactive : Theme.border)
                            .frame(height: 1.5)
                            .padding(.top, 9)
                    }
                    VStack(spacing: 6) {
                        ZStack {
                            if step.reached, !step.current {
                                Image(systemName: "checkmark.circle.fill")
                                    .font(Theme.font(size: 18))
                                    .foregroundStyle(Theme.interactive)
                            } else {
                                Circle()
                                    .strokeBorder(step.current ? Theme.interactive : Theme.border,
                                                  lineWidth: 1.5)
                                    .frame(width: 18, height: 18)
                                    .overlay {
                                        Text("\(index + 1)")
                                            .font(Theme.font(size: 10, weight: .semibold))
                                            .foregroundStyle(step.current ? Theme.interactive : Theme.textMuted)
                                    }
                            }
                        }
                        .frame(width: 18, height: 18)
                        Text(step.title)
                            .font(Theme.caption)
                            .foregroundStyle(step.current ? Theme.textPrimary : Theme.textMuted)
                            .lineLimit(1)
                            .minimumScaleFactor(0.8)
                    }
                    .fixedSize()
                }
            }
            .frame(maxWidth: .infinity)
        }
    }
}

/// A note row inside the Sessions card: a small mark, a title and a
/// caption, used for launches still on their way.
private struct NoteRow<Icon: View>: View {
    @ViewBuilder var icon: Icon
    var title: String
    var subtitle: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            icon.padding(.top, 1)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                Text(subtitle).font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: 8)
        }
        .padding(.vertical, 6)
    }
}

/// One linked session: harness mark, title, detail line, an unlink
/// button at the trailing edge. `action` nil draws the row still
/// (a gone session) with the same unlink.
private struct SessionRow<Icon: View>: View {
    @ViewBuilder var icon: Icon
    var title: String
    var titleMuted = false
    var subtitle: String
    var busy = false
    var action: (() -> Void)?
    var unlink: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            icon.padding(.top, 1)
            if let action {
                Button(action: action) { label }
                    .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))
            } else {
                label
            }
            Button {
                unlink()
            } label: {
                if busy {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "minus.circle")
                        .font(Theme.font(size: 14))
                        .foregroundStyle(Theme.textSecondary)
                }
            }
            .plainButton()
            .disabled(busy)
            .frame(width: 24, height: 20)
            .hoverTip("Unlink this session")
        }
        .padding(.vertical, 6)
    }

    private var label: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(Theme.rowTitle)
                .foregroundStyle(titleMuted ? Theme.textMuted : Theme.textPrimary)
                .lineLimit(1)
            Text(subtitle).font(Theme.caption).foregroundStyle(Theme.textMuted)
                .lineLimit(1).truncationMode(.middle)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(.rect)
    }
}

/// A session the link panel offers: its row and the Link button.
private struct LinkRow: View {
    var session: HubSessionRow
    var busy: Bool
    var link: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            HarnessIcon(harness: session.harness ?? "", size: 16)
                .frame(width: 16, height: 16)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 3) {
                Text(session.title ?? session.key)
                    .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1)
                Text(subtitle)
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: 12)
            Button(busy ? "Linking…" : "Link", action: link)
                .secondaryButton()
                .disabled(busy)
        }
        .padding(.vertical, 8)
    }

    private var subtitle: String {
        let name = session.harness.map(UsageFormat.harness) ?? session.agent ?? "Session"
        var parts = [name, session.status ?? ""]
        if session.remote == true { parts.append(session.machine ?? "remote") }
        if let at = session.lastActiveAt {
            parts.append(UsageFormat.ago(Date(timeIntervalSince1970: at), now: .now))
        }
        return parts.filter { !$0.isEmpty }.joined(separator: " · ")
    }
}
