import Foundation

/// The maths behind dragging a row up or down a list: where it would land,
/// how far it may travel, and how far every other row slides aside. Indices
/// are into the full list; a `target` is an index into the list with the
/// dragged row removed (0...count-1, count-1 meaning the end).
public enum RowReorder {
    /// Where the dragged row would land among the others, as an index into
    /// the list with the dragged row removed. Moving down it takes the slot
    /// once its bottom edge passes a row's centre; moving up, once its top
    /// edge does; so the last slot is reachable at the movement limit.
    public static func targetIndex(heights: [CGFloat], dragged: Int, translation: CGFloat) -> Int {
        guard heights.indices.contains(dragged) else { return 0 }
        let t = clamped(translation, heights: heights, dragged: dragged)
        let top = heights[..<dragged].reduce(0, +)
        if t > 0 {
            let bottom = top + heights[dragged] + t
            let passed = ((dragged + 1)..<heights.count).count { centre($0, in: heights) < bottom }
            return min(dragged + passed, heights.count - 1)
        }
        if t < 0 {
            let edge = top + t
            let passed = (0..<dragged).count { centre($0, in: heights) > edge }
            return dragged - passed
        }
        return dragged
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
