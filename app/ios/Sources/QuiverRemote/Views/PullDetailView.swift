import SwiftUI

/// One pull request in full: title and meta, the rendered body, the
/// per-check rows and reviews, then Open on GitHub and the primary
/// Open in session. Reads `GET /v1/pulls/<o>/<r>/<n>`.
struct PullDetailView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    let repo: String
    let number: Int
    /// The tab pushes the new session's chat once a launch lands.
    var onStarted: (String) -> Void

    @State private var reply: PullResponse?
    @State private var error: String?
    @State private var opening = false
    @State private var startedKey: String?

    var body: some View {
        PageScroll {
            if let pr = reply?.pr {
                // A failed refresh keeps the PR it already had — the
                // notice says why it's stale rather than replacing it.
                if let error {
                    Label("Couldn't refresh: \(error)", systemImage: "exclamationmark.triangle")
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
        do {
            let body = try await client.pull(repo: repo, number: number, refresh: refresh)
            reply = body
            error = nil
        } catch {
            self.error = (error as? HubError)?.message ?? error.localizedDescription
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
        if !bits.isEmpty || !labels.isEmpty {
            HStack(spacing: 4) {
                if !bits.isEmpty {
                    Text(bits.joined(separator: " · "))
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                }
                ForEach(labels.prefix(4), id: \.self) { Badge($0) }
            }
        }
    }

    private func actions(_ pr: PullDetail) -> some View {
        VStack(spacing: 8) {
            Button { opening = true } label: {
                Label("Open in session", systemImage: "play")
            }
            .primaryButton()
            if let url = pr.url.flatMap(URL.init) {
                Button { openURL(url) } label: {
                    Label("Open on GitHub", systemImage: "safari")
                }
                .secondaryButton()
            }
        }
        .padding(.top, 4)
    }

    @ViewBuilder private func checksSection(_ pr: PullDetail) -> some View {
        let runs = pr.checkRuns ?? []
        if !runs.isEmpty {
            GroupLabel(text: "Checks", trailing: pr.checks)
            CardList(data: runs.enumerated().map(CheckSlot.init)) { slot in
                let run = slot.run
                let row = HStack(spacing: 12) {
                    ChecksGlyph(state: run.state)
                    Text(run.name ?? "check").font(Theme.control).foregroundStyle(Theme.textPrimary).lineLimit(1)
                    if let workflow = run.workflow, !workflow.isEmpty {
                        Text(workflow).font(Theme.caption).foregroundStyle(Theme.textMuted).lineLimit(1)
                    }
                    Spacer(minLength: 8)
                    if run.url != nil {
                        Image(systemName: "arrow.up.right").font(.system(size: 12)).foregroundStyle(Theme.textMuted)
                    }
                }
                .padding(.vertical, Theme.rowVertical - 2)
                .padding(.horizontal, Theme.rowHorizontal)
                .contentShape(.rect)
                if let url = run.url.flatMap(URL.init) {
                    Button { openURL(url) } label: { row }
                        .buttonStyle(RowButtonStyle())
                } else {
                    row
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
                    startsIn(machine)
                    harnessSection(machine)
                    promptSection
                    startSection(machine)
                } else {
                    StateCard(icon: "terminal", title: "No machine can start a session",
                              message: "Update swe on the hub. A machine offers the harnesses swe lists there, minus the ones switched off in Quiver's Provider visibility.")
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
        .onDisappear { launch?.cancel() }
    }

    private func startsIn(_ machine: Machine) -> some View {
        Card(padding: 0) {
            HStack(spacing: 12) {
                Image(systemName: "arrow.triangle.pull").font(.system(size: 15)).foregroundStyle(Theme.textSecondary).frame(width: 20)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Starts in").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    Text("\(repo)#\(detail.number) worktree")
                        .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                }
                Spacer(minLength: 8)
                Text("on \(machine.displayName)")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
            }
            .padding(.vertical, Theme.rowVertical).padding(.horizontal, Theme.rowHorizontal)
        }
        .padding(.top, 4)
    }

    @ViewBuilder private func harnessSection(_ machine: Machine) -> some View {
        let offered = NewSessionOptions.harnesses(on: machine)
        GroupLabel(text: "Harness", trailing: harness.map { NewSessionOptions.name($0, on: machine, sessions: rows) })
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 48, maximum: 48), spacing: 8)], alignment: .leading, spacing: 8) {
            ForEach(offered, id: \.self) { h in
                let selected = h == harness
                let name = NewSessionOptions.name(h, on: machine, sessions: rows)
                Button { harness = h } label: {
                    HarnessIcon(harness: h, name: name, size: 24)
                        .font(.system(size: 10))
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                        .frame(width: 48, height: 48)
                        .background(selected ? Theme.selected : Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
                        .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius)
                            .strokeBorder(selected ? Theme.textPrimary : Theme.border, lineWidth: selected ? 1.5 : 1))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(name)
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
        if let harness, machine.untrackedHarnesses?.contains(harness) == true {
            Text("Quiver can't read \(NewSessionOptions.name(harness, on: machine, sessions: rows)) sessions yet: it starts, but no chat opens.")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 4)
        }
    }

    private var promptSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            GroupLabel(text: "First prompt")
            TextField("Describe the first task", text: $prompt, axis: .vertical)
                .lineLimit(4...10)
                .font(Theme.body)
                .foregroundStyle(Theme.textPrimary)
                .padding(12)
                .background(Theme.field, in: .rect(cornerRadius: Theme.blockRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
        }
    }

    private func startSection(_ machine: Machine) -> some View {
        let ready = harness != nil && !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return VStack(alignment: .leading, spacing: 8) {
            Button { launch = Task { await start(machine) } } label: {
                HStack(spacing: 8) {
                    if flow.busy { ProgressView().controlSize(.small).tint(Theme.primaryForeground) }
                    Text(flow.phase == .waiting ? "Waiting for the session" : flow.opening ? "Opening the chat" : flow.busy ? "Starting" : "Start session")
                }
            }
            .primaryButton()
            .disabled(!ready || flow.busy)
            if case .failed(let message) = flow.phase {
                Text(message).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
            } else if case .unconfirmed(let message) = flow.phase {
                Text(message).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("Runs under tmux on \(machine.displayName) in a checkout of the PR's head. The chat opens once the session shows up.")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func start(_ machine: Machine) async {
        guard let client = app.client, let harness else { return }
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        let key = await flow.run(
            post: { try await client.openPull(repo: repo, number: detail.number, harness: harness, prompt: text) },
            fetch: { try await client.launch($0) })
        guard let key, !Task.isCancelled else { return }
        await app.refresh()
        guard !Task.isCancelled else { return }
        onStarted(key)
        dismiss()
    }
}
