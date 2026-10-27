import Foundation

/// The Hooks page's main view: one harness's declared hooks laid out by the
/// event that fires them, in the order a session meets those events. A
/// hook declared on three events sits in three lanes; one declared on none
/// never fires and goes to `HookTimeline.notRunning` instead.
public struct HookTimeline: Sendable, Equatable {
    /// One declaration of one hook: the script, and the matcher that gates
    /// it on this event.
    public struct Entry: Identifiable, Sendable, Equatable {
        public let hook: Hook
        public let matcher: String
        public var id: String { "\(hook.id)|\(matcher)" }
        /// A script another installer declared (herdr, Agent Relay).
        public var isForeign: Bool { hook.tier == .foreign }

        /// The matcher worth showing: a tool name or pattern, "any tool" on
        /// a tool event with no real matcher, nil elsewhere ("*" on
        /// SessionStart gates nothing).
        public var matcherLabel: String? {
            let bare = matcher.isEmpty || matcher == "*"
            if !bare { return matcher }
            return HookEvents.isToolEvent(laneEvent) ? "any tool" : nil
        }

        /// The lane this entry sits in, for `matcherLabel`.
        let laneEvent: String
    }

    /// One event with at least one hook on it.
    public struct Lane: Identifiable, Sendable, Equatable {
        public let event: String
        public let entries: [Entry]
        public var id: String { event }
        /// "before a tool runs", or nil for an event the table doesn't know.
        public var caption: String? { HookEvents.caption(for: event) }
    }

    public let harness: String
    /// Events something fires on, in session order.
    public let lanes: [Lane]
    /// Events the harness supports that nothing shown is declared on, in
    /// session order.
    public let quietEvents: [String]
    /// Foreign hooks declared in this harness, shown or not.
    public let foreignCount: Int

    /// - Parameters:
    ///   - hooks: the whole inventory; rows for other harnesses are skipped.
    ///   - includeForeign: lay out other installers' hooks too.
    public init(harness: String, hooks: [Hook], includeForeign: Bool) {
        self.harness = harness
        let mine = hooks.filter { $0.harness == harness }
        foreignCount = mine.filter { $0.tier == .foreign && !$0.declared.isEmpty }.count
        var byEvent: [String: [Entry]] = [:]
        var supported: [String] = []
        for hook in mine {
            for event in hook.eventsSupported ?? [] where !supported.contains(event) {
                supported.append(event)
            }
            if hook.tier == .foreign && !includeForeign { continue }
            for declaration in hook.declared {
                let entry = Entry(hook: hook, matcher: declaration.matcher, laneEvent: declaration.event)
                // The same script declared twice (two settings files, or
                // swe listing one installer's script on two rows) is one
                // entry: the lane says what fires, not how often it's named.
                guard !(byEvent[declaration.event] ?? []).contains(where: { $0.id == entry.id }) else { continue }
                byEvent[declaration.event, default: []].append(entry)
            }
        }
        lanes = HookEvents.sorted(Array(byEvent.keys)).map { event in
            Lane(event: event, entries: (byEvent[event] ?? []).sorted(by: Self.entryOrder))
        }
        quietEvents = HookEvents.sorted(supported.filter { byEvent[$0] == nil })
    }

    /// Yours before other installers'; then no matcher before a named one,
    /// then by matcher and name, so a lane reads the same on every load.
    static func entryOrder(_ a: Entry, _ b: Entry) -> Bool {
        if a.isForeign != b.isForeign { return !a.isForeign }
        let aBare = a.matcherLabel == nil || a.matcherLabel == "any tool"
        let bBare = b.matcherLabel == nil || b.matcherLabel == "any tool"
        if aBare != bBare { return aBare }
        if a.matcher != b.matcher { return a.matcher < b.matcher }
        return a.hook.script.localizedStandardCompare(b.hook.script) == .orderedAscending
    }

    /// The harnesses worth a tab: any with a declared hook. Harnesses with
    /// your own hooks come first, most first; the rest by name.
    public static func harnesses(in hooks: [Hook]) -> [String] {
        var own: [String: Int] = [:]
        var any: Set<String> = []
        for hook in hooks where !hook.declared.isEmpty {
            any.insert(hook.harness)
            if hook.tier != .foreign { own[hook.harness, default: 0] += 1 }
        }
        return any.sorted { a, b in
            let ca = own[a] ?? 0, cb = own[b] ?? 0
            if ca != cb { return ca > cb }
            return a < b
        }
    }

    /// Your scripts nothing declares, so they never fire: a skeleton not
    /// pasted into settings yet, a dormant shared script, a folder that is
    /// no harness. Other installers' leftovers are theirs to keep.
    public static func notRunning(in hooks: [Hook]) -> [Hook] {
        hooks.filter { $0.declared.isEmpty && $0.tier != .foreign }
    }

    /// The summary without the "PreToolUse hook (Bash): " the lane already
    /// says, first letter raised: "Block synchronous sleep-polling…".
    public static func shortSummary(_ summary: String) -> String {
        let trimmed = summary.replacing(/^\w+ hook(\s*\([^)]*\))?:\s*/, with: "")
        guard let first = trimmed.first else { return summary }
        return first.uppercased() + trimmed.dropFirst()
    }
}

/// Hook event names across harnesses: the order a session meets them and a
/// few words on when each fires. Names compare case-insensitively, so
/// Cursor's `preToolUse` sits with Claude Code's `PreToolUse`.
public enum HookEvents {
    /// Session order. Unknown events go after these, by name.
    static let order: [String] = [
        "sessionstart", "userpromptsubmit", "beforesubmitprompt", "beforeagent",
        "premodelrequest", "postmodelrequest",
        "pretooluse", "beforetool", "beforeshellexecution", "beforemcpexecution",
        "permissionrequest", "posttooluse", "aftertool", "posttoolusefailure",
        "notification", "subagentstart", "subagentstop", "interrupt",
        "precompact", "postcompact", "postcompaction",
        "afteragentresponse", "afteragent", "stop", "stopfailure", "teammateidle",
        "sessionend",
    ]

    static let captions: [String: String] = [
        "sessionstart": "a session opens",
        "userpromptsubmit": "you send a message",
        "beforesubmitprompt": "you send a message",
        "beforeagent": "before the agent's turn",
        "premodelrequest": "before a model call",
        "postmodelrequest": "after a model call",
        "pretooluse": "before a tool runs",
        "beforetool": "before a tool runs",
        "beforeshellexecution": "before a shell command",
        "beforemcpexecution": "before an MCP call",
        "permissionrequest": "a tool asks permission",
        "posttooluse": "after a tool runs",
        "aftertool": "after a tool runs",
        "posttoolusefailure": "after a tool fails",
        "notification": "a notification shows",
        "subagentstart": "a subagent starts",
        "subagentstop": "a subagent finishes",
        "interrupt": "you interrupt a turn",
        "precompact": "before compacting",
        "postcompact": "after compacting",
        "postcompaction": "after compacting",
        "afteragentresponse": "after the reply",
        "afteragent": "after the agent's turn",
        "stop": "the reply finishes",
        "stopfailure": "a reply fails",
        "teammateidle": "a teammate goes idle",
        "sessionend": "the session closes",
    ]

    /// Events whose matcher names a tool.
    static let toolEvents: Set<String> = [
        "pretooluse", "posttooluse", "posttoolusefailure", "permissionrequest",
        "beforetool", "aftertool",
    ]

    public static func caption(for event: String) -> String? {
        captions[event.lowercased()]
    }

    static func isToolEvent(_ event: String) -> Bool {
        toolEvents.contains(event.lowercased())
    }

    public static func sorted(_ events: [String]) -> [String] {
        events.sorted { a, b in
            let ra = order.firstIndex(of: a.lowercased()) ?? order.count
            let rb = order.firstIndex(of: b.lowercased()) ?? order.count
            if ra != rb { return ra < rb }
            return a < b
        }
    }
}
