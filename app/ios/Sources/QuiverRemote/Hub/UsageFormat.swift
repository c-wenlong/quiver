import Foundation

/// Pure formatting and grading for the Usage page, kept apart from the
/// views so the rules are tested on their own.
enum UsageFormat {
    /// A reading older than this when its machine took the snapshot is the
    /// outage fallback: the 300 s rate-limit cache plus slack, as the remote PWA used.
    static let staleReadingS: Double = 360
    /// A satellite reports every 300 s; two missed reports means its
    /// figures are that old.
    static let staleReportS: Double = 660

    // MARK: Limits

    enum Tone: Equatable { case normal, warning, danger }

    /// Amber past 80%, red at 100% or once the provider says the limit is hit.
    static func tone(usedPercent: Double, limitReached: Bool = false) -> Tone {
        if limitReached || usedPercent >= 100 { return .danger }
        if usedPercent > 80 { return .warning }
        return .normal
    }

    /// "42%", clamped to 0...100 and rounded.
    static func percent(_ value: Double?) -> String {
        "\(Int(clampPercent(value).rounded()))%"
    }

    static func clampPercent(_ value: Double?) -> Double {
        min(100, max(0, value ?? 0))
    }

    /// "resets in 2 h 10 m". Minutes round up under an hour so a reset
    /// 20 seconds out never reads "0 m". Empty without a reset time.
    static func resetCountdown(_ resetAt: Double?, now: Double) -> String {
        guard let resetAt, resetAt > 0 else { return "" }
        let d = resetAt - now
        if d <= 0 { return "resets now" }
        if d < 3600 { return "resets in \(Int((d / 60).rounded(.up))) m" }
        let (big, small, units): (Int, Int, (String, String)) = d < 86_400
            ? (Int(d / 3600), Int(d.truncatingRemainder(dividingBy: 3600) / 60), ("h", "m"))
            : (Int(d / 86_400), Int(d.truncatingRemainder(dividingBy: 86_400) / 3600), ("d", "h"))
        // "1 d", not "1 d 0 h".
        return small == 0 ? "resets in \(big) \(units.0)" : "resets in \(big) \(units.0) \(small) \(units.1)"
    }

    /// A provider's window label as a person would say it: "5h" is "5 h",
    /// "7d" is "Weekly", "7d_sonnet" is "Weekly, Sonnet". With no label the
    /// window's length names it (Codex reports a bare 7-day window).
    static func windowLabel(_ raw: String?, seconds: Double?) -> String {
        if let raw, !raw.isEmpty {
            let parts = raw.split(separator: "_", maxSplits: 1).map(String.init)
            if let span = span(parts[0]) {
                return parts.count > 1 ? "\(span), \(capitalized(parts[1].replacingOccurrences(of: "_", with: " ")))" : span
            }
            return capitalized(raw.replacingOccurrences(of: "_", with: " "))
        }
        guard let seconds, seconds > 0 else { return "Quota" }
        if seconds.truncatingRemainder(dividingBy: 86_400) == 0 { return span("\(Int(seconds / 86_400))d") ?? "Quota" }
        return span("\(Int((seconds / 3600).rounded()))h") ?? "Quota"
    }

    /// "5h", "1d", "7d", "30d" as words; nil when `token` isn't a length.
    private static func span(_ token: String) -> String? {
        guard let unit = token.last, unit == "h" || unit == "d", let n = Int(token.dropLast()), n > 0 else { return nil }
        if unit == "h" { return "\(n) h" }
        switch n {
        case 1: return "Daily"
        case 7: return "Weekly"
        default: return "\(n) days"
        }
    }

    /// What a non-ok limit status means. `machine` names where to fix it:
    /// a satellite's credentials live on the satellite.
    static func limitStatus(_ status: String?, machine: String) -> String {
        switch status {
        case "no-sub": "No subscription on this account."
        case "auth-required": "Signed out. Log in to it again on \(machine)."
        case "timeout": "The provider didn't answer in time."
        default: status ?? "No reading."
        }
    }

    /// True when the reading was already old when its machine took the
    /// snapshot, so newer reads failed. Both times are on that machine's
    /// clock, so clock skew to the phone doesn't matter.
    static func isStale(fetchedAt: Double?, snapshotAt: Double?) -> Bool {
        guard let fetchedAt, fetchedAt > 0, let snapshotAt else { return false }
        return snapshotAt - fetchedAt > staleReadingS
    }

    /// How old a reading is now, in seconds: its age when the machine took
    /// the snapshot, plus how long that snapshot has sat on the hub. Each
    /// difference is on one clock, so no two clocks have to agree.
    static func readingAge(fetchedAt: Double, reportedAt: Double?, receivedAt: Double?, serverTime: Double?) -> Double {
        let atSnapshot = (reportedAt ?? fetchedAt) - fetchedAt
        let onHub = (serverTime ?? 0) - (receivedAt ?? serverTime ?? 0)
        return max(0, atSnapshot) + max(0, onHub)
    }

    /// "just now", "4 m ago", "3 h ago", "2 d ago".
    static func ago(_ seconds: Double) -> String {
        let d = max(0, seconds)
        if d < 60 { return "just now" }
        if d < 3600 { return "\(Int(d / 60)) m ago" }
        if d < 86_400 { return "\(Int(d / 3600)) h ago" }
        return "\(Int(d / 86_400)) d ago"
    }

    /// "Teams", "Pro team"; nil for no plan.
    static func plan(_ raw: String?) -> String? {
        guard let raw, !raw.isEmpty else { return nil }
        return capitalized(raw.replacingOccurrences(of: "_", with: " "))
    }

    static func harnessName(id: String, displayName: String?) -> String {
        let n = (displayName?.isEmpty == false ? displayName : nil) ?? id
        return capitalized(n)
    }

    private static func capitalized(_ s: String) -> String {
        s.prefix(1).uppercased() + s.dropFirst()
    }

    // MARK: Spend

    /// 842, 12K, 12.3K, 1.2M, 13B: one decimal, dropped when it is .0.
    /// The Mac's `UsageFormat.tokens` and the web's `Fmt.tokens`, so the
    /// three clients print the same figure for the same count.
    static func tokens(_ value: Int?) -> String {
        let count = max(0, value ?? 0)
        let v = Double(count)
        let units: [(limit: Double, suffix: String)] = [(1e3, "K"), (1e6, "M"), (1e9, "B")]
        guard var unit = units.lastIndex(where: { v >= $0.limit }) else { return "\(count)" }
        // The unit after rounding: 999,950 rounds to 1000K, which reads as 1M.
        while unit + 1 < units.count, ((v / units[unit].limit) * 10).rounded() / 10 >= 1000 {
            unit += 1
        }
        return compact(v / units[unit].limit) + units[unit].suffix
    }

    /// One decimal, without a trailing ".0".
    private static func compact(_ value: Double) -> String {
        let rounded = (value * 10).rounded() / 10
        return rounded == rounded.rounded() ? String(Int(rounded)) : String(format: "%.1f", rounded)
    }

    /// "$0", "<$0.01", "$0.42", "$12.30", "$123", "$5,432": cents below
    /// $100, whole dollars from there, as the Mac's `UsageFormat.dollars`
    /// and the web's `Fmt.dollars` print a spend.
    static func usd(_ value: Double?) -> String {
        let v = value ?? 0
        if v == 0 { return "$0" }
        if v < 0.01 { return "<$0.01" }
        let f = v < 100 ? usdCents : usdWhole
        return f.string(from: NSNumber(value: v)) ?? "$\(v)"
    }

    /// Built once: every spend row formats a figure, and a formatter is
    /// costly to make. Never changed after this, so safe to share.
    private static let usdCents = usdFormatter(fractionDigits: 2)
    private static let usdWhole = usdFormatter(fractionDigits: 0)

    private static func usdFormatter(fractionDigits: Int) -> NumberFormatter {
        let f = NumberFormatter()
        f.locale = Locale(identifier: "en_US")
        f.numberStyle = .currency
        f.currencyCode = "USD"
        f.maximumFractionDigits = fractionDigits
        f.minimumFractionDigits = fractionDigits
        return f
    }

    /// "$800", "$1.2K" for a chart axis. A week of small spend scales
    /// the axis to $1, with a tick at $0.50: rounded, that read "$1" twice
    /// up the axis, so a tick between whole dollars keeps its cents.
    static func usdAxis(_ value: Double) -> String {
        if value >= 1000 { return "$" + tokens(Int(value.rounded())) }
        if value == value.rounded() { return "$\(Int(value))" }
        return String(format: value < 10 ? "$%.2f" : "$%.1f", value)
    }

    /// One bar per day from `start` to `end` (ISO dates, inclusive), with
    /// days the hub left out (no usage) as zero. Oldest first.
    static func dailySeries(_ days: [OverviewResponse.Day], start: String?, end: String?,
                            timeZone: TimeZone = .current, maxDays: Int = 1100) -> [DailySpend] {
        // Local midnight, so the chart's own date labels name the same day.
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let parse = DateFormatter()
        parse.calendar = calendar
        parse.locale = Locale(identifier: "en_US_POSIX")
        parse.timeZone = timeZone
        parse.dateFormat = "yyyy-MM-dd"
        var byDay: [Date: OverviewResponse.Day] = [:]
        for day in days { if let d = parse.date(from: day.date) { byDay[d] = day } }
        let known = byDay.keys.sorted()
        guard var first = start.flatMap(parse.date(from:)) ?? known.first,
              let last = end.flatMap(parse.date(from:)) ?? known.last, first <= last else { return [] }
        // All time can run for years: the chart keeps the newest
        // `maxDays`, so a long ledger never drops this week's bars.
        if let floor = calendar.date(byAdding: .day, value: -(maxDays - 1), to: last), first < floor {
            first = floor
        }
        var out: [DailySpend] = []
        var cursor = first
        // A malformed range can't loop forever.
        while cursor <= last, out.count < maxDays {
            let day = byDay[cursor]
            out.append(DailySpend(date: cursor, cost: day?.costUsd ?? 0, tokens: day?.tokens ?? 0))
            guard let next = calendar.date(byAdding: .day, value: 1, to: cursor) else { break }
            cursor = next
        }
        return out
    }
}

/// One bar of the daily chart.
struct DailySpend: Identifiable, Equatable {
    let date: Date
    let cost: Double
    let tokens: Int
    var id: Date { date }
}
