import SwiftUI

/// P4: every session on the paired machines, grouped by project.
struct SessionsView: View {
    @Environment(AppModel.self) private var app
    /// The tab's navigation path, so a just-started session's chat can open.
    @Binding var path: [Route]
    @State private var filter: SessionFilter = .all
    @State private var showDevices = false
    @State private var showConsole = false
    @State private var showNewSession = false
    @State private var startedKey: String?
    @State private var confirmUnpair = false
    @State private var pairingAnother = false
    @State private var search = SessionSearch()
    @State private var swipes = SwipeCoordinator()
    @State private var renaming: SessionRow?
    @State private var forking: SessionRow?
    @State private var forkedKey: String?
    @State private var detachAsk: SessionRow?
    @State private var toast: ListToast?

    var body: some View {
        let rows = app.displaySessions.filter(filter.includes)
        let groups = SessionGrouping.groupsWithPins(rows, machines: app.machines)
        PageScroll {
            if search.isActive {
                searchResults
            } else {
                filterBar
            }
            if search.isActive {
                EmptyView()
            } else if !app.loadedOnce {
                LoadingRow(text: "Loading sessions")
            } else if let error = app.loadError, app.sessions.isEmpty {
                StateCard.unreachable(error) { Task { await app.refresh() } }
            } else if groups.isEmpty {
                StateCard(icon: "tray", title: filter == .all ? "No sessions yet" : "Nothing here",
                          message: filter == .all ? "Sessions on this machine show up here as soon as an agent starts." : "No session matches this filter right now.")
            } else {
                ForEach(groups) { group in
                    GroupLabel(text: group.title, trailing: group.id == "earlier" ? "\(group.rows.count)" : nil)
                    CardList(data: group.rows) { row in
                        SwipeRow(key: row.key, leading: SessionAction.leading(row), trailing: SessionAction.trailing(row),
                                 coordinator: swipes, perform: { act($0, on: row) }) {
                            NavigationLink(value: Route.chat(row.key)) {
                                SessionRowView(row: row,
                                               place: group.id == "pinned"
                                                   ? SessionGrouping.pinnedPlace(row, rows: app.sessions, machines: app.machines) : nil,
                                               detachPending: app.isDetachPending(row.key))
                            }
                            .buttonStyle(RowButtonStyle())
                            .contentShape(.contextMenuPreview, .rect(cornerRadius: Theme.blockRadius))
                            .contextMenu { menu(for: row) }
                        }
                    }
                }
            }
        }
        .modifier(CloseSwipesOnScroll(swipes: swipes))
        .overlay(alignment: .bottom) {
            if let toast {
                ToastView(toast: toast) { self.toast = nil }
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .task(id: toast.id) {
                        try? await Task.sleep(for: toast.duration)
                        if self.toast?.id == toast.id { withAnimation { self.toast = nil } }
                    }
            }
        }
        .animation(.spring(response: 0.3, dampingFraction: 0.9), value: toast)
        .refreshable { await app.refresh() }
        .searchable(text: $search.query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search sessions")
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        // Reruns on a new query and on each list refresh (pull to refresh,
        // the 15 s poll), so results track sessions that just arrived.
        .task(id: SearchTrigger(query: search.query, refreshedAt: app.refreshedAt)) { await search.run(app: app) }
        .navigationTitle("Sessions")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showNewSession = true } label: {
                    Image(systemName: "plus").font(Theme.button)
                }
                .accessibilityLabel("New session")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Section("Hubs") {
                        ForEach(app.hubs.hubs) { hub in
                            Button { app.switchHub(to: hub.id) } label: {
                                HubMenuLabel(name: app.hubs.name(of: hub), address: hub.address, current: hub.id == app.hub?.id)
                            }
                        }
                        Button {
                            app.beginPairingAnother()
                            pairingAnother = true
                        } label: { Label("Pair another hub", systemImage: "plus") }
                    }
                    Section(app.hub?.address ?? "") {
                        Button { showDevices = true } label: { Label("Pair requests and devices", systemImage: "iphone.and.arrow.forward") }
                        Button { showConsole = true } label: { Label("Console", systemImage: "terminal") }
                        Button { Task { await app.refresh() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                    }
                    Button(role: .destructive) { confirmUnpair = true } label: { Label("Unpair from \(app.hubName)", systemImage: "xmark.circle") }
                } label: {
                    HubChip(name: app.hubName, connection: app.connection)
                }
            }
        }
        .sheet(isPresented: $showDevices) { NavigationStack { DevicesView() } }
        .fullScreenCover(isPresented: $showConsole) { ConsoleView() }
        .sheet(isPresented: $pairingAnother, onDismiss: { app.cancelPairing() }) { WelcomeView(addingHub: true) }
        // The chat opens after the sheet is gone, so the push isn't lost
        // under the dismiss animation.
        .sheet(isPresented: $showNewSession, onDismiss: {
            if let key = startedKey { path.append(.chat(key)) }
            startedKey = nil
        }) {
            NewSessionView { startedKey = $0 }
        }
        .sheet(item: $renaming) { RenameSheet(row: $0) }
        .sheet(item: $forking, onDismiss: {
            if let key = forkedKey { path.append(.chat(key)) }
            forkedKey = nil
        }) { row in
            ForkSheet(row: row) { forkedKey = $0 }
        }
        .confirmationDialog(detachAsk.map { "Detach \($0.displayTitle)?" } ?? "Detach?",
                            isPresented: Binding(get: { detachAsk != nil }, set: { if !$0 { detachAsk = nil } }),
                            titleVisibility: .visible, presenting: detachAsk) { row in
            Button("Detach when it finishes") { Task { await detach(row, mode: .whenIdle) } }
            Button("Keep it running in tmux") { Task { await detach(row, mode: .keep) } }
            Button("Detach now", role: .destructive) { Task { await detach(row, mode: .now) } }
            Button("Cancel", role: .cancel) {}
        } message: { _ in
            Text("It's working right now. Detaching now stops the turn; keeping it lets you attach to it from the Mac with tmux.")
        }
        .confirmationDialog("Unpair from \(app.hubName)?", isPresented: $confirmUnpair, titleVisibility: .visible) {
            Button("Unpair", role: .destructive) { app.unpair() }
        } message: {
            Text(app.hubs.hubs.count > 1
                 ? "This phone asks the hub to sign it out; if the hub can't be reached, remove it under Pair requests and devices on another device. Your other hubs stay paired."
                 : "This phone asks the hub to sign it out; if the hub can't be reached, remove it under Pair requests and devices on another device. You'll need to pair again to see sessions.")
        }
    }

    @ViewBuilder private var searchResults: some View {
        let waiting = search.answered != search.query.trimmingCharacters(in: .whitespaces)
        if search.results.isEmpty {
            if waiting || search.searching {
                LoadingRow(text: "Searching")
            } else {
                StateCard(icon: "magnifyingglass", title: "No matches",
                          message: search.indexed
                            ? "Nothing in titles, folders or transcripts matches that."
                            : "Nothing in titles or folders matches that. Transcript search needs the Quiver app's search index on the hub machine.")
            }
        } else {
            GroupLabel(text: "\(search.results.count) \(search.results.count == 1 ? "match" : "matches")",
                       trailing: search.indexed ? "Transcripts and titles" : "Titles and folders")
            CardList(data: search.results) { result in
                NavigationLink(value: Route.chat(result.key)) { SearchResultRow(result: result) }
                    .buttonStyle(RowButtonStyle())
            }
        }
        if let error = search.error {
            Text(error).font(Theme.caption).foregroundStyle(Theme.textMuted).padding(.horizontal, 4)
        }
    }

    // MARK: Row actions

    @ViewBuilder private func menu(for row: SessionRow) -> some View {
        ForEach(SessionAction.menu(row)) { action in
            Button { act(action, on: row) } label: { Label(action.title, systemImage: action.symbol) }
        }
    }

    private func act(_ action: SessionAction, on row: SessionRow) {
        switch action {
        case .attach:
            Task {
                do {
                    if try await app.attach(row.key) { show("Attaching on \(machineName(row))") }
                } catch {
                    show(Self.message(error))
                }
                await app.refresh()
            }
        case .detach:
            switch DetachPlan.of(row) {
            case .ask: detachAsk = row
            case .detach(let mode): Task { await detach(row, mode: mode, offerUndo: true) }
            }
        case .pin, .unpin:
            Task {
                let note = await app.setPinned(row.key, action == .pin) { change in
                    withAnimation(.spring(response: 0.35, dampingFraction: 0.9)) { change() }
                }
                if let note { show(note) }
            }
        case .open:
            path.append(.chat(row.key))
        case .rename:
            renaming = row
        case .fork:
            forking = row
        case .resolve, .newSession, .clear:
            // Issue, task and inbox rows wire their own swipes; no
            // session row offers any of these.
            break
        }
    }

    /// `offerUndo` is the swipe on a row that looked idle: Undo attaches
    /// it again, which also cancels the detach if the hub is still waiting.
    private func detach(_ row: SessionRow, mode: DetachMode, offerUndo: Bool = false) async {
        do {
            let outcome = try await app.detach(row.key, mode: mode)
            if offerUndo, outcome != .stale {
                let key = row.key
                show(ListToast(message: "Detaching \(row.displayTitle)", actionTitle: "Undo") {
                    Task {
                        do { try await app.attach(key) } catch { show(Self.message(error)) }
                        await app.refresh()
                    }
                })
                // The hub checks every 2 s and detaches an idle session then.
                Task {
                    try? await Task.sleep(for: .seconds(3))
                    await app.refresh()
                }
                return
            }
            switch outcome {
            case .done where mode == .keep:
                show("Detached. It keeps running in tmux on \(machineName(row)).")
            case .done:
                show("Detached \(row.displayTitle)")
            case .waiting, .stale:
                break
            case .forwarded:
                show("Detaching on \(machineName(row))")
            }
        } catch let error as HubError where mode == .keep && error.code == "control_failed"
                    && (error.reason ?? error.message).contains("no tmux session") {
            show("This session doesn't run in tmux, so it can't keep running. Detach when it finishes or now instead.")
        } catch {
            show(Self.message(error))
        }
        await app.refresh()
    }

    private func machineName(_ row: SessionRow) -> String {
        app.machines.first { $0.id == row.machineId }?.displayName ?? "the machine"
    }

    private func show(_ message: String) { show(ListToast(message: message)) }
    private func show(_ t: ListToast) { toast = t }

    private static func message(_ error: Error) -> String {
        error.localizedDescription
    }

    private var filterBar: some View {
        WrappingHStack {
            ForEach(SessionFilter.allCases) { f in
                FilterChip(title: f.title, count: app.displaySessions.filter(f.includes).count, selected: filter == f) {
                    filter = f
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 4)
    }
}

struct SessionRowView: View {
    var row: SessionRow
    /// In the Pinned section, where no folder heading says where it runs:
    /// the folder, and the machine when there is more than one.
    var place: String?
    /// A detach is waiting for the current turn to end.
    var detachPending = false
    @Environment(\.dynamicTypeSize) private var size

    var body: some View {
        let status = row.sessionStatus
        let waiting = row.needsYouNow
        // At accessibility sizes the attached/watching mark leads the
        // details line instead of taking a column from the title.
        let markInline = size.isAccessibilitySize
        HStack(spacing: 12) {
            StatusDot(status: status, stale: status.needsYou && !waiting)
            VStack(alignment: .leading, spacing: 3) {
                Text(row.displayTitle)
                    .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1, larger: 3).truncationMode(.tail)
                WrappingHStack(spacing: 6) {
                    if markInline { attachMark }
                    HarnessIcon(harness: row.harness, name: row.harnessName, size: 13)
                    Text(status.label).foregroundStyle(waiting ? Theme.warningText : Theme.textSecondary)
                    let when = RelativeTime.short(row.lastActiveAt)
                    if !when.isEmpty { Text("· \(when)") }
                    if let model = row.modelLabel {
                        Text("· \(model)").accessibilityLabel("model \(model)")
                    }
                    if let place { Text("· \(place)").truncationMode(.head) }
                }
                .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                if detachPending {
                    Label("Detaching when it finishes", systemImage: "clock")
                        .labelStyle(.titleAndIcon)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if !markInline { attachMark }
            Image(systemName: "chevron.right").font(Theme.chevron).foregroundStyle(Theme.textMuted)
        }
        .padding(.vertical, Theme.rowVertical)
        .padding(.horizontal, Theme.rowHorizontal)
        .contentShape(.rect)
    }

    private var attachMark: some View {
        Image(systemName: row.isAttached ? "link" : "eye")
            .font(Theme.font(size: 13))
            .foregroundStyle(row.isAttached ? Theme.accent : Theme.textMuted)
            .accessibilityLabel(row.isAttached ? "Attached" : "Watching only")
    }
}

struct RowButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.background(configuration.isPressed ? Theme.pressed : .clear)
    }
}

/// A hub in the hub menu: its name with its address under it, and a
/// check on the one on screen. The menu takes the image as the row's icon
/// and the second text as its subtitle.
struct HubMenuLabel: View {
    var name: String
    var address: String
    var current: Bool

    var body: some View {
        if current { Image(systemName: "checkmark") }
        Text(name)
        if address != name { Text(address) }
    }
}

/// The hub's name with a connection dot, in the toolbar.
struct HubChip: View {
    var name: String
    var connection: Connection

    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(name).font(Theme.control).foregroundStyle(Theme.textPrimary).lineLimit(1)
            Image(systemName: "chevron.down").font(Theme.disclosure).foregroundStyle(Theme.textMuted)
        }
        .padding(.horizontal, 10)
        .scaledMinHeight(30)
        .frame(maxWidth: 190)
        .modifier(ChipCapsule())
        .accessibilityLabel("\(name), \(connection == .online ? "connected" : connection == .offline ? "offline" : "connecting")")
    }

    private var color: Color {
        switch connection {
        case .online: Theme.accentFill
        case .offline: Theme.danger
        case .connecting: Theme.borderStrong
        }
    }
}

/// The chip's own hairline capsule, before iOS 26. From iOS 26 the
/// toolbar sets every item on its own glass capsule, so a second capsule
/// inside it read as a dark pill in a grey one; there the glass is the
/// chip's shape, as it is for the + button beside it.
private struct ChipCapsule: ViewModifier {
    func body(content: Content) -> some View {
        if #available(iOS 26.0, *) {
            content
        } else {
            content
                .background(Theme.card, in: .capsule)
                .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
        }
    }
}

/// What a tab's navigation stack can push. `pull` and `issue` appear on
/// the GitHub stack, `task` on the Tasks stack, which can also push the
/// other three from its detail page; every stack can push a chat.
enum Route: Hashable {
    case chat(String), pull(repo: String, number: Int),
         issue(repo: String, number: Int), task(String)
}

/// A search hit: the session row, then the matching transcript line with
/// the matched words in the primary colour.
struct SearchResultRow: View {
    var result: SearchResult

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SessionRowView(row: result.session)
            if let marked = result.markedSnippet ?? result.snippet, !marked.isEmpty {
                Text(Self.highlighted(marked))
                    .font(Theme.caption)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, Theme.rowHorizontal + Theme.statusDot + 12)
                    .padding(.trailing, Theme.rowHorizontal)
                    .padding(.top, -6)
                    .padding(.bottom, Theme.rowVertical)
            }
        }
    }

    static func highlighted(_ marked: String) -> AttributedString {
        var out = AttributedString()
        for run in SessionSearch.snippetRuns(marked) {
            var piece = AttributedString(run.text.replacingOccurrences(of: "\n", with: " "))
            piece.foregroundColor = run.hit ? Theme.textPrimary : Theme.textSecondary
            if run.hit { piece.font = Theme.font(size: 13, weight: .semibold) }
            out += piece
        }
        return out
    }
}

private struct SearchTrigger: Equatable {
    var query: String
    var refreshedAt: Date?
}

/// A scroll of the list closes a swiped-open row, as `List` does.
struct CloseSwipesOnScroll: ViewModifier {
    let swipes: SwipeCoordinator

    func body(content: Content) -> some View {
        if #available(iOS 18.0, *) {
            content.onScrollPhaseChange { _, phase in
                if phase == .interacting, swipes.openKey != nil { swipes.openKey = nil }
            }
        } else {
            content
        }
    }
}
