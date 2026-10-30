import SwiftUI

/// The Usage tab: how close each harness is to its limits, then what the
/// hub machine's agents spent over one of the overview's seven periods,
/// with the Mac's quick figures, top models, heatmap and daily table.
struct UsageView: View {
    @Environment(AppModel.self) private var app
    @State private var model = UsageModel()

    var body: some View {
        PageScroll {
            // Ticks once a minute so the reset countdowns stay true.
            TimelineView(.everyMinute) { context in
                UsageSections(model: model, now: context.date,
                              retryLimits: { Task { await model.loadUsage(app: app, refresh: false) } },
                              retrySpend: { Task { await model.loadPeriod(app: app, model.period, force: true) } })
            }
        }
        .refreshable { await model.refresh(app: app) }
        .task { await model.loadIfStale(app: app) }
        .onChange(of: model.period) { _, period in
            Task { await model.loadPeriod(app: app, period) }
        }
        .navigationTitle("Usage")
    }
}

/// The page body without its scroll view, so it can be rendered on its own.
struct UsageSections: View {
    @Bindable var model: UsageModel
    var now: Date
    var retryLimits: () -> Void = {}
    var retrySpend: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionTitle(text: "Limits")
            if let usage = model.usage {
                if let error = model.usageError {
                    Text(inlineCode: "Couldn't refresh: \(error)").font(Theme.caption).foregroundStyle(Theme.textMuted).padding(.horizontal, 4)
                }
                LimitsSection(usage: usage, now: now)
            } else if let error = model.usageError {
                StateCard(icon: "wifi.exclamationmark", title: "Can't load limits", message: error, tone: .error,
                          actionTitle: "Try again", action: retryLimits)
            } else {
                LoadingRow(text: "Loading limits")
            }

            SectionTitle(text: "Spend").padding(.top, 20)
            PeriodChips(selection: $model.period)
                .padding(.bottom, 4)
            if let overview = model.overviews[model.period] {
                SpendSection(overview: overview, period: model.period, harnessName: model.harnessName)
            } else if let error = model.overviewErrors[model.period] {
                StateCard(icon: "wifi.exclamationmark", title: "Can't load spend", message: error, tone: .error,
                          actionTitle: "Try again", action: retrySpend)
            } else {
                LoadingRow(text: "Loading spend")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct SectionTitle: View {
    var text: String
    var body: some View {
        Text(text).font(Theme.sectionTitle).foregroundStyle(Theme.textPrimary)
            .padding(.horizontal, 4).padding(.top, 8)
            .accessibilityAddTraits(.isHeader)
    }
}
