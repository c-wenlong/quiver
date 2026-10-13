import Foundation

/// Reordering a list of source ids, the moves the menu bar popup and the
/// widget limits selection share. `shown` is the ids in the order the
/// list shows them right now.
enum SourceOrdering {
    /// Drag `id` before `target`. Shown ids not yet ordered join `order`
    /// in their shown order first, so the move lands where it was dropped.
    static func move(_ id: String, before target: String,
                     in shown: [String], order: inout [String]) {
        guard id != target, shown.contains(id), shown.contains(target) else { return }
        order += shown.filter { !order.contains($0) }
        order.removeAll { $0 == id }
        if let index = order.firstIndex(of: target) {
            order.insert(id, at: index)
        }
    }

    /// Drop `id` below the last shown row: it lands at the end of the
    /// visible list, without jumping past hidden sources that follow it.
    static func moveToEnd(_ id: String, in shown: [String], order: inout [String]) {
        guard shown.contains(id) else { return }
        order += shown.filter { !order.contains($0) }
        order.removeAll { $0 == id }
        if let last = shown.last(where: { $0 != id }),
           let index = order.firstIndex(of: last) {
            order.insert(id, at: index + 1)
        } else {
            order.append(id)
        }
    }
}
