import SwiftUI

/// P4 new-session sheet: machine, folder, harness, first prompt. Once
/// the hub names the new session, `onStarted` gets its key so Sessions
/// can open the chat.
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
    @State private var flow = LaunchFlow()
    /// The session list as it was when the sheet opened: the 15 s poll
    /// would otherwise re-sort the folders under a finger.
    @State private var rows: [SessionRow] = []

    private static let foldersShown = 4

    var body: some View {
        let machines = NewSessionOptions.machines(app.machines, sessions: rows)
        let machine = machines.first { $0.id == machineId }
        PageScroll {
            if machines.isEmpty {
                StateCard(icon: "terminal", title: "No machine can start a session",
                          message: "Update swe on the hub. Machines report which harnesses they can start, and only Claude Code, Codex, Cursor, Droid and Pi can start from here.")
            } else {
                GroupLabel(text: "Machine")
                machinePicker(machines)
                if let machine {
                    folderSection(machine)
                    harnessSection(machine)
                    promptSection
                    startSection(machine)
                }
            }
        }
        .navigationTitle("New session")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
        }
        .interactiveDismissDisabled(flow.busy)
        .onAppear {
            guard machineId == nil else { return }
            rows = app.sessions
            select(machine: NewSessionOptions.machines(app.machines, sessions: rows).first)
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
                    .accessibilityAddTraits(selected ? .isSelected : [])
                }
            }
        }
    }

    @ViewBuilder private func folderSection(_ machine: Machine) -> some View {
        let folders = NewSessionOptions.folders(on: machine.id, sessions: rows)
        GroupLabel(text: "Folder", trailing: folders.count > 1 ? "\(folders.count) recent" : nil)
        if folders.isEmpty {
            StateCard(icon: "folder", title: "No recent folders",
                      message: "Folders show up here once a session has run in them on \(machine.displayName).")
        } else {
            CardList(data: folderChoices(folders)) { choice in
                switch choice {
                case .folder(let path):
                    Button { pick(folder: path, machine: machine) } label: { FolderRow(path: path, selected: path == folder) }
                        .buttonStyle(RowButtonStyle())
                case .more(let count):
                    Button { showAllFolders = true } label: {
                        HStack(spacing: 12) {
                            Image(systemName: "chevron.down").font(.system(size: 13)).foregroundStyle(Theme.textMuted).frame(width: 20)
                            Text("Show all \(count) folders").font(Theme.body).foregroundStyle(Theme.textSecondary)
                            Spacer()
                        }
                        .padding(.vertical, Theme.rowVertical).padding(.horizontal, Theme.rowHorizontal)
                        .contentShape(.rect)
                    }
                    .buttonStyle(RowButtonStyle())
                }
            }
        }
    }

    private func folderChoices(_ folders: [String]) -> [FolderChoice] {
        let shown = showAllFolders ? folders : Array(folders.prefix(Self.foldersShown))
        var choices = shown.map { FolderChoice.folder($0) }
        if folders.count > shown.count { choices.append(.more(folders.count)) }
        return choices
    }

    @ViewBuilder private func harnessSection(_ machine: Machine) -> some View {
        let offered = NewSessionOptions.harnesses(on: machine, sessions: rows)
        GroupLabel(text: "Harness", trailing: harness.map { NewSessionOptions.name($0, sessions: rows) })
        HStack(spacing: 8) {
            ForEach(offered, id: \.self) { h in
                let selected = h == harness
                let name = NewSessionOptions.name(h, sessions: rows)
                Button {
                    harness = h
                    harnessPicked = true
                } label: {
                    HarnessIcon(harness: h, name: name, size: 24)
                        .font(Theme.caption)
                        .frame(width: 48, height: 48)
                        .background(selected ? Theme.selected : Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
                        .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius)
                            .strokeBorder(selected ? Theme.textPrimary : Theme.border, lineWidth: selected ? 1.5 : 1))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(name)
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
            Spacer(minLength: 0)
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
            Button { Task { await start(machine) } } label: {
                HStack(spacing: 8) {
                    if flow.busy { ProgressView().controlSize(.small).tint(Theme.primaryForeground) }
                    Text(flow.phase == .waiting ? "Waiting for the session" : flow.busy ? "Starting" : "Start session")
                }
            }
            .primaryButton()
            .disabled(!ready || flow.busy)
            .padding(.top, 8)
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

    // MARK: Actions

    private func select(machine: Machine?) {
        guard let machine else { return }
        machineId = machine.id
        showAllFolders = false
        harnessPicked = false
        flow.reset()
        pick(folder: NewSessionOptions.folders(on: machine.id, sessions: rows).first, machine: machine)
    }

    private func pick(folder path: String?, machine: Machine) {
        folder = path
        let offered = NewSessionOptions.harnesses(on: machine, sessions: rows)
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
        guard let key else { return }
        await app.refresh()
        onStarted(key)
        dismiss()
    }
}

private enum FolderChoice: Identifiable {
    case folder(String)
    case more(Int)
    var id: String {
        switch self {
        case .folder(let p): p
        case .more: "_more"
        }
    }
}

private struct FolderRow: View {
    var path: String
    var selected: Bool

    var body: some View {
        let name = path.split(separator: "/").last.map(String.init) ?? path
        let parent = (path as NSString).deletingLastPathComponent
        HStack(spacing: 12) {
            Image(systemName: "folder").font(.system(size: 15)).foregroundStyle(Theme.textSecondary).frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                Text(SessionGrouping.abbreviateHome(parent)).font(Theme.caption).foregroundStyle(Theme.textSecondary)
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
