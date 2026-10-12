import AppKit
import SwiftUI

/// TokenTracker's search input: 36pt, radius 8, a search glyph, 14/20
/// text on the field fill, a hairline that strengthens on focus, and the
/// focus ring. Give it the room: it takes the slack in a filter bar.
struct SearchField: View {
    var prompt: String
    @Binding var text: String
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 14))
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
                    .plainButton(cornerRadius: Theme.smallRadius)
                    .help("Clear")
            }
        }
        .padding(.horizontal, 12)
        .frame(height: Theme.fieldHeight)
        .background(Theme.field, in: .rect(cornerRadius: Theme.fieldRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.fieldRadius)
            .strokeBorder(focused ? Theme.borderHover : Theme.border, lineWidth: 1))
        .focusRing(focused, cornerRadius: Theme.fieldRadius, afterPointer: true)
        .layoutPriority(1)
    }
}

/// TokenTracker's select: 36pt, radius 6, 14/20 at 400 on the field fill,
/// a chevron, and a border that strengthens under the pointer. An optional
/// "Label:" prefix and symbol. A menu underneath, so it reads as a popup.
/// An option that stands for a harness can carry its id: the menu item and,
/// once picked, the closed label then draw its logo. A harness with no
/// bundled logo shows its title only.
struct SelectMenu<Value: Hashable>: View {
    var label: String? = nil
    var symbol: String? = nil
    var options: [(value: Value, title: String)]
    @Binding var selection: Value
    /// 168pt as measured; nil sizes to the text.
    var width: CGFloat? = 168
    /// What the closed menu shows. Defaults to the selected option's title;
    /// pass it when the selection can drop out of `options` (a filter whose
    /// value vanished on refresh) so the menu still says what it's set to.
    var title: String? = nil
    /// The harness id whose logo goes beside an option, by option value.
    /// The three-part options initialiser fills it in.
    var icons: [Value: String] = [:]
    @State private var hovering = false

    private var current: String {
        title ?? options.first { $0.value == selection }?.title ?? ""
    }

    /// The selected option's harness, when it has a logo to draw.
    private var currentIcon: String? {
        guard let id = icons[selection], HarnessIcon.exists(for: id) else { return nil }
        return id
    }

    var body: some View {
        Menu {
            ForEach(options, id: \.value) { option in
                if icons.isEmpty {
                    Button {
                        selection = option.value
                    } label: {
                        if option.value == selection {
                            Label(option.title, systemImage: "checkmark")
                        } else {
                            Text(option.title)
                        }
                    }
                } else {
                    // The logo takes the item's image, so the checkmark goes
                    // in the menu's own state column instead.
                    Toggle(isOn: Binding(get: { option.value == selection },
                                         set: { if $0 { selection = option.value } })) {
                        if let id = icons[option.value], let image = HarnessIcon.menuImage(for: id) {
                            Label { Text(option.title) } icon: { Image(nsImage: image) }
                        } else {
                            Text(option.title)
                        }
                    }
                }
            }
        } label: {
            HStack(spacing: 6) {
                if let symbol { Image(systemName: symbol).foregroundStyle(Theme.textSecondary) }
                if let label {
                    // The prefix keeps its width; the value truncates instead.
                    Text("\(label):").foregroundStyle(Theme.textSecondary).lineLimit(1).fixedSize()
                }
                if let currentIcon { HarnessIcon(harness: currentIcon, size: 16) }
                Text(current).foregroundStyle(Theme.textPrimary).lineLimit(1).truncationMode(.tail)
                Spacer(minLength: 4)
                Image(systemName: "chevron.down").font(.system(size: 12))
                    .foregroundStyle(Theme.textSecondary)
            }
            .font(Theme.bodySmall)
            .padding(.horizontal, 12)
            .frame(width: width, height: Theme.fieldHeight)
            .fixedSize(horizontal: width == nil, vertical: false)
            .background(Theme.field, in: .rect(cornerRadius: Theme.selectRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.selectRadius)
                .strokeBorder(hovering ? Theme.borderHover : Theme.border, lineWidth: 1))
            .contentShape(.rect(cornerRadius: Theme.selectRadius))
        }
        .menuStyle(.button)
        .menuIndicator(.hidden)
        .plainButton(cornerRadius: Theme.selectRadius)
        .onHover { hovering = $0 }
        .fixedSize()
    }
}

extension SelectMenu {
    /// Options as (value, title, harness id or nil).
    init(label: String? = nil, symbol: String? = nil,
         options: [(value: Value, title: String, icon: String?)],
         selection: Binding<Value>, width: CGFloat? = 168, title: String? = nil) {
        var icons: [Value: String] = [:]
        for option in options { if let icon = option.icon { icons[option.value] = icon } }
        self.init(label: label, symbol: symbol, options: options.map { ($0.value, $0.title) },
                  selection: selection, width: width, title: title, icons: icons)
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
func displayPath(_ path: String) -> String {
    let home = FileManager.default.homeDirectoryForCurrentUser.path
    return path.hasPrefix(home + "/") ? "~" + path.dropFirst(home.count) : path
}

func displayPath(_ url: URL) -> String {
    displayPath(url.path)
}

func copyToPasteboard(_ text: String) {
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(text, forType: .string)
}
