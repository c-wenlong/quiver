import QuiverCore
import SwiftUI

/// The Queued tray above the chat's prompt box: queue-mode sends the hub
/// holds until the agent's turn ends, oldest first, each with Edit,
/// Remove and Send now. A port of the iPhone app's `QueuedTray` (app/ios)
/// at desktop density.
struct QueuedTrayCard: View {
    var tray: QueuedTray
    var onEdit: (String) -> Void
    var onRemove: (String) -> Void
    var onSendNow: (String) -> Void
    var onRemoveAll: () -> Void
    @State private var confirmRemoveAll = false

    /// Rows shown before the card scrolls.
    static let visibleRows = 3

    private var rowHeight: CGFloat { TextZoom.pointSize(32, at: TextZoomModel.shared.zoom) }

    var body: some View {
        let sends = tray.sends
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 0) {
                Text(QueuedTray.countLabel(sends.count))
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                TrayIconButton(symbol: "xmark", label: "Remove all") { confirmRemoveAll = true }
            }
            .padding(.leading, 12).padding(.trailing, 4).padding(.top, 2)
            if sends.count > Self.visibleRows {
                ScrollView(.vertical) { rows(sends) }
                    // The chat anchors its scroll views at the bottom; the
                    // tray starts at its oldest send.
                    .defaultScrollAnchor(.top)
                    .frame(height: CGFloat(Self.visibleRows) * rowHeight)
            } else {
                rows(sends)
            }
        }
        .padding(.bottom, 4)
        .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
        .clipShape(.rect(cornerRadius: Theme.blockRadius))
        .animation(.easeOut(duration: 0.2), value: sends.map(\.id))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Queued messages")
        .confirmationDialog(sends.count == 1 ? "Remove 1 queued message?" : "Remove \(sends.count) queued messages?",
                            isPresented: $confirmRemoveAll) {
            Button("Remove all", role: .destructive, action: onRemoveAll)
        }
    }

    private func rows(_ sends: [QueuedSend]) -> some View {
        VStack(spacing: 0) {
            ForEach(sends) { send in
                row(send).transition(.opacity)
            }
        }
    }

    private func row(_ send: QueuedSend) -> some View {
        let editing = send.id == tray.editingId
        return HStack(spacing: 10) {
            Group {
                if send.phase == .sendingNow {
                    ProgressView().controlSize(.mini)
                } else {
                    Image(systemName: editing ? "pencil.circle" : "circle.dashed")
                        .font(Theme.font(size: 13)).foregroundStyle(Theme.textMuted)
                }
            }
            .frame(width: 18)
            Text(send.text.replacingOccurrences(of: "\n", with: " "))
                .font(Theme.bodySmall).foregroundStyle(editing ? Theme.textSecondary : Theme.textPrimary)
                .lineLimit(1).truncationMode(.tail)
                .frame(maxWidth: .infinity, alignment: .leading)
            if let note = send.note {
                Text(note).font(Theme.caption)
                    .foregroundStyle(send.phase == .left ? Theme.textMuted : Theme.dangerText)
                    .lineLimit(1)
                // A send that already left can still be cleared from the tray.
                if send.phase == .left {
                    TrayIconButton(symbol: "xmark", label: "Remove") { onRemove(send.clientId) }
                } else {
                    actions(send)
                }
            } else if send.phase == .sendingNow {
                Text("Sending now").font(Theme.caption).foregroundStyle(Theme.textMuted).padding(.trailing, 8)
            } else {
                actions(send)
            }
        }
        .padding(.leading, 12).padding(.trailing, 4)
        .frame(height: rowHeight)
    }

    private func actions(_ send: QueuedSend) -> some View {
        HStack(spacing: 0) {
            TrayIconButton(symbol: "pencil", label: "Edit") { onEdit(send.clientId) }
            TrayIconButton(symbol: "xmark", label: "Remove") { onRemove(send.clientId) }
            TrayIconButton(symbol: "arrow.up", label: "Send now", tip: "Send now: writes it into the running turn") {
                onSendNow(send.clientId)
            }
        }
    }
}

/// A small chromeless glyph button on a tray row.
private struct TrayIconButton: View {
    var symbol: String
    var label: String
    var tip: String?
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(Theme.font(size: 12, weight: .medium))
                .foregroundStyle(Theme.textSecondary)
                .frame(width: 26, height: 26)
                .contentShape(.rect)
        }
        .plainButton(cornerRadius: Theme.controlRadius)
        .hoverTip(tip ?? label)
        .accessibilityLabel(label)
    }
}

/// Above the prompt box while a queued message is being edited, or to
/// say why an edit didn't save.
struct QueuedEditBar: View {
    var editing: Bool
    var note: String?
    var cancel: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: editing ? "pencil" : "exclamationmark.circle")
                .font(Theme.font(size: 12, weight: .medium)).foregroundStyle(Theme.textSecondary)
            VStack(alignment: .leading, spacing: 1) {
                if editing {
                    Text("Editing queued message").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                if let note {
                    Text(note).font(Theme.caption).foregroundStyle(Theme.dangerText).lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if editing {
                Button("Cancel", action: cancel)
                    .plainButton()
                    .font(Theme.caption)
                    .foregroundStyle(Theme.textSecondary)
                    .hoverTip("Put back what you were typing")
            }
        }
        .padding(.horizontal, 12)
    }
}
