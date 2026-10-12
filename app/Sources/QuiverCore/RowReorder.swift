import Foundation

/// The maths behind dragging a row up or down a list: where it would land,
/// how far it may travel, and how far every other row slides aside. Indices
/// are into the full list; a `target` is an index into the list with the
/// dragged row removed (0...count-1, count-1 meaning the end).
public enum RowReorder {
    /// Where the dragged row would land among the others: the count of
    /// other rows whose centre is above the dragged row's centre after
    /// moving by `translation`.
    public static func targetIndex(heights: [CGFloat], dragged: Int, translation: CGFloat) -> Int {
        guard heights.indices.contains(dragged) else { return 0 }
        let moved = centre(dragged, in: heights) + translation
        var target = 0
        for index in heights.indices where index != dragged && centre(index, in: heights) < moved {
            target += 1
        }
        return min(target, heights.count - 1)
    }

    /// The translation clamped so the dragged row stays inside the list.
    public static func clamped(_ translation: CGFloat, heights: [CGFloat], dragged: Int) -> CGFloat {
        guard heights.indices.contains(dragged) else { return 0 }
        let above = heights[..<dragged].reduce(0, +)
        let below = heights[(dragged + 1)...].reduce(0, +)
        return min(max(translation, -above), below)
    }

    /// How far row `index` slides to make room: -heights[dragged] for rows
    /// between the dragged row and the target when moving down,
    /// +heights[dragged] when moving up, else 0.
    public static func offset(of index: Int, dragged: Int, target: Int, heights: [CGFloat]) -> CGFloat {
        guard heights.indices.contains(dragged), index != dragged else { return 0 }
        if target > dragged, index > dragged, index <= target { return -heights[dragged] }
        if target < dragged, index >= target, index < dragged { return heights[dragged] }
        return 0
    }

    /// The id to insert before when the dragged row lands at `target` among
    /// the others; nil means the end.
    public static func insertBefore(ids: [String], dragged: Int, target: Int) -> String? {
        guard ids.indices.contains(dragged) else { return nil }
        var others = ids
        others.remove(at: dragged)
        return target < others.count ? others[target] : nil
    }

    private static func centre(_ index: Int, in heights: [CGFloat]) -> CGFloat {
        heights[..<index].reduce(0, +) + heights[index] / 2
    }
}
