import SwiftUI

/// The task detail's "New agent session" sheet. A GitHub issue or pull
/// goes through the worktree routes (`resolve`/`open`), whose launch
/// `origin` links the adopted session to the task itself; any other
/// task gets a plain session in a picked folder and the sheet links it
/// by hand once the hub names it. Either way a blank prompt lets the
/// hub build its brief.
struct TaskSessionSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let task: TaskRow
    /// The hub's machine id, from the tasks reply; worktrees live
    /// there, so only its harnesses are on offer.
    let machineId: String?
    var onStarted: (String) -> Void

    @State private var harness: String?
    /// A harness the user tapped stays: the folder default only
    /// improves the *default*.
    @State private var harnessTouched = false
    @State private var prompt: String
    /// The Model menu; `follow` re-defaults it on a harness change.
    @State private var model = ModelSelection()
    @State private var folder: String?
    @State private var browsePath: [FolderBrowse] = []
    @State private var flow = LaunchFlow()
    /// The running launch, so Cancel or a swipe-down stops it before it
    /// can open a chat behind a sheet that is gone.
    @State private var launch: Task<Void, Never>?
    /// The session list as it was when the sheet opened, for the
    /// harness last used in the picked folder.
    @State private var rows: [SessionRow] = []

    /// `(repo, number)` when the task maps onto a worktree route.
    private var issueRoute: (repo: String, number: Int)? {
        guard task.source == "github", task.kind == "issue",
              let repo = task.repo, let number = task.number else { return nil }
        return (repo, number)
    }

    private var pullRoute: (repo: String, number: Int)? {
        guard task.source == "github", task.kind == "pull",
              let repo = task.repo, let number = task.number else { return nil }
        return (repo, number)
    }

    /// A worktree launch links itself through `origin` on adoption.
    private var worktreeLaunch: Bool { issueRoute != nil || pullRoute != nil }

    init(task: TaskRow, machineId: String?, onStarted: @escaping (String) -> Void) {
        self.task = task
        self.machineId = machineId
        self.onStarted = onStarted
        _prompt = State(initialValue: task.source == "github" ? "" : Self.prefill(task))
    }

    /// The first prompt a non-GitHub task gets: its ref, title and link.
    private static func prefill(_ task: TaskRow) -> String {
        var text = "Work on \(task.refLabel.isEmpty ? "this task" : task.refLabel): \(task.title ?? "")"
        if let url = task.url, !url.isEmpty { text += "\n\(url)" }
        return text
    }

    /// The machine the worktree or session lives on: the hub itself.
    private var machine: Machine? {
        app.machines.first { $0.id == machineId } ?? app.machines.first
    }

    var body: some View {
        NavigationStack(path: $browsePath) {
            PageScroll {
                if let machine {
                    startsIn(machine)
                    HarnessPicker(machine: machine, harness: $harness, sessions: rows) { harnessTouched = true }
                    ModelField(machine: machine, harness: harness,
                               selection: $model,
                               sessions: rows, disabled: flow.busy)
                    promptSection
                    if !worktreeLaunch {
                        // The folder a plain session starts in. GitHub
                        // tasks never see this: their worktree is the
                        // hub's choice.
                        RecentFolderPicker(machine: machine, sessions: rows, selected: folder, disabled: flow.busy,
                                           browse: {
                                               browsePath = [FolderBrowse(machineId: machine.id,
                                                                          path: folder.map(FolderRow.parent))]
                                           },
                                           pick: { pickFolder($0, on: machine) })
                    }
                    startSection(machine)
                } else {
                    StateCard(icon: "terminal", title: "No machine can start a session",
                              message: NewSessionOptions.noMachineMessage(app.machines))
                }
            }
            .navigationTitle("New agent session")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        launch?.cancel()
                        dismiss()
                    }
                }
            }
            .navigationDestination(for: FolderBrowse.self) { place in
                FolderBrowserView(machineId: place.machineId, path: place.path) { picked in
                    if let machine = app.machines.first(where: { $0.id == place.machineId }) {
                        pickFolder(picked, on: machine)
                    }
                    browsePath = []
                }
            }
        }
        .interactiveDismissDisabled(flow.busy)
        .onAppear {
            rows = app.sessions
            guard harness == nil, let machine else { return }
            let offered = NewSessionOptions.harnesses(on: machine)
            if !worktreeLaunch, folder == nil {
                folder = NewSessionOptions.folders(on: machine.id, sessions: rows).first
            }
            harness = NewSessionOptions.defaultHarness(
                folder: folder, machineId: machine.id, offered: offered, sessions: rows)
        }
        .onChange(of: harness) { _, _ in
            if let machine { refillModel(on: machine) }
        }
        .onDisappear { launch?.cancel() }
    }

    /// The Model menu after a harness change; `ModelSelection.follow`
    /// says which picks survive it.
    private func refillModel(on machine: Machine) {
        model.follow(harness, on: machine)
    }

    private func startsIn(_ machine: Machine) -> some View {
        let icon: String
        let where_: String
        if let r = issueRoute {
            icon = "exclamationmark.circle"
            where_ = "\(r.repo) · quiver/issue-\(r.number)"
        } else if let r = pullRoute {
            icon = "arrow.triangle.pull"
            where_ = "\(r.repo)#\(r.number) worktree"
        } else {
            icon = "folder"
            where_ = folder.map(FolderRow.name) ?? "Pick a folder below"
        }
        return StartsInCard(icon: icon, title: where_, trailing: "on \(machine.displayName)")
            .padding(.top, 4)
    }

    private var promptSection: some View {
        FirstPromptField(prompt: $prompt) {
            if worktreeLaunch {
                Text("Leave it empty and the hub writes the brief from the \(issueRoute != nil ? "issue" : "pull request").")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 4)
            }
        }
    }

    /// A picked folder, from the list or the browser: the harness last
    /// run there becomes the default, unless one was tapped.
    private func pickFolder(_ path: String, on machine: Machine) {
        folder = path
        if !harnessTouched {
            harness = NewSessionOptions.defaultHarness(
                folder: path, machineId: machine.id,
                offered: NewSessionOptions.harnesses(on: machine),
                sessions: rows)
        }
    }

    private func startSection(_ machine: Machine) -> some View {
        let ready = harness != nil && (worktreeLaunch || folder != nil)
        return VStack(alignment: .leading, spacing: 8) {
            LaunchStartButton(flow: flow, ready: ready, footnote: footnote(machine)) {
                launch = Task { await start(machine) }
            }
        }
    }

    private func footnote(_ machine: Machine) -> String {
        if let r = issueRoute {
            return "Runs under tmux on \(machine.displayName) on branch quiver/issue-\(r.number) in a worktree of \(r.repo). The chat opens once the session shows up."
        }
        if pullRoute != nil {
            return "Runs under tmux on \(machine.displayName) in a checkout of the PR's head. The chat opens once the session shows up."
        }
        return "Runs under tmux on \(machine.displayName) and links to this task once the session shows up. The chat opens then."
    }

    private func start(_ machine: Machine) async {
        guard let client = app.client, let harness else { return }
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        let pick = model.model(for: harness, on: machine)
        let post: () async throws -> LaunchStatus
        if let r = issueRoute {
            post = { try await client.resolveIssue(repo: r.repo, number: r.number, harness: harness, prompt: text, model: pick) }
        } else if let r = pullRoute {
            post = { try await client.openPull(repo: r.repo, number: r.number, harness: harness, prompt: text, model: pick) }
        } else {
            guard let folder else { return }
            post = { try await client.startSession(machine: machine.id, harness: harness, cwd: folder, prompt: text, model: pick) }
        }
        let key = await flow.run(post: post, fetch: { try await client.launch($0) })
        if !worktreeLaunch, let launchId = flow.launchId {
            // A worktree launch's `origin` links on adoption; a plain
            // one links here, and keeps linking after this sheet is
            // gone — a cancel or a slow adoption still lands the link
            // when the launch names its session.
            Self.linkWhenAdopted(client: client, launchId: launchId, task: task)
        }
        guard let key, !Task.isCancelled else { return }
        await app.refresh()
        guard !Task.isCancelled else { return }
        onStarted(key)
        dismiss()
    }

    /// Watches an accepted launch until it names its session, then posts
    /// the task link. Detached so it outlives the sheet's own task.
    private static func linkWhenAdopted(client: HubClient, launchId: String, task: TaskRow) {
        Task.detached {
            for _ in 0..<300 {
                let status = try? await client.launch(launchId)
                if let key = status?.key, status?.state == "started" {
                    if (try? await client.linkTask(key, task: task)) != nil {
                        await MainActor.run {
                            NotificationCenter.default.post(name: .taskLinked, object: nil)
                        }
                    }
                    return
                }
                if status?.state == "failed" || status?.state == "untracked" { return }
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }
}

extension Notification.Name {
    /// A task link landed outside a sheet (a plain launch adopted after
    /// its sheet closed): the Tasks pages reload to show it.
    static let taskLinked = Notification.Name("QuiverRemote.taskLinked")
}

/// The task detail's "Link a session" sheet: the sessions the hub
/// knows, minus the ones already linked, newest first. A pick posts
/// `POST /v1/sessions/<key>/task` and refreshes the list behind it.
struct LinkSessionSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let task: TaskRow
    /// Called after a link lands, so the list refetches.
    var onLinked: () -> Void

    @State private var query = ""
    /// The key being linked right now; keeps one tap from posting twice.
    @State private var busy: String?
    @State private var error: String?

    init(task: TaskRow, onLinked: @escaping () -> Void) {
        self.task = task
        self.onLinked = onLinked
    }

    /// Sessions not linked yet, matching every word of the query on
    /// title, harness or folder, newest first. Read live off `app` so a
    /// session that lands while the sheet is open joins the list.
    private var candidates: [SessionRow] {
        let linked = Set(task.sessions ?? [])
        let words = query.lowercased().split(whereSeparator: \.isWhitespace)
        return app.displaySessions
            .filter { !linked.contains($0.key) }
            .filter { row in
                guard !words.isEmpty else { return true }
                let hay = "\(row.displayTitle) \(row.harnessName) \(row.cwd ?? "")".lowercased()
                return words.allSatisfy { hay.contains($0) }
            }
            .sorted { ($0.lastActiveAt ?? 0) > ($1.lastActiveAt ?? 0) }
    }

    var body: some View {
        NavigationStack {
            PageScroll {
                SearchField(placeholder: "Search sessions", query: $query)
                .padding(.top, 4)

                if app.displaySessions.isEmpty {
                    StateCard(icon: "link", title: "No sessions to link",
                              message: "Sessions show up here once the hub lists them.")
                } else if candidates.isEmpty {
                    Text("No session matches.").font(Theme.caption).foregroundStyle(Theme.textSecondary).padding(.horizontal, 4)
                } else {
                    CardList(data: candidates) { row in
                        Button { link(row) } label: {
                            HStack(spacing: 0) {
                                SessionRowView(row: row, unread: app.isUnread(row))
                                if busy == row.key {
                                    ProgressView().controlSize(.small)
                                        .padding(.trailing, Theme.rowHorizontal)
                                }
                            }
                        }
                        .buttonStyle(RowButtonStyle())
                        .disabled(busy != nil)
                    }
                }
                if let error {
                    Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                        .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
                }
            }
            .navigationTitle("Link a session")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            }
        }
        .task { await app.refresh() }
    }

    private func link(_ row: SessionRow) {
        guard let client = app.client, busy == nil else { return }
        busy = row.key
        error = nil
        Task {
            do {
                try await client.linkTask(row.key, task: task)
            } catch {
                self.error = error.localizedDescription
                busy = nil
                return
            }
            onLinked()
            dismiss()
        }
    }
}
