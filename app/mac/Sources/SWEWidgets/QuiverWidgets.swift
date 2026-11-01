import SWECore
import SWEWidgetsUI
import SwiftUI
import WidgetKit

/// SWE's desktop and Notification Center widgets, after TokenTracker's.
/// They draw the snapshot the app writes to
/// `~/.swe/cache/widgets/snapshot.json`; the extension is sandboxed with
/// read access to that folder only, and never fetches anything itself.
@main
struct SWEWidgets: WidgetBundle {
    var body: some Widget {
        UsageLimitsWidget()
        UsageSummaryWidget()
        ActivityHeatmapWidget()
        TopModelsWidget()
        ComputeWidget()
    }
}

struct SnapshotProvider: TimelineProvider {
    func placeholder(in context: Context) -> SnapshotEntry {
        SnapshotEntry(date: .now, snapshot: .preview)
    }

    func getSnapshot(in context: Context, completion: @escaping (SnapshotEntry) -> Void) {
        let snapshot = WidgetSnapshotStore(file: WidgetSnapshotStore.defaultFile()).read()
        completion(SnapshotEntry(date: .now, snapshot: snapshot ?? (context.isPreview ? .preview : nil)))
    }

    /// The minutes between entries: how fast relative labels ("45m ago",
    /// reset countdowns) march on once an entry shows.
    static let step: TimeInterval = 900
    /// Entries cover eight hours, so a starved timeline keeps dating the
    /// figures honestly instead of freezing the last one at "45m ago".
    static let entries = 32

    /// The file is re-read about every 15 minutes; the app's own reload
    /// pushes are floored so WidgetKit's reload budget lasts the day.
    /// Between reads the entries' dates move every label that counts on.
    func getTimeline(in context: Context, completion: @escaping (Timeline<SnapshotEntry>) -> Void) {
        let snapshot = WidgetSnapshotStore(file: WidgetSnapshotStore.defaultFile()).read()
        let now = Date.now
        let entries = (0..<Self.entries).map { step in
            SnapshotEntry(date: now.addingTimeInterval(Double(step) * Self.step), snapshot: snapshot)
        }
        completion(Timeline(entries: entries, policy: .after(now.addingTimeInterval(Self.step))))
    }
}

struct UsageLimitsWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "limits", provider: SnapshotProvider()) { entry in
            WidgetFrame(entry: entry) { LimitsView(snapshot: $0, now: entry.date) }
        }
        .configurationDisplayName("Usage limits")
        .description("How much of each rate limit is used, in the order you set in SWE.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

struct UsageSummaryWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "summary", provider: SnapshotProvider()) { entry in
            WidgetFrame(entry: entry) { SummaryView(snapshot: $0, now: entry.date) }
        }
        .configurationDisplayName("Usage summary")
        .description("Today and the last seven days, with a sparkline.")
        .supportedFamilies([.systemMedium])
    }
}

struct ActivityHeatmapWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "heatmap", provider: SnapshotProvider()) { entry in
            WidgetFrame(entry: entry) { HeatmapView(snapshot: $0, now: entry.date) }
        }
        .configurationDisplayName("Activity heatmap")
        .description("Tokens per day for the last half year.")
        .supportedFamilies([.systemMedium])
    }
}

struct TopModelsWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "models", provider: SnapshotProvider()) { entry in
            WidgetFrame(entry: entry) { ModelsView(snapshot: $0) }
        }
        .configurationDisplayName("Top models")
        .description("Your most used models over the last 30 days, by token share.")
        .supportedFamilies([.systemMedium])
    }
}

struct ComputeWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "compute", provider: SnapshotProvider()) { entry in
            WidgetFrame(entry: entry) { ComputeView(snapshot: $0, now: entry.date) }
        }
        .configurationDisplayName("Compute")
        .description("What each running harness costs in RAM, CPU and GPU, with the Mac's temperatures.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}
