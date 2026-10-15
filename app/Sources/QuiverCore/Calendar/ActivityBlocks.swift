import Foundation

/// The block-building rule every activity source shares: one session's
/// responses join while no two consecutive ones sit more than `gap`
/// apart; the ledger's SQL does the same thing, and these stay in step.
public enum ActivityBlocks {
    /// `timestamps` must be sorted. Blocks that never touch `range` are
    /// dropped; ones straddling an edge keep their real start and end.
    public static func make(harness: String, accountHome: String, sessionId: String,
                            cwd: String?, timestamps: [Date],
                            range: DateInterval, gap: TimeInterval) -> [ActivityBlock] {
        var blocks: [ActivityBlock] = []
        var start: Date?
        var last = Date.distantPast
        var count = 0
        func close() {
            guard let start, count > 0 else { return }
            if last >= range.start, start < range.end {
                blocks.append(ActivityBlock(harness: harness, accountHome: accountHome,
                                            sessionId: sessionId, start: start, end: last,
                                            responses: count, cwd: cwd))
            }
        }
        for ts in timestamps {
            if start != nil, ts.timeIntervalSince(last) > gap {
                close()
                count = 0
                start = nil
            }
            if start == nil { start = ts }
            last = ts
            count += 1
        }
        close()
        return blocks
    }
}
