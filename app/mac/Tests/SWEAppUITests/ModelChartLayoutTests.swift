import AppKit
import Foundation
import SwiftUI
import Testing
@testable import SWEApp
@testable import SWECore

/// `Page` enables `.textSelection(.enabled)` for the whole app shell, and
/// selectable text lays out as a native AppKit view. Under the chart's
/// rotated labels that host's `systemLayoutSizeFittingSize` aborts on a
/// NaN in `_nsis_frameInEngine`, so `ChartCard` opts the chart back out.
/// These tests render the production `ChartCard` under the inherited
/// flag and drive real window resizes; a regression aborts the runner.
@Suite(.serialized)
@MainActor
struct ModelChartLayoutTests {
    /// Fake AA payload: two detailed models (one deliberately long name
    /// so the rotated label is exercised) plus 23 fillers — 25 bars make
    /// the columns genuinely narrow at the smaller widths instead of two
    /// enormous ones.
    private static var llmsFixture: String {
        var entries = ["""
          {"id": "op55", "name": "Claude Opus 5.5 With An Unusually Long Display Name For Label Layout (high)",
           "slug": "claude-opus-5-5-high",
           "model_creator": {"id": "anthropic", "name": "Anthropic", "slug": "anthropic"},
           "evaluations": {"artificial_analysis_intelligence_index": 61.0,
                           "terminalbench_v4_0": 0.59},
           "pricing": {"price_1m_blended_3_to_1": 8.75, "price_1m_input_tokens": 5.0,
                       "price_1m_output_tokens": 25.0},
           "median_output_tokens_per_second": 42.0}
        """, """
          {"id": "g6a", "name": "GPT-6 Astra", "slug": "gpt-6-astra",
           "model_creator": {"id": "openai", "name": "OpenAI", "slug": "openai"},
           "evaluations": {"artificial_analysis_intelligence_index": 55.0,
                           "terminalbench_v4_0": 0.54},
           "pricing": {"price_1m_blended_3_to_1": 4.0, "price_1m_input_tokens": 3.0,
                       "price_1m_output_tokens": 9.0},
           "median_output_tokens_per_second": 88.0}
        """]
        for i in 0..<23 {
            entries.append("""
              {"id": "fill\(i)", "name": "Filler Model \(i) With A Longer Display Name",
               "slug": "filler-\(i)",
               "model_creator": {"id": "openai", "name": "OpenAI", "slug": "openai"},
               "evaluations": {"artificial_analysis_intelligence_index": \(54 - i)},
               "pricing": {"price_1m_blended_3_to_1": 1.0}}
            """)
        }
        return #"{"status": 200, "prompt_options": [], "data": ["#
            + entries.joined(separator: ",") + "]}"
    }

    /// The page never refreshes here (no key); a send means the test
    /// leaked into the network path.
    private struct NoTransport: HTTPTransport {
        func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
            Issue.record("models refresh must not run in the layout test")
            throw AAError.network("layout test forbids network")
        }
    }

    private func makeModel(in dir: URL) -> ModelsModel {
        ModelsModel(store: ArtificialAnalysisStore(directory: dir),
                    keyReader: { nil },
                    transport: NoTransport(),
                    linksURL: dir.appending(path: "model-links.json"),
                    slices: { _ in [] })
    }

    /// A temp folder holding the fixture; it goes when the caller drops it.
    private func fixtureDir() throws -> TempDir {
        let dir = try TempDir()
        try Self.llmsFixture.write(to: dir.url.appending(path: "llms.json"),
                                   atomically: true, encoding: .utf8)
        return dir
    }

    private static func auditFinite(_ view: NSView, bad: inout Int, seen: inout Int) {
        seen += 1
        let rect = [view.frame.origin.x, view.frame.origin.y,
                    view.frame.size.width, view.frame.size.height,
                    view.bounds.origin.x, view.bounds.origin.y,
                    view.bounds.size.width, view.bounds.size.height]
        if rect.contains(where: { !$0.isFinite }) { bad += 1 }
        view.subviews.forEach { auditFinite($0, bad: &bad, seen: &seen) }
    }

    /// The room before the first bar, worked out by hand (inset 6,
    /// spacing 8, columns at least 36) rather than with the chart's own
    /// formula, so a mistake in that formula can't pass by agreeing with
    /// itself. Each case notes where the first bar's middle lands.
    @Test(arguments: [
        // Flexible columns: room 31 leaves (1388 − 31) / 25 = 54.28-wide
        // columns, middle 31 + 6 + 23.14 = 60.14, just past 60.
        (1400.0, 25, 60.0, 31.0),
        // The columns floor at 36 (flexible room 76.8 would leave 32.4):
        // room 95 − 6 − 14 = 75 puts the middle exactly at 95.
        (900.0, 25, 95.0, 75.0),
        // Wide columns already clear it: 588 / 3 = 196, middle 100 ≥ 95.
        (600.0, 3, 95.0, 0.0),
        // 1988 / 40 = 49.7-wide columns, middle 26.85 ≥ 20.
        (2000.0, 40, 20.0, 0.0),
        // Nothing to clear.
        (1400.0, 25, 0.0, 0.0),
    ])
    func leadingRoomClearsTheFirstLabel(width: Double, count: Int, reach: Double, room: Double) {
        let measured: CGFloat = ChartCard.leadingRoom(reach: reach, width: width, count: count)
        #expect(measured == CGFloat(room))
    }

    /// Every benchmark header tip reads "<Domain>: <measure>" over a
    /// line of explanation, and has an icon to stand in the header.
    @Test(arguments: ModelsModel.Metric.allCases)
    func everyMetricHasAHeaderTip(metric: ModelsModel.Metric) {
        let tip = ColumnTip.of(metric)
        #expect(!metric.symbol.isEmpty)
        #expect(tip.title.split(separator: ":").count == 2)
        #expect(!tip.text.isEmpty)
    }

    /// Columns never drop below their floors; room above the floors is
    /// shared by weight, so the table fills the card exactly and the model
    /// name gets about a quarter of the extra width, not all of it.
    @Test(arguments: [false, true])
    func tableColumnsShareTheWidth(extraMetric: Bool) {
        let floors = TableColumns.widths(for: 0, extraMetric: extraMetric)
        #expect(floors.model == CGFloat(TableColumns.model.min))
        #expect(floors.cost == CGFloat(TableColumns.cost.min))
        #expect((floors.extra > 0) == extraMetric)
        // Narrower than the floors: floors as they are, and the table scrolls.
        let narrow = TableColumns.widths(for: floors.total - 100, extraMetric: extraMetric)
        #expect(narrow == floors)

        let wide = TableColumns.widths(for: 1600, extraMetric: extraMetric)
        #expect(abs(wide.total - 1600) < 0.5)
        let room = 1600 - floors.total
        let modelShare = (wide.model - floors.model) / room
        #expect(modelShare > 0.2 && modelShare < 0.3)
        #expect(wide.cost - floors.cost > wide.coding - floors.coding)
    }

    @Test
    func chartLaysOutFiniteAcrossResizes() async throws {
        let scratch = try fixtureDir()
        let dir = scratch.url
        let model = makeModel(in: dir)
        await model.load()
        // All 25 fixture models: the columns go narrow, not enormous.
        model.set(scope: .top25)
        #expect(model.rows.count == 25)

        let app = NSApplication.shared
        app.setActivationPolicy(.prohibited)
        let hosting = NSHostingView(rootView:
            ScrollView { ChartCard(model: model).padding() }
                .textSelection(.enabled))
        let window = OffscreenWindow(size: CGSize(width: 1400, height: 900),
                                     styleMask: [.titled, .resizable])
        window.contentView = hosting
        window.orderBack(nil)
        defer { window.orderOut(nil) }

        let sizes: [CGSize] = [CGSize(width: 1400, height: 900), CGSize(width: 900, height: 600),
                               CGSize(width: 1180, height: 800), CGSize(width: 1600, height: 1000)]
        var bad = 0
        var seen = 0
        for _ in 0..<4 {
            for size in sizes {
                var frame = window.frame
                frame.size = size
                window.setFrame(frame, display: true)
                // Layout runs here, synchronously; a yield after it ran no
                // layout of its own, only whatever happened to be queued.
                window.contentView?.layoutSubtreeIfNeeded()
                if let content = window.contentView {
                    Self.auditFinite(content, bad: &bad, seen: &seen)
                }
            }
        }

        #expect(bad == 0)
        #expect(seen > 0)
        // Resizes never pull the window onto the user's screen.
        #expect(window.screen == nil)
        #expect(hosting.bounds.width > 0 && hosting.bounds.height > 0)
        withExtendedLifetime(scratch) {}

        // QUIVER_CHART_SNAPSHOT=<path> renders the tested chart offscreen.
        if let snap = ProcessInfo.processInfo.environment["QUIVER_CHART_SNAPSHOT"],
           let rep = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) {
            hosting.cacheDisplay(in: hosting.bounds, to: rep)
            if let png = rep.representation(using: .png, properties: [:]) {
                try? png.write(to: URL(fileURLWithPath: snap))
            }
        }
    }
}
