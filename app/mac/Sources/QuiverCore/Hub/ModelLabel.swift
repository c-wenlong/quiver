import Foundation

/// How a session's model reads on screen: the header of a session's chat
/// and a Sessions row. The iPhone's `ModelLabel` (app/ios, `Hub/Models.swift`)
/// and the web app's `Fmt.modelName` (`static/format.js`) follow the same
/// rules; change all three together.
public enum ModelLabel {
    /// The model id without its provider path or date stamp:
    /// "anthropic/claude-opus-4-5" and "claude-opus-4-5-20251101" both
    /// read "claude-opus-4-5", "openrouter/nvidia/nemotron-3:free" reads
    /// "nemotron-3:free". Nil for a missing or blank one, so a view shows
    /// nothing rather than a placeholder.
    public static func short(_ raw: String?) -> String? {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else { return nil }
        var name = trimmed.split(separator: "/").last.map(String.init) ?? trimmed
        if let dash = name.lastIndex(of: "-"), dash > name.startIndex {
            let stamp = name[name.index(after: dash)...]
            if stamp.count == 8, stamp.allSatisfy(\.isASCIIDigit) {
                name = String(name[..<dash])
            }
        }
        return name
    }

    /// The models a Sessions row names: the newest reply's `current` first
    /// (as the hub reports it, shortened), then the ledger's `used` models
    /// that aren't the same model spelled another way, so none reads twice.
    public static func rowModels(current: String?, used: [String]) -> [String] {
        guard let current = short(current) else { return used }
        return [current] + used.filter { short($0) != current }
    }
}

private extension Character {
    var isASCIIDigit: Bool { ("0"..."9").contains(self) }
}
