import Foundation

/// A list the user reorders by dragging and switches items of on and off:
/// the popup's tabs, the Compute tab's charts and harnesses. Only the
/// choices are stored; the items themselves come from the caller each
/// time, so an item the list has never seen shows, after the ordered
/// ones, in the order given.
public struct ToggleList: Equatable, Sendable {
    /// Dragged ids, first to last. An id not given right now keeps its
    /// place, so an item that comes back lands where it was.
    public private(set) var order: [String] = []
    public private(set) var hidden: Set<String> = []

    public init(order: [String] = [], hidden: Set<String> = []) {
        self.order = order
        self.hidden = hidden
    }

    /// Reads the two keys; missing keys read as nothing chosen.
    public init(defaults: UserDefaults, orderKey: String, hiddenKey: String) {
        order = defaults.stringArray(forKey: orderKey) ?? []
        hidden = Set(defaults.stringArray(forKey: hiddenKey) ?? [])
    }

    public func save(to defaults: UserDefaults, orderKey: String, hiddenKey: String) {
        defaults.set(order, forKey: orderKey)
        defaults.set(hidden.sorted(), forKey: hiddenKey)
    }

    /// The given ids in the dragged order, unordered ones after them in
    /// the given order. Hidden ids included: the settings list shows them.
    public func ordered(_ ids: [String]) -> [String] {
        let explicit = order.filter(ids.contains)
        return explicit + ids.filter { !order.contains($0) }
    }

    /// `ordered(_:)` less the hidden ids: what the popup draws.
    public func visible(_ ids: [String]) -> [String] {
        ordered(ids).filter { !hidden.contains($0) }
    }

    public func shows(_ id: String) -> Bool { !hidden.contains(id) }

    public mutating func setShown(_ shown: Bool, _ id: String) {
        if shown { hidden.remove(id) } else { hidden.insert(id) }
    }

    /// Drag `id` before `target`, where `ids` is what the list shows now.
    public mutating func move(_ id: String, before target: String, in ids: [String]) {
        SourceOrdering.move(id, before: target, in: ordered(ids), order: &order)
    }

    /// Drop `id` below the last row.
    public mutating func moveToEnd(_ id: String, in ids: [String]) {
        SourceOrdering.moveToEnd(id, in: ordered(ids), order: &order)
    }
}
