/// The fold a long list gets: the first `limit` rows, then a Show more row
/// that opens the rest. Pure so the page and its tests share the numbers.
public enum RowFold {
    /// All rows when expanded or short enough, else the first `limit`.
    public static func visible<T>(_ rows: [T], limit: Int, expanded: Bool) -> [T] {
        if expanded || rows.count <= limit { return rows }
        return Array(rows.prefix(limit))
    }

    /// How many rows sit behind the fold.
    public static func hiddenCount(total: Int, limit: Int) -> Int {
        max(0, total - limit)
    }

    /// How many hidden rows match `isFlagged`, so a row needing a look is
    /// never silently folded away. Zero once the list is expanded.
    public static func hiddenFlagged<T>(_ rows: [T], limit: Int, expanded: Bool,
                                        isFlagged: (T) -> Bool) -> Int {
        guard !expanded else { return 0 }
        return rows.dropFirst(limit).count(where: isFlagged)
    }
}
