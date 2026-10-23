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
    @State private var showAllFolders = false
    @State private var folderQuery = ""
    @State private var browsePath: [FolderBrowse] = []
    @State private var flow = LaunchFlow()
    /// The running launch, so Cancel or a swipe-down stops it before it
    /// can open a chat behind a sheet that is gone.
    @State private var launch: Task<Void, Never>?
    /// The session list as it was when the sheet opened: the 15 s poll
    /// would otherwise re-sort the folders under a finger.
    @State private var rows: [SessionRow] = []

    private static let foldersShown = 4

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
                          message: "Update swe on the hub. A machine offers the harnesses swe lists there, minus the ones switched off in Quiver's Provider visibility.")
            } else {
                GroupLabel(text: "Machine")
                machinePicker(machines)
                if let machine {
                    harnessSection(machine)
                    promptSection
                    startSection(machine)
                    folderSection(machine)
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
                        .frame(height: Theme.controlHeight)
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

    @ViewBuilder private func harnessSection(_ machine: Machine) -> some View {
        let offered = NewSessionOptions.harnesses(on: machine)
        GroupLabel(text: "Harness", trailing: harness.map { NewSessionOptions.name($0, on: machine, sessions: rows) })
        // Wraps to a second row: Provider visibility can switch on ten or more.
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 48, maximum: 48), spacing: 8)], alignment: .leading, spacing: 8) {
            ForEach(offered, id: \.self) { h in
                let selected = h == harness
                let name = NewSessionOptions.name(h, on: machine, sessions: rows)
                Button {
                    harness = h
                    harnessPicked = true
                } label: {
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
        let ready = folder != nil && harness != nil && !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return VStack(alignment: .leading, spacing: 8) {
            // The folder picked below, restated here so Start says where
            // it runs without scrolling down.
            Card(padding: 0) {
                HStack(spacing: 12) {
                    Image(systemName: "folder").font(.system(size: 15)).foregroundStyle(Theme.textSecondary).frame(width: 20)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Starts in").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        Text(folder.map(FolderRow.name) ?? "Pick a folder below")
                            .font(Theme.rowTitle).foregroundStyle(folder == nil ? Theme.textMuted : Theme.textPrimary)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 8)
                    if let folder {
                        Text(SessionGrouping.abbreviateHome(FolderRow.parent(folder)))
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .lineLimit(1).truncationMode(.head)
                    }
                }
                .padding(.vertical, Theme.rowVertical).padding(.horizontal, Theme.rowHorizontal)
            }
            .padding(.top, 8)
            Button { launch = Task { await start(machine) } } label: {
                HStack(spacing: 8) {
                    if flow.busy { ProgressView().controlSize(.small).tint(Theme.primaryForeground) }
                    Text(flow.phase == .waiting ? "Waiting for the session" : flow.busy ? "Starting" : "Start session")
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
            Text("Runs under tmux on \(machine.displayName). The chat opens once the session shows up.")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private func folderSection(_ machine: Machine) -> some View {
        let folders = NewSessionOptions.folders(on: machine.id, sessions: rows)
        let searching = !folderQuery.trimmingCharacters(in: .whitespaces).isEmpty
        let matches = NewSessionOptions.matching(folders, query: folderQuery)
        // The fold toggle and Browse sit at the top, so neither needs a
        // scroll past hundreds of rows.
        HStack(spacing: 14) {
            Text(folders.isEmpty ? "Folder" : "Folder · \(folders.count) recent")
                .font(Theme.groupLabel).foregroundStyle(Theme.textSecondary)
            Spacer(minLength: 8)
            Button {
                browsePath = [FolderBrowse(machineId: machine.id, path: folder.map(FolderRow.parent))]
            } label: {
                Label("Browse", systemImage: "folder.badge.magnifyingglass")
            }
            .disabled(flow.busy)
            if folders.count > Self.foldersShown && !searching {
                Button(showAllFolders ? "Show fewer" : "Show all") { showAllFolders.toggle() }
            }
        }
        .font(Theme.control)
        .foregroundStyle(Theme.textPrimary)
        .buttonStyle(.plain)
        .padding(.horizontal, 4)
        .padding(.top, 14)
        if folders.isEmpty {
            StateCard(icon: "folder", title: "No recent folders",
                      message: "Folders show up here once a session has run in them on \(machine.displayName). Browse to pick any folder.")
        } else {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundStyle(Theme.textMuted)
                TextField("Search folders", text: $folderQuery)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                if searching {
                    Button { folderQuery = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.textMuted) }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Clear search")
                }
            }
            .font(Theme.body)
            .padding(.horizontal, 12)
            .frame(height: Theme.controlHeight + 4)
            .background(Theme.field, in: .rect(cornerRadius: Theme.blockRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
            let shown = searching || showAllFolders ? matches : Array(matches.prefix(Self.foldersShown))
            if shown.isEmpty {
                Text("No recent folder matches. Browse to find it.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary).padding(.horizontal, 4)
            } else {
                CardList(data: shown.map(FolderChoice.init)) { choice in
                    Button { pick(folder: choice.path, machine: machine) } label: {
                        FolderRow(path: choice.path, selected: choice.path == folder)
                    }
                    .buttonStyle(RowButtonStyle())
                    .disabled(flow.busy)
                }
            }
        }
    }

    // MARK: Actions

    private func select(machine: Machine?) {
        guard let machine, !flow.busy else { return }
        machineId = machine.id
        showAllFolders = false
        folderQuery = ""
        harnessPicked = false
        flow.reset()
        pick(folder: NewSessionOptions.folders(on: machine.id, sessions: rows).first, machine: machine)
    }

    private func pick(folder path: String?, machine: Machine) {
        folder = path
        let offered = NewSessionOptions.harnesses(on: machine)
        if !harnessPicked || !(harness.map(offered.contains) ?? false) {
            harness = NewSessionOptions.defaultHarness(folder: path, machineId: machine.id, offered: offered, sessions: rows)
        }
    }

    private func start(_ machine: Machine) async {
        guard let client = app.client, let folder, let harness else { return }
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        let key = await flow.run(
            post: { try await client.startSession(machine: machine.id, harness: harness, cwd: folder, prompt: text) },
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
                    CardList(data: [FolderChoice(path: parent)]) { up in
                        NavigationLink(value: FolderBrowse(machineId: machineId, path: up.path)) {
                            HStack(spacing: 12) {
                                Image(systemName: "arrow.up").font(.system(size: 15)).foregroundStyle(Theme.textSecondary).frame(width: 20)
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
                                Image(systemName: "folder").font(.system(size: 15)).foregroundStyle(Theme.textSecondary).frame(width: 20)
                                Text(entry.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                                Spacer(minLength: 8)
                                Image(systemName: "chevron.right").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textMuted)
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
            listing = try await client.folders(machine: machineId, path: path)
        } catch {
            self.error = (error as? HubError)?.message ?? error.localizedDescription
        }
    }
}

private struct FolderChoice: Identifiable {
    let path: String
    var id: String { path }
}

struct FolderRow: View {
    var path: String
    var selected: Bool

    static func name(_ path: String) -> String { path.split(separator: "/").last.map(String.init) ?? path }
    static func parent(_ path: String) -> String { (path as NSString).deletingLastPathComponent }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "folder").font(.system(size: 15)).foregroundStyle(Theme.textSecondary).frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(Self.name(path)).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                Text(SessionGrouping.abbreviateHome(Self.parent(path))).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .lineLimit(1).truncationMode(.head)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if selected {
                Image(systemName: "checkmark").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textPrimary)
            }
        }
        .padding(.vertical, Theme.rowVertical)
        .padding(.horizontal, Theme.rowHorizontal)
        .contentShape(.rect)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}
