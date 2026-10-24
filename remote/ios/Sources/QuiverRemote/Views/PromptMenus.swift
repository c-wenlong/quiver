import SwiftUI

/// The prompt box. On iOS 18 it reports where the caret is and can move
/// it; iOS 17's TextField can't do either, so there `cursor` stays nil
/// (the end of the text) and a pick leaves the caret at the end.
struct PromptField: View {
    let placeholder: String
    @Binding var text: String
    /// Where the caret is, as a Character offset; nil is the end.
    @Binding var cursor: Int?
    /// Set to move the caret there; the field clears it once moved.
    @Binding var moveCaret: Int?

    var body: some View {
        if #available(iOS 18, *) {
            SelectingField(placeholder: placeholder, text: $text, cursor: $cursor, moveCaret: $moveCaret)
        } else {
            TextField(placeholder, text: $text, axis: .vertical)
                .onChange(of: moveCaret) { _, c in if c != nil { moveCaret = nil } }
        }
    }

    @available(iOS 18, *)
    private struct SelectingField: View {
        let placeholder: String
        @Binding var text: String
        @Binding var cursor: Int?
        @Binding var moveCaret: Int?
        @State private var selection: TextSelection?

        var body: some View {
            TextField(placeholder, text: $text, selection: $selection, axis: .vertical)
                .onChange(of: selection) { _, s in cursor = Self.caret(s, in: text) }
                .onChange(of: moveCaret) { _, c in
                    guard let c else { return }
                    moveCaret = nil
                    let clamped = Caret.clamp(c, in: text)
                    selection = TextSelection(insertionPoint: Caret.index(clamped, in: text))
                    cursor = clamped
                }
        }

        /// The caret's offset; a range selection counts from its end, and
        /// no selection is the end of the text.
        static func caret(_ s: TextSelection?, in text: String) -> Int? {
            guard let s else { return nil }
            switch s.indices {
            case .selection(let range): return Caret.offset(range.upperBound, in: text)
            default: return nil
            }
        }
    }
}

/// The card both prompt menus sit in, just above the prompt box: rows
/// on `Theme.card` with a hairline border; past `visibleRows` it scrolls.
struct PromptMenuCard<Rows: View>: View {
    let count: Int
    let rowHeight: CGFloat
    var visibleRows = 6
    @ViewBuilder let rows: () -> Rows

    var body: some View {
        Group {
            if count > visibleRows {
                ScrollView(.vertical) { VStack(spacing: 0) { rows() } }
                    // The chat anchors its scroll views at the bottom; the
                    // menu starts at its best match.
                    .defaultScrollAnchor(.top)
                    .scrollDismissesKeyboard(.never)
                    .frame(height: CGFloat(visibleRows) * rowHeight)
            } else {
                VStack(spacing: 0) { rows() }
            }
        }
        .padding(.vertical, 4)
        .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
        .clipShape(.rect(cornerRadius: Theme.blockRadius))
    }
}

/// A muted line in a prompt menu: still searching, nothing matched, or
/// why the menu can't help here.
struct PromptMenuNote: View {
    let text: String
    var icon: String? = nil

    var body: some View {
        HStack(spacing: 10) {
            if let icon {
                Image(systemName: icon).font(.system(size: 13)).foregroundStyle(Theme.textMuted).frame(width: 18)
            }
            Text(text).font(Theme.caption).foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 14)
        .frame(minHeight: MentionMenu.rowHeight)
    }
}

/// The `@` menu: files and folders under the session's folder that match
/// what's typed after the `@`. Best match first.
struct MentionMenu: View {
    let entries: [FileEntry]
    /// The query the entries answer; nil while the first search runs.
    let answered: String?
    let error: String?
    var onPick: (FileEntry) -> Void = { _ in }

    static let rowHeight: CGFloat = 44

    var body: some View {
        PromptMenuCard(count: error == nil ? entries.count : 1, rowHeight: Self.rowHeight) {
            if let error {
                PromptMenuNote(text: error, icon: "exclamationmark.circle")
            } else if answered == nil && entries.isEmpty {
                PromptMenuNote(text: "Searching files")
            } else if entries.isEmpty {
                PromptMenuNote(text: "No files match")
            } else {
                ForEach(entries, id: \.path) { entry in
                    Button { onPick(entry) } label: { MentionRow(entry: entry) }
                        .buttonStyle(PromptRowStyle())
                }
            }
        }
    }
}

private struct MentionRow: View {
    let entry: FileEntry

    var body: some View {
        let (name, parent) = Mention.split(entry.path)
        HStack(spacing: 10) {
            Image(systemName: entry.isDir ? "folder" : "doc")
                .font(.system(size: 14)).foregroundStyle(Theme.textSecondary)
                .frame(width: 18)
            Text(name).font(Theme.body).foregroundStyle(Theme.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .layoutPriority(1)
            if !parent.isEmpty {
                Text(parent).font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 14)
        .frame(height: MentionMenu.rowHeight)
        .contentShape(.rect)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(parent.isEmpty ? name : "\(name), in \(parent)")
        .accessibilityHint(entry.isDir ? "Inserts the folder and lists what's inside" : "Inserts the file")
    }
}

/// A prompt menu row: highlighted while pressed, no animation.
struct PromptRowStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(configuration.isPressed ? Theme.pressed : .clear)
    }
}
