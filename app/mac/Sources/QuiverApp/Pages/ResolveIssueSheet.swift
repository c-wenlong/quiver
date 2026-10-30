import QuiverCore
import SwiftUI

/// Starts an agent on an issue through this Mac's hub
/// (`POST /v1/issues/resolve`): the hub makes the `quiver/issue-<n>`
/// worktree off the default branch, sends the first prompt and titles the
/// session. The sheet shows the hub's steps while it works, then closes
/// and opens the new session's chat in Sessions. A failed launch stays
/// here with the reason.
struct ResolveIssueSheet: View {
    var selection: IssueSelection

    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss

    /// What the hub can start, once asked; nil while asking.
    @State private var offered: [String]?
    /// The hub machine's own `/v1/machines` row — the launch lands
    /// there, so its `model_at_launch`/`last_models` drive the Model
    /// row. nil while asking or when the ask failed.
    @State private var hubMachine: HubMachine?
    /// Why the hub couldn't be asked: down, unpaired, or refusing.
    @State private var hubProblem: String?
    @State private var harness: String?
    @State private var context = ""
    @State private var postContext = true
    @State private var prompt = ""
    /// The launch `model` menu's state; a harness change refits it
    /// (`ModelPick.refit`).
    @State private var modelPick = ModelPick()
    /// The suggested prompt last put in the field. A newer read replaces
    /// it only while the field still holds it, so edits stay.
    @State private var filledPrompt: String?
    /// The context that already posted as a comment on this opening, so
    /// a retried Start doesn't post it again.
    @State private var postedContext: String?
    @State private var flow = IssueLaunchFlow()
    @State private var launch: Task<Void, Never>?

    private static let columns = Array(repeating: GridItem(.flexible(), spacing: 8), count: 4)

    private var model: GitHubModel { app.github }
    private var key: String { "\(selection.repo)#\(selection.number)" }
    private var state: Loadable<GhIssueDetail> { model.issueDetails[key] ?? .idle }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            title
            switch state {
            case .idle, .loading:
                LoadingCard(text: "Fetching the issue…")
            case .failed(let message):
                ErrorCard(message: message, actionTitle: "Refresh") {
                    Task { await loadIssue() }
                }
            case .loaded(let issue):
                form(issue)
            }
            // Outside the switch: a failed issue read after a launch
            // started must not hide how the launch is going.
            status
            footer(ready: harness != nil && issueLoaded)
        }
        .sheetScaffold(width: 700)
        // A click on the scrim or Esc closes it only while nothing runs;
        // Cancel stops a running launch first.
        .onSheetDismissRequest { if !flow.busy { dismiss() } }
        .task { await loadIssue() }
        .task { await loadHarnesses() }
        .onChange(of: harness) { prefillModel() }
        .onDisappear { launch?.cancel() }
    }

    @ViewBuilder
    private var title: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Resolve #\(selection.number) with an agent")
                .font(Theme.sectionTitle).foregroundStyle(Theme.textPrimary)
            if case .loaded(let issue) = state {
                Text(issue.title).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .lineLimit(2)
            }
        }
    }

    @ViewBuilder
    private func form(_ issue: GhIssueDetail) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                startsIn(issue)
                harnessPicker
                modelField
                contextField
                promptField
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .disabled(flow.busy)
        }
        .frame(maxHeight: 540)
        .onAppear { fillPrompt(issue) }
        .onChange(of: issue.suggestedPrompt) { fillPrompt(issue) }
    }

    /// Where the run happens. Informational: v1 resolves only on the hub.
    private func startsIn(_ issue: GhIssueDetail) -> some View {
        group("Starts in") {
            SettingsCard {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Image(systemName: "arrow.triangle.branch")
                            .font(Theme.font(size: 13)).foregroundStyle(Theme.textMuted)
                        Text("\(selection.repo) · \(issue.branch ?? "quiver/issue-\(issue.number)")")
                            .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                            .lineLimit(1).truncationMode(.middle)
                    }
                    if let worktree = issue.worktree {
                        Text(displayPath(worktree))
                            .font(Theme.font(size: 12, design: .monospaced))
                            .foregroundStyle(Theme.textSecondary)
                            .lineLimit(1).truncationMode(.head)
                            .textSelection(.enabled)
                    }
                    Text("off the default branch · this Mac")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
                .padding(.vertical, 10)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    @ViewBuilder
    private var harnessPicker: some View {
        group("Harness") {
            if let problem = hubProblem {
                Label(problem, systemImage: "exclamationmark.triangle")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let offered {
                if offered.isEmpty {
                    Text("This Mac's hub can't start any harness. Install one, or turn it back on in Settings › Providers.")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    LazyVGrid(columns: Self.columns, spacing: 8) {
                        ForEach(offered, id: \.self) { id in
                            HarnessTile(harness: id, isOn: harness == id) { harness = id }
                        }
                    }
                }
            } else {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Asking the hub what it can start…")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
        }
    }

    /// Whether the hub's launch honours a `model` for the picked
    /// harness — the Model field's enabled state.
    private var takesModel: Bool {
        LaunchOptions.takesModel(harness, on: hubMachine)
    }

    /// The launch `model` pick (#914). Only a hub whose machine row
    /// reports `model_at_launch` gets the row: an older build would
    /// take the pick and silently drop it. A harness outside the list
    /// keeps the menu disabled — its launch always runs the default.
    @ViewBuilder private var modelField: some View {
        if LaunchOptions.showsModel(on: hubMachine) {
            group("Model") {
                ModelMenu(recent: LaunchOptions.recentModels(harness, on: hubMachine),
                          pick: $modelPick, supported: takesModel)
            }
        }
    }

    private var contextField: some View {
        group("Your context (optional)") {
            TextField("Anything the agent should know that the issue doesn't say.",
                      text: $context, axis: .vertical)
                .lineLimit(2...5)
                .quiverField(multiline: true)
            Toggle("Also post as a comment on #\(selection.number)", isOn: $postContext)
                .toggleStyle(.checkbox)
                .font(Theme.caption)
                .foregroundStyle(Theme.textSecondary)
        }
    }

    private var promptField: some View {
        group("First prompt") {
            TextField("The hub writes one from the issue when this is empty.",
                      text: $prompt, axis: .vertical)
                .lineLimit(5...12)
                .quiverField(multiline: true)
        }
    }

    /// The hub's step while it works, then why it stopped.
    @ViewBuilder
    private var status: some View {
        if let warning = flow.commentWarning {
            Label(warning, systemImage: "exclamationmark.triangle")
                .font(Theme.caption).foregroundStyle(Theme.warningText)
                .fixedSize(horizontal: false, vertical: true)
        }
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

    private func footer(ready: Bool) -> some View {
        HStack(spacing: 10) {
            Text("Runs under tmux on this Mac. The chat opens once the session shows up.")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .lineLimit(2)
            Spacer(minLength: 12)
            Button(flow.cancelTitle) {
                launch?.cancel()
                dismiss()
            }
            .secondaryButton()
            .fixedSize()
            .keyboardShortcut(.cancelAction)
            Button {
                launch = Task { await start() }
            } label: {
                HStack(spacing: 6) {
                    if flow.busy {
                        ProgressView().controlSize(.small).tint(Theme.primaryForeground)
                    }
                    Text(flow.busy ? "Starting" : "Start")
                }
            }
            .primaryButton()
            .fixedSize()
            .keyboardShortcut(.return, modifiers: .command)
            .disabled(!ready || flow.busy)
            .hoverTip("Start the session (⌘↩)")
        }
    }

    @ViewBuilder
    private func group<Content: View>(_ title: String,
                                      @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(Theme.caption).foregroundStyle(Theme.textMuted)
            content()
        }
    }

    // MARK: - Actions

    /// Every opening reads the issue again (joining a read already
    /// running), so the title and suggested prompt aren't a stale copy;
    /// a cached read shows meanwhile.
    private func loadIssue() async {
        await model.loadIssueDetail(repo: selection.repo, number: selection.number, swe: app.swe)
    }

    private var issueLoaded: Bool {
        if case .loaded = state { return true }
        return false
    }

    /// The hub's own machine row says what it can start. A hub that is
    /// down or unpaired says so here, in the Sessions chat's words, and
    /// the harnesses installed on this Mac stand in.
    private func loadHarnesses() async {
        await model.loadSessionHarnesses(swe: app.swe)
        let installed = model.sessionHarnesses.map(\.name)
        let local = IssueResolve.localMachineId(home: FileManager.default.homeDirectoryForCurrentUser)
        var list = installed
        do {
            let machines = try await app.hub.withClient { try await $0.machines() }
            list = IssueResolve.offeredHarnesses(machines: machines, localMachineId: local,
                                                 fallback: installed)
            hubMachine = IssueResolve.hubMachine(machines: machines, localMachineId: local)
            hubProblem = nil
        } catch is CancellationError {
            return
        } catch {
            hubMachine = nil
            hubProblem = (error as? HubError)?.message ?? error.localizedDescription
        }
        offered = list
        if harness == nil || !list.contains(harness ?? "") {
            harness = IssueResolve.defaultHarness(offered: list,
                                                  sessions: app.sessions?.rows.map(\.session) ?? [],
                                                  repo: selection.repo)
        }
    }

    private func fillPrompt(_ issue: GhIssueDetail) {
        guard let suggested = issue.suggestedPrompt,
              prompt.isEmpty || prompt == filledPrompt else { return }
        prompt = suggested
        filledPrompt = suggested
    }

    /// Refits the Model menu to the picked harness: an untouched menu
    /// takes its newest model; a pick survives as `ModelPick.refit` says.
    private func prefillModel() {
        modelPick.refit(LaunchOptions.recentModels(harness, on: hubMachine))
    }

    private func start() async {
        guard let harness else { return }
        let repo = selection.repo
        let number = selection.number
        let context = context
        let prompt = prompt
        // A pick goes only for a harness that can honour it.
        let launchModel = modelPick.model(supported: takesModel)
        let posts = IssueResolve.postsComment(context: context, ticked: postContext,
                                              posted: postedContext)
        let model = model
        let swe = app.swe
        let hub = app.hub
        let comment: (() async -> String?)? = posts
            ? {
                let failure = await model.commentOnIssue(repo: repo, number: number,
                                                         body: context, swe: swe)
                if failure == nil { postedContext = context }
                return failure
            }
            : nil
        let key = await flow.run(
            comment: comment,
            post: {
                try await hub.withClient {
                    try await $0.resolveIssue(repo: repo, number: number, harness: harness,
                                              prompt: prompt, context: context, model: launchModel)
                }
            },
            fetch: { id in try await hub.withClient { try await $0.launch(id) } })
        guard let key, !Task.isCancelled else { return }
        dismiss()
        app.openSessionChat(key: key)
    }
}

/// One harness the hub can start: its logo and name, outlined when
/// picked. The first-run sheet's agent tile, single-select, without the
/// tick so four fit across.
struct HarnessTile: View {
    var harness: String
    var isOn: Bool
    var pick: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: pick) {
            HStack(spacing: 8) {
                HarnessIcon(harness: harness, size: 16)
                    .frame(width: 26, height: 26)
                    .background(Theme.panel, in: .rect(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.border, lineWidth: 1))
                Text(HarnessMark(harness).name)
                    .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 12)
            .frame(height: 44)
            .background(isOn ? Theme.cardHover : Theme.card, in: .rect(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12)
                .strokeBorder(isOn ? Theme.textSecondary : (hovering ? Theme.borderHover : Theme.border),
                              lineWidth: 1))
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .focusRing(cornerRadius: 12)
        .onHover { hovering = $0 }
        .accessibilityAddTraits(isOn ? .isSelected : [])
    }
}

extension IssueLaunchFlow {
    /// Once the hub has accepted the launch, cancelling only stops this
    /// sheet waiting: the session starts anyway, so the button says so.
    /// After the wait gave up unconfirmed there is nothing left to stop,
    /// and the button only closes.
    var cancelTitle: String {
        switch phase {
        case .waiting, .started: "Stop waiting"
        case .unconfirmed: "Close"
        default: "Cancel"
        }
    }
}
