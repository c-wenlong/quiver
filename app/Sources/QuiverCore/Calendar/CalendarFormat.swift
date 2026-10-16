import Foundation

/// Shared calendar text rules — times, durations and session labels — so
/// every block, card, row and sheet prints them the same way.
public enum CalendarFormat {
    private static let clock: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f
    }()

    /// "09:41–10:02", or just "09:41" when start and end land on the
    /// same minute: a one-response block reads as a moment, not a span.
    public static func range(start: Date, end: Date) -> String {
        let s = clock.string(from: start)
        let e = clock.string(from: end)
        return s == e ? s : "\(s)–\(e)"
    }

    /// "2h 5m", "47m", or "<1m" when the active time is under a minute —
    /// "0m" would read as nothing happening.
    public static func duration(_ seconds: TimeInterval) -> String {
        let minutes = Int(seconds / 60)
        if minutes >= 60 { return "\(minutes / 60)h \(minutes % 60)m" }
        if minutes < 1 { return "<1m" }
        return "\(minutes)m"
    }

    /// A session row's label: its title, else the id — whole when it is
    /// a readable slug ("natural-riverbed"), chopped to its first eight
    /// characters plus an ellipsis only for UUID-like ids, where the
    /// whole string carries no words.
    public static func sessionTitle(_ title: String?, sessionId: String) -> String {
        if let title, !title.isEmpty { return title }
        let bare = sessionId.replacingOccurrences(of: "-", with: "")
        let looksUUID = sessionId.count > 24
            || (bare.count == 32 && bare.allSatisfy(\.isHexDigit))
        return looksUUID ? String(sessionId.prefix(8)) + "…" : sessionId
    }
}
