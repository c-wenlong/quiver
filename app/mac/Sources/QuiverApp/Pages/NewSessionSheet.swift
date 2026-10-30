import QuiverCore
import SwiftUI

/// The general new-session sheet: a machine, a folder, a launchable
/// harness and the first prompt, then `POST /v1/sessions` and a poll of
/// the launch until it names the session it became — the same flow the
/// web app's `nsStart()` and the iPhone's NewSessionView run. A started
/// key closes the sheet and opens the chat in Sessions.
struct NewSessionSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss

    /// `GET /v1/machines` and `/v1/sessions`, asked once per open.
    @State private var machines: [HubMachine]?
    /// Every row the hub listed, the ones that can launch nothing too:
    /// they say whether an empty `machines` means "update swe".
    @State private var listedMachines: [HubMachine] = []
    @State private var machinesError: String?
    @State private var sessions: [HubSessionRow] = []

    @State private var machineId: String?
    @State private var harness: String?
    /// A harness the user picked stays: the folder default only
    /// improves the *default*.
    @State private var harnessTouched = false
    @State private var folder = ""
    @State private var folderQuery = ""
    @State private var prompt = ""
    /// The launch `model` menu's state; a harness or machine change
    /// refits it (`ModelPick.refit`).
    @State private var modelPick = ModelPick()
    /// The folder browser's place; non-nil swaps the form for it.
    @State private var browse: FolderBrowse?
    @State private var flow = IssueLaunchFlow()
    /// The running launch, so Cancel stops it before it can open a chat
    /// behind a sheet that is gone.
    @State private var launchTask: Task<Void, Never>?

    private static let columns = Array(repeating: GridItem(.flexible(), spacing: 8), count: 4)
    /// How many recent folders list under the field, like the web sheet.
    private static let foldersShown = 6

    /// This Mac's hub machine id from machine.json, read once by load():
    /// as a computed property it read and decoded the file on every body
    /// pass and inside each `first(where:)`.
    @State private var localMachineId: String?

    /// The hub's own machine: the one this Mac's hub runs on, else the
    /// listing's first row. Satellite machines can launch but their
    /// folders can't be browsed from the hub.
    private var hubMachineId: String? {
        localMachineId ?? machines?.first?.id
    }

    private var machine: HubMachine? {
        guard let machines else { return nil }
        return machines.first { $0.id == machineId } ?? machines.first
    }

    private var offered: [String] {
        machine.map { NewSessionOptions.harnesses(on: $0) } ?? []
    }

    /// Recent folders on the picked machine, filtered by the query,
    /// capped for the list.
    private var recentFolders: [String] {
        let all = machine.map { NewSessionOptions.folders(on: $0.id, sessions: sessions) } ?? []
        return Array(NewSessionOptions.matching(all, query: folderQuery).prefix(Self.foldersShown))
    }

    /// The harness a typed or picked folder re-defaults to — the one
    /// last used there, when it is on offer.
    private var untracked: Bool {
        guard let machine, let harness else { return false }
        return (machine.untrackedHarnesses ?? []).contains(harness)
    }

    /// Whether a launch on the picked machine honours a `model` for
    /// the picked harness — the Model field's enabled state.
    private var takesModel: Bool {
        LaunchOptions.takesModel(harness, on: machine)
    }

    private var ready: Bool {
        (harness.map { offered.contains($0) } ?? false) && !machinesPending
            && folder.trimmingCharacters(in: .whitespaces).hasPrefix("/")
            && !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var machinesPending: Bool { machines == nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("New session")
                .font(Theme.sectionTitle).foregroundStyle(Theme.textPrimary)
            if let browse {
                browser(browse)
            } else {
                form
            }
            status
            footer
        }
        .sheetScaffold(width: 700)
        // A click on the scrim or Esc closes it only while nothing runs;
        // Cancel stops a running launch first.
        .onSheetDismissRequest { if !flow.busy { dismiss() } }
        .task { await load() }
        .onChange(of: harness) { prefillModel() }
        // A machine switch re-fills even when the harness stays: the
        // last-used models are the new machine's own.
        .onChange(of: machineId) { prefillModel() }
        .onDisappear { launchTask?.cancel() }
    }

    // MARK: - form

    @ViewBuilder private var form: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                machineSection
                harnessSection
                modelSection
                folderSection
                promptSection
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .disabled(flow.busy)
        }
        .frame(maxHeight: 540)
    }

    private var machineSection: some View {
        group("Machine") {
            if let machinesError {
                Label(machinesError, systemImage: "exclamationmark.triangle")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let machines {
                if machines.isEmpty {
                    Text(NewSessionOptions.noMachineMessage(listedMachines))
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    SelectMenu(label: "On", symbol: "desktopcomputer",
                               options: machines.map { ($0.id, machineTitle($0)) },
                               selection: Binding(get: { machine?.id ?? "" },
                                                  set: { pick(machine: $0) }),
                               width: 280)
                }
            } else {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Reading the machines…")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
        }
    }

    private func machineTitle(_ machine: HubMachine) -> String {
        machine.online == false ? "\(machine.displayName) (offline)" : machine.displayName
    }

    private var harnessSection: some View {
        group("Harness") {
            if machines == nil {
                EmptyView()
            } else if offered.isEmpty {
                Text("This machine can't start any harness. Install one there, or turn it back on in Settings › Providers.")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                LazyVGrid(columns: Self.columns, spacing: 8) {
                    ForEach(offered, id: \.self) { id in
                        HarnessTile(harness: id, isOn: harness == id) {
                            harness = id
                            harnessTouched = true
                        }
                    }
                }
                if untracked {
                    Text("Quiver can't read \(NewSessionOptions.name(harness ?? "", on: machine, sessions: sessions)) sessions yet: it starts, but no chat opens.")
                        .font(Theme.caption).foregroundStyle(Theme.warningText)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// The launch `model` pick (#914). Only a machine row that reports
    /// `model_at_launch` gets the row: an older build would take the
    /// pick and silently drop it. A harness outside the list keeps the
    /// menu disabled — its launch always runs the default.
    @ViewBuilder private var modelSection: some View {
        if LaunchOptions.showsModel(on: machine) {
            group("Model") {
                ModelMenu(recent: LaunchOptions.recentModels(harness, on: machine),
                          pick: $modelPick, supported: takesModel)
            }
        }
    }

    private var folderSection: some View {
        group("Folder") {
            TextField("/absolute/path", text: $folder)
                .quiverField(monospaced: true)
                .onChange(of: folder) { _, _ in harnessFromFolder() }
            let recent = recentFolders
            if !recent.isEmpty || !folderQuery.isEmpty {
                SearchField(prompt: "Recent folders on this machine — type to filter",
                            text: $folderQuery)
                VStack(spacing: 0) {
                    if recent.isEmpty {
                        Text("No recent folders match.")
                            .font(Theme.caption).foregroundStyle(Theme.textMuted)
                            .padding(.vertical, 6)
                    }
                    ForEach(Array(recent.enumerated()), id: \.element) { index, path in
                        Button { pick(folder: path) } label: {
                            Text(displayPath(path))
                                .font(Theme.font(size: 13, design: .monospaced))
                                .foregroundStyle(path == folder ? Theme.textPrimary : Theme.textSecondary)
                                .lineLimit(1).truncationMode(.middle)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 10).padding(.vertical, 6)
                                .contentShape(.rect)
                        }
                        .plainButton(cornerRadius: 0)
                        if index < recent.count - 1 { Divider().overlay(Theme.rowDivider) }
                    }
                }
            }
            // Only the hub machine's disk is listable from here — a
            // satellite's Browse asks the hub for a folder it can't see.
            if let m = machine, m.id == hubMachineId {
                Button {
                    browse = FolderBrowse(path: folder.trimmingCharacters(in: .whitespaces))
                } label: {
                    Text("Browse this machine's folders…")
                        .font(Theme.caption).foregroundStyle(Theme.link)
                }
                .plainButton()
            }
        }
    }

    private var promptSection: some View {
        group("First prompt") {
            TextField("What should the agent do?", text: $prompt, axis: .vertical)
                .lineLimit(4...10)
                .quiverField(multiline: true)
        }
    }

    // MARK: - folder browser

    /// Subfolders of `browse.path` on the hub machine, one level at a
    /// time; "Use this folder" writes the field and goes back.
    private func browser(_ browse: FolderBrowse) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            group("Choose a folder") {
                Text(displayPath(browse.path.isEmpty ? "/" : browse.path))
                    .font(Theme.font(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.textSecondary)
                    .lineLimit(1).truncationMode(.head)
                if browse.loading {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Listing folders…").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                    .frame(minHeight: 120)
                } else {
                    if let error = browse.error {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .font(Theme.caption).foregroundStyle(Theme.warningText)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    ScrollView {
                        VStack(spacing: 0) {
                            if let parent = browse.parent {
                                browseRow(name: "..", path: parent)
                                Divider().overlay(Theme.rowDivider)
                            }
                            ForEach(Array(browse.folders.enumerated()), id: \.element.id) { index, entry in
                                browseRow(name: "\(entry.name)/", path: entry.path)
                                if index < browse.folders.count - 1 { Divider().overlay(Theme.rowDivider) }
                            }
                            if browse.folders.isEmpty, browse.parent == nil {
                                Text("No subfolders here.").font(Theme.caption)
                                    .foregroundStyle(Theme.textMuted)
                                    .padding(.vertical, 8)
                            }
                        }
                    }
                    .frame(minHeight: 120, maxHeight: 300)
                }
            }
        }
        .task(id: browse.path) { await browseFolders(browse) }
    }

    private func browseRow(name: String, path: String) -> some View {
        Button { descend(to: path) } label: {
            Text(name)
                .font(Theme.font(size: 13, design: .monospaced))
                .foregroundStyle(Theme.textPrimary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 10).padding(.vertical, 6)
                .contentShape(.rect)
        }
        .plainButton(cornerRadius: 0)
    }

    private func descend(to path: String) {
        browse = FolderBrowse(path: path)
    }

    private func browseFolders(_ place: FolderBrowse) async {
        var place = place
        place.loading = true
        browse = place
        do {
            let listing = try await app.hub.withClient {
                try await $0.folders(path: place.path.isEmpty ? nil : place.path)
            }
            guard browse?.path == place.path else { return }
            place.path = listing.path
            place.parent = listing.parent
            place.folders = listing.folders
            place.error = listing.truncated == true
                ? "This folder has too many entries to list fully." : nil
            place.loading = false
            browse = place
        } catch is CancellationError {
        } catch {
            guard browse?.path == place.path else { return }
            place.loading = false
            place.error = (error as? HubError)?.message ?? error.localizedDescription
            browse = place
        }
    }

    // MARK: - status + footer

    @ViewBuilder private var status: some View {
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

    private var footer: some View {
        HStack(spacing: 10) {
            Text("Runs under tmux on the machine. The chat opens once the session shows up.")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .lineLimit(2)
            Spacer(minLength: 12)
            if browse != nil {
                Button("Back") { browse = nil }
                    .secondaryButton()
                    .fixedSize()
                Button("Use this folder") { useBrowsed() }
                    .primaryButton()
                    .fixedSize()
                    .disabled(browse?.loading != false)
            } else {
                Button("Cancel") {
                    launchTask?.cancel()
                    dismiss()
                }
                .secondaryButton()
                .fixedSize()
                .keyboardShortcut(.cancelAction)
                Button {
                    launchTask = Task { await start() }
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
    }

    @ViewBuilder
    private func group<Content: View>(_ title: String,
                                      @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(Theme.caption).foregroundStyle(Theme.textMuted)
            content()
        }
    }

    // MARK: - state + actions

    /// The sheet's folder browse state for one path.
    private struct FolderBrowse: Equatable {
        var path: String
        var parent: String?
        var folders: [HubFolderListing.Entry] = []
        var error: String?
        var loading = false
    }

    /// One read of machines and sessions: the pickers and the folder
    /// and harness defaults derive from these.
    private func load() async {
        let home = FileManager.default.homeDirectoryForCurrentUser
        localMachineId = await offMain { IssueResolve.localMachineId(home: home) }
        do {
            async let machinesCall = app.hub.withClient { try await $0.machines() }
            async let sessionsCall = app.hub.withClient { try await $0.sessions() }
            let (listed, rows) = try await (machinesCall, sessionsCall)
            listedMachines = listed
            machines = NewSessionOptions.machines(listed)
            sessions = rows
            machinesError = nil
            // The default comes from the filtered list — when the hub
            // can't launch but a satellite can, the satellite is the
            // pick, not the hub id the listing happened to name first.
            if machineId == nil || !(machines ?? []).contains(where: { $0.id == machineId }) {
                machineId = (machines ?? []).first(where: { $0.id == hubMachineId })?.id
                    ?? (machines ?? []).first?.id
            }
            if folder.isEmpty {
                folder = NewSessionOptions.folders(on: machine?.id ?? "", sessions: rows).first ?? ""
            }
            if harness == nil {
                harness = NewSessionOptions.defaultHarness(folder: folder, machineId: machine?.id ?? "",
                                                         offered: offered, sessions: rows)
            }
        } catch is CancellationError {
        } catch {
            machines = []
            listedMachines = []
            machinesError = (error as? HubError)?.message ?? error.localizedDescription
        }
    }

    private func pick(machine id: String) {
        guard !id.isEmpty, id != machineId else { return }
        machineId = id
        let folders = NewSessionOptions.folders(on: id, sessions: sessions)
        folder = folders.first ?? ""
        // A hand-picked harness survives the switch only while the new
        // machine offers it; otherwise re-default — `offered` already
        // reads the machine just selected.
        if let harness, offered.contains(harness) { return }
        harness = NewSessionOptions.defaultHarness(
            folder: folder, machineId: id, offered: offered, sessions: sessions)
        harnessTouched = false
    }

    private func pick(folder path: String) {
        folder = path
        harnessFromFolder()
    }

    /// Refits the Model menu to the picked machine and harness: an
    /// untouched menu takes their newest model; a pick survives as
    /// `ModelPick.refit` says.
    private func prefillModel() {
        modelPick.refit(LaunchOptions.recentModels(harness, on: machine))
    }

    /// A folder change re-defaults the harness unless the user picked
    /// one by hand — and the hand-pick only holds while it's on offer.
    private func harnessFromFolder() {
        if harnessTouched, let harness, offered.contains(harness) { return }
        harness = NewSessionOptions.defaultHarness(
            folder: folder.trimmingCharacters(in: .whitespaces),
            machineId: machine?.id ?? "", offered: offered, sessions: sessions)
        harnessTouched = false
    }

    private func useBrowsed() {
        guard let place = browse else { return }
        browse = nil
        pick(folder: place.path)
    }

    private func start() async {
        guard let harness, offered.contains(harness), let machine else { return }
        let cwd = folder.trimmingCharacters(in: .whitespaces)
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard cwd.hasPrefix("/"), !text.isEmpty else { return }
        let hub = app.hub
        // The hub is the default machine; a satellite is named — the
        // resolved machine's id, not whatever the picker stored.
        let target = machine.id == hubMachineId ? nil : machine.id
        // A pick goes only for a harness that can honour it — anything
        // else is a token the hub would refuse or a satellite would drop.
        let model = modelPick.model(supported: takesModel)
        let key = await flow.run(
            post: {
                try await hub.withClient {
                    try await $0.startSession(harness: harness, cwd: cwd,
                                              prompt: text, machine: target,
                                              model: model)
                }
            },
            fetch: { id in try await hub.withClient { try await $0.launch(id) } })
        guard let key, !Task.isCancelled else { return }
        dismiss()
        app.openSessionChat(key: key)
    }
}
