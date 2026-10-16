import AppKit
import Foundation
import SwiftUI
import Testing
@testable import QuiverApp
@testable import QuiverCore

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
                           "artificial_analysis_coding_index": 59.0},
           "pricing": {"price_1m_blended_3_to_1": 8.75, "price_1m_input_tokens": 5.0,
                       "price_1m_output_tokens": 25.0},
           "median_output_tokens_per_second": 42.0}
        """, """
          {"id": "g6a", "name": "GPT-6 Astra", "slug": "gpt-6-astra",
           "model_creator": {"id": "openai", "name": "OpenAI", "slug": "openai"},
           "evaluations": {"artificial_analysis_intelligence_index": 55.0,
                           "artificial_analysis_coding_index": 54.0},
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

    private func fixtureDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appending(path: "quiver-chart-layout-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try Self.llmsFixture.write(to: dir.appending(path: "llms.json"),
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

    /// The room before the first bar holds against the column width it
    /// leaves: the first bar's middle sits at least `reach` from the edge,
    /// with no more room than needed, flexible or at the minimum width.
    @Test(arguments: [(1400.0, 25), (900.0, 25), (1600.0, 8), (600.0, 3), (2000.0, 40)])
    func leadingRoomClearsTheFirstLabel(width: Double, count: Int) {
        for reach in [0.0, 20.0, 60.0, 95.0] {
            let room = ChartCard.leadingRoom(reach: reach, width: width, count: count)
            let column = max(ChartCard.minColumnWidth,
                             (width - 2 * ChartCard.inset - room) / CGFloat(count))
            let middle = room + ChartCard.inset + (column - ChartCard.spacing) / 2
            #expect(middle >= reach - 0.001, "width \(width), count \(count), reach \(reach)")
            if room > 0 { #expect(middle - reach < 1.5, "no more room than needed") }
        }
    }

    @Test
    func chartLaysOutFiniteAcrossResizes() async throws {
        let dir = try fixtureDir()
        defer { try? FileManager.default.removeItem(at: dir) }
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
        let window = NSWindow(
            contentRect: NSRect(x: -10_000, y: -10_000, width: 1400, height: 900),
            styleMask: [.titled, .resizable], backing: .buffered, defer: false)
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
                window.contentView?.layoutSubtreeIfNeeded()
                await Task.yield()
                if let content = window.contentView {
                    Self.auditFinite(content, bad: &bad, seen: &seen)
                }
            }
        }

        #expect(bad == 0)
        #expect(seen > 0)
        #expect(hosting.bounds.width > 0 && hosting.bounds.height > 0)

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
