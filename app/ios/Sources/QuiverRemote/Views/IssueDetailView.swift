import SwiftUI

/// One issue in full: title and meta, the rendered body, its comments
/// (markdown, as the chat renders agent text), then Open on GitHub and
/// the primary Resolve with agent. Reads `GET /v1/issues/<o>/<r>/<n>`.
struct IssueDetailView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    let repo: String
    let number: Int
    /// The tab pushes the new session's chat once a launch lands.
    var onStarted: (String) -> Void

    @State private var reply: IssueResponse?
    @State private var error: String?
    @State private var resolving = false
    @State private var startedKey: String?
    /// Bumped per load so a late reply can't overwrite a newer one.
    @State private var generation = 0

    var body: some View {
        PageScroll {
            if let issue = reply?.issue {
                // A failed refresh keeps the issue it already had — the
                // notice says why it's stale rather than replacing it.
                if let error {
                    Label("Couldn't refresh: \(error)", systemImage: "exclamationmark.triangle")
                        .font(Theme.caption).foregroundStyle(Theme.warningText)
                        .lineLimit(2)
                }
                header(issue)
                actions(issue)
                if let body = issue.body, !body.isEmpty {
                    GroupLabel(text: "Description")
                    Card { RichText(text: body) }
                }
                commentsSection(issue)
            } else if let error {
                StateCard(icon: "exclamationmark.triangle", title: "Can't load this issue",
                          message: error, tone: .error,
                          actionTitle: "Try again") { Task { await load(refresh: true) } }
            } else if let status = reply?.status, status != "ok" {
                StateCard(icon: "exclamationmark.circle", title: "Can't load this issue",
                          message: reply?.error ?? "GitHub answered \(status).", tone: .error,
                          actionTitle: "Try again") { Task { await load(refresh: true) } }
            } else {
                LoadingRow(text: "Loading issue")
            }
        }
        .refreshable { await load(refresh: true) }
        .navigationTitle("\(repo)#\(number)")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    // The app's own deep link opens straight back here
                    // from wherever it's pasted; the share sheet wants
                    // the GitHub one.
                    Button {
                        UIPasteboard.general.url = IssueLink(repo: repo, number: number).url
                    } label: {
                        Label("Copy link", systemImage: "doc.on.doc")
                    }
                    if let url = reply?.issue?.url.flatMap(URL.init) {
                        ShareLink(item: url) {
                            Label("Share on GitHub", systemImage: "square.and.arrow.up")
                        }
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
        }
        .task { if reply == nil { await load() } }
        // The chat opens after the sheet is gone, so the push isn't
        // lost under the dismiss animation.
        .sheet(isPresented: $resolving, onDismiss: {
            if let key = startedKey { onStarted(key) }
            startedKey = nil
        }) {
            ResolveIssueSheet(repo: repo, number: number, detail: reply?.issue,
                              machineId: reply?.machineId) { startedKey = $0 }
        }
    }

    private func load(refresh: Bool = false) async {
        guard let client = app.client else { return }
        generation += 1
        let gen = generation
        do {
            let body = try await client.issue(repo: repo, number: number, refresh: refresh)
            guard gen == generation else { return }
            if body.issue == nil, reply?.issue != nil {
                // A `gh` failure answers 200 with `issue: null`: keep the
                // issue already shown and let the notice say why it's
                // stale, rather than blanking the page for an outage.
                error = body.error ?? "GitHub couldn't read the issue."
            } else {
                reply = body
                error = nil
            }
        } catch {
            guard gen == generation else { return }
            self.error = (error as? HubError)?.message ?? error.localizedDescription
        }
    }

    // MARK: Sections

    private func header(_ issue: IssueDetail) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(issue.title ?? "Untitled")
                .font(Theme.sectionTitle).foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 6) {
                IssueGlyph(state: issue.state)
                Text(stateLine(issue))
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .lineLimit(2)
            }
            stats(issue)
        }
    }

    /// "Open · by alice · opened 3d ago"
    private func stateLine(_ issue: IssueDetail) -> String {
        var parts = [(issue.state ?? "open").capitalized]
        if let author = issue.author, !author.isEmpty, author != "-" {
            parts.append("by \(author)")
        }
        let when = RelativeTime.short(issue.updatedDate?.timeIntervalSince1970)
        if !when.isEmpty { parts.append(when) }
        return parts.joined(separator: " · ")
    }

    /// "3 comments" then the label and assignee badges.
    private func statBits(_ issue: IssueDetail) -> [String] {
        var bits: [String] = []
        let comments = issue.comments?.count ?? 0
        if comments > 0 {
            bits.append("\(comments) comment\(comments == 1 ? "" : "s")")
        }
        return bits
    }

    @ViewBuilder private func stats(_ issue: IssueDetail) -> some View {
        let bits = statBits(issue)
        let labels = issue.labels ?? []
        let assignees = issue.assignees ?? []
        if !bits.isEmpty || !labels.isEmpty || !assignees.isEmpty {
            HStack(spacing: 4) {
                if !bits.isEmpty {
                    Text(bits.joined(separator: " · "))
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                }
                ForEach(labels.prefix(4), id: \.self) { Badge($0) }
                ForEach(assignees.prefix(2), id: \.self) { Badge("@\($0)") }
            }
        }
    }

    private func actions(_ issue: IssueDetail) -> some View {
        VStack(spacing: 8) {
            Button { resolving = true } label: {
                Label("Resolve with agent", systemImage: "sparkles")
            }
            .primaryButton()
            if let url = issue.url.flatMap(URL.init) {
                Button { openURL(url) } label: {
                    Label("Open on GitHub", systemImage: "safari")
                }
                .secondaryButton()
            }
        }
        .padding(.top, 4)
    }

    /// The issue's comments, oldest first as `gh` returns them, each a
    /// card with the author and age over the rendered body.
    @ViewBuilder private func commentsSection(_ issue: IssueDetail) -> some View {
        let comments = issue.comments ?? []
        if !comments.isEmpty {
            GroupLabel(text: "Comments", trailing: "\(comments.count)")
            ForEach(comments, id: \.self) { comment in
                CommentCard(comment: comment)
            }
        }
    }
}

/// One comment's card: the author and how long ago, then the body the
/// chat's markdown renderer draws.
private struct CommentCard: View {
    @Environment(\.openURL) private var openURL
    let comment: IssueComment

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    Text(comment.author ?? "-")
                        .font(Theme.control).foregroundStyle(Theme.textPrimary).lineLimit(1)
                    let when = RelativeTime.short(GitHubTime.date(comment.createdAt)?.timeIntervalSince1970)
                    if !when.isEmpty {
                        Text("· \(when)").font(Theme.caption).foregroundStyle(Theme.textMuted)
                    }
                    Spacer(minLength: 8)
                    if let url = comment.url.flatMap(URL.init) {
                        Button { openURL(url) } label: {
                            Image(systemName: "arrow.up.right")
                                .font(.system(size: 12)).foregroundStyle(Theme.textMuted)
                        }
                        .buttonStyle(.plain)
                    }
                }
                if let body = comment.body, !body.isEmpty {
                    RichText(text: body)
                }
            }
        }
    }
}

extension IssueDetail {
    var updatedDate: Date? { GitHubTime.date(updatedAt) }
}

/// The resolve sheet: the issue's worktree is chosen by the hub, so the
/// sheet only asks which harness and with what first prompt — prefilled
/// with the resolve brief the hub built. A row's Resolve hands no detail
/// over, so the sheet fetches the issue for the prefill itself.
struct ResolveIssueSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let repo: String
    let number: Int
    /// The hub's machine id, from the reply that opened the sheet; the
    /// worktree lives there, so only its harnesses are on offer.
    let machineId: String?
    var onStarted: (String) -> Void

    @State private var detail: IssueDetail?
    /// Why the prompt field is empty: the detail fetch failed, so the
    /// suggested prompt never arrived to prefill it.
    @State private var detailNote: String?
    @State private var harness: String?
    /// A harness the user tapped stays: the late-arriving detail only
    /// improves the *default*.
    @State private var harnessTouched = false
    @State private var prompt: String
    @State private var model = ""
    /// What the Model field last prefilled: `model` differing from it
    /// means the user typed, so a harness change must not overwrite.
    @State private var modelFilled = ""
    @State private var flow = LaunchFlow()
    @State private var launch: Task<Void, Never>?
    /// The session list as it was when the sheet opened, for the
    /// harness last used in this worktree.
    @State private var rows: [SessionRow] = []

    init(repo: String, number: Int, detail: IssueDetail?, machineId: String?,
         onStarted: @escaping (String) -> Void) {
        self.repo = repo
        self.number = number
        self.machineId = machineId
        self.onStarted = onStarted
        _detail = State(initialValue: detail)
        _prompt = State(initialValue: detail?.suggestedPrompt ?? "")
    }

    /// The machine the worktree lives on: the hub itself.
    private var machine: Machine? {
        app.machines.first { $0.id == machineId } ?? app.machines.first
    }

    /// `quiver/issue-<n>` — known before the detail lands, since the
    /// branch name is the hub's own scheme.
    private var branch: String { detail?.branch ?? "quiver/issue-\(number)" }

    var body: some View {
        NavigationStack {
            PageScroll {
                if let machine {
                    startsIn(machine)
                    harnessSection(machine)
                    ModelField(machine: machine, harness: harness, text: $model, sessions: rows, disabled: flow.busy)
                    promptSection
                    startSection(machine)
                } else {
                    StateCard(icon: "terminal", title: "No machine can start a session",
                              message: "Update swe on the hub. A machine offers the harnesses swe lists there, minus the ones switched off in Quiver's Provider visibility.")
                }
            }
            .navigationTitle("Resolve issue")
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
            if harness == nil { harness = defaultHarness() }
        }
        .onChange(of: harness) { _, _ in
            if let machine { refillModel(on: machine) }
        }
        .task { await loadDetail() }
        .onDisappear { launch?.cancel() }
    }

    /// The Model field's prefill after a harness change: an untouched
    /// field takes the machine's last-used model for the new harness, a
    /// typed one keeps what the user wrote.
    private func refillModel(on machine: Machine) {
        let next = NewSessionOptions.modelText(text: model, filled: modelFilled, for: harness, on: machine)
        model = next.text
        modelFilled = next.filled
    }

    /// Same default as the new-session sheet: the harness last run in
    /// this issue's worktree, else the first on offer. The worktree is
    /// unknown until the detail lands, so a row's Resolve starts from
    /// the first offered and is improved by `loadDetail`.
    private func defaultHarness() -> String? {
        guard let machine else { return nil }
        return NewSessionOptions.defaultHarness(
            folder: detail?.worktree, machineId: machine.id,
            offered: NewSessionOptions.harnesses(on: machine),
            sessions: rows)
    }

    /// The detail fetch behind a row's Resolve: only for the prompt
    /// prefill, so a failure never blocks the sheet — a blank field
    /// sends nothing and the hub builds the same brief itself.
    private func loadDetail() async {
        guard detail == nil, let client = app.client else { return }
        do {
            let body = try await client.issue(repo: repo, number: number)
            guard let issue = body.issue else {
                detailNote = body.error ?? "GitHub couldn't read the issue."
                return
            }
            detail = issue
            // Only an untouched field gets the prefill: whatever the
            // user typed while the fetch ran is theirs to keep.
            if prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                prompt = issue.suggestedPrompt ?? ""
            }
            // The worktree is now known, so the last-harness-used
            // default can match it — unless the user already picked.
            if !harnessTouched { harness = defaultHarness() }
        } catch {
            detailNote = (error as? HubError)?.message ?? error.localizedDescription
        }
    }

    private func startsIn(_ machine: Machine) -> some View {
        Card(padding: 0) {
            HStack(spacing: 12) {
                Image(systemName: "exclamationmark.circle").font(.system(size: 15)).foregroundStyle(Theme.textSecondary).frame(width: 20)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Starts in").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    Text("\(repo) · \(branch)")
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
                Button { harness = h; harnessTouched = true } label: {
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
            if let detailNote {
                Text("Couldn't load the suggested prompt: \(detailNote) Start anyway and the hub writes it.")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func startSection(_ machine: Machine) -> some View {
        let ready = harness != nil
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
            Text("Runs under tmux on \(machine.displayName) on branch \(branch) in a worktree of \(repo). The chat opens once the session shows up.")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func start(_ machine: Machine) async {
        guard let client = app.client, let harness else { return }
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        let pick = NewSessionOptions.modelPick(model, for: harness, on: machine)
        let key = await flow.run(
            post: { try await client.resolveIssue(repo: repo, number: number, harness: harness, prompt: text, model: pick) },
            fetch: { try await client.launch($0) })
        guard let key, !Task.isCancelled else { return }
        await app.refresh()
        guard !Task.isCancelled else { return }
        onStarted(key)
        dismiss()
    }
}
