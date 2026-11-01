import AppKit
import SWECore
import SwiftUI

/// Rows that drag straight up or down to reorder: the dragged row follows
/// the pointer, clamped to the list, while the others spring out of its
/// way; on drop it glides into its new slot. `onMove` gets the row's id
/// and the row it should land before — nil for the end. Plain `.gesture`,
/// so a switch, chip or button inside a row keeps its own click and drag.
struct ReorderableRows<Item: Identifiable, Row: View>: View where Item.ID == String {
    let items: [Item]
    let showsDividers: Bool
    let onMove: (_ id: String, _ before: String?) -> Void
    @ViewBuilder let row: (Item) -> Row

    init(_ items: [Item], showsDividers: Bool = false,
         onMove: @escaping (_ id: String, _ before: String?) -> Void,
         @ViewBuilder row: @escaping (Item) -> Row) {
        self.items = items
        self.showsDividers = showsDividers
        self.onMove = onMove
        self.row = row
    }

    @State private var heights: [String: CGFloat] = [:]
    @State private var draggingID: String?
    @State private var translation: CGFloat = 0
    /// True only while the gesture is live: resets on cancel, which never
    /// reaches `onEnded`.
    @GestureState private var down = false

    private var ids: [String] { items.map(\.id) }
    private var rowHeights: [CGFloat] { items.map { heights[$0.id] ?? 44 } }
    private var dragged: Int? { draggingID.flatMap { ids.firstIndex(of: $0) } }
    /// Where the dragged row would land, as an index among the other rows.
    private var target: Int? {
        dragged.map { RowReorder.targetIndex(heights: rowHeights, dragged: $0, translation: translation) }
    }

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                rowView(item, index: index)
            }
        }
        .onChange(of: down) { _, isDown in
            guard !isDown else { return }
            // A cancelled gesture skips onEnded. The hop lands after it on
            // a normal drop, so this tidies up only a real cancel.
            Task { await Task.yield(); finish(move: false) }
        }
    }

    private func rowView(_ item: Item, index: Int) -> some View {
        let isDragged = draggingID == item.id
        return VStack(spacing: 0) {
            row(item)
            if showsDividers, index < items.count - 1 { SettingsDivider() }
        }
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { heights[item.id] = $0 }
        .contentShape(.rect)
        .background {
            if isDragged {
                RoundedRectangle(cornerRadius: Theme.controlRadius)
                    .fill(Theme.card)
                    .shadow(color: .black.opacity(0.25), radius: 8, y: 2)
            }
        }
        .offset(y: yOffset(for: index, isDragged: isDragged))
        .zIndex(isDragged ? 1 : 0)
        .animation(isDragged ? nil : .spring(response: 0.28, dampingFraction: 0.86), value: target)
        // A drag that starts on a row's text must reorder, not select.
        .textSelection(.disabled)
        .gesture(
            DragGesture(minimumDistance: 3, coordinateSpace: .local)
                .updating($down) { _, state, _ in state = true }
                .onChanged { value in
                    if draggingID == nil { NSCursor.closedHand.push() }
                    draggingID = item.id
                    translation = value.translation.height
                }
                .onEnded { _ in finish(move: true) }
        )
        .accessibilityAction(named: "Move up") {
            guard index > 0 else { return }
            onMove(item.id, ids[index - 1])
        }
        .accessibilityAction(named: "Move down") {
            guard index < items.count - 1 else { return }
            onMove(item.id, index + 2 < items.count ? ids[index + 2] : nil)
        }
    }

    /// The dragged row follows the pointer (clamped); the others slide to
    /// where they'd sit if it landed at the current target.
    private func yOffset(for index: Int, isDragged: Bool) -> CGFloat {
        if isDragged {
            return RowReorder.clamped(translation, heights: rowHeights, dragged: index)
        }
        guard let dragged, let target else { return 0 }
        return RowReorder.offset(of: index, dragged: dragged, target: target, heights: rowHeights)
    }

    private func finish(move: Bool) {
        guard let id = draggingID else { return }
        withAnimation(.spring(response: 0.28, dampingFraction: 0.86)) {
            if move, let dragged {
                let target = RowReorder.targetIndex(
                    heights: rowHeights, dragged: dragged, translation: translation)
                if target != dragged {
                    onMove(id, RowReorder.insertBefore(ids: ids, dragged: dragged, target: target))
                }
            }
            translation = 0
            draggingID = nil
        }
        NSCursor.pop()
    }
}
