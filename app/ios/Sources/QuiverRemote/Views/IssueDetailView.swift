import SwiftUI

/// One issue in full: title and meta, the rendered body, its comments
/// (markdown, as the chat renders agent text) and a comment box, then
/// Open on GitHub and the primary Resolve with agent. Reads `GET
/// /v1/issues/<o>/<r>/<n>`; a comment posts through the hub's own `gh`
/// login (`POST /v1/issues/comment`), as the Mac's posts through `gh`.
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
    /// The resolve sheet posted its context as a comment: read the issue
    /// again once it closes, so the thread lists it.
    @State private var commentedFromSheet = false
    @State private var composer = IssueCommentComposer()
    /// Bumped per load so a late reply can't overwrite a newer one.
    @State private var generation = 0

    var body: some View {
        PageScroll {
            if let issue = reply?.issue {
                // A failed refresh keeps the issue it already had — the
                // notice says why it's stale rather than replacing it.
                if let error {
                    Label { Text(inlineCode: "Couldn't refresh: \(error)") } icon: { Image(systemName: "exclamationmark.triangle") }
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
                commentBox
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
            if commentedFromSheet {
                commentedFromSheet = false
                Task { await load() }
            }
        }) {
            ResolveIssueSheet(repo: repo, number: number, detail: reply?.issue,
                              machineId: reply?.machineId,
                              onCommented: { commentedFromSheet = true }) { startedKey = $0 }
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
        } catch is CancellationError {
            // The page went away mid-fetch: nothing failed.
            return
        } catch {
            guard gen == generation else { return }
            self.error = error.localizedDescription
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
                ForEach(Array(labels.prefix(4).enumerated()), id: \.offset) { Badge($0.element) }
                ForEach(Array(assignees.prefix(2).enumerated()), id: \.offset) { Badge("@\($0.element)") }
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
    /// card with the author and age over the rendered body. The label
    /// stays with none, over the comment box.
    @ViewBuilder private func commentsSection(_ issue: IssueDetail) -> some View {
        let comments = issue.comments ?? []
        GroupLabel(text: "Comments", trailing: comments.isEmpty ? nil : "\(comments.count)")
        ForEach(comments, id: \.self) { comment in
            CommentCard(comment: comment)
        }
    }

    /// The Mac sheet's comment box: posts through the hub's `gh`, then
    /// reads the issue again (the hub dropped its cached copy). A
    /// refusal keeps the draft and says why under the box.
    private var commentBox: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("Add context for whoever picks this up, you or an agent…",
                      text: $composer.draft, axis: .vertical)
                .lineLimit(2...8)
                .font(Theme.body)
                .foregroundStyle(Theme.textPrimary)
                .padding(12)
                .background(Theme.field, in: .rect(cornerRadius: Theme.blockRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
                .disabled(composer.posting)
                .accessibilityLabel("Comment")
            Button { Task { await postComment() } } label: {
                HStack(spacing: 8) {
                    if composer.posting { ProgressView().controlSize(.small) }
                    Text(composer.posting ? "Posting" : "Comment")
                }
            }
            .secondaryButton()
            .disabled(!composer.canPost)
            Text(IssueComments.caption(login: reply?.github?.login, machine: reply?.machine))
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
            if let error = composer.error {
                Text(inlineCode: error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func postComment() async {
        guard let client = app.client else { return }
        let posted = await composer.post { text in
            _ = try await client.commentOnIssue(repo: repo, number: number, body: text)
        }
        if posted { await load() }
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
    /// The context posted as a comment, so the page can show it.
    var onCommented: () -> Void
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
    @State private var context = ""
    /// "Also post as a comment", on by default as on the Mac.
    @State private var postContext = true
    /// The context that already posted on this opening, so a retried
    /// Start doesn't post it again.
    @State private var postedContext: String?
    @State private var commenting = false
    /// The context comment didn't post; the launch went ahead anyway.
    @State private var commentWarning: String?
    /// The Model menu; `follow` re-defaults it on a harness change.
    @State private var model = ModelSelection()
    @State private var flow = LaunchFlow()
    @State private var launch: Task<Void, Never>?
    /// The session list as it was when the sheet opened, for the
    /// harness last used in this worktree.
    @State private var rows: [SessionRow] = []

    init(repo: String, number: Int, detail: IssueDetail?, machineId: String?,
         onCommented: @escaping () -> Void = {},
         onStarted: @escaping (String) -> Void) {
        self.repo = repo
        self.number = number
        self.machineId = machineId
        self.onCommented = onCommented
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
                    StartsInCard(icon: "exclamationmark.circle", title: "\(repo) · \(branch)",
                                 trailing: "on \(machine.displayName)")
                        .padding(.top, 4)
                    HarnessPicker(machine: machine, harness: $harness, sessions: rows) { harnessTouched = true }
                    ModelField(machine: machine, harness: harness,
                               selection: $model,
                               sessions: rows, disabled: flow.busy)
                    contextSection
                    promptSection
                    startSection(machine)
                } else {
                    StateCard(icon: "terminal", title: "No machine can start a session",
                              message: NewSessionOptions.noMachineMessage(app.machines))
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
        .interactiveDismissDisabled(flow.busy || commenting)
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

    /// The Model menu after a harness change; `ModelSelection.follow`
    /// says which picks survive it.
    private func refillModel(on machine: Machine) {
        model.follow(harness, on: machine)
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
        } catch is CancellationError {
            // The sheet closed mid-fetch: nothing failed.
        } catch {
            detailNote = error.localizedDescription
        }
    }

    /// The Mac's "Your context (optional)" and its "Also post as a
    /// comment" switch. The context joins the hub's prompt either way.
    private var contextSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            GroupLabel(text: "Your context (optional)")
            TextField("Anything the agent should know that the issue doesn't say.",
                      text: $context, axis: .vertical)
                .lineLimit(2...6)
                .font(Theme.body)
                .foregroundStyle(Theme.textPrimary)
                .padding(12)
                .background(Theme.field, in: .rect(cornerRadius: Theme.blockRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
                .disabled(flow.busy || commenting)
            Toggle("Also post as a comment on #\(number)", isOn: $postContext)
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .tint(Theme.accentFill)
                .disabled(flow.busy || commenting)
        }
    }

    private var promptSection: some View {
        FirstPromptField(prompt: $prompt) {
            if let detailNote {
                Text("Couldn't load the suggested prompt: \(detailNote) Start anyway and the hub writes it.")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func startSection(_ machine: Machine) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if commenting {
                Text("Posting your context to GitHub…")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            if let commentWarning {
                Label { Text(inlineCode: commentWarning) } icon: { Image(systemName: "exclamationmark.triangle") }
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            LaunchStartButton(flow: flow, ready: harness != nil && !commenting,
                              footnote: "Runs under tmux on \(machine.displayName) on branch \(branch) in a worktree of \(repo). The chat opens once the session shows up.") {
                launch = Task { await start(machine) }
            }
        }
    }

    private func start(_ machine: Machine) async {
        guard let client = app.client, let harness, !commenting, !flow.busy else { return }
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        let note = context.trimmingCharacters(in: .whitespacesAndNewlines)
        let pick = model.model(for: harness, on: machine)
        commentWarning = nil
        if IssueComments.postsComment(context: note, ticked: postContext, posted: postedContext) {
            commenting = true
            do {
                _ = try await client.commentOnIssue(repo: repo, number: number, body: note)
                postedContext = note
                onCommented()
            } catch is CancellationError {
                commenting = false
                return
            } catch {
                commentWarning = IssueComments.contextWarning(
                    (error as? HubError)?.message ?? error.localizedDescription)
            }
            commenting = false
            guard !Task.isCancelled else { return }
        }
        let key = await flow.run(
            post: { try await client.resolveIssue(repo: repo, number: number, harness: harness, prompt: text,
                                                  context: note, model: pick) },
            fetch: { try await client.launch($0) })
        guard let key, !Task.isCancelled else { return }
        await app.refresh()
        guard !Task.isCancelled else { return }
        onStarted(key)
        dismiss()
    }
}
