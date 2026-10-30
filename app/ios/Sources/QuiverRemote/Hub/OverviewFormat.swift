import Foundation

/// The overview's side figures, shaped for the phone: quick figures,
/// top models, the activity heatmap and the daily table. Pure, so the
/// rules are tested on their own; the layout numbers follow the Mac's
/// `ActivityCard` and the web's `drawHeatmap`.
enum OverviewFormat {
    // MARK: Days

    /// Hub days are ISO dates in the period's zone; parsing and printing
    /// them in UTC keeps each one the same calendar day.
    private static let isoDay: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    static func day(_ iso: String?) -> Date? { iso.flatMap(isoDay.date(from:)) }

    /// "Jan 3", as the Mac's busiest chip.
    static func shortDay(_ iso: String?, locale: Locale = .current) -> String? {
        guard let date = day(iso) else { return nil }
        var style = Date.FormatStyle(locale: locale, timeZone: TimeZone(identifier: "UTC")!)
        style = style.month(.abbreviated).day()
        return date.formatted(style)
    }

    /// "Sat, Jan 3", the heatmap's picked-day line.
    static func longDay(_ iso: String?, locale: Locale = .current) -> String? {
        guard let date = day(iso) else { return nil }
        var style = Date.FormatStyle(locale: locale, timeZone: TimeZone(identifier: "UTC")!)
        style = style.weekday(.abbreviated).month(.abbreviated).day()
        return date.formatted(style)
    }

    /// The daily chart's span: the period's range, or for All time
    /// (which has none) the ledger's first day to the newest active day.
    static func chartRange(_ overview: OverviewResponse) -> (start: String?, end: String?) {
        if let range = overview.range { return (range.startDay, range.endDay) }
        return (overview.firstDay, overview.days?.first?.date)
    }

    // MARK: Quick figures

    struct Chip: Equatable {
        let value: String
        let label: String
    }

    /// Active days, tokens a day, busiest day: the Mac's three chips.
    static func quickChips(_ quick: OverviewResponse.Quick?, locale: Locale = .current) -> [Chip] {
        let active = quick?.activeDays ?? 0
        return [
            Chip(value: "\(active)", label: active == 1 ? "active day" : "active days"),
            Chip(value: UsageFormat.tokens(quick?.tokensPerDay), label: "a day"),
            Chip(value: shortDay(quick?.busiestDay, locale: locale) ?? "None", label: "busiest"),
        ]
    }

    /// "50.0%", one decimal as the Mac and web show a model's share.
    static func share(_ value: Double?) -> String {
        String(format: "%.1f%%", (value ?? 0) * 100)
    }

    /// A model's first three harnesses, and "+N" for the rest.
    static func harnessMarks(_ harnesses: [String]?) -> (shown: [String], more: String?) {
        let all = harnesses ?? []
        return (Array(all.prefix(3)), all.count > 3 ? "+\(all.count - 3)" : nil)
    }

    // MARK: Heatmap

    static let heatCell: CGFloat = 12
    static let heatGap: CGFloat = 3
    static let heatGutter: CGFloat = 24

    /// As many recent weeks as `width` fits at 12pt cells 3pt apart after
    /// the weekday labels: 8 at the least, never more than the hub sent.
    static func weeksThatFit(width: CGFloat, available: Int) -> Int {
        guard available > 0 else { return 0 }
        let fit = Int((width - heatGutter) / (heatCell + heatGap))
        return min(available, max(8, fit))
    }

    /// The hub's cells as week columns of seven (Sunday first), oldest
    /// first, keeping the newest `weeks`. A future day is nil, a gap.
    static func columns(_ heatmap: OverviewResponse.Heatmap?, weeks: Int) -> [[OverviewResponse.Heatmap.Cell?]] {
        let cells = heatmap?.days ?? []
        var out: [[OverviewResponse.Heatmap.Cell?]] = []
        var index = 0
        while index + 7 <= cells.count {
            out.append(cells[index..<index + 7].map { $0.future == true ? nil : $0 })
            index += 7
        }
        return Array(out.suffix(max(0, weeks)))
    }

    /// The month's short name over a month's first week; the first
    /// column gives way when the next week already starts another month,
    /// since a label is wider than its column (the web's rule).
    static func monthLabels(_ columns: [[OverviewResponse.Heatmap.Cell?]], locale: Locale = .current) -> [String?] {
        func month(_ w: Int) -> String? {
            guard w < columns.count else { return nil }
            let iso = columns[w].compactMap { $0?.date }.first
            guard let date = day(iso) else { return nil }
            var style = Date.FormatStyle(locale: locale, timeZone: TimeZone(identifier: "UTC")!)
            style = style.month(.abbreviated)
            return date.formatted(style)
        }
        var last: String?
        return columns.indices.map { w in
            let name = month(w)
            defer { last = name }
            guard let name, name != last else { return nil }
            if w == 0, let next = month(1), next != name { return nil }
            return name
        }
    }

    /// "12.3M tokens · $4.20", or "No usage".
    static func cellDetail(_ cell: OverviewResponse.Heatmap.Cell) -> String {
        let tokens = cell.tokens ?? 0
        if tokens == 0 { return "No usage" }
        return "\(UsageFormat.tokens(tokens)) tokens · \(UsageFormat.usd(cell.costUsd))"
    }

    // MARK: Daily table

    /// The table shows this many days first, then `dailyPage` more a tap.
    static let dailyPreview = 10
    static let dailyPage = 30

    /// The rows on screen: the newest `shown`.
    static func dailyRows(_ days: [OverviewResponse.Day], shown: Int) -> [OverviewResponse.Day] {
        Array(days.prefix(max(0, shown)))
    }

    /// What the next "Show N more" adds; 0 once every day is shown,
    /// and the button reads "Show less".
    static func dailyMore(total: Int, shown: Int) -> Int {
        min(dailyPage, max(0, total - shown))
    }

    /// One row's cells: date, total, input, output, cached, spend.
    static func dailyCells(_ day: OverviewResponse.Day) -> [String] {
        [day.date, UsageFormat.tokens(day.tokens), UsageFormat.tokens(day.input),
         UsageFormat.tokens(day.output), UsageFormat.tokens(day.cached), UsageFormat.usd(day.costUsd)]
    }
}
