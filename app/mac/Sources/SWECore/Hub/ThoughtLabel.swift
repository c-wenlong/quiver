import Foundation

/// The title of a thinking card in a session's chat. The iPhone's
/// `ThoughtLabel` (app/ios, `Hub/Models.swift`) and the web app's
/// `Fmt.thought` (`static/format.js`) follow the same rules; change all
/// three together.
public enum ThoughtLabel {
    /// "Thought for 12s", "Thought for 1m 5s", "Thought for 1h 2m" from
    /// the hub's `duration_s`, rounded to the second and never under 1s.
    /// Plain "Thought" when the hub sent no usable duration.
    public static func text(_ seconds: Double?) -> String {
        guard let seconds, seconds.isFinite, seconds >= 0 else { return "Thought" }
        let total = max(1, Int(seconds.rounded()))
        let (h, m, s) = (total / 3600, total % 3600 / 60, total % 60)
        if h > 0 { return m > 0 ? "Thought for \(h)h \(m)m" : "Thought for \(h)h" }
        if m > 0 { return s > 0 ? "Thought for \(m)m \(s)s" : "Thought for \(m)m" }
        return "Thought for \(s)s"
    }
}
