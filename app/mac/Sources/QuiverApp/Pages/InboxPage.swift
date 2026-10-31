import QuiverCore
import SwiftUI

/// What needs you: the sessions this Mac's hub says wait on you, broke
/// or finished today (`NeedsYouCards`), then the GitHub notifications
/// inbox: one stream across every repo, with the unread state, the
/// subject type's icon and why GitHub notified you. A notification row
/// opens its subject on github.com.
struct InboxPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    /// Unread first by default; the All tab widens the list to read rows.
    @State private var unreadOnly = true
    @State private var needsYou = NeedsYouModel()

    private var model: GitHubModel { app.github }

    var body: some View {
        Page(title: "Inbox",
             subtitle: "Sessions waiting on you, then your GitHub notifications across every repo.") {
            Tabs(options: [(true, "Unread"), (false, "All")], selection: $unreadOnly)
            if model.unreadCount > 0 {
                Button {
                    Task { await model.markInboxRead([], all: true, swe: app.swe) }
                } label: {
                    if model.actionBusy.contains("inbox:all") {
                        ProgressView().controlSize(.small)
                    } else {
                        Label("Mark all read", systemImage: "envelope.open")
                    }
                }
                .secondaryButton()
                .help("Mark every notification read")
            }
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task {
                    async let sessions: Void = needsYou.load {
                        try await app.hub.withClient { try await $0.inbox(done: true) }
                    }
                    await model.loadInbox(swe: app.swe, force: true)
                    await sessions
                }
            }
            .iconButton()
            .keyboardShortcut("r", modifiers: .command)
            .help("Fetch the inbox again (⌘R)")
        } content: {
            NeedsYouCards(model: needsYou)
            if let setup = model.envSetup {
                GhSetupCard(status: setup) {
                    Task { await model.loadInbox(swe: app.swe, force: true) }
                }
            } else if let blocked = model.envBlocked {
                ErrorCard(message: blocked, actionTitle: "Refresh") {
                    Task { await model.loadInbox(swe: app.swe, force: true) }
                }
            } else {
                switch model.inbox {
                case .idle, .loading:
                    SkeletonRows(rows: 5, shape: .note, label: "Reading GitHub notifications…")
                case .failed(let message):
                    ErrorCard(message: message, actionTitle: "Refresh") {
                        Task { await model.loadInbox(swe: app.swe, force: true) }
                    }
                case .loaded(let notifications):
                    if let error = model.actionError {
                        ErrorCard(message: error)
                    }
                    let rows = unreadOnly ? notifications.filter(\.unread) : notifications
                    if rows.isEmpty {
                        EmptyCard(text: unreadOnly ? "No unread notifications." : "The inbox is empty.",
                                  icon: "tray")
                    } else {
                        Card(title: needsYou.items.isEmpty ? nil : "GitHub") {
                            VStack(spacing: 0) {
                                ForEach(Array(rows.enumerated()), id: \.element.id) { index, note in
                                    NotificationRow(note: note) {
                                        if let url = URL(string: note.url), url.isWebURL { openURL(url) }
                                    }
                                    .contextMenu {
                                        if note.unread {
                                            Button("Mark as read") {
                                                Task {
                                                    await model.markInboxRead(
                                                        [note.id], swe: app.swe)
                                                }
                                            }
                                            Divider()
                                        }
                                        Button("Open on GitHub") {
                                            if let url = URL(string: note.url), url.isWebURL { openURL(url) }
                                        }
                                    }
                                    if index < rows.count - 1 { Divider().overlay(Theme.rowDivider) }
                                }
                            }
                            if let at = model.inboxFetchedAt {
                                Text("Updated \(UsageFormat.ago(at, now: .now))")
                                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                            }
                        }
                    }
                }
            }
        }
        // GitHubNotifier polls the inbox every minute for the app's whole
        // life; a second loop here doubled the swe runs, and its replies,
        // landing out of step with the notifier's, could put back a row
        // marked read meanwhile. The page only catches up when stale.
        .task(id: app.swePath) { await model.loadInbox(swe: app.swe) }
        .task(id: app.swePath) { await NeedsYouCards.poll(needsYou, hub: app.hub) }
    }
}

/// One notification: an accent dot while unread, the subject type's
/// icon, title, repo, the notify reason, and its age.
private struct NotificationRow: View {
    var note: GhNotification
    var open: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: open) {
            HStack(alignment: .top, spacing: 12) {
                Circle()
                    .fill(note.unread ? Theme.interactive : .clear)
                    .frame(width: 8, height: 8)
                    .padding(.top, 5)
                Image(systemName: icon)
                    .font(Theme.font(size: 15))
                    .foregroundStyle(iconColor)
                VStack(alignment: .leading, spacing: 3) {
                    Text(note.title)
                        .font(note.unread ? Theme.rowTitle : Theme.bodySmall)
                        .foregroundStyle(note.unread ? Theme.textPrimary : Theme.textSecondary)
                        .lineLimit(2)
                    Text("\(note.repo) · \(reason)"
                         + (note.updatedAt.date.map { " · \(UsageFormat.ago($0, now: .now))" } ?? ""))
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                        .lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 8)
            }
            .padding(.vertical, 10)
            .padding(.horizontal, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(hovering ? Theme.rowHover : .clear,
                        in: .rect(cornerRadius: Theme.controlRadius))
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))
        .onHover { hovering = $0 }
    }

    private var icon: String {
        switch note.type {
        case "PullRequest": "arrow.triangle.pull"
        case "Issue": "exclamationmark.circle"
        case "CheckSuite", "CheckRun": "xmark.circle.fill"
        case "Release": "tag"
        case "Discussion": "bubble.left"
        case "Commit": "point.3.connected.trianglepath.dotted"
        default: "bell"
        }
    }

    private var iconColor: Color {
        switch note.reason {
        case "ci_activity": Theme.danger
        case "review_requested", "mention": Theme.warning
        default: Theme.textSecondary
        }
    }

    /// The notify reason as a short phrase.
    private var reason: String {
        note.reason.replacing("_", with: " ")
    }
}
