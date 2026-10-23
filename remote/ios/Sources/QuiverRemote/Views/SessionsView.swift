import SwiftUI

/// P4: every session on the paired machines, grouped by project.
struct SessionsView: View {
    @Environment(AppModel.self) private var app
    @State private var filter: SessionFilter = .all
    @State private var showDevices = false
    @State private var confirmUnpair = false

    var body: some View {
        let rows = app.sessions.filter(filter.includes)
        let groups = SessionGrouping.groups(rows, machines: app.machines)
        PageScroll {
            filterBar
            if !app.loadedOnce {
                LoadingRow(text: "Loading sessions")
            } else if let error = app.loadError, app.sessions.isEmpty {
                StateCard(icon: "wifi.exclamationmark", title: "Can't reach the hub", message: error, tone: .error,
                          actionTitle: "Try again") { Task { await app.refresh() } }
            } else if groups.isEmpty {
                StateCard(icon: "tray", title: filter == .all ? "No sessions yet" : "Nothing here",
                          message: filter == .all ? "Sessions on this machine show up here as soon as an agent starts." : "No session matches this filter right now.")
            } else {
                ForEach(groups) { group in
                    GroupLabel(text: group.title, trailing: group.id == "earlier" ? "\(group.rows.count)" : nil)
                    CardList(data: group.rows) { row in
                        NavigationLink(value: Route.chat(row.key)) { SessionRowView(row: row) }
                            .buttonStyle(RowButtonStyle())
                    }
                }
            }
        }
        .refreshable { await app.refresh() }
        .navigationTitle("Sessions")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Section(app.hub?.baseURL.host ?? "") {
                        Button { showDevices = true } label: { Label("Pair requests and devices", systemImage: "iphone.and.arrow.forward") }
                        Button { Task { await app.refresh() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                    }
                    Button(role: .destructive) { confirmUnpair = true } label: { Label("Unpair this device", systemImage: "xmark.circle") }
                } label: {
                    HubChip(name: app.hubName, connection: app.connection)
                }
            }
        }
        .sheet(isPresented: $showDevices) { NavigationStack { DevicesView() } }
        .confirmationDialog("Unpair this device?", isPresented: $confirmUnpair, titleVisibility: .visible) {
            Button("Unpair", role: .destructive) { app.unpair() }
        } message: {
            Text("You'll need to pair again to see sessions.")
        }
    }

    private var filterBar: some View {
        HStack(spacing: 6) {
            ForEach(SessionFilter.allCases) { f in
                FilterChip(title: f.title, count: app.sessions.filter(f.includes).count, selected: filter == f) {
                    filter = f
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.top, 4)
    }
}

struct SessionRowView: View {
    var row: SessionRow

    var body: some View {
        let status = row.sessionStatus
        let waiting = row.needsYouNow
        HStack(spacing: 12) {
            StatusDot(status: status, stale: status.needsYou && !waiting)
            VStack(alignment: .leading, spacing: 3) {
                Text(row.displayTitle)
                    .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1).truncationMode(.tail)
                HStack(spacing: 0) {
                    Text(row.harnessName).foregroundStyle(Theme.harness(row.harness))
                    Text(" · ")
                    Text(status.label).foregroundStyle(waiting ? Theme.warningText : Theme.textSecondary)
                    let when = RelativeTime.short(row.lastActiveAt)
                    if !when.isEmpty { Text(" · \(when)") }
                }
                .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Image(systemName: row.isAttached ? "link" : "eye")
                .font(.system(size: 13))
                .foregroundStyle(row.isAttached ? Theme.accent : Theme.textMuted)
                .accessibilityLabel(row.isAttached ? "Attached" : "Watching only")
            Image(systemName: "chevron.right").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textMuted)
        }
        .padding(.vertical, Theme.rowVertical)
        .padding(.horizontal, Theme.rowHorizontal)
        .contentShape(.rect)
    }
}

struct RowButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.background(configuration.isPressed ? Theme.pressed : .clear)
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
            Image(systemName: "chevron.down").font(.system(size: 10, weight: .semibold)).foregroundStyle(Theme.textMuted)
        }
        .padding(.horizontal, 10)
        .frame(height: 30)
        .frame(maxWidth: 190)
        .background(Theme.card, in: .capsule)
        .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
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

enum Route: Hashable { case chat(String) }
