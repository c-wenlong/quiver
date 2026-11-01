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

    /// Re-keys a provider's per-session blocks into project blocks and
    /// merges with the same `gap` rule, over the union of each
    /// harness+account+project key's sessions. Equivalent to merging the
    /// response streams first: consecutive blocks of one key join while
    /// the next starts within `gap` of the running end. Responses sum;
    /// sessions keep their own first and last response inside the block.
    /// `resolve` answers a working folder's project; a block that
    /// already names one keeps it, and a session with no folder groups
    /// under `ProjectResolver.noFolder`.
    /// `resolve` answers a folder's project identity: `key` groups (the
    /// repository root path), `name` displays.
    public static func groupByProject(_ blocks: [ActivityBlock], gap: TimeInterval,
                                      project resolve: (String?) -> (key: String, name: String))
        -> [ActivityBlock] {
        var grouped: [String: (harness: String, account: String, project: String,
                              key: String, blocks: [ActivityBlock])] = [:]
        for block in blocks {
            let identity = block.project.isEmpty
                ? resolve(block.sessions.first?.cwd)
                : (key: block.projectKey.isEmpty ? block.project : block.projectKey,
                   name: block.project)
            let key = "\(block.harness)|\(block.accountHome)|\(identity.key)"
            grouped[key, default: (block.harness, block.accountHome, identity.name,
                                   identity.key, [])]
                .blocks.append(block)
        }
        var result: [ActivityBlock] = []
        for (_, group) in grouped {
            var merged: [ActivityBlock] = []
            for block in group.blocks.sorted(by: { $0.start < $1.start }) {
                if let last = merged.last,
                   block.start.timeIntervalSince(last.end) <= gap {
                    merged[merged.count - 1] = ActivityBlock(
                        harness: group.harness, accountHome: group.account,
                        project: group.project,
                        sessions: merge(last.sessions, block.sessions),
                        start: last.start, end: max(last.end, block.end),
                        responses: last.responses + block.responses,
                        projectKey: group.key)
                } else {
                    merged.append(ActivityBlock(
                        harness: group.harness, accountHome: group.account,
                        project: group.project, sessions: block.sessions,
                        start: block.start, end: block.end,
                        responses: block.responses, projectKey: group.key))
                }
            }
            result += merged
        }
        return result.sorted { $0.start != $1.start ? $0.start < $1.start : $0.id < $1.id }
    }

    /// Union two blocks' session refs by session id: a session spanning
    /// both keeps its earliest first response and latest last response,
    /// and its response count sums.
    private static func merge(_ a: [ActivityBlock.SessionRef],
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
}
