import Foundation

/// The strip of machines above the Sessions list (#1443): one chip per
/// machine the hub lists, with how many of its sessions are working and
/// waiting. Tapping a chip filters the list to that machine.
///
/// Ordering and wording follow the Mac's "Other machines" group
/// (`OtherMachines`, #1436) so the clients agree: online machines first,
/// then by name; an offline machine reads "last seen 2h". The web app
/// keeps the same rules in `static/machine-strip.js`.
enum MachineStrip {
    struct Chip: Equatable, Identifiable {
        let id: String
        let name: String
        let online: Bool
        /// Sessions whose status is `active`.
        let working: Int
        /// Sessions waiting on you: a follow-up or an approval.
        let waiting: Int
        /// "2 working · 1 waiting" online; "last seen 2h" offline.
        let detail: String
    }

    /// One chip per machine, online first then by name. Empty (so the
    /// strip hides) unless the hub lists at least two machines.
    static func chips(machines: [Machine], rows: [SessionRow],
                      now: Double = Date().timeIntervalSince1970) -> [Chip] {
        guard machines.count > 1 else { return [] }
        var working: [String: Int] = [:], waiting: [String: Int] = [:]
        for row in rows {
            guard let id = row.machineId else { continue }
            switch row.sessionStatus {
            case .active: working[id, default: 0] += 1
            case .followup, .approval: waiting[id, default: 0] += 1
            default: break
            }
        }
        let chips = machines.map { machine -> Chip in
            let online = machine.online == true
            let w = working[machine.id] ?? 0, q = waiting[machine.id] ?? 0
            return Chip(id: machine.id, name: machine.displayName, online: online, working: w, waiting: q,
                        detail: online ? "\(w) working · \(q) waiting" : lastSeen(machine.lastSeenAt, now: now))
        }
        return chips.sorted { a, b in
            if a.online != b.online { return a.online }
            let order = a.name.localizedStandardCompare(b.name)
            return order != .orderedSame ? order == .orderedAscending : a.id < b.id
        }
    }

    /// "last seen 2h", "last seen just now", or "offline" when the hub
    /// has no time for it.
    static func lastSeen(_ ts: Double?, now: Double = Date().timeIntervalSince1970) -> String {
        let age = RelativeTime.short(ts, now: now)
        if age.isEmpty { return "offline" }
        return age == "now" ? "last seen just now" : "last seen \(age)"
    }

    /// Tapping the chosen chip clears the filter; any other chip chooses it.
    static func toggled(_ current: String?, tapping id: String) -> String? {
        current == id ? nil : id
    }

    /// The choice, dropped when that machine is no longer listed.
    static func effective(_ selected: String?, chips: [Chip]) -> String? {
        guard let selected, chips.contains(where: { $0.id == selected }) else { return nil }
        return selected
    }

    /// The rows on the chosen machine; every row with no choice.
    static func filter(_ rows: [SessionRow], machine: String?) -> [SessionRow] {
        guard let machine else { return rows }
        return rows.filter { $0.machineId == machine }
    }
}
