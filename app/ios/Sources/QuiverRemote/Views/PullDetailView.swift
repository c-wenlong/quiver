import SwiftUI

/// One pull request in full: title and meta, the rendered body, the
/// per-check rows and reviews, then the primary Open in session, merge
/// with a strategy or arm auto-merge (behind a confirm, as on the Mac
/// and web), and Open on GitHub. A check row from a GitHub Actions run
/// re-runs that run's failed jobs, or all of them, from its menu.
/// Reads `GET /v1/pulls/<o>/<r>/<n>`; the writes are the web app's
/// `POST /v1/app/repositories/merge` and `…/rerun`.
struct PullDetailView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    @Environment(\.dynamicTypeSize) private var size
    let repo: String
    let number: Int
    /// The tab pushes the new session's chat once a launch lands.
    var onStarted: (String) -> Void

    @State private var reply: PullResponse?
    @State private var error: String?
    @State private var opening = false
    @State private var startedKey: String?
    /// Bumped per load so a late reply can't overwrite a newer one.
    @State private var generation = 0
    @State private var method: MergeMethod = .squash
    /// The write the confirm dialog is asking about.
    @State private var asking: MergeAsk?
    @State private var merging = false
    /// A merge's error stays on the page, under the buttons.
    @State private var mergeError: String?
    /// Workflow runs whose re-run is in flight.
    @State private var rerunning: Set<Int> = []
    @State private var rerunError: String?

    var body: some View {
        PageScroll {
            if let pr = reply?.pr {
                // A failed refresh keeps the PR it already had — the
                // notice says why it's stale rather than replacing it.
                if let error {
                    Label { Text(inlineCode: "Couldn't refresh: \(error)") } icon: { Image(systemName: "exclamationmark.triangle") }
                        .font(Theme.caption).foregroundStyle(Theme.warningText)
                        .lineLimit(2)
                }
                header(pr)
                actions(pr)
                checksSection(pr)
                reviewsSection(pr)
                if let body = pr.body, !body.isEmpty {
                    GroupLabel(text: "Description")
                    Card { RichText(text: body) }
                }
            } else if let error {
                StateCard(icon: "exclamationmark.triangle", title: "Can't load this pull request",
                          message: error, tone: .error,
                          actionTitle: "Try again") { Task { await load(refresh: true) } }
            } else if let status = reply?.status, status != "ok" {
                StateCard(icon: "arrow.triangle.pull", title: "Can't load this pull request",
                          message: reply?.error ?? "GitHub answered \(status).", tone: .error,
                          actionTitle: "Try again") { Task { await load(refresh: true) } }
            } else {
                LoadingRow(text: "Loading pull request")
            }
        }
        .refreshable { await load(refresh: true) }
        .navigationTitle("\(repo)#\(number)")
        .navigationBarTitleDisplayMode(.inline)
        .task { if reply == nil { await load() } }
        .confirmationDialog(asking == .auto ? "Arm auto-merge" : "Merge pull request",
                            isPresented: Binding(get: { asking != nil },
                                                 set: { if !$0 { asking = nil } }),
                            titleVisibility: .visible, presenting: asking) { ask in
            if let pr = reply?.pr {
                Button(PullActions.confirmButton(ask, pr)) { Task { await merge(ask) } }
            }
            Button("Cancel", role: .cancel) {}
        } message: { ask in
            if let pr = reply?.pr {
                Text(PullActions.confirmMessage(ask, pr, repo: repo, method: method))
            }
        }
        // The chat opens after the sheet is gone, so the push isn't
        // lost under the dismiss animation.
        .sheet(isPresented: $opening, onDismiss: {
            if let key = startedKey { onStarted(key) }
            startedKey = nil
        }) {
            if let pr = reply?.pr {
                OpenPullSheet(repo: repo, detail: pr, machineId: reply?.machineId) {
                    startedKey = $0
                }
            }
        }
    }

    private func load(refresh: Bool = false) async {
        guard let client = app.client else { return }
        generation += 1
        let gen = generation
        do {
            let body = try await client.pull(repo: repo, number: number, refresh: refresh)
            guard gen == generation else { return }
            (reply, error) = Self.landing(body, over: reply)
        } catch is CancellationError {
            // The page went away mid-fetch: nothing failed.
            return
        } catch {
            guard gen == generation else { return }
            self.error = error.localizedDescription
        }
    }

    /// What a reply leaves on screen. A `gh` failure answers 200 with
    /// `pr: null`: the PR already shown stays, and the notice says why
    /// it's stale, rather than blanking the page for an outage.
    static func landing(_ body: PullResponse, over shown: PullResponse?) -> (reply: PullResponse?, error: String?) {
        if body.pr == nil, shown?.pr != nil {
            return (shown, body.error ?? "GitHub couldn't read the pull request.")
        }
        return (body, nil)
    }

    /// Merge or arm auto-merge, then read the PR again: the hub dropped
    /// its cached copy, so the page shows merged or armed. A 409 or 404
    /// means the page was out of date; the error stays and the PR is
    /// read again so it shows what the hub saw. The buttons stay busy
    /// until that read lands, so the old picture can't be acted on twice.
    private func merge(_ ask: MergeAsk) async {
        guard let client = app.client, !merging else { return }
        merging = true
        mergeError = nil
        defer { merging = false }
        do {
            try await client.mergePull(repo: repo, number: number, method: method, auto: ask == .auto)
            await load(refresh: true)
        } catch is CancellationError {
            return
        } catch {
            mergeError = error.localizedDescription
            if let e = error as? HubError, e.status == 409 || e.status == 404 {
                await load(refresh: true)
            }
        }
    }

    /// Re-run a workflow run's failed jobs, or all of them. Success
    /// reads the PR again; the checks flip on GitHub's schedule.
    private func rerun(_ run: Int, failed: Bool) async {
        guard let client = app.client, !rerunning.contains(run) else { return }
        rerunning.insert(run)
        rerunError = nil
        // Busy until the fresh read lands, as for a merge.
        defer { rerunning.remove(run) }
        do {
            try await client.rerunRun(repo: repo, run: run, failed: failed)
            await load(refresh: true)
        } catch is CancellationError {
            return
        } catch {
            rerunError = error.localizedDescription
        }
    }

    // MARK: Sections

    private func header(_ pr: PullDetail) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(pr.title ?? "Untitled")
                .font(Theme.sectionTitle).foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 6) {
                PullGlyph(state: pr.state, draft: pr.draft == true)
                Text(stateLine(pr))
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .lineLimit(2)
            }
            stats(pr)
        }
    }

    /// "Open · fix-thing → main · by octocat · 3d"
    private func stateLine(_ pr: PullDetail) -> String {
        var parts = [pr.draft == true ? "Draft" : (pr.state ?? "open").capitalized]
        if let branch = pr.branch, !branch.isEmpty {
            parts.append("\(branch) → \(pr.base ?? "")")
        }
        if let author = pr.author, !author.isEmpty, author != "-" {
            parts.append("by \(author)")
        }
        let when = RelativeTime.short(pr.updatedDate?.timeIntervalSince1970)
        if !when.isEmpty { parts.append(when) }
        return parts.joined(separator: " · ")
    }

    /// "conflicts · +10 −2 · 3 files", then the label badges.
    private func statBits(_ pr: PullDetail) -> [String] {
        var bits: [String] = []
        if pr.mergeable == "conflicting" { bits.append("conflicts") }
        if let a = pr.additions, let d = pr.deletions { bits.append("+\(a) −\(d)") }
        if let f = pr.changedFiles { bits.append("\(f) file\(f == 1 ? "" : "s")") }
        return bits
    }

    @ViewBuilder private func stats(_ pr: PullDetail) -> some View {
        let bits = statBits(pr)
        let labels = pr.labels ?? []
        if !bits.isEmpty || !labels.isEmpty || pr.autoMerge != nil {
            HStack(spacing: 4) {
                if !bits.isEmpty {
                    Text(bits.joined(separator: " · "))
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                }
                if let auto = pr.autoMerge {
                    Badge("Auto-merge · \(auto.method ?? "on")")
                }
                ForEach(Array(labels.prefix(4).enumerated()), id: \.offset) { Badge($0.element) }
            }
        }
    }

    private func actions(_ pr: PullDetail) -> some View {
        VStack(spacing: 8) {
            Button { opening = true } label: {
                Label("Open in session", systemImage: "play")
            }
            .primaryButton()
            if PullActions.offersMerge(pr) {
                mergeControls(pr)
            }
            // Outside the open-only controls: a merge refused because the
            // PR closed meanwhile still says why after the re-read.
            if let mergeError {
                Text(mergeError).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            if let url = pr.url.flatMap(URL.init) {
                Button { openURL(url) } label: {
                    Label("Open on GitHub", systemImage: "safari")
                }
                .secondaryButton()
            }
        }
        .padding(.top, 4)
    }

    /// The strategy menu, then Merge and Auto-merge; both ask first.
    /// The phone has no hover, so why a button is off is written under.
    private func mergeControls(_ pr: PullDetail) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Menu {
                Picker("Merge strategy", selection: $method) {
                    ForEach(MergeMethod.allCases) { Text($0.title).tag($0) }
                }
            } label: {
                HStack(spacing: 4) {
                    Text(method.title)
                    Image(systemName: "chevron.up.chevron.down").font(Theme.small)
                }
                .font(Theme.control).foregroundStyle(Theme.textPrimary)
                .scaledMinHeight(Theme.controlHeight)
            }
            .disabled(merging)
            .accessibilityLabel("Merge strategy, \(method.title)")
            StackingRow {
                Button {
                    mergeError = nil
                    asking = .merge
                } label: {
                    if merging {
                        ProgressView().controlSize(.small)
                    } else {
                        Label("Merge", systemImage: "arrow.triangle.merge")
                    }
                }
                .secondaryButton()
                .disabled(merging || PullActions.mergeBlock(pr) != nil)
                Button {
                    mergeError = nil
                    asking = .auto
                } label: {
                    Label("Auto-merge", systemImage: "clock")
                }
                .secondaryButton()
                .disabled(merging || PullActions.autoBlock(pr) != nil)
            }
            if let note = PullActions.blockNote(pr) {
                Text(note).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
    }

    @ViewBuilder private func checkName(_ run: CheckRun) -> some View {
        Text(run.name ?? "check").font(Theme.control).foregroundStyle(Theme.textPrimary).lineLimit(1, larger: 3)
        if let workflow = run.workflow, !workflow.isEmpty {
            Text(workflow).font(Theme.caption).foregroundStyle(Theme.textMuted).lineLimit(1, larger: 2)
        }
    }

    /// A check row's ⋯ menu: Re-run failed jobs (a run with a failing
    /// check only) and Re-run all jobs. The run spins while it's asked.
    private func rerunMenu(_ run: Int, name: String, checks: [CheckRun]) -> some View {
        let busy = rerunning.contains(run)
        return Menu {
            if PullActions.runFailed(run, in: checks, repo: repo) {
                Button {
                    Task { await rerun(run, failed: true) }
                } label: { Label("Re-run failed jobs", systemImage: "arrow.clockwise") }
            }
            Button {
                Task { await rerun(run, failed: false) }
            } label: { Label("Re-run all jobs", systemImage: "arrow.clockwise") }
        } label: {
            Group {
                if busy {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "ellipsis.circle").font(Theme.font(size: 16))
                        .foregroundStyle(Theme.textSecondary)
                        .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
                }
            }
            .frame(width: 44, height: 44)
            .contentShape(.rect)
        }
        .disabled(busy)
        .accessibilityLabel("Re-run \(name)")
    }

    @ViewBuilder private func checksSection(_ pr: PullDetail) -> some View {
        let runs = pr.checkRuns ?? []
        if !runs.isEmpty {
            GroupLabel(text: "Checks", trailing: pr.checks)
            if let rerunError {
                Text(rerunError).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            CardList(data: runs.enumerated().map(CheckSlot.init)) { slot in
                let run = slot.run
                let runId = PullActions.runId(run.url, repo: repo)
                let row = HStack(spacing: 12) {
                    ChecksGlyph(state: run.state)
                    // Above the default size the workflow goes under the
                    // name, which gets more lines: beside each other they
                    // cut "test (3.11, shard 1)" to "test (".
                    if size > .large {
                        VStack(alignment: .leading, spacing: 2) { checkName(run) }
                    } else {
                        checkName(run)
                    }
                    Spacer(minLength: 8)
                    if run.url != nil {
                        Image(systemName: "arrow.up.right").font(Theme.font(size: 12)).foregroundStyle(Theme.textMuted)
                    }
                }
                .padding(.vertical, Theme.rowVertical - 2)
                .padding(.leading, Theme.rowHorizontal)
                .padding(.trailing, runId == nil ? Theme.rowHorizontal : 4)
                .contentShape(.rect)
                HStack(spacing: 0) {
                    if let url = run.url.flatMap(URL.init) {
                        Button { openURL(url) } label: { row }
                            .buttonStyle(RowButtonStyle())
                    } else {
                        row
                    }
                    if let runId {
                        rerunMenu(runId, name: run.name ?? "check", checks: runs)
                            .padding(.trailing, Theme.rowHorizontal - 12)
                    }
                }
            }
        }
    }

    @ViewBuilder private func reviewsSection(_ pr: PullDetail) -> some View {
        let reviews = pr.reviews ?? []
        let requested = pr.reviewRequests ?? []
        if !reviews.isEmpty || !requested.isEmpty {
            GroupLabel(text: "Reviews")
            Card {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(reviews, id: \.self) { review in
                        HStack(spacing: 6) {
                            Text(review.author ?? "?").font(Theme.control).foregroundStyle(Theme.textPrimary).lineLimit(1)
                            Text(review.state ?? "").font(Theme.caption)
                                .foregroundStyle(review.state == "changes_requested" ? Theme.dangerText
                                                 : review.state == "approved" ? Theme.accentFill
                                                 : Theme.textSecondary)
                            Spacer(minLength: 8)
                            let when = RelativeTime.short(GitHubTime.date(review.submittedAt)?.timeIntervalSince1970)
                            if !when.isEmpty {
                                Text(when).font(Theme.caption).foregroundStyle(Theme.textMuted)
                            }
                        }
                    }
                    ForEach(requested, id: \.self) { name in
                        HStack(spacing: 6) {
                            Text(name).font(Theme.control).foregroundStyle(Theme.textPrimary).lineLimit(1)
                            Text("requested").font(Theme.caption).foregroundStyle(Theme.warningText)
                            Spacer(minLength: 8)
                        }
                    }
                }
            }
        }
    }
}

extension PullDetail {
    var updatedDate: Date? { GitHubTime.date(updatedAt) }
}

/// Two providers can report a check under the same `workflow/name`,
/// so the row fields can't key a `ForEach` — the check's slot in the
/// reply is its identity.
private struct CheckSlot: Identifiable {
    let offset: Int
    let run: CheckRun
    var id: Int { offset }

    init(offset: Int, run: CheckRun) {
        self.offset = offset
        self.run = run
    }

    init(_ pair: EnumeratedSequence<[CheckRun]>.Element) {
        self.init(offset: pair.offset, run: pair.element)
    }
}

/// The Open-in-session sheet: the PR's worktree is chosen by the hub,
/// so the sheet only asks which harness and with what first prompt —
/// prefilled with the review brief the hub built.
struct OpenPullSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let repo: String
    let detail: PullDetail
    /// The hub's machine id, from the detail reply; the worktree lives
    /// there, so only its harnesses are on offer.
    let machineId: String?
    var onStarted: (String) -> Void

    @State private var harness: String?
    @State private var prompt: String
    /// The Model menu; `follow` re-defaults it on a harness change.
    @State private var model = ModelSelection()
    @State private var flow = LaunchFlow()
    @State private var launch: Task<Void, Never>?
    /// The session list as it was when the sheet opened, for the
    /// harness last used in this worktree.
    @State private var rows: [SessionRow] = []

    init(repo: String, detail: PullDetail, machineId: String?,
         onStarted: @escaping (String) -> Void) {
        self.repo = repo
        self.detail = detail
        self.machineId = machineId
        self.onStarted = onStarted
        _prompt = State(initialValue: detail.suggestedPrompt ?? "")
    }

    /// The machine the worktree lives on: the hub itself.
    private var machine: Machine? {
        app.machines.first { $0.id == machineId } ?? app.machines.first
    }

    var body: some View {
        NavigationStack {
            PageScroll {
                if let machine {
                    StartsInCard(icon: "arrow.triangle.pull", title: "\(repo)#\(detail.number) worktree",
                                 trailing: "on \(machine.displayName)")
                        .padding(.top, 4)
                    HarnessPicker(machine: machine, harness: $harness, sessions: rows)
                    ModelField(machine: machine, harness: harness,
                               selection: $model,
                               sessions: rows, disabled: flow.busy)
                    promptSection
                    startSection(machine)
                } else {
                    StateCard(icon: "terminal", title: "No machine can start a session",
                              message: NewSessionOptions.noMachineMessage(app.machines))
                }
            }
            .navigationTitle("Open in session")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        launch?.cancel()
                        dismiss()
                    }
                }
            }
        }
        .interactiveDismissDisabled(flow.busy)
        .onAppear {
            rows = app.sessions
            // Same default as the new-session sheet: the harness last
            // run in this PR's worktree, else the first on offer.
            if harness == nil, let machine {
                harness = NewSessionOptions.defaultHarness(
                    folder: detail.worktree, machineId: machine.id,
                    offered: NewSessionOptions.harnesses(on: machine),
                    sessions: rows)
            }
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

    private var promptSection: some View {
        FirstPromptField(prompt: $prompt) {
            if prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Text("Leave it empty and the hub writes the review brief.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 4)
            }
        }
    }

    private func startSection(_ machine: Machine) -> some View {
        // A blank prompt is fine: the hub writes its review brief then.
        let ready = harness != nil
        return VStack(alignment: .leading, spacing: 8) {
            LaunchStartButton(flow: flow, ready: ready,
                              footnote: "Runs under tmux on \(machine.displayName) in a checkout of the PR's head. The chat opens once the session shows up.") {
                launch = Task { await start(machine) }
            }
        }
    }

    private func start(_ machine: Machine) async {
        guard let client = app.client, let harness else { return }
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        let pick = model.model(for: harness, on: machine)
        let key = await flow.run(
            post: { try await client.openPull(repo: repo, number: detail.number, harness: harness, prompt: text, model: pick) },
            fetch: { try await client.launch($0) })
        guard let key, !Task.isCancelled else { return }
        await app.refresh()
        guard !Task.isCancelled else { return }
        onStarted(key)
        dismiss()
    }
}
