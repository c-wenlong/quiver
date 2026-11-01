import QuiverCore
import SwiftUI

/// The Sessions list's "Other machines" group: the hub's sessions for each
/// satellite, under this Mac's own groups (`OtherMachines`). The page's
/// filters and search don't reach it, and the hub sends no usage for these
/// rows, so Tokens and Spend read "–".
struct OtherMachinesGroup: View {
    var group: OtherMachines
    /// The hub's pin, name and fork flag for a key, with the choices made
    /// here since; nil offers no hub actions.
    var meta: (String) -> HubSessionMeta?
    /// Whether a key's row shows the unread dot.
    var unread: (String) -> Bool = { _ in false }
    var open: (OtherMachines.Row) -> Void
    var act: (SessionMenuAction, OtherMachines.Row) -> Void
    /// Rows a machine shows before folding behind a Show more row.
    var foldLimit = 10
    /// Machines the user has unfolded.
    @State private var opened: Set<String> = []

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "server.rack")
                    .foregroundStyle(Theme.textSecondary)
                Text("Other machines")
                    .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                Text(group.caption)
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 10)
            .background(Theme.accentTint, in: .rect(cornerRadius: Theme.controlRadius))
            .padding(.top, 18).padding(.bottom, 4)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)
            ForEach(group.machines) { machine in
                subheader(machine)
                Divider().overlay(Theme.border)
                let isOpen = opened.contains(machine.id)
                ForEach(RowFold.visible(machine.rows, limit: foldLimit, expanded: isOpen)) { row in
                    OtherMachineRow(row: row, meta: meta(row.key), unread: unread(row.key),
                                    open: { open(row) }, act: { act($0, row) })
                    Divider().overlay(Theme.border)
                }
                let hidden = RowFold.hiddenCount(total: machine.rows.count, limit: foldLimit)
                if hidden > 0 {
                    ExpandRow(isExpanded: isOpen, hidden: hidden,
                              help: (expand: "Show every session on \(machine.name)",
                                     collapse: "Show only the first \(foldLimit)"),
                              edge: .inset) {
                        withAnimation(.easeOut(duration: Theme.fadeDuration)) {
                            if isOpen { opened.remove(machine.id) } else { opened.insert(machine.id) }
                        }
                    }
                }
            }
        }
    }

    private func subheader(_ machine: OtherMachines.Machine) -> some View {
        HStack(spacing: 8) {
            Circle()
                .fill(machine.online ? Theme.success : Theme.textMuted)
                .frame(width: Theme.statusDot, height: Theme.statusDot)
            Text(machine.title)
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                .lineLimit(1)
        }
        .padding(.horizontal, 8)
        .padding(.top, 14).padding(.bottom, 8)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(machine.online ? machine.name : "\(machine.name), offline")
    }
}

/// One satellite session: the Mac row's layout without usage or Resume,
/// since the session runs on another machine.
private struct OtherMachineRow: View {
    var row: OtherMachines.Row
    var meta: HubSessionMeta?
    var unread = false
    var open: () -> Void
    var act: (SessionMenuAction) -> Void
    @State private var hovering = false

    /// The hub's current name, so a rename or "Use the original name"
    /// made here shows before the next read; the session id when the
    /// hub knows no title at all.
    private var title: String {
        guard let meta else { return row.title }
        return meta.title.isEmpty ? row.sessionId : meta.title
    }

    var body: some View {
        let harness = row.hub.harness ?? ""
        HStack(alignment: .top, spacing: 16) {
            HStack(alignment: .center, spacing: 12) {
                HarnessIcon(harness: HarnessMark(harness).icon ?? harness, size: 16)
                    .hoverTip(HarnessMark(harness).name)
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        if unread { UnreadDot() }
                        Text(title)
                            .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                        if meta?.pinned == true {
                            Image(systemName: "pin.fill")
                                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                                .accessibilityLabel("Pinned")
                        }
                    }
                    Text(row.details)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text("–")
                .font(Theme.bodySmall).foregroundStyle(Theme.textMuted)
                .frame(width: tokensColumnWidth, alignment: .trailing)
            Text("–")
                .font(Theme.bodySmall).foregroundStyle(Theme.textMuted)
                .frame(width: spendColumnWidth, alignment: .trailing)
            // The Resume button's column on the Mac's rows.
            Color.clear.frame(width: Theme.iconButton, height: 1)
        }
        .padding(.vertical, 12)
        .padding(.horizontal, 8)
        .background(hovering ? Theme.panelHover : .clear, in: .rect(cornerRadius: Theme.controlRadius))
        .contentShape(.rect)
        .onHover { hovering = $0 }
        .textSelection(.disabled)
        .onTapGesture(perform: open)
        .contextMenu {
            Button("Open chat", action: open)
            if let meta {
                Divider()
                SessionHubMenuItems(meta: meta, act: act)
            }
            Divider()
            Button("Copy session ID") { copyToPasteboard(row.sessionId) }
        }
        .accessibilityElement(children: .combine)
        .accessibilityAction(named: "Open chat", open)
        .accessibilityActions {
            if let meta {
                Button(meta.pinned ? "Unpin" : "Pin") { act(.pin(!meta.pinned)) }
                Button("Rename") { act(.rename) }
                if meta.canFork { Button("Fork") { act(.fork) } }
            }
        }
    }
}
