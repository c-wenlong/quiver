import Foundation

/// One numbered row of the menu an agent's pane prints for an approval
/// ("1. Yes", "2. Yes, and don't ask again", ...), as the hub's tailer
/// sends it on `approval.requested` (`options: [{n, label}]`). The row's
/// number is itself a decision the hub accepts
/// (`protocol.parse_approval_command`).
struct ApprovalOption: Decodable, Hashable, Identifiable {
    let n: String
    let label: String
    var id: String { n }

    init(n: String, label: String) {
        self.n = n
        self.label = label
    }

    private enum CodingKeys: String, CodingKey { case n, label }

    /// `n` is a digit string from the pane; a number is read as one too.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let text = try? c.decode(String.self, forKey: .n) {
            n = text
        } else {
            n = String(try c.decode(Int.self, forKey: .n))
        }
        label = (try? c.decode(String.self, forKey: .label)) ?? ""
    }
}

/// How the approval card answers: the pane's own menu rows when the
/// event carried them (as the web and Mac show them), else Allow,
/// Always and Deny. Pure, so tests feed it events.
enum ApprovalMenu {
    /// The rows the card offers: labelled ones whose number the hub
    /// takes (one or two digits). Older hubs and non-tmux approvals
    /// send none, and the card keeps Allow / Always / Deny.
    static func rows(_ options: [ApprovalOption]?) -> [ApprovalOption] {
        (options ?? []).filter { option in
            !option.label.trimmingCharacters(in: .whitespaces).isEmpty
                && (1...2).contains(option.n.count) && option.n.allSatisfy { $0.isASCII && $0.isNumber }
        }
    }

    /// "1. Yes": the row as the pane numbers it.
    static func buttonTitle(_ option: ApprovalOption) -> String {
        "\(option.n). \(option.label)"
    }

    /// The card's heading. A menu row's number names its row once
    /// answered ("Answered: Yes, and don't ask again").
    static func title(_ state: ApprovalState, options: [ApprovalOption]) -> String {
        switch state {
        case .pending, .failed: return "Approval needed"
        case .submitting: return "Sending"
        case .queued: return "Queued, waiting for the machine"
        case .resolved(nil): return "Answered"
        case .resolved(let d?):
            switch d {
            case "deny": return "Denied"
            case "always": return "Always allowed"
            case "allow": return "Allowed"
            default:
                if let row = options.first(where: { $0.n == d }) { return "Answered: \(row.label)" }
                return d.allSatisfy(\.isNumber) ? "Answered" : "Allowed"
            }
        }
    }
}
