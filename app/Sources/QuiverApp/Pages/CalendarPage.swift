import SwiftUI
import QuiverCore

/// The Notion-Calendar-style week view of when each harness was actually
/// working: `CalendarModel` turns the ledger's responses into activity
/// blocks, this lays them out on a day grid with a legend of harnesses
/// in place of calendar lists.
struct CalendarPage: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        if let model = app.calendar {
            CalendarContent(model: model)
        } else {
            Page(title: "Calendar", subtitle: "When each harness was working") {
                ErrorCard(message: app.overviewError ?? "No ledger.")
            }
        }
    }
}

private struct CalendarContent: View {
    @Environment(AppModel.self) private var app
    var model: CalendarModel
    /// The session detail sheet's row, when a block resolves to a listed
    /// session.
    @State private var detail: SessionsModel.Row?
    @State private var actionError: String?
    /// Session rows indexed by account, rebuilt when the list reloads so
    /// block titles and clicks are an O(1) lookup each render.
    @State private var sessionIndex = CalendarSessionIndex(rows: [])
    /// The page holds key focus so the plain-key mode/arrow shortcuts
    /// work without a click; a focused text field keeps its keys.
    @FocusState private var focused: Bool

    private static let monthTitle: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MMMM yyyy"; return f
    }()
    private static let hourLabel: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f
    }()

    var body: some View {
        let inMonth = model.mode == .month
        return Page(title: Self.monthTitle.string(from: model.anchor),
             subtitle: inMonth ? "" : "Week \(model.calendar.component(.weekOfYear, from: model.visibleRange.start))",
             scrolls: false,
             actions: { controls }) {
            HStack(spacing: 0) {
                side
                grid
            }
            .focusable()
            .focusEffectDisabled()
            .focused($focused)
            .onAppear { focused = true }
            .onKeyPress { press in
                if press.key == .leftArrow {
                    model.apply(CalendarModel.KeyCommand(arrow: .left)); return .handled
                }
                if press.key == .rightArrow {
                    model.apply(CalendarModel.KeyCommand(arrow: .right)); return .handled
                }
                guard press.modifiers.isEmpty,
                      let key = press.characters.first,
                      let command = CalendarModel.KeyCommand(character: key)
                else { return .ignored }
                model.apply(command)
                return .handled
            }
        }
        .task {
            // Blocks load concurrently with the session list: cached
            // ranges paint immediately and titles fill in when rows land.
            async let loadBlocks: () = model.load()
            await app.sessions?.restore()
            await app.sessions?.loadIfStale(for: app.swePath)
            sessionIndex = CalendarSessionIndex(rows: app.sessions?.rows ?? [])
            await loadBlocks
        }
        .onChange(of: app.sessions?.rows ?? []) {
            sessionIndex = CalendarSessionIndex(rows: app.sessions?.rows ?? [])
        }
        .onChange(of: model.visibleRange) {
            Task { await model.load() }
        }
        .onChange(of: app.overview?.completedScans ?? 0) {
            // A finished scan may have logged new responses for the days
            // on show; stale ranges keep painting until their refetch
            // lands.
            model.noteScanCompleted()
            Task { await model.load(force: true) }
        }
        .sheet(item: $detail) { row in
            SessionDetail(row: row,
                          resume: { resume(row) },
                          close: { detail = nil })
        }
        .onChange(of: detail == nil) {
            // The sheet takes key focus; take it back when it closes.
            if detail == nil { focused = true }
        }
    }

    private func resume(_ row: SessionsModel.Row) {
        Task {
            do {
                try await app.resume(row)
                actionError = nil
            } catch {
                actionError = "Couldn't resume: \(error.localizedDescription)"
            }
        }
    }

    private var controls: some View {
        HStack(spacing: 6) {
            SelectMenu(options: CalendarModel.Mode.menu(for: model.mode)
                           .map { ($0, "\($0.title) (\($0.keyHint))") },
                       selection: Bindable(model).mode, width: 92)
            Button("Today") { model.today() }
                .secondaryButton()
                .keyboardShortcut("t", modifiers: .command)
                .help("Today (T)")
            Button("Previous \(model.mode.title.lowercased())", systemImage: "chevron.left") {
                model.previous()
            }
            .iconButton()
            .keyboardShortcut(.leftArrow, modifiers: .command)
            .help("Previous \(model.mode.title.lowercased()) (←)")
            Button("Next \(model.mode.title.lowercased())", systemImage: "chevron.right") {
                model.next()
            }
            .iconButton()
            .keyboardShortcut(.rightArrow, modifiers: .command)
            .help("Next \(model.mode.title.lowercased()) (→)")
        }
    }

    // MARK: - Left pane

    private var side: some View {
        VStack(alignment: .leading, spacing: 0) {
            MiniMonth(model: model)
                .padding(.bottom, 10)
            Text("Harnesses").font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Theme.textSecondary).textCase(.uppercase)
                .padding(.bottom, 4)
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(model.harnesses, id: \.self) { harness in
                        HarnessLegend(model: model, harness: harness,
                                      colors: app.widgetHarnessStyle.colors)
                    }
                }
            }
        }
        .frame(width: 220)
        .padding(12)
        .overlay(alignment: .trailing) {
            Rectangle().fill(Theme.border).frame(width: 1)
        }
    }

    // MARK: - Week grid

    private var grid: some View {
        VStack(spacing: 0) {
            dayHeaders
            if let error = model.loadError ?? actionError {
                ErrorCard(message: error).padding(8)
            }
            if model.visibleBlocks.isEmpty {
                Spacer()
                Text("No harness activity in this \(model.mode.title.lowercased())")
                    .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                Spacer()
            } else if model.mode == .month {
                monthGrid
            } else {
                TimelineView(.everyMinute) { context in
                    ScrollViewReader { proxy in
                        ScrollView {
                            hourGrid(now: context.date)
                                .id("grid")
                        }
                        .onAppear {
                            // Open at the earliest block of the range, or 08:00.
                            let earliest = model.visibleBlocks.map(\.start).min()
                            let hour = earliest.map { model.calendar.component(.hour, from: $0) } ?? 8
                            proxy.scrollTo("h\(max(0, min(23, hour)))", anchor: .top)
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var dayHeaders: some View {
        if model.mode == .month { return AnyView(monthWeekdayHeader) }
        return AnyView(weekDayHeaders)
    }

    private var weekDayHeaders: some View {
        HStack(spacing: 0) {
            Color.clear.frame(width: Self.axisWidth, height: 0)
            ForEach(model.days, id: \.start) { day in
                let isToday = day.contains(now)
                HStack(spacing: 5) {
                    Text(day.start.formatted(.dateTime.weekday(.abbreviated)))
                    Text(day.start.formatted(.dateTime.day()))
                        .font(.system(size: 12, weight: isToday ? .semibold : .regular))
                        .foregroundStyle(isToday ? .white : .primary)
                        .padding(.horizontal, 5).padding(.vertical, 2)
                        .background(isToday ? Color.red : .clear,
                                    in: RoundedRectangle(cornerRadius: 5))
                }
                .font(Theme.caption)
                .padding(.leading, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.vertical, 8)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Theme.border).frame(height: 1)
        }
    }

    private static let axisWidth: CGFloat = 52
    private static let hourHeight: CGFloat = 48
    private static let weekNumberWidth: CGFloat = 22

    private var now: Date { Date() }

    /// ISO week numbers down the month's leading edge.
    private var iso: Calendar {
        var c = Calendar(identifier: .iso8601); c.timeZone = model.calendar.timeZone; return c
    }

    // MARK: - Month grid

    /// The weekday row above the month cells; order follows the user's
    /// first weekday, like the mini month.
    private var monthWeekdayHeader: some View {
        let week = MonthLayout.weeks(for: model.anchor, calendar: model.calendar).first ?? []
        return HStack(spacing: 0) {
            Color.clear.frame(width: Self.weekNumberWidth, height: 0)
            ForEach(week, id: \.self) { day in
                Text(day.formatted(.dateTime.weekday(.abbreviated)))
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.vertical, 8)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Theme.border).frame(height: 1)
        }
    }

    /// Six week rows of session entries, always the anchor's month plus
    /// the adjacent days the fixed grid pulls in.
    private var monthGrid: some View {
        let cal = model.calendar
        let weeks = MonthLayout.weeks(for: model.anchor, calendar: cal)
        return VStack(spacing: 0) {
            ForEach(weeks.indices, id: \.self) { row in
                HStack(alignment: .top, spacing: 0) {
                    Text("\(iso.component(.weekOfYear, from: weeks[row].first ?? model.anchor))")
                        .font(.system(size: 9)).foregroundStyle(Theme.textSecondary)
                        .frame(width: Self.weekNumberWidth, alignment: .leading)
                        .padding(.top, 4)
                    ForEach(weeks[row], id: \.self) { day in
                        MonthCell(model: model, day: day,
                                  entries: monthEntries(on: day),
                                  color: { self.color(for: $0) },
                                  title: { self.title(for: $0) }) { block in
                            open(block)
                        } more: {
                            model.go(to: day)
                            model.mode = .day
                        }
                        .frame(maxWidth: .infinity)
                    }
                }
                .frame(maxHeight: .infinity)
                .overlay(alignment: .bottom) {
                    if row < weeks.count - 1 {
                        Rectangle().fill(Theme.border).frame(height: 1)
                    }
                }
            }
        }
        .padding(.leading, 4)
    }

    /// The day's session rows after the legend's hidden toggles apply.
    private func monthEntries(on day: Date) -> [MonthLayout.Entry] {
        let interval = DateInterval(
            start: day,
            end: model.calendar.date(byAdding: .day, value: 1, to: day) ?? day)
        return MonthLayout.entries(model.visibleBlocks, day: interval)
    }

    private func hourGrid(now: Date) -> some View {
        let days = model.days
        return HStack(alignment: .top, spacing: 0) {
            // Hour labels, with the now-pill replacing the nearest label on
            // a day that contains it.
            ZStack(alignment: .topLeading) {
                VStack(spacing: 0) {
                    ForEach(0..<24, id: \.self) { hour in
                        Text(hourLabel(hour))
                            .font(.system(size: 9)).foregroundStyle(Theme.textSecondary)
                            .frame(width: Self.axisWidth - 8, height: Self.hourHeight,
                                   alignment: .topTrailing)
                            .id("h\(hour)")
                    }
                }
                if let today = days.first(where: { $0.contains(now) }) {
                    Text(now, format: .dateTime.hour().minute())
                        .font(.system(size: 9, weight: .medium))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 5).padding(.vertical, 2)
                        .background(.red, in: Capsule())
                        .offset(y: Self.hourHeight * CGFloat(now.timeIntervalSince(today.start) / 3600) - 9)
                }
            }
            .frame(width: Self.axisWidth)

            ForEach(days, id: \.start) { day in
                DayColumn(model: model, day: day, now: now,
                          color: { self.color(for: $0) },
                          title: { self.title(for: $0) }) { block in
                    open(block)
                }
                .frame(maxWidth: .infinity)
            }
        }
    }

    private func hourLabel(_ hour: Int) -> String {
        var components = DateComponents(); components.hour = hour
        let date = model.calendar.date(from: components) ?? Date()
        return Self.hourLabel.string(from: date)
    }

    /// The block's session row, or the index's stand-in when the session
    /// is gone from the listing: its detail sheet still opens.
    private func session(for block: ActivityBlock) -> SessionsModel.Row {
        sessionIndex.row(for: block)
    }

    /// A block's title: its session's title, else the folder it ran in,
    /// else a short session id.
    private func title(for block: ActivityBlock) -> String {
        let row = session(for: block)
        if let title = row.session.title, !title.isEmpty { return title }
        if let cwd = row.session.cwd, let last = cwd.split(separator: "/").last {
            return String(last)
        }
        return String(block.sessionId.prefix(8))
    }

    private func open(_ block: ActivityBlock) {
        detail = session(for: block)
    }

    private func color(for block: ActivityBlock) -> Color {
        let hex = app.widgetHarnessStyle.colors[block.harness]
            ?? HarnessColor.resolve(id: block.harness, primaryColor: nil).hex
        return Color(hex: hex)
    }
}

/// One day column: hour lines and the day's segments laid out in their
/// overlap lanes, with the red now-line when the column is today.
private struct DayColumn: View {
    var model: CalendarModel
    var day: DateInterval
    var now: Date
    var color: (ActivityBlock) -> Color
    var title: (ActivityBlock) -> String
    var open: (ActivityBlock) -> Void

    private static let clock: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f
    }()
    private let hourHeight: CGFloat = 48
    private var gridHeight: CGFloat { 24 * hourHeight }

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .topLeading) {
                // Hour lines.
                VStack(spacing: 0) {
                    ForEach(0..<24, id: \.self) { _ in
                        VStack(spacing: 0) {
                            Rectangle().fill(Theme.border.opacity(0.5)).frame(height: 1)
                            Spacer()
                        }
                        .frame(height: hourHeight)
                    }
                }
                ForEach(segments) { segment in
                    let lane = geo.size.width / CGFloat(segment.laneCount)
                    BlockView(segment: segment, color: color(segment.block),
                              title: title(segment.block),
                              account: model.accountName(for: segment.block.accountHome))
                        .frame(width: lane - 2, height: blockHeight(segment))
                        .offset(x: CGFloat(segment.lane) * lane + 1, y: y(segment.start))
                        .onTapGesture { open(segment.block) }
                }
                if day.contains(now) {
                    Rectangle().fill(.red).frame(height: 1.5)
                        .offset(y: y(now))
                }
            }
        }
        .frame(height: gridHeight)
        .overlay(alignment: .leading) {
            Rectangle().fill(Theme.border).frame(width: 1)
        }
    }

    private var segments: [CalendarLayout.Segment] {
        // 18pt of minimum height at 48pt per hour is 22.5 minutes: short
        // blocks get a lane and a readable sliver of height.
        CalendarLayout.segments(model.visibleBlocks, day: day,
                                minimumDuration: 18 / 48 * 3600)
    }

    private func blockHeight(_ segment: CalendarLayout.Segment) -> CGFloat {
        max(18, CGFloat(CalendarLayout.clockOffset(segment.end, in: day, calendar: model.calendar)
                        - CalendarLayout.clockOffset(segment.start, in: day, calendar: model.calendar))
            * hourHeight)
    }

    /// Clock hours into the day, not elapsed seconds: on a DST day the
    /// hour a response happened still decides where its block sits.
    private func y(_ date: Date) -> CGFloat {
        CGFloat(CalendarLayout.clockOffset(date, in: day, calendar: model.calendar)) * hourHeight
    }
}

/// One activity block: rounded fill in the harness colour at low opacity,
/// a full-colour left bar, the session title and its time range when it is
/// tall enough.
private struct BlockView: View {
    var segment: CalendarLayout.Segment
    var color: Color
    var title: String
    var account: String

    private static let clock: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f
    }()

    /// The block's real times: `start`/`end` on the segment are display
    /// times stretched by the minimum duration.
    private var timeRange: String {
        "\(Self.clock.string(from: segment.block.start))–\(Self.clock.string(from: segment.block.end))"
    }

    private var duration: String {
        let minutes = Int(segment.block.end.timeIntervalSince(segment.block.start) / 60)
        return minutes >= 60 ? "\(minutes / 60)h \(minutes % 60)m" : "\(minutes)m"
    }

    var body: some View {
        HStack(spacing: 0) {
            RoundedRectangle(cornerRadius: 1.5).fill(color)
                .frame(width: 3)
                .padding(.vertical, 1.5)
            VStack(alignment: .leading, spacing: 0) {
                Text(title).font(.system(size: 10, weight: .medium))
                    .lineLimit(1)
                if segment.end.timeIntervalSince(segment.start) >= 30 * 60 {
                    Text(timeRange).font(.system(size: 9))
                        .foregroundStyle(Theme.textSecondary)
                }
            }
            .padding(.horizontal, 5).padding(.vertical, 3)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(color.opacity(0.15), in: RoundedRectangle(cornerRadius: 4))
        .help("\(segment.block.harness.capitalized) · \(account)\n\(title)\n\(timeRange) · \(duration) · \(segment.block.responses) responses")
        .accessibilityElement()
        .accessibilityLabel("\(segment.block.harness), \(title), \(timeRange)")
    }
}

/// The month picker in the left pane: ISO week numbers down the leading
/// edge, the visible range's week row highlighted, today a filled circle,
/// a day click jumping the grid there.
private struct MiniMonth: View {
    var model: CalendarModel
    /// The month on show; starts at the anchor's and follows it when the
    /// grid moves.
    @State private var month: Date

    init(model: CalendarModel) {
        self.model = model
        _month = State(initialValue: model.anchor)
    }

    private static let monthTitle: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MMMM yyyy"; return f
    }()
    private var iso: Calendar {
        var c = Calendar(identifier: .iso8601); c.timeZone = model.calendar.timeZone; return c
    }

    /// The 7-day rows covering the month, week-aligned to the user's
    /// first weekday.
    private var weeks: [[Date]] {
        let cal = model.calendar
        let monthStart = cal.date(from: cal.dateComponents([.year, .month], from: month)) ?? month
        let firstWeek = cal.date(from: cal.dateComponents([.yearForWeekOfYear, .weekOfYear], from: monthStart)) ?? monthStart
        var rows: [[Date]] = []
        var day = firstWeek
        while rows.count < 6 && (rows.isEmpty || day < cal.date(byAdding: .month, value: 1, to: monthStart) ?? day) {
            rows.append((0..<7).compactMap { cal.date(byAdding: .day, value: $0, to: day) })
            day = cal.date(byAdding: .day, value: 7, to: day) ?? day
        }
        return rows
    }

    var body: some View {
        let cal = model.calendar
        let shown = model.visibleRange
        VStack(spacing: 4) {
            HStack {
                Text(Self.monthTitle.string(from: month))
                    .font(.system(size: 11, weight: .semibold))
                Spacer()
                Button { shiftMonth(-1) } label: { Image(systemName: "chevron.up") }
                    .buttonStyle(.plain).foregroundStyle(Theme.textSecondary)
                Button { shiftMonth(1) } label: { Image(systemName: "chevron.down") }
                    .buttonStyle(.plain).foregroundStyle(Theme.textSecondary)
            }
            // Weekday initials above a muted ISO-week column.
            HStack(spacing: 0) {
                Text("").frame(width: 16)
                ForEach(weeks.first ?? [], id: \.self) { day in
                    Text(day.formatted(.dateTime.weekday(.narrow)))
                        .font(.system(size: 8)).foregroundStyle(Theme.textSecondary)
                        .frame(maxWidth: .infinity)
                }
            }
            ForEach(weeks, id: \.self) { week in
                let isShown = week.contains { shown.contains($0) }
                HStack(spacing: 0) {
                    Text("\(iso.component(.weekOfYear, from: week[0]))")
                        .font(.system(size: 8)).foregroundStyle(Theme.textSecondary)
                        .frame(width: 16, alignment: .leading)
                    ForEach(week, id: \.self) { day in
                        let inMonth = cal.isDate(day, equalTo: month, toGranularity: .month)
                        let isToday = cal.isDateInToday(day)
                        Button { model.go(to: day) } label: {
                            Text(day.formatted(.dateTime.day()))
                                .font(.system(size: 9))
                                .foregroundStyle(isToday ? .white : (inMonth ? .primary : Theme.textMuted))
                                .frame(maxWidth: .infinity).frame(height: 16)
                                .background(isToday ? Color.red : .clear, in: Circle())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.vertical, 1)
                .background(isShown ? Theme.border.opacity(0.5) : .clear,
                            in: RoundedRectangle(cornerRadius: 4))
            }
        }
        .onChange(of: model.anchor) { month = model.anchor }
    }

    private func shiftMonth(_ count: Int) {
        month = model.calendar.date(byAdding: .month, value: count, to: month) ?? month
    }
}

/// One legend row: a colour checkbox, the harness mark and title, and its
/// total time in the range. Expands into per-account rows when more than
/// one account has activity.
private struct HarnessLegend: View {
    var model: CalendarModel
    var harness: String
    var colors: [String: String]
    @State private var expanded = false

    private var color: Color {
        let hex = colors[harness] ?? HarnessColor.resolve(id: harness, primaryColor: nil).hex
        return Color(hex: hex)
    }

    private var accounts: [String] { model.accounts(for: harness) }

    var body: some View {
        HStack(spacing: 6) {
            if accounts.count > 1 {
                Button { expanded.toggle() } label: {
                    Image(systemName: expanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 8)).foregroundStyle(Theme.textSecondary)
                        .frame(width: 10)
                }
                .buttonStyle(.plain)
            } else {
                Spacer().frame(width: 10)
            }
            Button { model.toggle(harness) } label: {
                RoundedRectangle(cornerRadius: 3)
                    .fill(model.isHidden(harness) ? .clear : color)
                    .overlay(RoundedRectangle(cornerRadius: 3).stroke(color, lineWidth: 1.5))
                    .frame(width: 12, height: 12)
            }
            .buttonStyle(.plain)
            .help(model.isHidden(harness) ? "Show \(harness)" : "Hide \(harness)")
            HarnessIcon(harness: harness, size: 13)
            Text(harness.capitalized).font(.system(size: 11))
                .foregroundStyle(model.isHidden(harness) ? .secondary : .primary)
            Spacer()
            Text(Self.time(model.totals[harness] ?? 0))
                .font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
        }
        .frame(height: 20)
        if expanded {
            ForEach(accounts, id: \.self) { home in
                HStack(spacing: 6) {
                    Spacer().frame(width: 10)
                    Button { model.toggle(harness: harness, accountHome: home) } label: {
                        RoundedRectangle(cornerRadius: 3)
                            .fill(model.isHidden(harness: harness, accountHome: home) ? .clear : color)
                            .overlay(RoundedRectangle(cornerRadius: 3).stroke(color, lineWidth: 1.5))
                            .frame(width: 12, height: 12)
                    }
                    .buttonStyle(.plain)
                    Text(model.accountName(for: home))
                        .font(.system(size: 10))
                        .foregroundStyle(model.isHidden(harness: harness, accountHome: home) ? .secondary : .primary)
                        .lineLimit(1)
                    Spacer()
                    Text(Self.time(model.accountTotals(for: harness)[home] ?? 0))
                        .font(.system(size: 9)).foregroundStyle(Theme.textSecondary)
                }
                .frame(height: 18)
                .padding(.leading, 8)
            }
        }
    }

    private static func time(_ seconds: TimeInterval) -> String {
        let minutes = Int(seconds / 60)
        if minutes >= 60 { return "\(minutes / 60)h \(minutes % 60)m" }
        return "\(minutes)m"
    }
}

/// One month cell: the day number top-right ("October 1" on a 1st,
/// muted for adjacent-month days, red badge for today), then as many
/// session entries as fit, the rest under a "N more" button that opens
/// the day.
private struct MonthCell: View {
    var model: CalendarModel
    var day: Date
    var entries: [MonthLayout.Entry]
    var color: (ActivityBlock) -> Color
    var title: (ActivityBlock) -> String
    var open: (ActivityBlock) -> Void
    var more: () -> Void

    /// An entry row's height; the split reserves the same room for the
    /// "N more" row.
    private static let rowHeight: CGFloat = 16

    var body: some View {
        let cal = model.calendar
        let inMonth = cal.isDate(day, equalTo: model.anchor, toGranularity: .month)
        let isFirst = cal.component(.day, from: day) == 1
        let isToday = cal.isDateInToday(day)
        GeometryReader { geo in
            let fitting = max(0, Int((geo.size.height - 18) / Self.rowHeight))
            let split = MonthLayout.split(entries, fitting: fitting)
            VStack(alignment: .leading, spacing: 1) {
                HStack {
                    Spacer(minLength: 0)
                    if isFirst {
                        Text(day, format: .dateTime.month(.wide).day())
                            .font(.system(size: 9, weight: .semibold))
                            .foregroundStyle(isToday ? .white : (inMonth ? .primary : Theme.textMuted))
                            .padding(.horizontal, 4).padding(.vertical, 1)
                            .background(isToday ? Color.red : .clear,
                                        in: RoundedRectangle(cornerRadius: 4))
                    } else {
                        Text(day, format: .dateTime.day())
                            .font(.system(size: 9))
                            .foregroundStyle(isToday ? .white : (inMonth ? .primary : Theme.textMuted))
                            .padding(.horizontal, 4).padding(.vertical, 1)
                            .background(isToday ? Color.red : .clear,
                                        in: RoundedRectangle(cornerRadius: 4))
                    }
                }
                .padding(.horizontal, 3).padding(.top, 2)
                ForEach(split.shown) { entry in
                    MonthEntry(entry: entry, model: model,
                               color: color(entry.block), title: title(entry.block))
                        .onTapGesture { open(entry.block) }
                }
                if split.more > 0 {
                    Button { more() } label: {
                        Text("\(split.more) more")
                            .font(.system(size: 9))
                            .foregroundStyle(Theme.textSecondary)
                    }
                    .buttonStyle(.plain)
                    .padding(.horizontal, 6)
                    .frame(height: Self.rowHeight)
                    .help("Show \(day, format: .dateTime.day().month(.wide)) in the day view")
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .contentShape(Rectangle())
            .onTapGesture { more() }
        }
        .overlay(alignment: .leading) {
            Rectangle().fill(Theme.border).frame(width: 1)
        }
    }
}

/// One session row in a month cell: a harness-coloured bar, the first
/// block's start, and the session title on one line.
private struct MonthEntry: View {
    var entry: MonthLayout.Entry
    var model: CalendarModel
    var color: Color
    var title: String

    private static let clock: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f
    }()

    private var active: String {
        let minutes = Int(entry.active / 60)
        return minutes >= 60 ? "\(minutes / 60)h \(minutes % 60)m" : "\(minutes)m"
    }

    var body: some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 1.5).fill(color)
                .frame(width: 3, height: 10)
            Text(Self.clock.string(from: entry.start))
                .font(.system(size: 8)).foregroundStyle(Theme.textSecondary)
            Text(title).font(.system(size: 9)).lineLimit(1)
        }
        .padding(.horizontal, 6)
        .frame(height: 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .help("\(UsageFormat.harness(entry.block.harness)) · "
              + "\(model.accountName(for: entry.block.accountHome))\n\(title)\n"
              + "\(active) active · \(entry.blockCount) block\(entry.blockCount == 1 ? "" : "s")")
        .accessibilityElement()
        .accessibilityLabel("\(UsageFormat.harness(entry.block.harness)), \(title), \(active)")
    }
}
