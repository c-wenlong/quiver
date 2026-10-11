import AppKit
import SwiftUI

/// TokenTracker's filter field: a search glyph, 14pt text on the field
/// fill, a hairline that strengthens on focus, and the focus ring.
struct SearchField: View {
    var prompt: String
    @Binding var text: String
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 13))
                .foregroundStyle(Theme.textMuted)
            TextField(prompt, text: $text)
                .textFieldStyle(.plain)
                .font(Theme.bodySmall)
                .focused($focused)
            if !text.isEmpty {
                Button("Clear", systemImage: "xmark") { text = "" }
                    .labelStyle(.iconOnly)
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.textMuted)
                    .buttonStyle(RingedPlainStyle(cornerRadius: 4))
                    .help("Clear")
            }
        }
        .padding(.horizontal, 12)
        .frame(height: Theme.controlHeight + 4)
        .background(Theme.field, in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
            .strokeBorder(focused ? Theme.textSecondary.opacity(0.6) : Theme.border, lineWidth: 1))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius + 3)
            .strokeBorder(Theme.focus, lineWidth: Theme.focusRing)
            .padding(-3)
            .opacity(focused ? 1 : 0)
            .allowsHitTesting(false))
    }
}

/// TokenTracker's select: "Label: value" on a hairline, 6pt radius, with a
/// chevron. A menu underneath, so it reads as a native popup.
struct SelectMenu<Value: Hashable>: View {
    var label: String
    var options: [(value: Value, title: String)]
    @Binding var selection: Value

    private var current: String {
        options.first { $0.value == selection }?.title ?? ""
    }

    var body: some View {
        Menu {
            ForEach(options, id: \.value) { option in
                Button {
                    selection = option.value
                } label: {
                    if option.value == selection {
                        Label(option.title, systemImage: "checkmark")
                    } else {
                        Text(option.title)
                    }
                }
            }
        } label: {
            HStack(spacing: 6) {
                Text("\(label):").foregroundStyle(Theme.textSecondary)
                Text(current).foregroundStyle(Theme.textPrimary)
                Image(systemName: "chevron.down").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
            }
            .font(Theme.control)
            .padding(.horizontal, 12)
            .frame(height: Theme.controlHeight + 4)
            .background(Theme.field, in: .rect(cornerRadius: 6))
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Theme.border, lineWidth: 1))
            .contentShape(.rect(cornerRadius: 6))
        }
        .menuStyle(.button)
        .buttonStyle(RingedPlainStyle(cornerRadius: 6))
        .menuIndicator(.hidden)
        .fixedSize()
    }
}

/// Lays children out left to right and wraps, like inline text.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6
    var lineSpacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(width: proposal.width ?? .infinity, subviews: subviews)
        let width = rows.map(\.width).max() ?? 0
        let height = rows.map(\.height).reduce(0, +) + lineSpacing * CGFloat(max(rows.count - 1, 0))
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(width: bounds.width, subviews: subviews) {
            var x = bounds.minX
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y + (row.height - size.height) / 2),
                                      proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += row.height + lineSpacing
        }
    }

    private struct Row {
        var indices: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func arrange(width: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = []
        var row = Row()
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            let next = row.indices.isEmpty ? size.width : row.width + spacing + size.width
            if next > width, !row.indices.isEmpty {
                rows.append(row)
                row = Row()
            }
            row.width = row.indices.isEmpty ? size.width : row.width + spacing + size.width
            row.height = max(row.height, size.height)
            row.indices.append(index)
        }
        if !row.indices.isEmpty { rows.append(row) }
        return rows
    }
}

/// `~/…` for a path under the home folder.
func abbreviated(_ path: String) -> String {
    let home = FileManager.default.homeDirectoryForCurrentUser.path
    return path.hasPrefix(home + "/") ? "~" + path.dropFirst(home.count) : path
}

func copyToPasteboard(_ text: String) {
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(text, forType: .string)
}
