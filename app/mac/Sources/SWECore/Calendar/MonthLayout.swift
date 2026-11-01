import Foundation

/// The month view's pure layout: which days fill the 6×7 grid and which
/// session entries sit in each cell. The model's `range(for: .month)`
/// uses the same arithmetic, so the grid and the query never disagree.
public enum MonthLayout {
    /// The six week rows covering the anchor's month: the grid starts on
    /// the first weekday on or before the 1st and always holds 42 days,
    /// adjacent-month days included (Notion Calendar's fixed six rows).
    public static func weeks(for anchor: Date, calendar: Calendar) -> [[Date]] {
        guard let first = calendar.date(
            from: calendar.dateComponents([.year, .month], from: anchor)),
            let start = calendar.date(
                from: calendar.dateComponents([.yearForWeekOfYear, .weekOfYear], from: first))
        else { return [] }
        return (0..<6).map { week in
            (0..<7).compactMap {
                calendar.date(byAdding: .day, value: week * 7 + $0, to: start)
            }
        }
    }

    /// The interval the six weeks span; `CalendarModel.range(for:.month)`
    /// is exactly this, so the cell borders are the range borders.
    static func weekRange(for anchor: Date, calendar: Calendar) -> DateInterval? {
        guard let first = weeks(for: anchor, calendar: calendar).first,
              let start = first.first,
              let end = calendar.date(byAdding: .day, value: 42, to: start)
        else { return nil }
        return DateInterval(start: start, end: end)
    }

    /// One project's activity inside a day: the row a month cell shows.
    public struct Entry: Equatable, Sendable, Identifiable {
        /// A synthetic block for the day: the project's harness, account
        /// and projectKey, the union of the day's session refs (each
        /// clipped to the day) and the day's span. It is what the row's
        /// click opens — one session goes to its detail, several to the
        /// project sheet, same as the week view.
        public let block: ActivityBlock
        /// Its first block's start that day, clipped to the day.
        public let start: Date
        /// Seconds active inside the day, all its blocks summed.
        public let active: TimeInterval
        /// How many blocks of the project touch the day.
        public let blockCount: Int

        public var id: String {
            "\(block.harness)|\(block.accountHome)|\(block.projectKey.isEmpty ? block.project : block.projectKey)|\(start.timeIntervalSince1970)"
        }
    }

    /// One entry per harness+account+project with blocks overlapping the
    /// day, sorted by the first block's start. A block running across
    /// midnight contributes its clipped share to both days' sums; each
    /// session's span inside the day unions into the entry's block, so
    /// the click sees every session that worked that day.
    public static func entries(_ blocks: [ActivityBlock], day: DateInterval) -> [Entry] {
        var grouped: [String: (earliest: ActivityBlock, start: Date,
                               active: TimeInterval, count: Int,
                               sessions: [ActivityBlock.SessionRef])] = [:]
        for block in blocks where block.start < day.end && block.end >= day.start {
            let key = "\(block.harness)|\(block.accountHome)|\(block.projectKey.isEmpty ? block.project : block.projectKey)"
            let clippedStart = max(block.start, day.start)
            let clipped = min(block.end, day.end).timeIntervalSince(clippedStart)
            // Only sessions that worked that day: a block spanning
            // midnight can hold a session from the other side, and
            // clipping a ref that never touched the day would invert
            // its span.
            let refs = block.sessions
                .filter { $0.start < day.end && $0.end >= day.start }
                .map { ref in
                    ActivityBlock.SessionRef(sessionId: ref.sessionId, cwd: ref.cwd,
                                             start: max(ref.start, day.start),
                                             end: min(ref.end, day.end),
                                             responses: ref.responses)
                }
            if var group = grouped[key] {
                group.active += clipped
                group.count += 1
                if clippedStart < group.start { group.start = clippedStart }
                if block.start < group.earliest.start { group.earliest = block }
                group.sessions = union(group.sessions, refs)
                grouped[key] = group
            } else {
                grouped[key] = (block, clippedStart, clipped, 1, refs)
            }
        }
        return grouped.values
            .map { group in
                let block = ActivityBlock(
                    harness: group.earliest.harness,
                    accountHome: group.earliest.accountHome,
                    project: group.earliest.project,
                    sessions: group.sessions,
                    start: group.start,
                    end: group.sessions.map(\.end).max() ?? group.start,
                    responses: group.sessions.reduce(0) { $0 + $1.responses },
                    projectKey: group.earliest.projectKey)
                return Entry(block: block, start: group.start,
                             active: group.active, blockCount: group.count)
            }
            .sorted { $0.start != $1.start ? $0.start < $1.start : $0.id < $1.id }
    }

    /// Union session refs by id, earliest first: a session with two
    /// blocks that day keeps its earliest clipped start and latest
    /// clipped end, responses summed.
    private static func union(_ a: [ActivityBlock.SessionRef],
                              _ b: [ActivityBlock.SessionRef]) -> [ActivityBlock.SessionRef] {
        var out = a
        for ref in b {
            if let index = out.firstIndex(where: { $0.sessionId == ref.sessionId }) {
                out[index].start = min(out[index].start, ref.start)
                out[index].end = max(out[index].end, ref.end)
                out[index].responses += ref.responses
                if out[index].cwd == nil { out[index].cwd = ref.cwd }
            } else {
                out.append(ref)
            }
        }
        return out.sorted { $0.start != $1.start ? $0.start < $1.start : $0.sessionId < $1.sessionId }
    }

    /// `fitting` is how many rows the cell has room for. When the entries
    /// don't all fit, the last row becomes a "N more" button, so at most
    /// `fitting - 1` entries show alongside it.
    public static func split(_ entries: [Entry],
                             fitting rows: Int) -> (shown: [Entry], more: Int) {
        if entries.count <= rows { return (entries, 0) }
        let shown = max(0, rows - 1)
        return (Array(entries.prefix(shown)), entries.count - shown)
    }
}
