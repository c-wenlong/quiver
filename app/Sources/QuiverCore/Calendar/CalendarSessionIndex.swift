import Foundation

/// Session rows by account, so a block's click and title are an O(1)
/// lookup. The key is "harness|accountHome|sessionId"; a row with no
/// ledger usage sits under the empty-account key and answers only when no
/// account-specific row matches, so one session id in two accounts still
/// resolves each block to its own row.
public struct CalendarSessionIndex: Sendable {
    private var rows: [String: SessionsModel.Row] = [:]

    public init(rows: [SessionsModel.Row]) {
        for row in rows {
            let key = "\(row.session.harness)|\(row.usage?.accountHome ?? "")|\(row.session.id)"
            self.rows[key] = row
        }
    }

    /// The listed row for the block, or a stand-in when the session is no
    /// longer in the harness's history (logs gone, ledger kept): the
    /// stand-in has no resume command and no usage, and the detail sheet
    /// says so.
    public func row(for block: ActivityBlock) -> SessionsModel.Row {
        if let row = rows["\(block.harness)|\(block.accountHome)|\(block.sessionId)"]
            ?? rows["\(block.harness)||\(block.sessionId)"] {
            return row
        }
        return SessionsModel.Row(
            session: SessionInfo(id: block.sessionId, harness: block.harness,
                                 agent: UsageFormat.harness(block.harness),
                                 cwd: block.cwd, title: nil, titleSource: nil,
                                 lastActiveAt: block.end, status: nil, resume: nil),
            usage: nil, id: block.id)
    }
}
