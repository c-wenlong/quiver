import QuiverCore
import QuiverWidgetsUI
import SwiftUI
import WidgetKit

/// Quiver's desktop and Notification Center widgets, after TokenTracker's
/// four. They draw the snapshot the app writes to
/// `~/.quiver/cache/widgets/snapshot.json`; the extension is sandboxed with
/// read access to that folder only, and never fetches anything itself.
@main
struct QuiverWidgets: WidgetBundle {
    var body: some Widget {
        UsageLimitsWidget()
        UsageSummaryWidget()
        ActivityHeatmapWidget()
        TopModelsWidget()
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

    /// The app asks for a reload whenever the file changes. Between those,
    /// redraw every 15 minutes so reset countdowns and passed resets move on.
    func getTimeline(in context: Context, completion: @escaping (Timeline<SnapshotEntry>) -> Void) {
        let snapshot = WidgetSnapshotStore(file: WidgetSnapshotStore.defaultFile()).read()
        let now = Date.now
        let entries = (0..<4).map { step in
            SnapshotEntry(date: now.addingTimeInterval(Double(step) * 900), snapshot: snapshot)
        }
        completion(Timeline(entries: entries, policy: .after(now.addingTimeInterval(3_600))))
    }
}

struct UsageLimitsWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "limits", provider: SnapshotProvider()) { entry in
            WidgetFrame(entry: entry) { LimitsView(snapshot: $0, now: entry.date) }
        }
        .configurationDisplayName("Usage limits")
        .description("How much of each rate limit is used, most used first.")
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
