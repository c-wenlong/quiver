import Foundation

/// Session rows by account, so a block's click and title are an O(1)
/// lookup. The key is "harness|accountHome|sessionId"; a row with no
/// ledger usage sits under the empty-account key and answers only when no
/// account-specific row matches, so one session id in two accounts still
/// resolves each block to its own row.
public struct CalendarSessionIndex: Sendable {
    private var rows: [String: SessionsModel.Row] = [:]
    private var feed: [String: SessionsModel.Row] = [:]

    public init(rows: [SessionsModel.Row]) {
        for row in rows {
            let key = "\(row.session.harness)|\(row.usage?.accountHome ?? "")|\(row.session.id)"
            self.rows[key] = row
            // A feed row merges every profile home that polled it, so a
            // block from an older home finds it by conversation.
            if SessionFilter.feedHarnesses.contains(row.session.harness) {
                self.feed[SessionsModel.feedKey(harness: row.session.harness, account: row.usage?.accountHome ?? "",
                                                session: row.session.id)] = row
            }
        }
    }

    /// The listed row for one session of a block, or a stand-in when the
    /// session is no longer in the harness's history (logs gone, ledger
    /// kept): the stand-in has no resume command and no usage, and the
    /// detail sheet says so.
    public func row(for ref: ActivityBlock.SessionRef, harness: String,
                    accountHome: String) -> SessionsModel.Row {
        if let row = rows["\(harness)|\(accountHome)|\(ref.sessionId)"]
            ?? feed[SessionsModel.feedKey(harness: harness, account: accountHome, session: ref.sessionId)]
            ?? rows["\(harness)||\(ref.sessionId)"] {
            return row
        }
        return SessionsModel.Row(
            session: SessionInfo(id: ref.sessionId, harness: harness,
                                 agent: UsageFormat.harness(harness),
                                 cwd: ref.cwd, title: nil, titleSource: nil,
                                 lastActiveAt: ref.end, status: nil, resume: nil),
            usage: nil, id: "\(harness)|\(accountHome)|\(ref.sessionId)")
    }

    /// Every session of a block as a row, earliest first: the project
    /// sheet's list, ready to render.
    public func rows(for block: ActivityBlock)
        -> [(ref: ActivityBlock.SessionRef, row: SessionsModel.Row)] {
        block.sessions.sorted { $0.start != $1.start ? $0.start < $1.start : $0.sessionId < $1.sessionId }
            .map { ($0, row(for: $0, harness: block.harness, accountHome: block.accountHome)) }
    }

    /// The first `limit` rows plus how many more there are, for the hover
    /// card's "at most 8 then +N" list.
    public func rows(for block: ActivityBlock, limit: Int)
        -> (shown: [(ref: ActivityBlock.SessionRef, row: SessionsModel.Row)], more: Int) {
        let all = rows(for: block)
        return (Array(all.prefix(limit)), max(0, all.count - limit))
    }
}
