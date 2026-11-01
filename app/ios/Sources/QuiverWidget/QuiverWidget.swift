import SwiftUI
import WidgetKit

/// The home screen widget (#1442): what needs you, what's still working
/// and today's spend on the hub on screen in the app. It reads the
/// paired hubs from the App Group and the shared Keychain group, as the
/// share sheet does, and draws the App Group's snapshot when the hub
/// can't be reached.
@main
struct QuiverWidgets: WidgetBundle {
    var body: some Widget { NeedsYouWidget() }
}

struct NeedsYouWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "NeedsYou", provider: NeedsYouProvider()) { entry in
            NeedsYouView(entry: entry)
        }
        .configurationDisplayName("Needs you")
        .description("Sessions waiting on you, sessions still working, and today's spend.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

struct NeedsYouEntry: TimelineEntry {
    let date: Date
    let content: WidgetContent
}

struct NeedsYouProvider: TimelineProvider {
    /// The system's refresh: a new fetch about every 15 minutes, plus a
    /// redraw whenever the app rewrites the snapshot.
    static let interval: TimeInterval = 15 * 60

    func placeholder(in context: Context) -> NeedsYouEntry {
        NeedsYouEntry(date: Date(), content: .summary(.sample, stale: false))
    }

    func getSnapshot(in context: Context, completion: @escaping (NeedsYouEntry) -> Void) {
        if context.isPreview {
            completion(placeholder(in: context))
            return
        }
        Task { completion(await entry()) }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<NeedsYouEntry>) -> Void) {
        Task {
            let entry = await entry()
            let next = entry.date.addingTimeInterval(Self.interval)
            var entries = [entry]
            // Yesterday's spend leaves at midnight, not at the next fetch.
            if let midnight = Calendar.current.nextDate(after: entry.date, matching: DateComponents(hour: 0, minute: 0),
                                                        matchingPolicy: .nextTime), midnight < next {
                entries.append(NeedsYouEntry(date: midnight, content: entry.content))
            }
            completion(Timeline(entries: entries, policy: .after(next)))
        }
    }

    private func entry() async -> NeedsYouEntry {
        let hubs = KeychainHubStore(legacyDefaults: nil).load()
        let loader = WidgetLoader(store: WidgetSnapshotStore(), fetch: WidgetLoader.hubFetch)
        let now = Date()
        return NeedsYouEntry(date: now, content: await loader.load(hubs: hubs, now: now))
    }
}

extension WidgetSnapshot {
    /// The gallery's preview.
    static let sample: WidgetSnapshot = {
        var s = WidgetSnapshot(
            hubId: "", hubName: "mac",
            needsYou: [.init(key: "a", title: "Fix the login redirect", harness: "Claude Code", status: "approval")],
            needsYouCount: 1,
            working: [.init(key: "b", title: "Write the release notes", harness: "Codex", status: "active"),
                      .init(key: "c", title: "Port the usage chart", harness: "Claude Code", status: "active")],
            workingCount: 2, fetchedAt: Date())
        s.setSpend(4.2, at: Date())
        return s
    }()
}

/// The app's colours (`Theme`): amber for "needs you", green only for
/// "active".
private enum Palette {
    static let amber = Color(red: 0.96, green: 0.62, blue: 0.04)
    static let green = Color(red: 0.06, green: 0.73, blue: 0.51)
}

struct NeedsYouView: View {
    let entry: NeedsYouEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        content
            .containerBackground(.background, for: .widget)
    }

    @ViewBuilder private var content: some View {
        switch entry.content {
        case .notPaired:
            message("Not paired", detail: "Open Quiver Remote to pair a hub.")
        case .unreachable(let hub):
            message("Can't reach \(hub)", detail: "Check that Tailscale is on.")
        case .summary(let snapshot, let stale):
            if family == .systemMedium {
                HStack(alignment: .top, spacing: 14) {
                    counts(snapshot, stale: stale).frame(maxWidth: 120, alignment: .leading)
                    sessions(snapshot)
                }
            } else {
                counts(snapshot, stale: stale)
            }
        }
    }

    private func message(_ title: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Quiver").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            Spacer(minLength: 0)
            Text(title).font(.headline)
            Text(detail).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    private func counts(_ s: WidgetSnapshot, stale: Bool) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(s.hubName).font(.caption.weight(.semibold)).foregroundStyle(.secondary).lineLimit(1)
            Spacer(minLength: 0)
            if s.needsYouCount > 0 {
                Text("\(s.needsYouCount)").font(.system(size: 34, weight: .semibold, design: .rounded))
                    .foregroundStyle(Palette.amber)
                Text(s.needsYouCount == 1 ? "needs you" : "need you").font(.caption.weight(.medium))
            } else {
                Text("Nothing needs you").font(.subheadline.weight(.semibold))
            }
            Spacer(minLength: 0)
            Label("\(s.workingCount) working", systemImage: "circle.fill")
                .labelStyle(DotLabel(color: s.workingCount > 0 ? Palette.green : .secondary))
                .font(.caption)
            Text(spend(s)).font(.caption).foregroundStyle(.secondary)
            if stale {
                Text("as of \(s.fetchedAt.formatted(date: .omitted, time: .shortened))")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    private func spend(_ s: WidgetSnapshot) -> String {
        guard let usd = s.spendToday(now: entry.date) else { return "Spend today: -" }
        return "\(UsageFormat.usd(usd)) today"
    }

    /// Up to three sessions, waiting on you first. Each opens its chat;
    /// anywhere else opens the app.
    @ViewBuilder private func sessions(_ s: WidgetSnapshot) -> some View {
        let rows = Array(s.rows.prefix(3))
        VStack(alignment: .leading, spacing: 8) {
            if rows.isEmpty {
                Spacer(minLength: 0)
                Text("No sessions running").font(.caption).foregroundStyle(.secondary)
                Spacer(minLength: 0)
            } else {
                ForEach(rows) { row in
                    if let url = SessionLink(key: row.key, hubId: s.hubId.isEmpty ? nil : s.hubId).url {
                        Link(destination: url) { sessionRow(row) }
                    } else {
                        sessionRow(row)
                    }
                }
                Spacer(minLength: 0)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func sessionRow(_ row: WidgetSnapshot.Row) -> some View {
        let waiting = row.status != "active"
        return HStack(alignment: .firstTextBaseline, spacing: 6) {
            Circle().fill(waiting ? Palette.amber : Palette.green).frame(width: 7, height: 7)
            VStack(alignment: .leading, spacing: 0) {
                Text(row.title).font(.caption.weight(.medium)).lineLimit(1)
                Text(waiting ? (row.needsApproval ? "approval needed" : "needs you") : row.harness)
                    .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
        }
    }
}

private struct DotLabel: LabelStyle {
    let color: Color
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 7, height: 7)
            configuration.title
        }
    }
}
