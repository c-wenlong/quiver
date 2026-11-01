import SwiftUI

/// P4 new-session sheet: machine, harness, first prompt and Start on
/// top, then the folder list, which can run to hundreds of rows. Once the
/// hub names the new session, `onStarted` gets its key so Sessions can
/// open the chat.
struct NewSessionView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    var onStarted: (String) -> Void

    @State private var machineId: String?
    @State private var folder: String?
    @State private var harness: String?
    @State private var harnessPicked = false
    @State private var prompt = ""
    /// The Model menu; `follow` re-defaults it on a harness change.
    @State private var model = ModelSelection()
    /// The Autonomy switch: on until the user turns it off. What the
    /// launch actually takes is gated per harness by `acceptsYolo`.
    @State private var yolo = true
    /// The Provider menu's slug; nil is the harness default. Cleared
    /// when a harness or machine change stops offering it.
    @State private var provider: String?
    @State private var browsePath: [FolderBrowse] = []
    @State private var flow = LaunchFlow()
    /// The running launch, so Cancel or a swipe-down stops it before it
    /// can open a chat behind a sheet that is gone.
    @State private var launch: Task<Void, Never>?
    /// The session list as it was when the sheet opened: the 15 s poll
    /// would otherwise re-sort the folders under a finger.
    @State private var rows: [SessionRow] = []

    var body: some View {
        NavigationStack(path: $browsePath) {
            form
                .navigationDestination(for: FolderBrowse.self) { place in
                    FolderBrowserView(machineId: place.machineId, path: place.path) { picked in
                        if let machine = app.machines.first(where: { $0.id == place.machineId }) {
                            pick(folder: picked, machine: machine)
                        }
                        browsePath = []
                    }
                }
        }
        // On the stack, not the form: pushing Browse makes the form
        // disappear too, and that must not stop a running launch.
        .onDisappear { launch?.cancel() }
    }

    private var form: some View {
        let machines = NewSessionOptions.machines(app.machines)
        let machine = machines.first { $0.id == machineId }
        return PageScroll {
            if machines.isEmpty {
                StateCard(icon: "terminal", title: "No machine can start a session",
                          message: NewSessionOptions.noMachineMessage(app.machines))
            } else {
                GroupLabel(text: "Machine")
                machinePicker(machines)
                if let machine {
                    HarnessPicker(machine: machine, harness: $harness, sessions: rows) { harnessPicked = true }
                    ModelField(machine: machine, harness: harness,
                               selection: $model,
                               sessions: rows, disabled: flow.busy)
                    ProviderField(machine: machine, harness: harness,
                                  selection: $provider, disabled: flow.busy)
                    YoloField(machine: machine, harness: harness,
                              isOn: $yolo,
                              sessions: rows, disabled: flow.busy)
                    FirstPromptField(prompt: $prompt)
                    startSection(machine)
                    RecentFolderPicker(machine: machine, sessions: rows, selected: folder, disabled: flow.busy,
                                       browse: { browse(on: machine) },
                                       pick: { pick(folder: $0, machine: machine) })
                }
            }
        }
        .navigationTitle("New session")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancel") {
                    launch?.cancel()
                    dismiss()
                }
            }
        }
        .interactiveDismissDisabled(flow.busy)
        .onAppear {
            guard machineId == nil else { return }
            rows = app.sessions
            select(machine: NewSessionOptions.machines(app.machines).first)
        }
        .onChange(of: harness) { _, _ in
            if let machine { refillModel(on: machine) }
        }
        // A machine switch can keep the same harness while its
        // recent_models change, so the menu follows the machine too.
        .onChange(of: machineId) { _, _ in
            if let machine { refillModel(on: machine) }
        }
    }

    // MARK: Sections

    private func machinePicker(_ machines: [Machine]) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(machines) { m in
                    let selected = m.id == machineId
                    Button { select(machine: m) } label: {
                        HStack(spacing: 6) {
                            Circle().fill(m.online == true ? Theme.accentFill : Theme.borderStrong).frame(width: 7, height: 7)
                            Text(m.displayName).lineLimit(1)
                        }
                        .font(Theme.control)
                        .foregroundStyle(selected ? Theme.textPrimary : Theme.textSecondary)
                        .padding(.horizontal, 12)
                        .scaledMinHeight(Theme.controlHeight)
                        .background(selected ? Theme.selected : Theme.card, in: .rect(cornerRadius: Theme.controlRadius))
                        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
                            .strokeBorder(selected ? Theme.borderStrong : Theme.border, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .disabled(flow.busy)
                    .accessibilityAddTraits(selected ? .isSelected : [])
                }
            }
        }
    }

    private func startSection(_ machine: Machine) -> some View {
        let ready = folder != nil && harness != nil && !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return VStack(alignment: .leading, spacing: 8) {
            // The folder picked below, restated here so Start says where
            // it runs without scrolling down.
            StartsInCard(icon: "folder", title: folder.map(FolderRow.name) ?? "Pick a folder below",
                         titleMuted: folder == nil,
                         trailing: folder.map { SessionGrouping.abbreviateHome(FolderRow.parent($0)) },
                         trailingTruncation: .head)
                .padding(.top, 8)
            LaunchStartButton(flow: flow, ready: ready,
                              footnote: "Runs under tmux on \(machine.displayName). The chat opens once the session shows up.") {
                launch = Task { await start(machine) }
            }
        }
    }

    // MARK: Actions

    private func select(machine: Machine?) {
        guard let machine, !flow.busy else { return }
        machineId = machine.id
        harnessPicked = false
        flow.reset()
        pick(folder: NewSessionOptions.folders(on: machine.id, sessions: rows).first, machine: machine)
    }

    /// Browse opens beside the picked folder, or at the home folder.
    private func browse(on machine: Machine) {
        browsePath = [FolderBrowse(machineId: machine.id, path: folder.map(FolderRow.parent))]
    }

    private func pick(folder path: String?, machine: Machine) {
        folder = path
        let offered = NewSessionOptions.harnesses(on: machine)
        if !harnessPicked || !(harness.map(offered.contains) ?? false) {
            harness = NewSessionOptions.defaultHarness(folder: path, machineId: machine.id, offered: offered, sessions: rows)
        }
    }

    /// The Model menu after a harness or machine change;
    /// `ModelSelection.follow` says which picks survive it.
    private func refillModel(on machine: Machine) {
        model.follow(harness, on: machine)
        provider = NewSessionOptions.providerPick(provider, for: harness, on: machine)
    }

    private func start(_ machine: Machine) async {
        guard let client = app.client, let folder, let harness else { return }
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        let pick = model.model(for: harness, on: machine)
        let autonomous = yolo && NewSessionOptions.acceptsYolo(harness, on: machine)
        let via = NewSessionOptions.providerPick(provider, for: harness, on: machine)
        let key = await flow.run(
            post: { try await client.startSession(machine: machine.id, harness: harness, cwd: folder, prompt: text, model: pick, yolo: autonomous, provider: via) },
            fetch: { try await client.launch($0) })
        guard let key, !Task.isCancelled else { return }
        await app.refresh()
        guard !Task.isCancelled else { return }
        onStarted(key)
        dismiss()
    }
}

/// One step of the folder browser: a machine and a folder on it (nil is
/// the hub user's home).
struct FolderBrowse: Hashable {
    var machineId: String
    var path: String?
}

/// Walks a machine's folders through `GET /v1/folders`: tap a folder to
/// open it, Use to start there.
struct FolderBrowserView: View {
    @Environment(AppModel.self) private var app
    var machineId: String
    var path: String?
    var onPick: (String) -> Void

    /// Where this screen reads from; Home replaces it when `path` is gone
    /// (an old worktree folder).
    @State private var current: String??
    @State private var listing: FolderListing?
    @State private var error: String?
    @State private var query = ""

    var body: some View {
        PageScroll {
            if let listing {
                Text(SessionGrouping.abbreviateHome(listing.path))
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .lineLimit(2).truncationMode(.head)
                    .padding(.horizontal, 4)
                // Browse opens beside the picked folder; this climbs higher.
                if let parent = listing.parent {
                    CardList(data: [FolderPath(path: parent)]) { up in
                        NavigationLink(value: FolderBrowse(machineId: machineId, path: up.path)) {
                            HStack(spacing: 12) {
                                Image(systemName: "arrow.up").font(Theme.rowIcon).foregroundStyle(Theme.textSecondary).iconColumn(20)
                                Text("Up to \(FolderRow.name(up.path))").font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                                Spacer(minLength: 8)
                            }
                            .padding(.vertical, Theme.rowVertical).padding(.horizontal, Theme.rowHorizontal)
                            .contentShape(.rect)
                        }
                        .buttonStyle(RowButtonStyle())
                    }
                }
                let shown = NewSessionOptions.matching(listing.folders.map(\.path), query: query)
                if listing.folders.isEmpty {
                    StateCard(icon: "folder", title: "No folders inside", message: "Use this folder, or go back.")
                } else if shown.isEmpty {
                    Text("No folder here matches.").font(Theme.caption).foregroundStyle(Theme.textSecondary).padding(.horizontal, 4)
                } else {
                    CardList(data: listing.folders.filter { shown.contains($0.path) }) { entry in
                        NavigationLink(value: FolderBrowse(machineId: machineId, path: entry.path)) {
                            HStack(spacing: 12) {
                                Image(systemName: "folder").font(Theme.rowIcon).foregroundStyle(Theme.textSecondary).iconColumn(20)
                                Text(entry.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                                Spacer(minLength: 8)
                                Image(systemName: "chevron.right").font(Theme.chevron).foregroundStyle(Theme.textMuted)
                            }
                            .padding(.vertical, Theme.rowVertical).padding(.horizontal, Theme.rowHorizontal)
                            .contentShape(.rect)
                        }
                        .buttonStyle(RowButtonStyle())
                    }
                    if listing.truncated == true {
                        Text("Showing the first \(listing.folders.count). Search narrows them.")
                            .font(Theme.caption).foregroundStyle(Theme.textMuted).padding(.horizontal, 4)
                    }
                }
            } else if let error {
                StateCard(icon: "exclamationmark.triangle", title: "Can't open this folder", message: error, tone: .error,
                          actionTitle: "Try again") { Task { await load() } }
                if (current ?? path) != nil {
                    Button("Open your home folder") {
                        current = .some(nil)
                        Task { await load() }
                    }
                    .secondaryButton(compact: true)
                }
            } else {
                LoadingRow(text: "Loading folders")
            }
        }
        .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Filter folders")
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        .navigationTitle(listing.map { FolderRow.name($0.path) } ?? "Folders")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Use") { if let listing { onPick(listing.path) } }
                    .disabled(listing == nil)
            }
        }
        .task { if listing == nil { await load() } }
    }

    private func load() async {
        guard let client = app.client else { return }
        error = nil
        do {
            listing = try await client.folders(machine: machineId, path: current ?? path)
        } catch is CancellationError {
            // The picker closed mid-fetch: nothing failed.
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// The Model row every launch sheet shows under its harness picker: a
/// menu of Harness default, the machine's recent models for the harness
/// (newest first) and Other, which opens a field for a typed model id.
/// The sheet owns the `ModelSelection` and calls `follow` on a harness
/// or machine change. The row hides on a machine row too old to name
/// the harnesses that honour a pick (an old satellite would refuse one
/// anyway), and disables for a harness that can't — the hub would
/// answer that pick with a 409.
struct ModelField: View {
    let machine: Machine
    let harness: String?
    @Binding var selection: ModelSelection
    /// The sheet's session rows, for the harness's display name.
    var sessions: [SessionRow] = []
    /// A launch in flight locks the menu with the sheet's other inputs.
    var disabled = false

    @FocusState private var otherFocused: Bool

    private var accepts: Bool { NewSessionOptions.acceptsModel(harness, on: machine) }

    var body: some View {
        if NewSessionOptions.showsModelField(on: machine) {
            GroupLabel(text: "Model")
            menu
            if selection.choice == .other {
                TextField("Type a model id", text: $selection.otherText)
                    .quiverField(monospaced: true)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($otherFocused)
                    .disabled(disabled || !accepts)
                    .accessibilityIdentifier("model-other")
            }
            if !accepts, let harness {
                Text("\(NewSessionOptions.name(harness, on: machine, sessions: sessions)) can't take a model at launch: it starts with its own default.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 4)
            }
        }
    }

    private var menu: some View {
        let choice = selection.choice
        let isModel = if case .recent = choice { true } else { false }
        return Menu {
            Picker("Model", selection: Binding(
                get: { selection.choice },
                set: { selection.choose($0) })) {
                ForEach(selection.choices(for: harness, on: machine), id: \.self) { c in
                    Text(c.label).tag(c)
                }
            }
            .pickerStyle(.inline)
        } label: {
            HStack(spacing: 8) {
                Text(choice.label)
                    .font(isModel ? Theme.monoBody : Theme.body)
                    .foregroundStyle(accepts ? Theme.textPrimary : Theme.textMuted)
                    .lineLimit(1).truncationMode(.middle)
                Spacer(minLength: 8)
                Image(systemName: "chevron.up.chevron.down")
                    .font(Theme.font(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.textMuted)
            }
            .padding(.horizontal, 12)
            .scaledMinHeight(Theme.fieldHeight)
            .background(Theme.field, in: .rect(cornerRadius: Theme.blockRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
            .contentShape(.rect)
        }
        .disabled(disabled || !accepts)
        // Picking Other opens its field ready to type.
        .onChange(of: choice) { _, now in if now == .other { otherFocused = true } }
        .accessibilityLabel("Model")
        .accessibilityValue(choice.label)
        .accessibilityIdentifier("model-menu")
    }
}

struct FolderRow: View {
    var path: String
    var selected: Bool

    static func name(_ path: String) -> String { path.split(separator: "/").last.map(String.init) ?? path }
    static func parent(_ path: String) -> String { (path as NSString).deletingLastPathComponent }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "folder").font(Theme.rowIcon).foregroundStyle(Theme.textSecondary).iconColumn(20)
            VStack(alignment: .leading, spacing: 2) {
                Text(Self.name(path)).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                Text(SessionGrouping.abbreviateHome(Self.parent(path))).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .lineLimit(1).truncationMode(.head)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if selected {
                Image(systemName: "checkmark").font(Theme.font(size: 13, weight: .semibold)).foregroundStyle(Theme.textPrimary)
            }
        }
        .padding(.vertical, Theme.rowVertical)
        .padding(.horizontal, Theme.rowHorizontal)
        .contentShape(.rect)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}
