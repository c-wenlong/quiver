import SWECore
import SwiftUI

/// The Inbox's Needs you rows from this Mac's hub (`GET /v1/inbox`):
/// Waiting on you, Errors and Finished today, as the iPhone's Inbox
/// files them. A row opens that session's chat on the Sessions page;
/// Clear hides it for this Mac until the session moves again. Shows
/// nothing at all while the hub isn't running. A bare `ForEach`, so an
/// empty list adds no gap to the page; `poll` keeps it current.
struct NeedsYouCards: View {
    @Environment(AppModel.self) private var app
    var model: NeedsYouModel
    /// Finished today folds after five rows: a busy day finishes dozens
    /// of sessions, which would push GitHub off the page. The other
    /// sections each need a look, so they never fold.
    @State private var finishedExpanded = false
    private static let foldLimit = 5

    var body: some View {
        ForEach(model.groups, id: \.section) { group in
            let clearable = group.items.filter { !$0.isApproval }
            Card(title: group.section.title) {
                if !clearable.isEmpty {
                    Button("Clear") { dismiss(clearable) }
                        .secondaryButton()
                        .hoverTip("Clear \(group.section.clearTitle) on this Mac until the session moves again")
                }
            } content: {
                let folds = group.section == .finished
                let isExpanded = !folds || finishedExpanded
                let shown = RowFold.visible(group.items, limit: Self.foldLimit, expanded: isExpanded)
                let hidden = folds ? RowFold.hiddenCount(total: group.items.count, limit: Self.foldLimit) : 0
                VStack(spacing: 0) {
                    ForEach(Array(shown.enumerated()), id: \.element.id) { index, item in
                        NeedsYouRow(item: item,
                                    open: { app.openSessionChat(key: item.key) },
                                    clear: item.isApproval ? nil : { dismiss([item]) })
                        if index < shown.count - 1 || hidden > 0 { Divider().overlay(Theme.rowDivider) }
                    }
                    if hidden > 0 {
                        ExpandRow(isExpanded: isExpanded, hidden: hidden,
                                  help: (expand: "Show every row", collapse: "Show only the first \(Self.foldLimit)"),
                                  edge: .inset) {
                            withAnimation(.easeOut(duration: Theme.fadeDuration)) {
                                finishedExpanded.toggle()
                            }
                        }
                    }
                }
            }
        }
    }

    /// Loads while the page is open, every 15 s as the phone does, and
    /// only once a minute while the hub doesn't answer: each try asks
    /// swe where the hub listens.
    static func poll(_ model: NeedsYouModel, hub: LocalHub) async {
        while !Task.isCancelled {
            await model.load { try await hub.withClient { try await $0.inbox(done: true) } }
            try? await Task.sleep(for: .seconds(model.reachable ? 15 : 60))
        }
    }

    private func fetch() async throws -> [HubInboxItem] {
        try await app.hub.withClient { try await $0.inbox(done: true) }
    }

    private func dismiss(_ rows: [HubInboxItem]) {
        Task {
            await model.dismiss(rows,
                                post: { key, stamp in
                                    try await app.hub.withClient { try await $0.dismissInbox(key, lastActiveAt: stamp) }
                                },
                                refetch: { try await fetch() })
        }
    }
}

/// One session that needs you: its harness's logo, title, status and
/// age, then Review on an approval or a hover Clear on the rest.
private struct NeedsYouRow: View {
    var item: HubInboxItem
    var open: () -> Void
    var clear: (() -> Void)?
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 8) {
            Button(action: open) {
                HStack(spacing: 12) {
                    HarnessIcon(harness: item.harness ?? "", size: 16)
                        .frame(width: 16, height: 16)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(item.displayTitle)
                            .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                            .lineLimit(1)
                        Text(detail)
                            .font(Theme.caption).foregroundStyle(statusColor)
                            .lineLimit(1).truncationMode(.middle)
                    }
                    Spacer(minLength: 8)
                    if item.isApproval {
                        Text("Review").font(Theme.control).foregroundStyle(Theme.textPrimary)
                            .padding(.horizontal, 10).frame(height: Theme.controlHeight)
                            .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
                                .strokeBorder(Theme.borderStrong, lineWidth: 1))
                    }
                }
                .padding(.vertical, 10)
                .padding(.horizontal, 6)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(.rect(cornerRadius: Theme.controlRadius))
            }
            .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))
            .hoverTip("Open this session's chat")
            if let clear {
                Button(action: clear) {
                    Image(systemName: "xmark")
                }
                .iconButton()
                .opacity(hovering ? 1 : 0)
                .hoverTip("Clear on this Mac until the session moves again")
            }
        }
        .padding(.trailing, clear == nil ? 0 : 4)
        .background(hovering ? Theme.rowHover : .clear, in: .rect(cornerRadius: Theme.controlRadius))
        .onHover { hovering = $0 }
        .contextMenu {
            Button("Open chat", action: open)
            if let clear { Button("Clear", action: clear) }
        }
    }

    /// "Waiting · 4m ago"; the harness's name joins only when it has no
    /// logo to show.
    private var detail: String {
        var parts: [String] = []
        if let harness = item.harness, HarnessIcon.image(for: harness) == nil {
            parts.append(item.agent ?? harness)
        }
        parts.append(NeedsYouSection.statusLabel(item))
        if let at = item.lastActiveAt, at > 0 {
            parts.append(UsageFormat.ago(Date(timeIntervalSince1970: at), now: .now))
        }
        return parts.joined(separator: " · ")
    }

    private var statusColor: Color {
        switch NeedsYouSection.of(item) {
        case .waiting: Theme.warningText
        case .errors: Theme.dangerText
        default: Theme.textMuted
        }
    }
}
