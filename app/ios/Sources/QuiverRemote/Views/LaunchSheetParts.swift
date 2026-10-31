import SwiftUI

// The pieces every launch sheet shares: New session, Resolve with agent,
// Open in session and a task's Start session. Each sheet keeps its own
// state and start flow; these draw the same rows the same way.

/// "Harness" and the grid of the machine's harness logos, wrapping to a
/// second row (Provider visibility can switch on ten or more), then a
/// note when Quiver can't read the picked harness's sessions. `onPick`
/// runs after a tap sets `harness`, for sheets that track a manual pick.
struct HarnessPicker: View {
    let machine: Machine
    @Binding var harness: String?
    /// The session list, for the names harnesses go by.
    let sessions: [SessionRow]
    var onPick: () -> Void = {}
    /// A logo's square, 48pt at the default text size; it grows with the
    /// logo inside it (`HarnessIcon` scales relative to footnote too).
    @ScaledMetric(relativeTo: .footnote) private var cell: CGFloat = 48

    init(machine: Machine, harness: Binding<String?>, sessions: [SessionRow], onPick: @escaping () -> Void = {}) {
        self.machine = machine
        _harness = harness
        self.sessions = sessions
        self.onPick = onPick
    }

    var body: some View {
        let offered = NewSessionOptions.harnesses(on: machine)
        GroupLabel(text: "Harness", trailing: harness.map { NewSessionOptions.name($0, on: machine, sessions: sessions) })
        LazyVGrid(columns: [GridItem(.adaptive(minimum: cell, maximum: cell), spacing: 8)], alignment: .leading, spacing: 8) {
            ForEach(offered, id: \.self) { h in
                let selected = h == harness
                let name = NewSessionOptions.name(h, on: machine, sessions: sessions)
                Button {
                    harness = h
                    onPick()
                } label: {
                    HarnessIcon(harness: h, name: name, size: 24)
                        .font(Theme.font(size: 10))
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                        .frame(width: cell, height: cell)
                        .background(selected ? Theme.selected : Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
                        .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius)
                            .strokeBorder(selected ? Theme.textPrimary : Theme.border, lineWidth: selected ? 1.5 : 1))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(name)
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
        // The task and GitHub sheets launch on the hub even when it lists
        // no harness; say why the grid is empty (an older hub's row still
        // gets the update-swe advice).
        if offered.isEmpty {
            Text(NewSessionOptions.noMachineMessage([machine]))
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 4)
        }
        if let harness, machine.untrackedHarnesses?.contains(harness) == true {
            Text("Quiver can't read \(NewSessionOptions.name(harness, on: machine, sessions: sessions)) sessions yet: it starts, but no chat opens.")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 4)
        }
    }
}

/// "First prompt" and its field, with whatever note the sheet puts under
/// it (why it's empty, what an empty one does).
struct FirstPromptField<Note: View>: View {
    @Binding var prompt: String
    @ViewBuilder var note: Note

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            GroupLabel(text: "First prompt")
            TextField("Describe the first task", text: $prompt, axis: .vertical)
                .lineLimit(4...10)
                .font(Theme.body)
                .foregroundStyle(Theme.textPrimary)
                .padding(12)
                .background(Theme.field, in: .rect(cornerRadius: Theme.blockRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
            note
        }
    }
}

extension FirstPromptField where Note == EmptyView {
    init(prompt: Binding<String>) {
        self.init(prompt: prompt) { EmptyView() }
    }
}

/// The "Starts in" card: where the session will run, and on the right
/// the machine or the folder's parent.
struct StartsInCard: View {
    let icon: String
    let title: String
    /// No place picked yet: the title is a hint, drawn muted.
    var titleMuted = false
    let trailing: String?
    /// A path's tail is the part worth keeping.
    var trailingTruncation: Text.TruncationMode = .tail

    var body: some View {
        Card(padding: 0) {
            HStack(spacing: 12) {
                Image(systemName: icon).font(Theme.rowIcon).foregroundStyle(Theme.textSecondary).iconColumn(20)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Starts in").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    Text(title)
                        .font(Theme.rowTitle).foregroundStyle(titleMuted ? Theme.textMuted : Theme.textPrimary)
                        .lineLimit(1)
                }
                Spacer(minLength: 8)
                if let trailing {
                    Text(trailing)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1).truncationMode(trailingTruncation)
                }
            }
            .padding(.vertical, Theme.rowVertical).padding(.horizontal, Theme.rowHorizontal)
        }
    }
}

/// Start session, worded by where the launch is, then the launch's
/// failure or unconfirmed note and the sheet's footnote.
struct LaunchStartButton: View {
    let flow: LaunchFlow
    let ready: Bool
    let footnote: String
    let start: () -> Void

    var body: some View {
        Button(action: start) {
            HStack(spacing: 8) {
                if flow.busy { ProgressView().controlSize(.small).tint(Theme.primaryForeground) }
                Text(flow.phase == .waiting ? "Waiting for the session" : flow.opening ? "Opening the chat" : flow.busy ? "Starting" : "Start session")
            }
        }
        .primaryButton()
        .disabled(!ready || flow.busy)
        if case .failed(let message) = flow.phase {
            Text(inlineCode: message).font(Theme.caption).foregroundStyle(Theme.dangerText)
                .fixedSize(horizontal: false, vertical: true)
        } else if case .unconfirmed(let message) = flow.phase {
            Text(inlineCode: message).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        Text(footnote)
            .font(Theme.caption).foregroundStyle(Theme.textMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// Where a plain launch starts: "Folder · N recent" with Browse, the
/// machine's recent folders newest first (the first few, then Show all),
/// and a search once there are more than fit. New session and a task's
/// session sheet both use it; the GitHub sheets' worktree is the hub's
/// choice. Another machine starts folded, with no search.
struct RecentFolderPicker: View {
    let machine: Machine
    /// The sheet's session rows, for the recent folders.
    let sessions: [SessionRow]
    let selected: String?
    /// A launch in flight locks the list with the sheet's other inputs.
    var disabled = false
    let browse: () -> Void
    let pick: (String) -> Void

    @State private var showAll = false
    @State private var query = ""

    static let shownFirst = 4

    /// The rows under the header: every match while searching or shown
    /// in full, else the newest `shownFirst`.
    static func shown(_ folders: [String], query: String, showAll: Bool) -> [String] {
        let matches = NewSessionOptions.matching(folders, query: query)
        return isSearching(query) || showAll ? matches : Array(matches.prefix(shownFirst))
    }

    /// A few folders all show at once, so there's nothing to search; a
    /// search under way keeps its field.
    static func offersSearch(folderCount: Int, query: String) -> Bool {
        isSearching(query) || folderCount > shownFirst
    }

    private static func isSearching(_ query: String) -> Bool {
        !query.trimmingCharacters(in: .whitespaces).isEmpty
    }

    var body: some View {
        let folders = NewSessionOptions.folders(on: machine.id, sessions: sessions)
        let searching = Self.isSearching(query)
        // The fold toggle and Browse sit at the top, so neither needs a
        // scroll past hundreds of rows.
        HStack(spacing: 14) {
            Text(folders.isEmpty ? "Folder" : "Folder · \(folders.count) recent")
                .font(Theme.groupLabel).foregroundStyle(Theme.textSecondary)
            Spacer(minLength: 8)
            Button(action: browse) {
                Label("Browse", systemImage: "folder.badge.magnifyingglass")
            }
            .disabled(disabled)
            if folders.count > Self.shownFirst && !searching {
                Button(showAll ? "Show fewer" : "Show all") { showAll.toggle() }
            }
        }
        .font(Theme.control)
        .foregroundStyle(Theme.textPrimary)
        .buttonStyle(.plain)
        .padding(.horizontal, 4)
        .padding(.top, 14)
        .onChange(of: machine.id) {
            showAll = false
            query = ""
        }
        if folders.isEmpty {
            StateCard(icon: "folder", title: "No recent folders",
                      message: "Folders show up here once a session has run in them on \(machine.displayName). Browse to pick any folder.")
        } else {
            if Self.offersSearch(folderCount: folders.count, query: query) {
                SearchField(placeholder: "Search folders", query: $query)
            }
            let shown = Self.shown(folders, query: query, showAll: showAll)
            if shown.isEmpty {
                Text("No recent folder matches. Browse to find it.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary).padding(.horizontal, 4)
            } else {
                CardList(data: shown.map(FolderPath.init)) { choice in
                    Button { pick(choice.path) } label: {
                        FolderRow(path: choice.path, selected: choice.path == selected)
                    }
                    .buttonStyle(RowButtonStyle())
                    .disabled(disabled)
                }
            }
        }
    }
}

/// A folder path as an Identifiable, for card lists of folders.
struct FolderPath: Identifiable {
    let path: String
    var id: String { path }
}

/// A search field with a clear button while it holds a search: the
/// launch sheets' folder list and the Link a session sheet.
struct SearchField: View {
    let placeholder: String
    @Binding var query: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass").foregroundStyle(Theme.textMuted)
            TextField(placeholder, text: $query)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            if !query.trimmingCharacters(in: .whitespaces).isEmpty {
                Button { query = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.textMuted) }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Clear search")
            }
        }
        .font(Theme.body)
        .padding(.horizontal, 12)
        .scaledMinHeight(Theme.controlHeight + 4)
        .background(Theme.field, in: .rect(cornerRadius: Theme.blockRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
    }
}
