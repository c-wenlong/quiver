import Foundation

/// What a Sessions row offers on a swipe or a long press. Pure, so the
/// rules for which action shows when are tested without a view.
enum SessionAction: String, Hashable, Identifiable {
    case attach, detach, pin, unpin, rename, fork

    var id: String { rawValue }

    var title: String {
        switch self {
        case .attach: "Attach"
        case .detach: "Detach"
        case .pin: "Pin"
        case .unpin: "Unpin"
        case .rename: "Rename"
        case .fork: "Fork"
        }
    }

    var symbol: String {
        switch self {
        case .attach: "link"
        case .detach: "eye"
        case .pin: "pin"
        case .unpin: "pin.slash"
        case .rename: "pencil"
        case .fork: "arrow.triangle.branch"
        }
    }

    /// Swipe right: take the session over, or hand it back.
    static func leading(_ row: SessionRow) -> [SessionAction] {
        if row.isAttached { return [.detach] }
        if row.canAttach { return [.attach] }
        return []
    }

    /// Swipe left, the first one at the edge (a full swipe runs it).
    static func trailing(_ row: SessionRow) -> [SessionAction] {
        var out: [SessionAction] = [row.isPinned ? .unpin : .pin, .rename]
        if row.canFork == true { out.append(.fork) }
        return out
    }

    /// The long-press menu: the same actions, swipe-right ones first.
    static func menu(_ row: SessionRow) -> [SessionAction] { leading(row) + trailing(row) }
}

/// The hub's detach modes (`POST .../control {op: "detach", mode}`).
enum DetachMode: String, CaseIterable {
    /// Close tmux and the harness at once, even mid-turn.
    case now
    /// Wait for the turn to end, then close it.
    case whenIdle = "when_idle"
    /// Stop supervising but leave tmux running on the machine.
    case keep
}

/// How a Detach from the list goes: an idle session detaches at once
/// (with Undo); a working one asks first, since `now` would cut its turn.
enum DetachPlan: Equatable {
    case immediately
    case ask

    static func of(_ row: SessionRow) -> DetachPlan {
        switch row.sessionStatus {
        case .active, .approval: .ask
        default: .immediately
        }
    }

    /// The modes the dialog offers, the default first.
    static let choices: [DetachMode] = [.whenIdle, .keep, .now]
}

/// The rename sheet's rules, matching `sessionmeta.parse_title` on the hub:
/// one line, spaces collapsed, at most 120 characters.
enum RenameRules {
    static let maxLength = 120

    /// What the field holds after a keystroke or paste: newlines become
    /// spaces and anything past the limit is cut.
    static func clamp(_ text: String) -> String {
        let oneLine = text.replacingOccurrences(of: "\n", with: " ")
        return oneLine.count > maxLength ? String(oneLine.prefix(maxLength)) : oneLine
    }

    /// What Save sends: runs of whitespace collapsed, trimmed.
    static func normalized(_ text: String) -> String {
        String(text.split(whereSeparator: \.isWhitespace).joined(separator: " ").prefix(maxLength))
    }

    /// Save is on once the name is non-empty and differs from the current
    /// one. Clearing the name goes through "Use the original name".
    static func canSave(_ text: String, current: String?) -> Bool {
        let name = normalized(text)
        return !name.isEmpty && name != (current ?? "")
    }
}

extension SessionGrouping {
    /// Pinned rows first, in the order they were pinned, then the usual
    /// folder groups without them.
    static func groupsWithPins(_ rows: [SessionRow], machines: [Machine],
                               now: Double = Date().timeIntervalSince1970) -> [SessionGroup] {
        let pinned = rows.filter(\.isPinned).sorted {
            ($0.pinnedAt ?? .infinity, $0.key) < ($1.pinnedAt ?? .infinity, $1.key)
        }
        let rest = groups(rows.filter { !$0.isPinned }, machines: machines, now: now)
        guard !pinned.isEmpty else { return rest }
        return [SessionGroup(id: "pinned", title: "Pinned", rows: pinned)] + rest
    }
}
