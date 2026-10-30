import Foundation

/// Shared calendar text rules — times, durations and session labels — so
/// every block, card, row and sheet prints them the same way.
public enum CalendarFormat {
    private static let clock: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f
    }()

    /// "09:41–10:02", or just "09:41" when start and end land in the
    /// same minute of the same day: a one-response block reads as a
    /// moment, not a span. Matching clock text alone is not enough —
    /// 11:02 today to 11:02 tomorrow is a real range.
    public static func range(start: Date, end: Date) -> String {
        let s = clock.string(from: start)
        let e = clock.string(from: end)
        let sameMinute = Calendar.current.isDate(start, equalTo: end, toGranularity: .minute)
        return s == e && sameMinute ? s : "\(s)–\(e)"
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
    /// a short readable slug ("natural-riverbed"), chopped to its first
    /// eight characters plus an ellipsis for UUID-like ids (the whole
    /// string carries no words) and for ids past 24 characters.
    public static func sessionTitle(_ title: String?, sessionId: String) -> String {
        if let title, !title.isEmpty { return title }
        return sessionTitleFallback(sessionId)
    }

    /// A session row's label when the harness changes what the id
    /// means: a `grok-bot` conversation id is a Bot's id, so the roster
    /// names the row; every other harness uses the usual fallback.
    public static func sessionTitle(_ title: String?, sessionId: String,
                                    harness: String) -> String {
        if let title, !title.isEmpty { return title }
        if harness == "grok-bot" {
            return GrokBotRoster.shared.title(for: sessionId)
        }
        return sessionTitleFallback(sessionId)
    }

    /// The id-only fallback: whole for short readable slugs, chopped for
    /// UUID-like ids and anything past 24 characters, which would crowd a
    /// calendar block either way.
    private static func sessionTitleFallback(_ sessionId: String) -> String {
        let bare = sessionId.replacingOccurrences(of: "-", with: "")
        let chop = sessionId.count > 24
            || (bare.count == 32 && bare.allSatisfy(\.isHexDigit))
        return chop ? String(sessionId.prefix(8)) + "…" : sessionId
    }
}
