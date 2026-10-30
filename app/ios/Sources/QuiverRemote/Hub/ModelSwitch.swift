import Foundation

/// The chat's Model menu (#643): which models it offers and what a
/// typed one must look like. Pure, so tests can feed it hub rows.
enum ModelSwitchOptions {
    /// The menu's models, newest first: the machine's `recent_models`
    /// for the session's harness (the launch sheets' list), with the
    /// session's own current model added in front when the list lacks
    /// it, so the menu can always check the one it runs.
    static func choices(for row: SessionRow, on machine: Machine?) -> [String] {
        var models = machine.map { NewSessionOptions.recentModels(row.harness, on: $0) } ?? []
        if let current = row.model, !current.isEmpty, !models.contains(current) {
            models.insert(current, at: 0)
        }
        return models
    }

    /// A typed model id as the hub types it (`/model <id>`), or nil when
    /// it isn't one: trimmed, then the launch routes' token shape
    /// (`protocol._LAUNCH_VALUE_RE`), so a space or a newline can never
    /// put a second command or prompt text into the pane.
    static func validValue(_ text: String) -> String? {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let first = value.unicodeScalars.first, value.unicodeScalars.count <= 128,
              isAlphanumeric(first),
              value.unicodeScalars.allSatisfy({ isAlphanumeric($0) || "._:/+@-".unicodeScalars.contains($0) })
        else { return nil }
        return value
    }

    private static func isAlphanumeric(_ s: Unicode.Scalar) -> Bool {
        ("a"..."z").contains(s) || ("A"..."Z").contains(s) || ("0"..."9").contains(s)
    }
}

/// A model switch the chat sent and the transcript hasn't shown yet.
///
/// The header never claims the new model on the hub's word alone: the
/// `model` control op only types `/model <id>` into the pane, and the
/// row's `model` moves when a reply on the new model lands in the
/// transcript. Until then the header shows the request as pending
/// ("→ id"), and that marker always settles:
///
/// - when the row's model moves off the one it showed at the send
///   (the transcript now names a new model, whichever it is), or
/// - once a reply has come in after the send and the session list has
///   been fetched again after the hub's own next refresh. The row then
///   says what that reply ran, so a switch the harness refused, an
///   alias of the model already running, or a reply already under way
///   on the old model stops showing as pending and the header shows
///   the transcript's answer.
///
/// With no reply at all it stays pending, which is true: the switch is
/// asked for and nothing has run on it yet.
struct ModelSwitch: Equatable {
    /// How long the hub can take to read a new reply into its rows
    /// (`catalog.REFRESH_INTERVAL_S` is 10 s), with a little slack.
    static let hubRefresh: TimeInterval = 12
    /// A replayed message this much older than the send is history.
    static let clockSlack: TimeInterval = 60

    let requested: String
    /// The row's model when the switch was sent.
    let from: String?
    let sentAt: Date
    /// Sent with the control op; a satellite echoes it on its
    /// `command.result`, so only this switch's own failure clears it.
    let clientId: String
    /// When the first reply after the send arrived on this phone.
    private(set) var replyAt: Date?

    init(requested: String, from: String?, sentAt: Date, clientId: String = UUID().uuidString) {
        self.requested = requested
        self.from = from
        self.sentAt = sentAt
        self.clientId = clientId
    }

    /// Whether a `command.result` reports this switch failing.
    func failed(by result: CommandResult) -> Bool {
        result.op == "control" && result.ok == false && result.clientId == clientId
    }

    /// A model's output arrived at `at`; `messageTs` is the transcript's
    /// own time for it, when it has one.
    mutating func noteReply(at: Date, messageTs: Double?) {
        guard replyAt == nil, at >= sentAt else { return }
        if let messageTs, messageTs < sentAt.timeIntervalSince1970 - Self.clockSlack { return }
        replyAt = at
    }

    func settled(currentModel: String?, listRefreshedAt: Date?) -> Bool {
        if currentModel != from { return true }
        guard let replyAt, let listRefreshedAt else { return false }
        return listRefreshedAt >= replyAt.addingTimeInterval(Self.hubRefresh)
    }
}
