import Foundation

// The Sessions page's "Other machines" group: the hub's rows for its
// satellites, the sessions the iPhone and web app already list (#970).
//
// The Mac's own list comes from `swe session --json`, which only sees this
// Mac. The same `GET /v1/sessions` read that fills `LocalHub.overlay` also
// carries every satellite's rows and the machine list; this turns them into
// one group per other machine. The hub sends no token usage for them, so
// the page shows "–" in the Tokens and Spend columns.

/// Every other machine's sessions, grouped for the Sessions page.
public struct OtherMachines: Equatable, Sendable {
    /// One machine's subheader and rows.
    public struct Machine: Equatable, Sendable, Identifiable {
        public var id: String
        public var name: String
        public var online: Bool
        /// Epoch seconds the hub last heard from it; nil when unknown.
        public var lastSeenAt: Double?
        public var rows: [Row]

        /// "studio" online, "studio · offline" otherwise.
        public var title: String { online ? name : "\(name) · offline" }
    }

    /// One session row: what the page draws, and the hub's row it opens.
    public struct Row: Equatable, Sendable, Identifiable {
        public var hub: HubSessionRow
        public var title: String
        /// "<status> · <folder> · <Harness> · <model>", each part left
        /// out when unknown.
        public var details: String

        public var id: String { hub.key }
        public var key: String { hub.key }
        /// The id the session has on its own machine.
        public var sessionId: String { OtherMachines.sessionId(hub.key) }
    }

    public var machines: [Machine]

    public init(machines: [Machine] = []) { self.machines = machines }

    public var isEmpty: Bool { machines.isEmpty }
    public var sessionCount: Int { machines.reduce(0) { $0 + $1.rows.count } }

    /// "from the hub · 3 sessions".
    public var caption: String {
        let n = sessionCount
        return "from the hub · \(n) \(n == 1 ? "session" : "sessions")"
    }

    /// The hub's rows for machines other than this Mac, grouped: online
    /// machines first, then by name; each machine's rows newest first. A
    /// row counts as another machine's when the hub marks it `remote` and
    /// its machine id isn't this Mac's (`localMachineId`, from
    /// `machine.json`). A machine with no rows doesn't show.
    public static func build(rows: [HubSessionRow], machines: [HubMachine],
                             localMachineId: String?, now: Date = .now) -> OtherMachines {
        let known = Dictionary(machines.map { ($0.id, $0) }, uniquingKeysWith: { _, last in last })
        var byMachine: [String: [HubSessionRow]] = [:]
        for row in rows {
            guard row.remote == true, let mid = row.machineId, !mid.isEmpty, mid != localMachineId else { continue }
            byMachine[mid, default: []].append(row)
        }
        var out: [Machine] = byMachine.map { mid, rows in
            let machine = known[mid]
            let online = machine?.online == true
            let lastSeen = machine?.lastSeenAt
            let name = machine.map(\.displayName)
                ?? rows.lazy.compactMap(\.machine).first { !$0.isEmpty }.map(Self.shortName)
                ?? mid
            let sorted = rows.sorted {
                let a = $0.lastActiveAt ?? 0, b = $1.lastActiveAt ?? 0
                return a != b ? a > b : $0.key < $1.key
            }
            return Machine(id: mid, name: name, online: online, lastSeenAt: lastSeen,
                           rows: sorted.map { row in
                               Row(hub: row, title: title(row),
                                   details: details(row, online: online, lastSeenAt: lastSeen, now: now))
                           })
        }
        out.sort { a, b in
            if a.online != b.online { return a.online }
            let order = a.name.localizedStandardCompare(b.name)
            return order != .orderedSame ? order == .orderedAscending : a.id < b.id
        }
        return OtherMachines(machines: out)
    }

    /// The hub's title (a name set on the hub, else the harness's own),
    /// then the session id.
    public static func title(_ row: HubSessionRow) -> String {
        for candidate in [row.title, row.harnessTitle] {
            if let text = candidate?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty { return text }
        }
        return sessionId(row.key)
    }

    /// "claude:abc@m2" is session "abc": the hub prefixes the harness and
    /// suffixes the satellite's machine id (`protocol.remote_key`).
    public static func sessionId(_ key: String) -> String {
        var local = Substring(key)
        if let at = local.lastIndex(of: "@") { local = local[..<at] }
        if let colon = local.firstIndex(of: ":") { local = local[local.index(after: colon)...] }
        return String(local)
    }

    /// "Working · ~/src/app · Claude Code · claude-opus-4-5".
    public static func details(_ row: HubSessionRow, online: Bool, lastSeenAt: Double?,
                               now: Date = .now) -> String {
        var parts = [status(row, online: online, lastSeenAt: lastSeenAt, now: now)]
        if let cwd = row.cwd, !cwd.isEmpty { parts.append(folder(cwd)) }
        if let harness = row.harness, !harness.isEmpty { parts.append(HarnessMark(harness).name) }
        if let model = row.modelLabel { parts.append(model) }
        return parts.joined(separator: " · ")
    }

    /// What the session is doing: "Working", "Waiting", "Error",
    /// "Interrupted", else "Idle 2h" since its last activity ("Idle" under
    /// a minute). An offline
    /// machine's rows say "Last seen 1d" instead, from when the hub last
    /// heard from the machine.
    public static func status(_ row: HubSessionRow, online: Bool, lastSeenAt: Double?,
                              now: Date = .now) -> String {
        guard online else {
            let age = self.age(since: lastSeenAt ?? row.lastActiveAt, now: now)
            guard let age else { return "Offline" }
            return age == "now" ? "Last seen just now" : "Last seen \(age)"
        }
        switch row.status {
        case "active": return "Working"
        case "followup": return "Waiting"
        case "error": return "Error"
        case "interrupted": return "Interrupted"
        default:
            guard let age = age(since: row.lastActiveAt, now: now), age != "now" else { return "Idle" }
            return "Idle \(age)"
        }
    }

    /// "now", "5m", "2h", "3d": the coarsest whole unit. Nil when unknown.
    public static func age(since epoch: Double?, now: Date = .now) -> String? {
        guard let epoch, epoch > 0 else { return nil }
        let seconds = max(0, now.timeIntervalSince1970 - epoch)
        switch seconds {
        case ..<60: return "now"
        case ..<3600: return "\(Int(seconds / 60))m"
        case ..<86400: return "\(Int(seconds / 3600))h"
        default: return "\(Int(seconds / 86400))d"
        }
    }

    /// The folder with the other machine's home as "~": that home isn't
    /// this Mac's, so it is spotted by shape (`/Users/<name>`,
    /// `/home/<name>`, `/root`).
    public static func folder(_ path: String) -> String {
        let parts = path.split(separator: "/", omittingEmptySubsequences: false)
        // "/Users/kai/src" splits as ["", "Users", "kai", "src"].
        if parts.count >= 3, parts[0].isEmpty, parts[1] == "Users" || parts[1] == "home", !parts[2].isEmpty {
            let rest = parts.dropFirst(3).joined(separator: "/")
            return rest.isEmpty ? "~" : "~/\(rest)"
        }
        if path == "/root" { return "~" }
        if path.hasPrefix("/root/") { return "~/" + path.dropFirst(6) }
        return path
    }

    /// "dev-macbook-pro.local" reads as "dev-macbook-pro", as
    /// `HubMachine.displayName` has it.
    static func shortName(_ name: String) -> String {
        name.hasSuffix(".local") ? String(name.dropLast(6)) : name
    }
}
