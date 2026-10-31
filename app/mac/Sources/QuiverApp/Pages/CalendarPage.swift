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

/// Where the hour axis's now pill sits against its hour labels.
enum CalendarHourAxis {
    /// The pill's reach above and below the current time at 100% zoom:
    /// 9pt text with 2pt padding, drawn 9pt above the line.
    static let pillAbove: CGFloat = 9
    static let pillBelow: CGFloat = 7
    /// An hour label's height from the top of its hour at 100% zoom.
    static let labelHeight: CGFloat = 12

    /// Whether the pill, centred on `nowOffset` points down the day,
    /// overlaps `hour`'s label; that label hides while it does. `scale`
    /// is View › Zoom's factor on the 9pt text: the pill and the labels
    /// grow with it, the hours do not.
    static func pillCovers(hour: Int, nowOffset: CGFloat, hourHeight: CGFloat, scale: CGFloat = 1) -> Bool {
        let top = CGFloat(hour) * hourHeight
        return top < nowOffset + pillBelow * scale && top + labelHeight * scale > nowOffset - pillAbove * scale
    }
}

private struct CalendarContent: View {
    @Environment(AppModel.self) private var app
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var model: CalendarModel
    /// The session detail sheet's row, when a block resolves to a listed
    /// session.
    @State private var detail: SessionsModel.Row?
    /// A project block holding several sessions: its sheet lists them.
    @State private var projectDetail: ActivityBlock?
    @State private var actionError: String?
    /// Session rows indexed by account, rebuilt when the list reloads so
    /// block titles and clicks are an O(1) lookup each render.
    @State private var sessionIndex = CalendarSessionIndex(rows: [])
    /// The page holds key focus so the plain-key mode/arrow shortcuts
    /// work without a click; a focused text field keeps its keys.
    @FocusState private var focused: Bool
    /// The range whose initial scroll already ran, so each new range
    /// opens at its first block once.
    @State private var scrolledRange: DateInterval?
    /// The side panel's pointer state; `collapsed` mirrors the model's
    /// persisted choice. Same hover machinery as the window sidebar.
    @State private var panelHover = HoverExpansion()
    /// The hover card, one at a time: keyed on the segment/entry id so a
    /// block split across two day columns opens its card only where the
    /// pointer is. An object, so hover traffic redraws the anchors that
    /// read it and never this body or its day columns.
    @State private var cards = HoverCards()

    private var animation: Animation? { reduceMotion ? nil : Theme.sidebarAnimation }

    private static let monthTitle: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MMMM yyyy"; return f
    }()
    private static let hourLabel: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f
    }()

    var body: some View {
        let inMonth = model.mode == .month
        let ledgerChanges = (app.overview?.completedScans ?? 0)
            + (app.overview?.ledgerWrites ?? 0)
        return Page(title: Self.monthTitle.string(from: model.anchor),
             subtitle: inMonth ? "" : "Week \(model.calendar.component(.weekOfYear, from: model.visibleRange.start))",
             scrolls: false,
             actions: { controls }) {
            HStack(spacing: 0) {
                grid
                // Pinned open the panel takes layout space; collapsed it
                // hides and only hovers back in over the grid.
                if !model.panelCollapsed { panel }
            }
            .overlay(alignment: .trailing) {
                if panelHover.isFloating {
                    panel
                } else if model.panelCollapsed {
                    // The collapsed rail's stand-in: a thin strip on the
                    // grid's right edge that the delayed hover reads.
                    Color.clear
                        .frame(width: 10)
                        .frame(maxHeight: .infinity)
                        .contentShape(.rect)
                        .onHover { panelHover.hover($0, collapsed: model.panelCollapsed) }
                }
            }
            .animation(animation, value: model.panelCollapsed)
            .animation(animation, value: panelHover.isExpanded)
            .onAppear { panelHover.expansion.collapsed = model.panelCollapsed }
            .onChange(of: model.panelCollapsed) { _, new in
                // Route every collapse source through the toggle so a
                // pending hover settles first, like the app sidebar.
                if new != panelHover.expansion.collapsed { panelHover.toggle() }
            }
            .focusable()
            .focusEffectDisabled()
            .focused($focused)
            .onAppear {
                focused = true
                cards.reduceMotion = reduceMotion
            }
            .onChange(of: reduceMotion) { cards.reduceMotion = reduceMotion }
            .onKeyPress { press in
                if press.key == .leftArrow {
                    model.apply(CalendarModel.KeyCommand(arrow: .left))
                    scrolledRange = model.visibleRange // a step is a swipe: keep the hour
                    return .handled
                }
                if press.key == .rightArrow {
                    model.apply(CalendarModel.KeyCommand(arrow: .right))
                    scrolledRange = model.visibleRange
                    return .handled
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
        .onChange(of: ledgerChanges) {
            // A finished scan or an import may have logged new responses
            // for the days on show; stale ranges keep painting until
            // their refetch lands.
            model.noteScanCompleted()
            Task { await model.load(force: true) }
        }
        .quiverSheet(item: $detail) { row in
            SessionDetail(row: row,
                          resume: { resume(row) },
                          close: { detail = nil })
        }
        .quiverSheet(item: $projectDetail) { block in
            ProjectSessionsSheet(block: block, model: model,
                                 rows: sessionIndex.rows(for: block),
                                 resume: resume)
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
            // The closed button reads just the mode name; the key hints
            // stay on the menu items. Sizing to the text keeps "N days"
            // and "Month" from truncating.
            SelectMenu(options: CalendarModel.Mode.menu(for: model.mode)
                           .map { ($0, "\($0.title) (\($0.keyHint))") },
                       selection: Bindable(model).mode,
                       width: nil, title: model.mode.title)
            Button("Today") { model.today() }
                .secondaryButton()
                .keyboardShortcut("t", modifiers: .command)
                .help("Today (T)")
            Button("Previous \(model.mode.title.lowercased())", systemImage: "chevron.left") {
                model.previous()
                scrolledRange = model.visibleRange // like a swipe: keep the hour
            }
            .iconButton()
            .keyboardShortcut(.leftArrow, modifiers: .command)
            .help("Previous \(model.mode.title.lowercased()) (←)")
            Button("Next \(model.mode.title.lowercased())", systemImage: "chevron.right") {
                model.next()
                scrolledRange = model.visibleRange
            }
            .iconButton()
            .keyboardShortcut(.rightArrow, modifiers: .command)
            .help("Next \(model.mode.title.lowercased()) (→)")
            Button(model.panelCollapsed ? "Expand calendar panel" : "Collapse calendar panel",
                   systemImage: "sidebar.right") {
                model.apply(.togglePanel)
            }
            .iconButton()
            .help("\(model.panelCollapsed ? "Expand" : "Collapse") calendar panel (S)")
            .accessibilityLabel(model.panelCollapsed ? "Expand calendar panel"
                                                     : "Collapse calendar panel")
        }
    }

    // MARK: - Side panel

    /// The mini month and legend on the grid's right. Pinned it sits in
    /// the layout; collapsed and hovered it floats over the grid with
    /// the window surface and shadow, like the app sidebar.
    private var panel: some View {
        VStack(alignment: .leading, spacing: 0) {
            MiniMonth(model: model)
                .padding(.bottom, 10)
            Text("Harnesses").font(Theme.font(size: 11, weight: .semibold))
                .foregroundStyle(Theme.textSecondary)
                .padding(.bottom, 4)
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(model.harnesses, id: \.self) { harness in
                        HarnessLegend(model: model, harness: harness)
                    }
                    if !usageCards.isEmpty {
                        Text("Usage").font(Theme.font(size: 11, weight: .semibold))
                            .foregroundStyle(Theme.textSecondary)
                            .padding(.top, 8).padding(.bottom, 4)
                        ForEach(usageCards) { card in
                            UsageLegend(model: model, card: card)
                        }
                    }
                }
            }
        }
        .frame(width: 220)
        .padding(12)
        .frame(maxHeight: .infinity)
        .background {
            Theme.wash
                .shadow(color: .black.opacity(0.12), radius: 8)
                .opacity(panelHover.isFloating ? 1 : 0)
        }
        .overlay(alignment: .leading) {
            Rectangle().fill(Theme.border).frame(width: 1)
        }
        .onHover { panelHover.hover($0, collapsed: model.panelCollapsed) }
    }

    // MARK: - Week grid

    /// The horizontal scroll's position: moved by id for keyboard,
    /// chevron and mini-month steps, by offset after a swipe lands.
    @State private var position = ScrollPosition(idType: Date.self)
    /// The range start a landed swipe already put back in place, so the
    /// range change it causes is not animated a second time.
    @State private var settledStart: Date?
    /// The columns' horizontal scroll offset. Only `DayHeaderStrip`
    /// reads it, so a scroll tick re-renders the strip alone instead of
    /// invalidating CalendarContent's body (and every `DayColumn`).
    @State private var scrollOffset = HeaderOffset()

    /// Harnesses with more than one account, over every block the grid
    /// draws (buffer days included, so a harness only in the buffer still
    /// labels its accounts). `columns` computes it once per render.
    private var multiAccountHarnesses: Set<String> {
        Set(Set(model.visibleBlocks.map(\.harness)).filter { model.accounts(for: $0).count > 1 })
    }

    /// Days per on-screen window: the columns each take 1/span of the
    /// grid's width and the render range buffers one span either side.
    private var span: Int {
        switch model.mode {
        case .day: 1
        case .week: 7
        case .month: 7
        case .days(let n): max(1, n)
        }
    }

    /// Week mode snaps whole weeks; day and N-day snap per day.
    private var snapsByWeek: Bool { model.mode == .week }

    private var grid: some View {
        VStack(spacing: 0) {
            if let error = model.loadError ?? actionError {
                ErrorCard(message: error).padding(8)
            }
            ZStack {
                if model.mode == .month {
                    VStack(spacing: 0) {
                        monthWeekdayHeader
                        monthGrid
                    }
                } else {
                    slidingGrid
                }
                // The grid renders even when empty; the notice floats
                // over it without swallowing clicks on the cells.
                if model.visibleWindowBlocks.isEmpty {
                    Text("No harness activity \(model.emptyRangeNoun)")
                        .font(Theme.font(size: 12)).foregroundStyle(Theme.textSecondary)
                        .allowsHitTesting(false)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// The sliding window: a pinned header row that tracks the columns'
    /// horizontal offset, then the hour axis and day columns in one
    /// vertical scroll so they never drift apart, with the columns
    /// inside a horizontal scroll that snaps on day boundaries (or week
    /// boundaries in week mode).
    private var slidingGrid: some View {
        GeometryReader { geo in
            let gridWidth = max(0, geo.size.width - Self.axisWidth)
            let columnWidth = gridWidth / CGFloat(span)
            VStack(spacing: 0) {
                DayHeaderStrip(days: model.renderDays, columnWidth: columnWidth,
                               gridWidth: gridWidth, axisWidth: Self.axisWidth,
                               offset: scrollOffset, now: now)
                TimelineView(.everyMinute) { context in
                    ScrollViewReader { proxy in
                        ScrollView {
                            HStack(alignment: .top, spacing: 0) {
                                hourAxis(now: context.date)
                                ScrollView(.horizontal) {
                                    columns(now: context.date, width: columnWidth)
                                }
                                .scrollTargetBehavior(.viewAligned)
                                .scrollPosition($position, anchor: .leading)
                                .scrollIndicators(.hidden)
                                .onScrollGeometryChange(for: CGFloat.self) {
                                    $0.contentOffset.x + $0.contentInsets.leading
                                } action: { _, offset in
                                    scrollOffset.x = offset
                                }
                                .onScrollPhaseChange { old, phase, context in
                                    cards.scrollPhase("days", from: old, to: phase)
                                    // Land only when a swipe comes to rest:
                                    // re-centring the render window mid-swipe
                                    // shifted the content under the momentum
                                    // and skipped days. A programmatic scroll
                                    // already targets the anchor, and the
                                    // first layout's idle still reads offset
                                    // zero, so neither lands.
                                    guard phase == .idle,
                                          old == .interacting || old == .decelerating
                                    else { return }
                                    let geometry = context.geometry
                                    settle(offset: geometry.contentOffset.x
                                               + geometry.contentInsets.leading,
                                           columnWidth: columnWidth)
                                }
                            }
                            .id("grid")
                        }
                        // The hours scroll quiets cards like the days do.
                        .onScrollPhaseChange { old, phase in
                            cards.scrollPhase("hours", from: old, to: phase)
                        }
                        .onAppear { scrollToFirstBlock(proxy) }
                        // Month view replaces this grid; coming back must scroll
                        // again, and a swipe cut off mid-way never reports rest.
                        .onDisappear {
                            scrolledRange = nil
                            cards.scrollsRemoved()
                        }
                        .onChange(of: model.visibleWindowBlocks.isEmpty) {
                            scrollToFirstBlock(proxy)
                        }
                        .onChange(of: model.loadedRange) {
                            scrollToFirstBlock(proxy)
                        }
                        .onChange(of: model.visibleRange) {
                            scrollToFirstBlock(proxy)
                        }
                    }
                }
            }
        }
        .onAppear { position.scrollTo(id: model.visibleRange.start, anchor: .leading) }
        .onChange(of: model.visibleRange) { _, range in
            // Keyboard, chevrons and mini-month clicks still move the
            // anchor; the scroll animates to the new leading target.
            defer { settledStart = nil }
            guard range.start != settledStart else { return }
            withAnimation(reduceMotion ? nil : .snappy) {
                position.scrollTo(id: range.start, anchor: .leading)
            }
        }
    }

    /// The scroll came to rest: its leading day becomes the anchor, and
    /// every header/total/cache decision follows. `position` is never
    /// read in the body, so the days a swipe passes redraw nothing. A
    /// swipe never moves the vertical hour, so the new range counts as
    /// scrolled already.
    ///
    /// Landing re-centres the render days around the new anchor, and the
    /// scroll view keeps its pixel offset when its content changes, so
    /// left alone the resting day slid a few columns either way (let go
    /// on the 7th, end up on the 4th or the 10th). The same update puts
    /// the offset back on the resting day's new column.
    private func settle(offset: CGFloat, columnWidth: CGFloat) {
        guard let day = model.settledLeadingDay(offset: Double(offset),
                                                columnWidth: Double(columnWidth))
        else { return }
        let before = model.visibleRange.start
        model.land(on: day)
        scrolledRange = model.visibleRange
        let start = model.visibleRange.start
        guard start != before,
              let x = model.leadingOffset(of: start, columnWidth: Double(columnWidth))
        else { return }
        settledStart = start
        withTransaction(Transaction(animation: nil)) {
            position.scrollTo(x: CGFloat(x))
        }
    }

    /// Day columns grouped into snap targets: one view per day for Day
    /// and N-day, one per week for Week so a swipe lands whole weeks.
    private func columns(now: Date, width: CGFloat) -> some View {
        let multiAccount = multiAccountHarnesses
        return HStack(spacing: 0) {
            if snapsByWeek {
                ForEach(model.renderDays.chunked(by: span), id: \.first!.start) { week in
                    HStack(spacing: 0) {
                        ForEach(week, id: \.start) { day in
                            dayColumn(day, now: now, width: width, multiAccount: multiAccount)
                        }
                    }
                    .id(week.first!.start)
                }
            } else {
                ForEach(model.renderDays, id: \.start) { day in
                    dayColumn(day, now: now, width: width, multiAccount: multiAccount)
                        .id(day.start)
                }
            }
        }
        .scrollTargetLayout()
    }

    private func dayColumn(_ day: DateInterval, now: Date, width: CGFloat,
                           multiAccount: Set<String>) -> some View {
        DayColumn(model: model, day: day, now: now,
                  multiAccount: multiAccount,
                  resets: resets(on: day),
                  cards: cards,
                  color: { self.color(for: $0) },
                  resetColor: { self.resetColor($0) },
                  resetLabel: { self.resetLabel($0) },
                  title: { self.title(for: $0) },
                  cardRows: { self.cardRows(for: $0) },
                  open: { self.open($0) })
        .equatable()
        .frame(width: width)
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
                        .font(Theme.font(size: 9)).foregroundStyle(Theme.textSecondary)
                        .frame(width: Self.weekNumberWidth, alignment: .leading)
                        .padding(.top, 4)
                    ForEach(weeks[row], id: \.self) { day in
                        MonthCell(model: model, day: day,
                                  items: monthItems(on: day),
                                  cards: cards,
                                  color: { self.color(for: $0) },
                                  resetColor: { self.resetColor($0) },
                                  resetLabel: { self.resetLabel($0) },
                                  title: { self.title(for: $0) },
                                  cardRows: { self.cardRows(for: $0) }) { block in
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

    /// The day's rows after the legend's hidden toggles apply: session
    /// entries plus the shown accounts' resets, earliest first.
    private func monthItems(on day: Date) -> [MonthItem] {
        let interval = DateInterval(
            start: day,
            end: model.calendar.date(byAdding: .day, value: 1, to: day) ?? day)
        let entries = MonthLayout.entries(model.visibleBlocks, day: interval).map(MonthItem.block)
        let markers = resets(on: interval).map(MonthItem.reset)
        return (entries + markers).sorted { $0.start != $1.start ? $0.start < $1.start : $0.id < $1.id }
    }

    /// Accounts with any usage window to list; a kept stale reading
    /// still counts, since its windows show as last-known.
    private var usageCards: [AccountsModel.Card] {
        app.starredAccountCards.filter { card in
            card.usage.map { !$0.windows.isEmpty } ?? false
        }
    }

    /// The resets the grid draws: every scheduled reset of every account
    /// and window the Usage legend has ticked.
    private var usageResets: [UsageReset] {
        usageCards.flatMap { card in
            model.isUsageHidden(card.id) ? [] : card.usageResets.filter {
                !model.isUsageHidden(card.id, window: $0.window)
            }
        }
    }

    /// The resets landing inside one rendered day, earliest first.
    private func resets(on day: DateInterval) -> [UsageReset] {
        usageResets.filter { $0.at >= day.start && $0.at < day.end }
            .sorted { $0.at != $1.at ? $0.at < $1.at : $0.id < $1.id }
    }

    /// The marker's colour: the account's own profile colour, so two
    /// accounts of one vendor mark different lines.
    private func resetColor(_ reset: UsageReset) -> Color {
        usageCards.first { $0.id == reset.account }
            .map { Color(hex: $0.profile.color) } ?? Color(rgb: HarnessColor.of(reset.harness))
    }

    /// The marker's tag: just the window while one account shows ("5h
    /// reset"), the account's name too once several do.
    private func resetLabel(_ reset: UsageReset) -> String {
        Set(usageResets.map(\.account)).count > 1
            ? "\(reset.accountName) · \(reset.window)" : "\(reset.window) reset"
    }

    /// The fixed hour axis: inside the same vertical scroll as the day
    /// columns so labels and grid lines can never drift apart, and
    /// outside the horizontal scroll so it stays pinned while the
    /// columns slide underneath it.
    private func hourAxis(now: Date) -> some View {
        // Only the visible window's "today" gets the pill, not a buffer
        // day's.
        let nowOffset = model.days.first(where: { $0.contains(now) })
            .map { Self.hourHeight * CGFloat(now.timeIntervalSince($0.start) / 3600) }
        let scale = Theme.scaled(9) / 9
        return ZStack(alignment: .topLeading) {
            VStack(spacing: 0) {
                ForEach(0..<24, id: \.self) { hour in
                    Text(hourLabel(hour))
                        .font(Theme.font(size: 9)).foregroundStyle(Theme.textSecondary)
                        .frame(width: Self.axisWidth - 8, height: Self.hourHeight,
                               alignment: .topTrailing)
                        // The pill is narrower than the gutter, so a label
                        // it half-covers would show its tail beside it.
                        .opacity(nowOffset.map {
                            CalendarHourAxis.pillCovers(hour: hour, nowOffset: $0, hourHeight: Self.hourHeight,
                                                        scale: scale)
                        } == true ? 0 : 1)
                        .id("h\(hour)")
                }
            }
            if let nowOffset {
                // The axis's own 24-hour clock, not the locale's: a 12-hour
                // Mac read "1:05 PM" beside a "13:00" axis.
                Text(Self.hourLabel.string(from: now))
                    .font(Theme.font(size: 9, weight: .medium))
                    .foregroundStyle(Theme.calendarNowText)
                    .padding(.horizontal, 5).padding(.vertical, 2)
                    .background(Theme.calendarNow, in: Capsule())
                    .offset(y: nowOffset - CalendarHourAxis.pillAbove * scale)
            }
        }
        .frame(width: Self.axisWidth)
    }

    /// Scroll to the range's earliest block (or 08:00 when it is empty),
    /// once per visible range. Until the load finishes an empty range
    /// stays at the top, since later blocks may still arrive.
    private func scrollToFirstBlock(_ proxy: ScrollViewProxy) {
        // Kept blocks from outside the window must not mark it scrolled:
        // only the window's own blocks or a finished load of this exact
        // render range settle it.
        guard scrolledRange != model.visibleRange,
              !model.visibleWindowBlocks.isEmpty || model.loadedRange == model.renderRange
        else { return }
        let earliest = model.visibleWindowBlocks.map(\.start).min()
        let hour = earliest.map { model.calendar.component(.hour, from: $0) } ?? 8
        proxy.scrollTo("h\(max(0, min(23, hour)))", anchor: .top)
        scrolledRange = model.visibleRange
    }

    private func hourLabel(_ hour: Int) -> String {
        var components = DateComponents(); components.hour = hour
        let date = model.calendar.date(from: components) ?? Date()
        return Self.hourLabel.string(from: date)
    }

    /// The session's row, or the index's stand-in when the session is
    /// gone from the listing: its detail sheet still opens.
    private func session(for ref: ActivityBlock.SessionRef,
                         in block: ActivityBlock) -> SessionsModel.Row {
        sessionIndex.row(for: ref, harness: block.harness, accountHome: block.accountHome)
    }

    /// A block's title is its project; sessions show inside its sheet.
    private func title(for block: ActivityBlock) -> String {
        block.project
    }

    /// The card's session list: earliest first, capped at eight rows.
    private func cardRows(for block: ActivityBlock)
        -> (shown: [(ref: ActivityBlock.SessionRef, row: SessionsModel.Row)], more: Int) {
        sessionIndex.rows(for: block, limit: 8)
    }

    /// One session opens its detail straight away; several open the
    /// project's session list first. Either way the hover card goes.
    private func open(_ block: ActivityBlock) {
        cards.dismiss()
        if block.sessions.count == 1, let ref = block.sessions.first {
            detail = session(for: ref, in: block)
        } else {
            projectDetail = block
        }
    }

    private func color(for block: ActivityBlock) -> Color {
        Color(rgb: HarnessColor.of(block.harness))
    }
}

/// The columns' horizontal scroll offset, mirrored on the day header so
/// it slides in lockstep. Held in an object rather than view state so a
/// tick only invalidates the strip that reads `x`, not the grid.
@Observable
private final class HeaderOffset {
    var x: CGFloat = 0
}

/// The header row sits outside the scroll view so it stays pinned
/// vertically; the columns' scroll offset slides it in lockstep. The
/// strip inside is the full render width, so the container gets the
/// grid's width explicitly and clips what hangs past it.
private struct DayHeaderStrip: View {
    var days: [DateInterval]
    var columnWidth: CGFloat
    var gridWidth: CGFloat
    var axisWidth: CGFloat
    var offset: HeaderOffset
    var now: Date

    var body: some View {
        HStack(spacing: 0) {
            Color.clear.frame(width: axisWidth, height: 0)
            ZStack(alignment: .leading) {
                // The row never reads the offset, so a scroll frame
                // re-runs only the slide below, not 27 formatted headers.
                DayHeaderRow(days: days, columnWidth: columnWidth, now: now)
                    .equatable()
                    .modifier(HeaderSlide(offset: offset))
            }
            .frame(width: gridWidth, alignment: .leading)
            .clipped()
        }
        .padding(.vertical, 8)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Theme.border).frame(height: 1)
        }
    }
}

/// Slides the header row by the columns' scroll offset; the only view
/// that reads it.
private struct HeaderSlide: ViewModifier {
    var offset: HeaderOffset
    func body(content: Content) -> some View {
        content.offset(x: -offset.x)
    }
}

/// The day headers in one row.
private struct DayHeaderRow: View, Equatable {
    var days: [DateInterval]
    var columnWidth: CGFloat
    var now: Date

    /// `now` is read fresh on every page render; only which day it falls
    /// on changes the row.
    nonisolated static func == (lhs: DayHeaderRow, rhs: DayHeaderRow) -> Bool {
        lhs.days == rhs.days && lhs.columnWidth == rhs.columnWidth
            && lhs.days.firstIndex { $0.contains(lhs.now) }
                == rhs.days.firstIndex { $0.contains(rhs.now) }
    }

    var body: some View {
        HStack(spacing: 0) {
            ForEach(days, id: \.start) { day in
                dayHeader(day, width: columnWidth)
            }
        }
    }

    /// One day's header: "Mon 12" side by side when the column is wide,
    /// the weekday over the day number when it's tight, a one-letter
    /// weekday when it's very narrow. Today's red pill works in all three.
    @ViewBuilder
    private func dayHeader(_ day: DateInterval, width: CGFloat) -> some View {
        let isToday = day.contains(now)
        let number = Text(day.start.formatted(.dateTime.day()))
            .font(Theme.font(size: 12, weight: isToday ? .semibold : .regular))
            .foregroundStyle(isToday ? Theme.calendarNowText : .primary)
            .lineLimit(1)
            .padding(.horizontal, 5).padding(.vertical, 2)
            .background(isToday ? Theme.calendarNow : .clear,
                        in: RoundedRectangle(cornerRadius: 5))
        switch DayHeaderStyle.forWidth(width) {
        case .inline:
            HStack(spacing: 5) {
                Text(day.start.formatted(.dateTime.weekday(.abbreviated)))
                    .lineLimit(1)
                number
            }
            .font(Theme.caption)
            .padding(.leading, 8)
            .frame(width: width, alignment: .leading)
        case .stacked:
            VStack(spacing: 1) {
                Text(day.start.formatted(.dateTime.weekday(.abbreviated)))
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .lineLimit(1)
                number
            }
            .frame(width: width)
        case .compact:
            VStack(spacing: 1) {
                Text(day.start.formatted(.dateTime.weekday(.narrow)))
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .lineLimit(1)
                number
            }
            .frame(width: width)
        }
    }
}

/// One day column: hour lines and the day's segments laid out in their
/// overlap lanes, with the red now-line when the column is today.
private struct DayColumn: View, Equatable {
    var model: CalendarModel
    var day: DateInterval
    var now: Date
    /// Harnesses carrying more than one account in the window; the page
    /// hands down one set so each segment need not recount accounts.
    var multiAccount: Set<String>
    /// The shown accounts' limit resets inside the day: dashed markers
    /// drawn over the blocks.
    var resets: [UsageReset]
    /// The page-level hover card each block and chip anchors.
    var cards: HoverCards
    var color: (ActivityBlock) -> Color
    var resetColor: (UsageReset) -> Color
    var resetLabel: (UsageReset) -> String
    var title: (ActivityBlock) -> String
    /// The capped session list a block's card shows.
    var cardRows: (ActivityBlock)
        -> (shown: [(ref: ActivityBlock.SessionRef, row: SessionsModel.Row)], more: Int)
    var open: (ActivityBlock) -> Void
    /// The canvas reads its fonts at draw time, not in `body`, so a zoom
    /// change reaches the column only through this and `==`.
    var zoom = TextZoomModel.shared.zoom

    /// Redraw only when the column's own inputs move. The closures are
    /// rebuilt on every page render and would otherwise redraw (and
    /// re-lay-out) every column each time; they read live page state, so
    /// skipping them loses nothing. Block and legend changes still reach
    /// the column through its own observation of its day's slice and the
    /// legend's hidden sets. The minute
    /// tick only matters to the column holding the now-line.
    nonisolated static func == (lhs: DayColumn, rhs: DayColumn) -> Bool {
        lhs.model === rhs.model && lhs.cards === rhs.cards
            && lhs.day == rhs.day && lhs.zoom == rhs.zoom && lhs.multiAccount == rhs.multiAccount
            && lhs.resets == rhs.resets
            && (lhs.now == rhs.now
                || !(lhs.day.contains(lhs.now) || rhs.day.contains(rhs.now)))
    }

    private static let clock: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f
    }()
    /// A lane narrower than this is unreadable; extras fold into "+N".
    static let minimumLaneWidth: CGFloat = 56
    private let hourHeight: CGFloat = 48
    private var gridHeight: CGFloat { 24 * hourHeight }

    /// The hour lines, blocks, "+N" chips and now-line are drawn in one
    /// canvas instead of a stack of views per block: scrolling then moves
    /// one layer per column, with nothing for SwiftUI to lay out or hit
    /// test per block on every frame. One interactive layer on top finds
    /// the block under the pointer through `DayColumnGeometry`.
    var body: some View {
        GeometryReader { geo in
            // 18pt of minimum height at 48pt per hour is 22.5 minutes:
            // short blocks get a lane and a readable sliver of height.
            // Only this day's slice is read, so a new render range
            // redraws the columns whose blocks changed and no others.
            let layout = CalendarLayout.layout(
                model.blocks(on: day).blocks.filter(model.isShown), day: day,
                minimumDuration: 18 / 48 * 3600,
                maxLanes: Swift.max(2, Int(geo.size.width / Self.minimumLaneWidth)))
            let geometry = DayColumnGeometry(layout: layout, day: day, calendar: model.calendar,
                                             width: geo.size.width, hourHeight: hourHeight)
            ZStack(alignment: .topLeading) {
                Canvas { context, size in draw(geometry, in: &context, size: size) }
                DayColumnHits(geometry: geometry, cards: cards,
                              tap: activate) { item in card(for: item) }
            }
            .accessibilityElement(children: .contain)
            .accessibilityChildren {
                // VoiceOver still reaches and opens every block.
                ForEach(geometry.items) { item in
                    Color.clear
                        .frame(width: item.frame.width, height: item.frame.height)
                        .position(x: item.frame.midX, y: item.frame.midY)
                        .accessibilityElement()
                        .accessibilityLabel(accessibilityLabel(for: item))
                        .accessibilityAddTraits(.isButton)
                        .accessibilityAction { activate(item) }
                }
                ForEach(resets) { reset in
                    Color.clear
                        .frame(width: geo.size.width, height: 9)
                        .position(x: geo.size.width / 2, y: geometry.y(reset.at))
                        .accessibilityElement()
                        .accessibilityLabel("\(reset.title), \(reset.window) limit resets at "
                                            + Self.clock.string(from: reset.at))
                }
            }
        }
        .frame(height: gridHeight)
        .overlay(alignment: .leading) {
            Rectangle().fill(Theme.border).frame(width: 1)
        }
    }

    /// A block opens its session (or project sheet); a chip opens its
    /// card at once, since the hover delay only exists so a passing
    /// pointer never pops one.
    private func activate(_ item: DayColumnGeometry.Item) {
        switch item.kind {
        case .segment(let segment): open(segment.block)
        case .overflow: cards.open(item.id)
        }
    }

    @ViewBuilder
    private func card(for item: DayColumnGeometry.Item) -> some View {
        switch item.kind {
        case .segment(let segment):
            let block = segment.block
            let rows = cardRows(block)
            BlockCard(block: block, model: model,
                      active: block.end.timeIntervalSince(block.start),
                      shown: rows.shown, more: rows.more)
        case .overflow(let overflow):
            OverflowCard(overflow: overflow, model: model, cardRows: cardRows, open: open)
        }
    }

    private func accessibilityLabel(for item: DayColumnGeometry.Item) -> String {
        switch item.kind {
        case .segment(let segment):
            return "\(HarnessMark(segment.block.harness).name), \(title(segment.block)), "
                + CalendarFormat.range(start: segment.block.start, end: segment.block.end)
        case .overflow(let overflow):
            return "\(overflow.hidden.count) more blocks, "
                + "\(Self.clock.string(from: overflow.start)) to \(Self.clock.string(from: overflow.end))"
        }
    }

    // MARK: Drawing

    private func draw(_ geometry: DayColumnGeometry, in context: inout GraphicsContext,
                      size: CGSize) {
        for hour in 0..<24 {
            context.fill(Path(CGRect(x: 0, y: CGFloat(hour) * hourHeight,
                                     width: size.width, height: 1)),
                         with: .color(Theme.border.opacity(0.5)))
        }
        for item in geometry.items {
            switch item.kind {
            case .segment(let segment): drawBlock(segment, in: item.frame, context: context)
            case .overflow(let overflow): drawChip(overflow, in: item.frame, context: context)
            }
        }
        // A reset marker: a dashed line at its minute with a small tag
        // under it, over the blocks — a marker buried under a busy hour
        // tells nothing. Markers that land close stagger their tags.
        var tagY: CGFloat = -.infinity
        for reset in resets {
            let y = geometry.y(reset.at)
            let color = resetColor(reset).opacity(reset.isStale ? 0.45 : 0.9)
            context.stroke(Path { path in
                path.move(to: CGPoint(x: 0, y: y))
                path.addLine(to: CGPoint(x: size.width, y: y))
            }, with: .color(color), style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
            tagY = max(y + 2, tagY)
            if let tag = Self.fitted(resetLabel(reset), font: Theme.font(size: 8), color: color,
                                     width: size.width - 4, in: context) {
                context.draw(tag, at: CGPoint(x: 3, y: tagY), anchor: .topLeading)
                tagY += 9
            }
        }
        if day.contains(now) {
            context.fill(Path(CGRect(x: 0, y: geometry.y(now), width: size.width, height: 1.5)),
                         with: .color(Theme.calendarNow))
        }
    }

    /// One activity block: rounded fill in the harness colour at low
    /// opacity, a full-colour left bar, the project name and, when it is
    /// tall enough, its time range, centred down the block.
    private func drawBlock(_ segment: CalendarLayout.Segment, in frame: CGRect,
                           context: GraphicsContext) {
        let color = self.color(segment.block)
        context.fill(Path(roundedRect: frame, cornerRadius: 4),
                     with: .color(color.opacity(0.15)))
        context.fill(Path(roundedRect: CGRect(x: frame.minX, y: frame.minY + 1.5, width: 3,
                                              height: max(0, frame.height - 3)),
                          cornerRadius: 1.5),
                     with: .color(color))
        // 3pt bar, then 5pt of padding each side of the text.
        let textWidth = frame.width - 13
        var lines: [GraphicsContext.ResolvedText] = []
        if let title = Self.fitted(title(segment.block), font: Theme.font(size: 10, weight: .medium),
                                   color: nil, width: textWidth, in: context) {
            lines.append(title)
        }
        let unbounded = CGSize(width: CGFloat.greatestFiniteMagnitude,
                               height: .greatestFiniteMagnitude)
        if segment.end.timeIntervalSince(segment.start) >= 30 * 60 {
            let block = segment.block
            let range = CalendarFormat.range(start: block.start, end: block.end)
                + (multiAccount.contains(block.harness)
                   ? " · \(model.accountName(for: block.accountHome))" : "")
            // Zoomed in, a short block may hold the title only.
            if let detail = Self.fitted(range, font: Theme.font(size: 9), color: Theme.textSecondary,
                                        width: textWidth, in: context),
               (lines + [detail]).map({ $0.measure(in: unbounded).height }).reduce(0, +) <= frame.height {
                lines.append(detail)
            }
        }
        let heights = lines.map { $0.measure(in: unbounded).height }
        var y = frame.midY - heights.reduce(0, +) / 2
        var clipped = context
        clipped.clip(to: Path(frame))
        for (line, height) in zip(lines, heights) {
            clipped.draw(line, at: CGPoint(x: frame.minX + 8, y: y), anchor: .topLeading)
            y += height
        }
    }

    /// The "+N" chip in a crowded column's last lane: a subtle fill, a
    /// dashed border and the hidden count centred at its top.
    private func drawChip(_ overflow: CalendarLayout.Overflow, in frame: CGRect,
                          context: GraphicsContext) {
        context.fill(Path(roundedRect: frame, cornerRadius: 4), with: .color(Theme.rowHover))
        context.stroke(Path(roundedRect: frame.insetBy(dx: 0.5, dy: 0.5), cornerRadius: 3.5),
                       with: .color(Theme.textSecondary),
                       style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
        let count = context.resolve(Text("+\(overflow.hidden.count)")
            .font(Theme.font(size: 11, weight: .semibold)))
        context.draw(count, at: CGPoint(x: frame.midX, y: frame.minY + 4), anchor: .top)
    }

    /// One line of text cut to fit `width` with a trailing "…", as
    /// `lineLimit(1)` would. nil when there is no room at all.
    private static func fitted(_ string: String, font: Font, color: Color?, width: CGFloat,
                               in context: GraphicsContext) -> GraphicsContext.ResolvedText? {
        guard width > 0, !string.isEmpty else { return nil }
        func resolve(_ text: String) -> GraphicsContext.ResolvedText {
            var styled = Text(text).font(font)
            if let color { styled = styled.foregroundStyle(color) }
            return context.resolve(styled)
        }
        let unbounded = CGSize(width: CGFloat.greatestFiniteMagnitude,
                               height: .greatestFiniteMagnitude)
        let full = resolve(string)
        if full.measure(in: unbounded).width <= width { return full }
        let characters = Array(string)
        var low = 0, high = characters.count - 1
        while low < high {
            let mid = (low + high + 1) / 2
            if resolve(String(characters[..<mid]) + "…").measure(in: unbounded).width <= width {
                low = mid
            } else {
                high = mid - 1
            }
        }
        return resolve(String(characters[..<low]) + "…")
    }
}

/// A day column's one interactive layer. Hover and taps find their block
/// or chip through the column's geometry, and only the block whose card
/// is open gets a view, to anchor its popover.
private struct DayColumnHits<Card: View>: View {
    var geometry: DayColumnGeometry
    var cards: HoverCards
    var tap: (DayColumnGeometry.Item) -> Void
    @ViewBuilder var card: (DayColumnGeometry.Item) -> Card
    /// The item under the pointer, so enter and exit reach the hover
    /// card like the per-block `.onHover` they replace.
    @State private var hovered: String?

    var body: some View {
        // Read here, so a card opening redraws these layers alone.
        let open = geometry.items.first { $0.id == cards.key }
        Color.clear
            .contentShape(.rect)
            .onContinuousHover { phase in
                let under: String?
                switch phase {
                case .active(let point): under = geometry.item(at: point)?.id
                case .ended: under = nil
                }
                guard under != hovered else { return }
                if let hovered { cards.hover(hovered, inside: false) }
                hovered = under
                if let under { cards.hover(under, inside: true) }
            }
            .onTapGesture { point in
                if let item = geometry.item(at: point) { tap(item) }
            }
            .overlay(alignment: .topLeading) {
                if let open {
                    CardAnchor(cards: cards, key: open.id, frame: open.frame) { card(open) }
                        .id(open.id)
                }
            }
    }
}

/// The open card's block, as a view: it reports the block's screen rect
/// to the card poller and anchors the popover at the block.
private struct CardAnchor<Card: View>: View {
    var cards: HoverCards
    var key: String
    var frame: CGRect
    @ViewBuilder var card: () -> Card

    var body: some View {
        Color.clear
            .frame(width: frame.width, height: frame.height)
            .background(HoverRegionView { [cards, key] view, attached in
                cards.geometry.register(chip: key, view: view, attached: attached)
            })
            .popover(isPresented: Binding(get: { true },
                                          set: { [cards, key] in if !$0 { cards.closed(key) } }),
                     attachmentAnchor: .rect(.bounds),
                     arrowEdge: .leading) {
                card()
                    .background(HoverRegionView { [cards, key] view, attached in
                        cards.geometry.register(card: key, view: view, attached: attached)
                    })
            }
            // Padding, not .offset: only the layout position gives the
            // popover the block's real frame.
            .padding(.leading, frame.minX)
            .padding(.top, frame.minY)
            .allowsHitTesting(false)
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

    /// In-range day-cell runs for the highlight; month mode lights none.
    private var segments: [(row: Int, firstColumn: Int, lastColumn: Int)] {
        guard !model.mode.isMonth else { return [] }
        return MiniMonthHighlight.segments(weeks: weeks, range: model.visibleRange,
                                           calendar: model.calendar)
    }

    var body: some View {
        let cal = model.calendar
        VStack(spacing: 4) {
            HStack {
                Text(Self.monthTitle.string(from: month))
                    .font(Theme.font(size: 11, weight: .semibold))
                Spacer()
                Button { shiftMonth(-1) } label: { Image(systemName: "chevron.up") }
                    .buttonStyle(.plain).foregroundStyle(Theme.textSecondary)
                    .accessibilityLabel("Previous month")
                Button { shiftMonth(1) } label: { Image(systemName: "chevron.down") }
                    .buttonStyle(.plain).foregroundStyle(Theme.textSecondary)
                    .accessibilityLabel("Next month")
            }
            // Weekday initials above a muted ISO-week column.
            HStack(spacing: 0) {
                Text("").frame(width: 16)
                ForEach(weeks.first ?? [], id: \.self) { day in
                    Text(day.formatted(.dateTime.weekday(.narrow)))
                        .font(Theme.font(size: 8)).foregroundStyle(Theme.textSecondary)
                        .frame(maxWidth: .infinity)
                }
            }
            ForEach(Array(weeks.enumerated()), id: \.offset) { row, week in
                HStack(spacing: 0) {
                    Text("\(iso.component(.weekOfYear, from: week[0]))")
                        .font(Theme.font(size: 8)).foregroundStyle(Theme.textSecondary)
                        .frame(width: 16, alignment: .leading)
                    ForEach(week, id: \.self) { day in
                        let inMonth = cal.isDate(day, equalTo: month, toGranularity: .month)
                        let isToday = cal.isDateInToday(day)
                        Button { model.go(to: day) } label: {
                            Text(day.formatted(.dateTime.day()))
                                .font(Theme.font(size: 9))
                                .foregroundStyle(isToday ? Theme.calendarNowText : (inMonth ? .primary : Theme.textMuted))
                                .frame(maxWidth: .infinity).frame(height: 16)
                                .background(isToday ? Theme.calendarNow : .clear, in: Circle())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.vertical, 1)
                .background(alignment: .leading) {
                    // A pill behind just the in-range day cells; the
                    // 16pt week column is never lit.
                    GeometryReader { geo in
                        if let segment = segments.first(where: { $0.row == row }) {
                            let cellWidth = (geo.size.width - 16) / 7
                            RoundedRectangle(cornerRadius: 4)
                                .fill(Theme.border.opacity(0.5))
                                .frame(width: cellWidth * CGFloat(segment.lastColumn - segment.firstColumn + 1))
                                .offset(x: 16 + cellWidth * CGFloat(segment.firstColumn))
                        }
                    }
                }
            }
        }
        .onChange(of: model.anchor) { month = model.anchor }
    }

    private func shiftMonth(_ count: Int) {
        month = model.calendar.date(byAdding: .month, value: count, to: month) ?? month
    }
}

/// The Notion-style checkbox both legends share: a filled square with a
/// white check when shown, an outlined square when hidden.
private struct LegendCheckbox: View {
    var shown: Bool
    var color: Color

    var body: some View {
        RoundedRectangle(cornerRadius: 3)
            .fill(shown ? color : .clear)
            .overlay {
                if shown {
                    Image(systemName: "checkmark")
                        .font(Theme.font(size: 9, weight: .bold))
                        .foregroundStyle(.white)
                }
            }
            .overlay(RoundedRectangle(cornerRadius: 3)
                .stroke(color, lineWidth: shown ? 0 : 1.5))
            .frame(width: 14, height: 14)
    }
}

/// A full-width toggle row: the content shape and hover wash cover the
/// name and time too, not just the checkbox.
private struct LegendToggle<Label: View>: View {
    var action: () -> Void
    @ViewBuilder var label: () -> Label

    var body: some View {
        Button(action: action) {
            label()
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(.rect)
        }
        .buttonStyle(LegendRowStyle())
    }
}

/// One legend row: a colour checkbox, the harness mark and title, and its
/// total time in the range, the whole row toggling visibility like
/// Notion's calendar list. Account rows sit under it, expanded by
/// default once the harness has more than one account.
private struct HarnessLegend: View {
    var model: CalendarModel
    var harness: String

    private var color: Color {
        Color(rgb: HarnessColor.of(harness))
    }

    private var accounts: [String] { model.accounts(for: harness) }
    private var expanded: Bool { model.accountsExpanded(harness) }

    var body: some View {
        HStack(spacing: 0) {
            if accounts.count > 1 {
                Button { model.toggleAccountsExpanded(harness) } label: {
                    Image(systemName: expanded ? "chevron.down" : "chevron.right")
                        .font(Theme.font(size: 8)).foregroundStyle(Theme.textSecondary)
                        .frame(width: 20, height: 20)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .help(expanded ? "Collapse accounts" : "Expand accounts")
                .accessibilityLabel(expanded ? "Collapse \(HarnessMark(harness).name) accounts"
                                           : "Expand \(HarnessMark(harness).name) accounts")
            } else {
                Spacer().frame(width: 10)
            }
            LegendToggle(action: { model.toggle(harness) }) {
                HStack(spacing: 6) {
                    LegendCheckbox(shown: !model.isHidden(harness), color: color)
                    HarnessIcon(harness: harness, size: 13)
                    Text(HarnessMark(harness).name).font(Theme.font(size: 11))
                        .foregroundStyle(model.isHidden(harness) ? .secondary : .primary)
                    Spacer()
                    Text(Self.time(model.totals[harness] ?? 0))
                        .font(Theme.font(size: 10)).foregroundStyle(Theme.textSecondary)
                }
            }
            .help(model.isHidden(harness) ? "Show \(HarnessMark(harness).name)" : "Hide \(HarnessMark(harness).name)")
            .accessibilityLabel("\(HarnessMark(harness).name), \(model.isHidden(harness) ? "hidden" : "shown")")
            .accessibilityAddTraits(.isToggle)
            .accessibilityValue(model.isHidden(harness) ? "0" : "1")
        }
        .frame(height: 22)
        if expanded {
            ForEach(accounts, id: \.self) { home in
                let shown = !model.isHidden(harness: harness, accountHome: home)
                LegendToggle(action: { model.toggle(harness: harness, accountHome: home) }) {
                    HStack(spacing: 6) {
                        LegendCheckbox(shown: shown, color: color)
                        Text(model.accountName(for: home))
                            .font(Theme.font(size: 10))
                            .foregroundStyle(shown ? .primary : .secondary)
                            .lineLimit(1)
                        Spacer()
                        Text(Self.time(model.accountTotals(for: harness)[home] ?? 0))
                            .font(Theme.font(size: 9)).foregroundStyle(Theme.textSecondary)
                    }
                    .padding(.leading, 18)
                }
                .help(shown ? "Hide \(model.accountName(for: home))"
                            : "Show \(model.accountName(for: home))")
                .accessibilityLabel("\(model.accountName(for: home)), \(shown ? "shown" : "hidden")")
                .accessibilityAddTraits(.isToggle)
                .accessibilityValue(shown ? "1" : "0")
                .frame(height: 20)
            }
        }
    }

    private static func time(_ seconds: TimeInterval) -> String {
        CalendarFormat.duration(seconds)
    }
}

/// One account in the Usage section: the same row the Harnesses list
/// draws — a colour checkbox, the vendor mark and title, and the
/// tightest window's remaining share as its figure. More than one
/// window lists them below, expanded until the user folds them away.
private struct UsageLegend: View {
    var model: CalendarModel
    var card: AccountsModel.Card

    private var color: Color { Color(hex: card.profile.color) }
    private var hidden: Bool { model.isUsageHidden(card.id) }
    private var usage: AccountUsage? { card.usage }
    private var windows: [UsageWindow] { usage?.windows ?? [] }
    private var expanded: Bool {
        model.usageWindowsExpanded(card.id, windows: windows.count)
    }
    private var failed: Bool {
        usage.map { $0.isStale && $0.status != .ok } ?? false
    }
    /// A single window has no row of its own; its figure folds into the
    /// account's instead.
    private var solo: UsageWindow? { windows.count == 1 ? windows[0] : nil }

    /// The right-hand figure, like a harness row's range total: the
    /// least anyone has left in any window.
    private var tightest: Int {
        windows.map { max(0, 100 - Int($0.usedPercent.rounded())) }.min() ?? 0
    }

    var body: some View {
        // The countdowns and the reset-passed dimming both depend on
        // now; the grid's own minute ticker can't reach the panel.
        TimelineView(.everyMinute) { context in
            accountRow(now: context.date)
            if expanded, let usage {
                ForEach(usage.windows, id: \.label) { window in
                    windowRow(window, usage: usage, now: context.date)
                }
            }
        }
    }

    @ViewBuilder
    private func accountRow(now: Date) -> some View {
        let pastSolo = solo?.resetsAt.map { $0 <= now } ?? false
        HStack(spacing: 0) {
            if windows.count > 1 {
                Button { model.toggleUsageWindowsExpanded(card.id) } label: {
                    Image(systemName: expanded ? "chevron.down" : "chevron.right")
                        .font(Theme.font(size: 8)).foregroundStyle(Theme.textSecondary)
                        .frame(width: 20, height: 20)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .help(expanded ? "Collapse windows" : "Expand windows")
                .accessibilityLabel(expanded ? "Collapse \(card.usageTitle) windows"
                                           : "Expand \(card.usageTitle) windows")
            } else {
                Spacer().frame(width: 10)
            }
            LegendToggle(action: { model.toggleUsage(card.id) }) {
                HStack(spacing: 6) {
                    LegendCheckbox(shown: !hidden, color: color)
                    HarnessIcon(harness: card.profile.vendor.rawValue, size: 13)
                    Text(card.usageTitle).font(Theme.font(size: 11))
                        .foregroundStyle(solo != nil && failed ? Theme.dangerText
                                         : (hidden ? .secondary : .primary))
                        .lineLimit(1)
                    Spacer()
                    Text(figure(now: now))
                        .font(Theme.font(size: 10)).foregroundStyle(Theme.textSecondary)
                }
            }
            .help(hidden ? "Show \(card.usageTitle) resets" : "Hide \(card.usageTitle) resets")
            .accessibilityLabel("\(card.usageTitle), \(hidden ? "hidden" : "shown")")
            .accessibilityAddTraits(.isToggle)
            .accessibilityValue(hidden ? "0" : "1")
        }
        .frame(height: 22)
        .opacity(solo != nil && !failed && ((usage?.isStale ?? false) || pastSolo) ? 0.55 : 1)
    }

    /// The account row's figure: one window's own countdown when it
    /// stands alone, else the least anyone has left.
    private func figure(now: Date) -> String {
        guard let solo else { return "\(tightest)% left" }
        return Self.remaining(solo, failed: failed, now: now)
    }

    /// One window's "57% left · in 4h" figure: the countdown drops once
    /// the fetch fails or the moment has already passed.
    private static func remaining(_ window: UsageWindow, failed: Bool, now: Date) -> String {
        let left = max(0, 100 - Int(window.usedPercent.rounded()))
        let past = window.resetsAt.map { $0 <= now } ?? false
        let reset = !failed && !past ? window.resetsAt : nil
        return "\(left)% left" + (reset.map { " · \(UsageFormat.resetIn($0, now: now))" } ?? "")
    }

    /// One window's row: a checkbox toggling its reset marker, its
    /// label, then how much of it is left. A kept older or already-reset
    /// reading dims; a failed fetch reddens the label — the same
    /// vocabulary the Accounts page's UsageBar speaks.
    private func windowRow(_ window: UsageWindow, usage: AccountUsage, now: Date) -> some View {
        let failed = usage.isStale && usage.status != .ok
        let past = window.resetsAt.map { $0 <= now } ?? false
        let shown = !model.isUsageHidden(card.id, window: window.label)
        return LegendToggle(action: { model.toggleUsage(card.id, window: window.label) }) {
            HStack(spacing: 6) {
                LegendCheckbox(shown: shown, color: color)
                Text(window.label).font(Theme.font(size: 10))
                    .foregroundStyle(failed ? Theme.dangerText
                                     : (shown && !hidden ? .primary : .secondary))
                Spacer()
                Text(Self.remaining(window, failed: failed, now: now))
                    .font(Theme.font(size: 9)).foregroundStyle(Theme.textSecondary)
            }
            .padding(.leading, 18)
        }
        .help(shown ? "Hide the \(window.label) reset marker"
                    : "Show the \(window.label) reset marker")
        .accessibilityLabel("\(card.usageTitle), \(window.label), \(shown ? "shown" : "hidden")")
        .accessibilityAddTraits(.isToggle)
        .accessibilityValue(shown ? "1" : "0")
        .frame(height: 20)
        .opacity(!failed && (usage.isStale || past) ? 0.55 : 1)
    }
}

/// A month cell's row: a project block's entry or an account's limit
/// reset, whichever lands first in the day.
private enum MonthItem: Identifiable, Equatable {
    case block(MonthLayout.Entry)
    case reset(UsageReset)

    var id: String {
        switch self {
        case .block(let entry): entry.id
        case .reset(let reset): reset.id
        }
    }

    var start: Date {
        switch self {
        case .block(let entry): entry.start
        case .reset(let reset): reset.at
        }
    }
}

/// One month cell: the day number top-right ("October 1" on a 1st,
/// muted for adjacent-month days, red badge for today), then as many
/// session entries and reset markers as fit, the rest under a "N more"
/// button that opens the day.
private struct MonthCell: View {
    var model: CalendarModel
    var day: Date
    var items: [MonthItem]
    /// The page-level hover card each entry anchors.
    var cards: HoverCards
    var color: (ActivityBlock) -> Color
    var resetColor: (UsageReset) -> Color
    var resetLabel: (UsageReset) -> String
    var title: (ActivityBlock) -> String
    var cardRows: (ActivityBlock)
        -> (shown: [(ref: ActivityBlock.SessionRef, row: SessionsModel.Row)], more: Int)
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
            // MonthLayout.split's rule, widened to both row kinds.
            let shown = items.count <= fitting ? items : Array(items.prefix(max(0, fitting - 1)))
            let moreCount = items.count - shown.count
            VStack(alignment: .leading, spacing: 1) {
                HStack {
                    Spacer(minLength: 0)
                    if isFirst {
                        Text(day, format: .dateTime.month(.wide).day())
                            .font(Theme.font(size: 9, weight: .semibold))
                            .foregroundStyle(isToday ? Theme.calendarNowText : (inMonth ? .primary : Theme.textMuted))
                            .padding(.horizontal, 4).padding(.vertical, 1)
                            .background(isToday ? Theme.calendarNow : .clear,
                                        in: RoundedRectangle(cornerRadius: 4))
                    } else {
                        Text(day, format: .dateTime.day())
                            .font(Theme.font(size: 9))
                            .foregroundStyle(isToday ? Theme.calendarNowText : (inMonth ? .primary : Theme.textMuted))
                            .padding(.horizontal, 4).padding(.vertical, 1)
                            .background(isToday ? Theme.calendarNow : .clear,
                                        in: RoundedRectangle(cornerRadius: 4))
                    }
                }
                .padding(.horizontal, 3).padding(.top, 2)
                ForEach(shown) { item in
                    switch item {
                    case .block(let entry):
                        MonthEntry(entry: entry, model: model,
                                   color: color(entry.block), title: title(entry.block))
                            .onTapGesture { open(entry.block) }
                            .hoverCard(cards, key: entry.id) {
                                let rows = cardRows(entry.block)
                                BlockCard(block: entry.block, model: model,
                                          active: entry.active,
                                          shown: rows.shown, more: rows.more)
                            }
                    case .reset(let reset):
                        MonthReset(reset: reset, color: resetColor(reset),
                                   label: resetLabel(reset))
                            .onTapGesture { more() }
                    }
                }
                if moreCount > 0 {
                    Button { more() } label: {
                        Text("\(moreCount) more")
                            .font(Theme.font(size: 9))
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

/// One reset row in a month cell: a clockwise arrow in the account's
/// colour, then the reset's clock time and window.
private struct MonthReset: View {
    var reset: UsageReset
    var color: Color
    var label: String

    private static let clock: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f
    }()

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "arrow.clockwise")
                .font(Theme.font(size: 8, weight: .semibold))
                .foregroundStyle(color)
                .frame(width: 10)
            Text(Self.clock.string(from: reset.at))
                .font(Theme.font(size: 8)).foregroundStyle(Theme.textSecondary)
            Text(label).font(Theme.font(size: 9)).lineLimit(1)
        }
        .padding(.horizontal, 6)
        .frame(height: 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .opacity(reset.isStale ? 0.55 : 1)
        .accessibilityElement()
        .accessibilityLabel("\(reset.title), \(reset.window) limit resets at "
                            + Self.clock.string(from: reset.at))
    }
}

/// One project row in a month cell: a harness-coloured bar, the first
/// block's start, and the project name on one line.
private struct MonthEntry: View {
    var entry: MonthLayout.Entry
    var model: CalendarModel
    var color: Color
    var title: String

    private static let clock: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f
    }()

    private var active: String { CalendarFormat.duration(entry.active) }

    var body: some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 1.5).fill(color)
                .frame(width: 3, height: 10)
            Text(Self.clock.string(from: entry.start))
                .font(Theme.font(size: 8)).foregroundStyle(Theme.textSecondary)
            Text(title).font(Theme.font(size: 9)).lineLimit(1)
        }
        .padding(.horizontal, 6)
        .frame(height: 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement()
        .accessibilityLabel("\(UsageFormat.harness(entry.block.harness)), \(title), \(active)")
    }
}

/// One session in a block's list: title (or short id), its time span
/// inside the block and its responses. The project sheet and the hover
/// card render identical rows; only the sheet's carry a chevron.
private struct SessionRow: View {
    var ref: ActivityBlock.SessionRef
    var row: SessionsModel.Row
    /// The block's harness: `grok-bot` resolves session titles through
    /// the Bot roster instead of the id fallback.
    var harness: String
    var last: Bool
    /// nil on the read-only hover card.
    var open: (() -> Void)? = nil

    var body: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text(CalendarFormat.sessionTitle(row.session.title,
                                                 sessionId: ref.sessionId,
                                                 harness: harness))
                    .font(Theme.font(size: 11, weight: .medium)).lineLimit(1)
                Text("\(CalendarFormat.range(start: ref.start, end: ref.end)) · "
                     + "\(ref.responses) response\(ref.responses == 1 ? "" : "s")")
                    .font(Theme.font(size: 9)).foregroundStyle(Theme.textSecondary)
                if let parent = row.parent {
                    Label(parent.label, systemImage: "arrow.turn.down.right")
                        .labelStyle(.titleAndIcon)
                        .font(Theme.font(size: 9)).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1)
                }
            }
            Spacer()
            if let open {
                Button { open() } label: {
                    Image(systemName: "chevron.right")
                        .font(Theme.font(size: 10)).foregroundStyle(Theme.textSecondary)
                }
                .buttonStyle(.plain)
                .help("Open session")
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Theme.border).frame(height: 1).opacity(last ? 0 : 1)
        }
    }
}

/// The hover card over a squeezed block: the same information as the
/// project sheet — icon, project, account when the harness has several,
/// range, active time, responses, and the sessions — read-only, capped
/// at eight rows so a long block never fills the screen.
private struct BlockCard: View {
    var block: ActivityBlock
    var model: CalendarModel
    /// The duration line's seconds: the block's span on the week grid,
    /// the day's summed active time on a month entry.
    var active: TimeInterval
    var shown: [(ref: ActivityBlock.SessionRef, row: SessionsModel.Row)]
    var more: Int

    private var range: String {
        CalendarFormat.range(start: block.start, end: block.end)
    }
    private var duration: String { CalendarFormat.duration(active) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                HarnessIcon(harness: HarnessMark(block.harness).icon ?? block.harness, size: 18)
                VStack(alignment: .leading, spacing: 1) {
                    Text(block.project).font(Theme.font(size: 12, weight: .semibold))
                        .lineLimit(1)
                    if model.accounts(for: block.harness).count > 1 {
                        Text(model.accountName(for: block.accountHome))
                            .font(Theme.font(size: 9)).foregroundStyle(Theme.textSecondary)
                    }
                }
                Spacer(minLength: 0)
            }
            Text("\(range) · \(duration) · \(block.responses) response\(block.responses == 1 ? "" : "s")")
                .font(Theme.font(size: 9)).foregroundStyle(Theme.textSecondary)
            VStack(spacing: 0) {
                ForEach(shown, id: \.ref.sessionId) { item in
                    SessionRow(ref: item.ref, row: item.row, harness: block.harness,
                               last: item.ref.sessionId == shown.last?.ref.sessionId)
                }
                if more > 0 {
                    Text("+\(more) more")
                        .font(Theme.font(size: 9)).foregroundStyle(Theme.textSecondary)
                        .padding(.vertical, 5)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 10)
                }
            }
        }
        .padding(12)
        .frame(width: 300, alignment: .leading)
    }
}

/// The chip's card: a list of the hidden blocks on the left, the hovered
/// one's full `BlockCard` on the right. Clicking a row opens the same
/// detail or project sheet a block click would.
private struct OverflowCard: View {
    var overflow: CalendarLayout.Overflow
    var model: CalendarModel
    var cardRows: (ActivityBlock)
        -> (shown: [(ref: ActivityBlock.SessionRef, row: SessionsModel.Row)], more: Int)
    var open: (ActivityBlock) -> Void

    /// The row under the pointer; stays put when the pointer moves to
    /// the right pane, so the detail never flickers.
    @State private var selected: ActivityBlock?

    private var shown: ActivityBlock? {
        // A refresh can swap the hidden list under an open card; fall
        // back to its first row when the pick is gone.
        if let selected,
           let current = overflow.hidden.first(where: { $0.block.id == selected.id }) {
            return current.block
        }
        return overflow.hidden.first?.block
    }

    private static func duration(of block: ActivityBlock) -> String {
        CalendarFormat.duration(block.end.timeIntervalSince(block.start))
    }

    private static func range(of block: ActivityBlock) -> String {
        CalendarFormat.range(start: block.start, end: block.end)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            VStack(alignment: .leading, spacing: 0) {
                Text("\(overflow.hidden.count) more · "
                     + CalendarFormat.range(start: overflow.start,
                                            end: overflow.end))
                    .font(Theme.font(size: 10)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 12).padding(.bottom, 8)
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(overflow.hidden) { segment in
                            row(segment.block)
                        }
                    }
                }
                .frame(maxHeight: 420)
            }
            .frame(width: 250, alignment: .leading)
            .padding(.vertical, 10)
            Divider()
            if let block = shown {
                let rows = cardRows(block)
                BlockCard(block: block, model: model,
                          active: block.end.timeIntervalSince(block.start),
                          shown: rows.shown, more: rows.more)
            }
        }
    }

    private func row(_ block: ActivityBlock) -> some View {
        let isOn = block.id == shown?.id
        return HStack(spacing: 8) {
            HarnessIcon(harness: HarnessMark(block.harness).icon ?? block.harness,
                        size: 14)
            VStack(alignment: .leading, spacing: 2) {
                Text(block.project).font(Theme.font(size: 11, weight: .semibold))
                    .lineLimit(1)
                Text(rowDetail(block))
                    .font(Theme.font(size: 10)).foregroundStyle(Theme.textSecondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12).padding(.vertical, 6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(isOn ? Theme.rowHover : Color.clear)
        .contentShape(.rect)
        .onHover { inside in if inside { selected = block } }
        .onTapGesture { open(block) }
    }

    private func rowDetail(_ block: ActivityBlock) -> String {
        var line = ""
        if model.accounts(for: block.harness).count > 1 {
            line += "\(model.accountName(for: block.accountHome)) · "
        }
        return line + "\(Self.range(of: block)) · \(Self.duration(of: block)) · "
            + "\(block.responses) response\(block.responses == 1 ? "" : "s")"
    }
}

/// The sheet a multi-session block opens: the project's sessions, each
/// with its title (or short id), its time inside the block and its
/// response count; a row's chevron opens the session's own detail.
private struct ProjectSessionsSheet: View {
    var block: ActivityBlock
    var model: CalendarModel
    var rows: [(ref: ActivityBlock.SessionRef, row: SessionsModel.Row)]
    var resume: (SessionsModel.Row) -> Void

    @Environment(\.dismissSheet) private var dismiss
    @State private var detail: SessionsModel.Row?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                HarnessIcon(harness: HarnessMark(block.harness).icon ?? block.harness, size: 20)
                VStack(alignment: .leading, spacing: 2) {
                    Text(block.project).font(Theme.sectionTitle).tracking(-0.5)
                    Text("\(CalendarFormat.range(start: block.start, end: block.end)) · "
                         + "\(block.sessions.count) session\(block.sessions.count == 1 ? "" : "s")")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                Spacer()
            }
            ScrollView {
                VStack(spacing: 0) {
                ForEach(rows, id: \.ref.sessionId) { item in
                    SessionRow(ref: item.ref, row: item.row, harness: block.harness,
                               last: item.ref.sessionId == rows.last?.ref.sessionId) {
                        detail = item.row
                    }
                }
                }
            }
            .frame(maxHeight: 420)
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.border, lineWidth: 1))
            HStack {
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
        }
        .padding(20)
        .frame(width: 420)
        .quiverSheet(item: $detail) { row in
            SessionDetail(row: row, resume: { resume(row) }, close: { detail = nil })
        }
    }
}

private extension Array {
    /// Evenly sized groups, for the week-mode snap targets.
    func chunked(by size: Int) -> [[Element]] {
        stride(from: 0, to: count, by: Swift.max(1, size)).map { Array(self[$0..<Swift.min($0 + size, count)]) }
    }
}

/// The page's hover card: `HoverCardState` fed by AppKit's pointer and
/// the live views behind each anchor and card. Not observed itself;
/// `key` reads through to the state, so only the anchors that read it
/// redraw when a card opens or closes.
@MainActor final class HoverCards {
    let geometry: CardGeometry
    let state: HoverCardState
    var reduceMotion = false

    init() {
        let geometry = CardGeometry()
        self.geometry = geometry
        state = HoverCardState(
            pointer: { NSEvent.mouseLocation },
            rects: { (geometry.anchorRect(for: $0), geometry.cardRect(for: $0)) })
        state.animate = { [unowned self] change in
            withAnimation(reduceMotion ? nil : .default, change)
        }
    }

    var key: String? { state.key }
    func hover(_ key: String, inside: Bool) { state.hover(key, inside: inside) }
    func open(_ key: String) { state.open(key) }
    func dismiss() { state.dismiss() }
    func closed(_ key: String) { state.closed(key) }

    /// A scroll view's phase: moving closes and holds every card; coming
    /// to rest starts the quiet before the next one.
    func scrollPhase(_ id: String, from old: ScrollPhase, to phase: ScrollPhase) {
        if phase == .idle {
            if old != .idle { state.scrollSettled(id) }
        } else if old == .idle {
            state.scrollBegan(id)
        }
    }

    func scrollsRemoved() { state.scrollsRemoved() }
}

/// Makes a view a hover-card anchor: registers its rect and the card's
/// for the poller, reports the pointer, and presents `card` while this
/// anchor's card is the open one.
private struct HoverCardAnchor<Card: View>: ViewModifier {
    var cards: HoverCards
    var key: String
    @ViewBuilder var card: () -> Card

    func body(content: Content) -> some View {
        // Read here, so a card opening redraws its anchors alone.
        let shown = cards.key == key
        content
            // The rect only matters while this anchor's card is open, so
            // only that anchor carries the AppKit view that reports it:
            // one view, not one per block for every hover hit test to
            // walk through.
            .background {
                if shown {
                    HoverRegionView { [cards, key] view, attached in
                        cards.geometry.register(chip: key, view: view, attached: attached)
                    }
                }
            }
            .onHover { [cards, key] in cards.hover(key, inside: $0) }
            .popover(isPresented: Binding(get: { shown },
                                          set: { [cards, key] in if !$0 { cards.closed(key) } }),
                     attachmentAnchor: .rect(.bounds),
                     arrowEdge: .leading) {
                card()
                    .background(HoverRegionView { [cards, key] view, attached in
                        cards.geometry.register(card: key, view: view, attached: attached)
                    })
            }
    }
}

private extension View {
    func hoverCard<Card: View>(_ cards: HoverCards, key: String,
                               @ViewBuilder card: @escaping () -> Card) -> some View {
        modifier(HoverCardAnchor(cards: cards, key: key, card: card))
    }
}

/// The views whose screen rects bound an open chip card's safe region.
/// The pointer poller reads them fresh every tick, so scrolling and
/// window moves need no re-reporting. Weak, so a torn-down view cannot
/// keep the card's geometry alive.
@MainActor final class CardGeometry {
    private struct WeakView {
        weak var view: NSView?
    }

    private var chips: [String: WeakView] = [:]
    /// One entry per open card, keyed like the chips: switching cards can
    /// run the old card's detach after the new card's attach, and a keyed
    /// slot plus the identity check keeps that detach from wiping the new
    /// card's frame.
    private var cards: [String: WeakView] = [:]

    func register(chip key: String, view: NSView, attached: Bool) {
        if attached {
            chips[key] = WeakView(view: view)
        } else if chips[key]?.view === view {
            chips[key] = nil
        }
    }

    func register(card key: String, view: NSView, attached: Bool) {
        if attached {
            cards[key] = WeakView(view: view)
        } else if cards[key]?.view === view {
            cards[key] = nil
        }
    }

    /// The anchor view's screen rect, nil until it reports in.
    func anchorRect(for key: String) -> CGRect? {
        guard let view = chips[key]?.view, let window = view.window else { return nil }
        return window.convertToScreen(view.convert(view.bounds, to: nil))
    }

    /// The popover window's frame, nil while it is still appearing — the
    /// poller gives a missing card its short pending grace.
    func cardRect(for key: String) -> CGRect? {
        cards[key]?.view?.window?.frame
    }
}

/// Registers its backing NSView while it is in a window, so the card
/// poller can read screen rects — reliable where `.onHover` is not,
/// like a popover's non-key window.
struct HoverRegionView: NSViewRepresentable {
    /// Reports the backing view itself on attach and detach, so a stale
    /// detach can be told apart from the view currently registered.
    var report: (NSView, Bool) -> Void

    func makeNSView(context: Context) -> View {
        let view = View()
        view.report = report
        return view
    }

    func updateNSView(_ view: View, context: Context) {
        view.report = report
    }

    final class View: NSView {
        var report: ((NSView, Bool) -> Void)?
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            report?(self, window != nil)
        }
    }
}
