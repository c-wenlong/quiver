import QuiverCore
import SwiftUI

/// The `@` menu above the prompt box: files under the session's folder
/// that match what follows the `@`, each as its name then the folder it
/// sits in. A port of the iPhone app's `MentionMenu` (app/ios) at
/// desktop density, with the arrows' lit row standing in for touch; it
/// sits where the `/` menu does and looks like it.
struct MentionMenuCard: View {
    var search: MentionSearch
    /// The highlighted row, which hovering also sets.
    @Binding var active: Int
    var onPick: (FileEntry) -> Void

    /// The row's height at the current text zoom, so the scroll window's
    /// cap grows with the text.
    private var rowHeight: CGFloat {
        TextZoom.pointSize(30, at: TextZoomModel.shared.zoom)
    }
    static let visibleRows = 8

    /// The rows the arrows and Return can pick: none while an error shows.
    static func rows(_ search: MentionSearch) -> [FileEntry] {
        search.error == nil ? search.entries : []
    }

    var body: some View {
        let rows = Self.rows(search)
        Group {
            if rows.count > Self.visibleRows {
                ScrollViewReader { proxy in
                    ScrollView(.vertical) { stack(rows) }
                        .defaultScrollAnchor(.top)
                        .frame(height: CGFloat(Self.visibleRows) * rowHeight)
                        .onChange(of: active) { _, i in proxy.scrollTo(min(i, rows.count - 1)) }
                }
            } else {
                stack(rows)
            }
        }
        .padding(.vertical, 4)
        .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
        .clipShape(.rect(cornerRadius: Theme.blockRadius))
        .accessibilityLabel("Files")
    }

    private func stack(_ rows: [FileEntry]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            if let error = search.error {
                note(error, icon: "exclamationmark.circle")
            } else if rows.isEmpty {
                note(search.answered == nil ? "Searching files" : "No files match")
            } else {
                ForEach(Array(rows.enumerated()), id: \.offset) { i, entry in
                    Button { onPick(entry) } label: { row(entry) }
                        .buttonStyle(.plain)
                        .background(i == min(active, rows.count - 1) ? Theme.hover : .clear)
                        .onHover { if $0 { active = i } }
                        .id(i)
                }
            }
        }
    }

    /// A muted line in place of rows: still searching, nothing matched,
    /// or why the menu can't search here.
    private func note(_ text: String, icon: String? = nil) -> some View {
        HStack(spacing: 10) {
            if let icon {
                Image(systemName: icon).font(Theme.font(size: 13)).foregroundStyle(Theme.textMuted).frame(width: 18)
            }
            Text(text).font(Theme.caption).foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 12)
        .frame(minHeight: rowHeight)
    }

    private func row(_ entry: FileEntry) -> some View {
        let (name, parent) = Mention.split(entry.path)
        return HStack(spacing: 10) {
            Image(systemName: entry.isDir ? "folder" : "doc")
                .font(Theme.font(size: 13)).foregroundStyle(Theme.textSecondary)
                .frame(width: 18)
            Text(name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .layoutPriority(1)
            if !parent.isEmpty {
                Text(parent).font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .frame(height: rowHeight)
        .contentShape(.rect)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(parent.isEmpty ? name : "\(name), in \(parent)")
        .accessibilityHint(entry.isDir ? "Inserts the folder and lists what's inside" : "Inserts the file")
    }
}
