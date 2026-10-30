import SwiftUI

/// The overview's period picker: seven chips in a row that scrolls
/// sideways, since the Mac's seven tabs don't fit a phone's width.
struct PeriodChips: View {
    @Binding var selection: UsagePeriod

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(UsagePeriod.allCases) { period in
                        FilterChip(title: period.title, count: nil, selected: period == selection) {
                            selection = period
                        }
                        .id(period)
                        .accessibilityAddTraits(period == selection ? .isSelected : [])
                    }
                }
            }
            .onAppear { proxy.scrollTo(selection, anchor: .center) }
        }
    }
}

/// Active days, tokens a day and the busiest day, then the period's top
/// five models with the harnesses that ran them and their share.
struct QuickFiguresCard: View {
    var overview: OverviewResponse
    var harnessName: (String) -> String
    @Environment(\.dynamicTypeSize) private var size

    var body: some View {
        let models = overview.topModels ?? []
        Card {
            VStack(alignment: .leading, spacing: 12) {
                StackingRow {
                    ForEach(OverviewFormat.quickChips(overview.quick), id: \.label) { chip in
                        VStack(spacing: 2) {
                            Text(chip.value).font(Theme.rowTitle.monospacedDigit())
                                .foregroundStyle(Theme.textPrimary).lineLimit(1).minimumScaleFactor(0.8)
                            Text(chip.label).font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(Theme.subtle, in: .rect(cornerRadius: Theme.blockRadius))
                        .accessibilityElement(children: .combine)
                    }
                }
                Text("Top models").font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .accessibilityAddTraits(.isHeader)
                if models.isEmpty {
                    Text("No models in this period.").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                } else {
                    VStack(spacing: 0) {
                        ForEach(Array(models.enumerated()), id: \.element.id) { index, model in
                            modelRow(index: index, model: model)
                            if index < models.count - 1 { Divider().overlay(Theme.border) }
                        }
                    }
                }
            }
        }
    }

    private func modelRow(index: Int, model: OverviewResponse.TopModel) -> some View {
        let marks = OverviewFormat.harnessMarks(model.harnesses)
        return HStack(spacing: 10) {
            Text("\(index + 1)")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .iconColumn(22, height: 22)
                .background(Theme.subtle, in: .circle)
            if size.isAccessibilitySize {
                // No room beside the name for the share: the name wraps,
                // and the logos and share go on the line under it.
                VStack(alignment: .leading, spacing: 4) {
                    modelName(model.model, lines: 3)
                    HStack(spacing: 8) {
                        harnessLogos(marks)
                        Spacer(minLength: 8)
                        share(model.share)
                    }
                }
            } else {
                // The name comes first: the harnesses that ran it follow
                // only while the whole name still fits.
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 8) {
                        modelName(model.model)
                        harnessLogos(marks)
                    }
                    modelName(model.model)
                }
                Spacer(minLength: 8)
                share(model.share)
            }
        }
        .padding(.vertical, 9)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(index + 1). \(model.model), \(OverviewFormat.share(model.share)), "
                            + (model.harnesses ?? []).map(harnessName).joined(separator: ", "))
    }

    private func share(_ share: Double?) -> some View {
        Text(OverviewFormat.share(share))
            .font(Theme.rowTitle.monospacedDigit()).foregroundStyle(Theme.textPrimary)
            .fixedSize()
    }

    private func modelName(_ name: String, lines: Int = 1) -> some View {
        Text(name).font(Theme.control).foregroundStyle(Theme.textPrimary)
            .lineLimit(lines).truncationMode(.middle)
    }

    @ViewBuilder private func harnessLogos(_ marks: (shown: [String], more: String?)) -> some View {
        if !marks.shown.isEmpty {
            HStack(spacing: 2) {
                ForEach(marks.shown, id: \.self) { harness in
                    if HarnessIcon.assetName(harness) != nil {
                        HarnessIcon(harness: harness, name: harnessName(harness), size: 13)
                    } else {
                        Text(harnessName(harness)).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                }
                if let more = marks.more {
                    Text(more).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            .fixedSize()
        }
    }
}

/// Tokens a day over the last weeks that fit the card, as on the Mac
/// and web. The phone has no hover: a tap picks a day and the line
/// under the grid names it.
struct ActivityHeatmapCard: View {
    var heatmap: OverviewResponse.Heatmap
    @State private var width: CGFloat = 0
    @State private var picked: String?

    var body: some View {
        let available = (heatmap.days?.count ?? 0) / 7
        let weeks = OverviewFormat.weeksThatFit(width: width, available: available)
        let columns = OverviewFormat.columns(heatmap, weeks: width > 0 ? weeks : available)
        let months = OverviewFormat.monthLabels(columns)
        let cell = OverviewFormat.heatCell, gap = OverviewFormat.heatGap
        Card {
            VStack(alignment: .leading, spacing: 10) {
                Text("Tokens a day, last \(columns.count) weeks")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                // Measures the card's inner width without taking any height.
                Color.clear.frame(maxWidth: .infinity, maxHeight: 0)
                    .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
                if width > 0 {
                    VStack(alignment: .leading, spacing: gap) {
                        HStack(spacing: gap) {
                            Color.clear.frame(width: OverviewFormat.heatGutter, height: 1)
                            ForEach(columns.indices, id: \.self) { w in
                                Text(months[w] ?? "")
                                    .font(Theme.font(size: 10)).foregroundStyle(Theme.textSecondary)
                                    .fixedSize()
                                    .frame(width: cell, alignment: .leading)
                                    .accessibilityHidden(true)
                            }
                        }
                        ForEach(0..<7, id: \.self) { weekday in
                            HStack(spacing: gap) {
                                // Mon, Wed, Fri, as the web; the hub's weeks start on Sunday.
                                Text(weekday % 2 == 1 ? Calendar.current.shortWeekdaySymbols[weekday] : "")
                                    .font(Theme.font(size: 10)).foregroundStyle(Theme.textSecondary)
                                    .fixedSize()
                                    .frame(width: OverviewFormat.heatGutter, height: cell, alignment: .leading)
                                    .accessibilityHidden(true)
                                ForEach(columns.indices, id: \.self) { w in
                                    square(columns[w][weekday])
                                }
                            }
                        }
                    }
                    // The squares are a fixed 12pt grid sized to the card,
                    // so the month and weekday labels on it stop growing at
                    // xLarge, where "Wed" still fits its 24pt gutter.
                    .dynamicTypeSize(...DynamicTypeSize.xLarge)
                    // Each day is its own element, so VoiceOver reads them
                    // one by one with their figures.
                    .accessibilityElement(children: .contain)
                    .accessibilityLabel("Activity, last \(columns.count) weeks")
                }
                // On its own row, so a long day line wraps instead of
                // losing its cost to the legend.
                if let picked, let day = columns.joined().compactMap({ $0 }).first(where: { $0.date == picked }) {
                    Text(Self.dayLine(day))
                        .font(Theme.caption.monospacedDigit()).foregroundStyle(Theme.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityHidden(true)
                } else {
                    Text("Tap a day for its tokens").font(Theme.caption).foregroundStyle(Theme.textMuted)
                        .accessibilityHidden(true)
                }
                HStack(spacing: 4) {
                    Spacer(minLength: 0)
                    Text("Less").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    ForEach(0..<5, id: \.self) { level in
                        RoundedRectangle(cornerRadius: 2).fill(Theme.heatmap[level]).frame(width: 10, height: 10)
                    }
                    Text("More").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                .accessibilityHidden(true)
            }
        }
    }

    /// "Sat, Jan 3 · 12.3M tokens · $4.20"
    private static func dayLine(_ day: OverviewResponse.Heatmap.Cell) -> String {
        "\(OverviewFormat.longDay(day.date) ?? day.date) · \(OverviewFormat.cellDetail(day))"
    }

    @ViewBuilder private func square(_ day: OverviewResponse.Heatmap.Cell?) -> some View {
        let cell = OverviewFormat.heatCell
        if let day {
            let level = min(4, max(0, day.level ?? 0))
            RoundedRectangle(cornerRadius: 2)
                .fill(Theme.heatmap[level])
                .overlay(RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(picked == day.date ? Theme.textPrimary : .clear, lineWidth: 1.5))
                .frame(width: cell, height: cell)
                .contentShape(.rect)
                .onTapGesture { picked = picked == day.date ? nil : day.date }
                .accessibilityElement()
                .accessibilityLabel(Self.dayLine(day))
                .accessibilityAddTraits(picked == day.date ? [.isButton, .isSelected] : .isButton)
                .accessibilityAction { picked = picked == day.date ? nil : day.date }
        } else {
            Color.clear.frame(width: cell, height: cell)
        }
    }
}

/// Date, total, input, output, cached and spend per active day, newest
/// first. Six columns are wider than a small phone, so the table
/// scrolls sideways there. It shows the newest ten days, then 30 more
/// a tap, so a years-long All time never builds every row at once.
struct DailyTable: View {
    var days: [OverviewResponse.Day]
    @State private var shown = OverviewFormat.dailyPreview

    private static let headers = ["Date", "Total", "Input", "Output", "Cached", "Spend"]

    var body: some View {
        let rows = OverviewFormat.dailyRows(days, shown: shown)
        let more = OverviewFormat.dailyMore(total: days.count, shown: shown)
        Card(padding: 0) {
            VStack(alignment: .leading, spacing: 0) {
                // Columns size to their widest cell, so "2026-10-29" and
                // "Output" never cut off. When that fits, the date takes
                // the slack as on the Mac; when not, the table scrolls.
                ViewThatFits(in: .horizontal) {
                    grid(rows, dateFills: true)
                    ScrollView(.horizontal, showsIndicators: false) { grid(rows, dateFills: false) }
                }
                if days.count > OverviewFormat.dailyPreview {
                    Rectangle().fill(Theme.border).frame(height: 1)
                    Button {
                        withAnimation(.easeOut(duration: 0.15)) {
                            shown = more > 0 ? shown + OverviewFormat.dailyPage : OverviewFormat.dailyPreview
                        }
                    } label: {
                        Text(more > 0 ? "Show \(more) more" : "Show less")
                            .font(Theme.control).foregroundStyle(Theme.textSecondary)
                            .frame(maxWidth: .infinity, minHeight: 44)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    // Each cell carries its own font and padding: a modifier on a
    // GridRow turns it into one view spanning the whole grid.
    private func grid(_ rows: [OverviewResponse.Day], dateFills: Bool) -> some View {
        Grid(alignment: .trailing, horizontalSpacing: 10, verticalSpacing: 0) {
            GridRow { cells(Self.headers, dateFills: dateFills, header: true) }
            ForEach(rows) { day in
                Rectangle().fill(Theme.border).frame(height: 1)
                GridRow { cells(OverviewFormat.dailyCells(day), dateFills: dateFills, header: false) }
            }
        }
        .padding(.horizontal, Theme.rowHorizontal)
    }

    /// One row's cells. A day row reads as one line to VoiceOver.
    @ViewBuilder private func cells(_ cells: [String], dateFills: Bool, header: Bool) -> some View {
        let font = header ? Theme.caption.weight(.medium) : Theme.caption.monospacedDigit()
        let ink = header ? Theme.textSecondary : Theme.textPrimary
        Text(cells[0]).font(font).foregroundStyle(ink).lineLimit(1).fixedSize()
            .frame(maxWidth: dateFills ? .infinity : nil, alignment: .leading)
            .padding(.vertical, 8)
            .gridColumnAlignment(.leading)
            .accessibilityLabel(header ? cells[0] : cells.joined(separator: ", "))
        ForEach(1..<cells.count, id: \.self) { index in
            Text(cells[index]).font(font)
                .foregroundStyle(!header && index == cells.count - 1 ? Theme.accentFill : ink)
                .lineLimit(1).fixedSize()
                .padding(.vertical, 8)
                .accessibilityHidden(!header)
        }
    }
}
