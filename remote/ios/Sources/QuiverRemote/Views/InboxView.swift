import SwiftUI

/// Rows the person cleared, kept on this phone. A row comes back when its
/// session moves again (a newer `last_active_at` than the one cleared).
@MainActor @Observable
final class InboxDismissals {
    private static let key = "quiver.inbox.dismissed"
    private(set) var dismissed: [String: Double]

    init() {
        dismissed = UserDefaults.standard.dictionary(forKey: Self.key) as? [String: Double] ?? [:]
    }

    func isDismissed(_ key: String, at ts: Double?) -> Bool {
        guard let at = dismissed[key] else { return false }
        return (ts ?? 0) <= at
    }

    func dismiss(_ key: String, at ts: Double?) {
        dismissed[key] = ts ?? Date().timeIntervalSince1970
        // Keep the store small: entries older than two weeks can't match.
        let cutoff = Date().timeIntervalSince1970 - 14 * 86_400
        dismissed = dismissed.filter { $0.value >= cutoff }
        UserDefaults.standard.set(dismissed, forKey: Self.key)
    }
}

/// P7: what needs you, what broke, and what finished today.
struct InboxView: View {
    @Environment(AppModel.self) private var app
    @State private var dismissals = InboxDismissals()
    @State private var showOlder = false

    var body: some View {
        let now = Date().timeIntervalSince1970
        let recent = now - 3 * 86_400
        let visible = app.inbox.filter { !dismissals.isDismissed($0.key, at: $0.lastActiveAt) }
        let waiting = visible.filter { $0.sessionStatus == .approval || ($0.sessionStatus == .followup && ($0.lastActiveAt ?? 0) >= recent) }
        let errors = visible.filter { [.error, .interrupted].contains($0.sessionStatus) && ($0.lastActiveAt ?? 0) >= recent }
        let older = visible.filter { item in (item.lastActiveAt ?? 0) < recent && item.sessionStatus != .approval }
        let startOfDay = Calendar.current.startOfDay(for: Date()).timeIntervalSince1970
        let finished = app.sessions.filter {
            $0.sessionStatus == .done && ($0.lastActiveAt ?? 0) >= startOfDay && !dismissals.isDismissed($0.key, at: $0.lastActiveAt)
        }

        PageScroll {
            if !app.loadedOnce {
                LoadingRow(text: "Loading inbox")
            } else if waiting.isEmpty && errors.isEmpty && finished.isEmpty && older.isEmpty {
                StateCard(icon: "checkmark.circle", title: "Nothing needs you",
                          message: "Approvals, questions and errors from your agents land here.")
            }
            if !waiting.isEmpty {
                GroupLabel(text: "Waiting on you", color: Theme.warningText)
                CardList(data: waiting) { item in inboxRow(item, detail: item.sessionStatus == .approval ? "Approval needed" : nil) }
            }
            if !errors.isEmpty {
                GroupLabel(text: "Errors", color: Theme.dangerText)
                CardList(data: errors) { item in inboxRow(item) }
            }
            if !finished.isEmpty {
                GroupLabel(text: "Finished today")
                CardList(data: finished) { row in
                    NavigationLink(value: Route.chat(row.key)) { SessionRowView(row: row) }.buttonStyle(RowButtonStyle())
                }
            }
            if !older.isEmpty {
                Button {
                    withAnimation(.easeOut(duration: 0.15)) { showOlder.toggle() }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: showOlder ? "chevron.down" : "chevron.right").font(.system(size: 11, weight: .semibold))
                        Text(showOlder ? "Hide older" : "\(older.count) older, more than three days")
                    }
                    .font(Theme.control).foregroundStyle(Theme.textSecondary)
                    .padding(.top, 12).padding(.horizontal, 4)
                }
                .buttonStyle(.plain)
                if showOlder {
                    CardList(data: older) { item in inboxRow(item) }
                }
            }
        }
        .refreshable { await app.refresh() }
        .navigationTitle("Inbox")
        .toolbar {
            if !older.isEmpty || !finished.isEmpty || !errors.isEmpty {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        if !finished.isEmpty {
                            Button("Clear finished") { finished.forEach { dismissals.dismiss($0.key, at: $0.lastActiveAt) } }
                        }
                        if !errors.isEmpty {
                            Button("Clear errors") { errors.forEach { dismissals.dismiss($0.key, at: $0.lastActiveAt) } }
                        }
                        if !older.isEmpty {
                            Button("Clear older") { older.forEach { dismissals.dismiss($0.key, at: $0.lastActiveAt) } }
                        }
                    } label: { Text("Clear").font(Theme.control) }
                }
            }
        }
    }

    private func inboxRow(_ item: InboxItem, detail: String? = nil) -> some View {
        NavigationLink(value: Route.chat(item.key)) {
            HStack(spacing: 12) {
                StatusDot(status: item.sessionStatus)
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.displayTitle).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                    HStack(spacing: 6) {
                        HarnessIcon(harness: item.harness, name: item.harnessName, size: 13)
                        Text(detail ?? item.sessionStatus.label)
                        let when = RelativeTime.short(item.lastActiveAt)
                        if !when.isEmpty { Text("· \(when)") }
                    }
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if item.sessionStatus == .approval {
                    Text("Review").font(Theme.control).foregroundStyle(Theme.textPrimary)
                        .padding(.horizontal, 10).frame(height: 28)
                        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.borderStrong, lineWidth: 1))
                } else {
                    Image(systemName: "chevron.right").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textMuted)
                }
            }
            .padding(.vertical, Theme.rowVertical)
            .padding(.horizontal, Theme.rowHorizontal)
            .contentShape(.rect)
        }
        .buttonStyle(RowButtonStyle())
        .contextMenu {
            if item.sessionStatus != .approval {
                Button("Clear") { dismissals.dismiss(item.key, at: item.lastActiveAt) }
            }
        }
    }
}
