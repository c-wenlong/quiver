import Foundation
import Testing
@testable import QuiverApp

/// Compute's charts drop the label of a tick near the window's newest
/// edge, where it would be cut to a single character, and keep every other.
@Suite struct ChartTimeAxisTests {
    static let end = Date(timeIntervalSince1970: 1_800_000_000)
    static let window = end.addingTimeInterval(-300)...end

    @Test func aTickAtTheNewestEdgeGoesUnlabelled() {
        #expect(!ChartTimeAxis.labels(Self.end, in: Self.window))
        #expect(!ChartTimeAxis.labels(Self.end.addingTimeInterval(-10), in: Self.window))
    }

    @Test func ticksInsideTheWindowKeepTheirLabels() {
        #expect(ChartTimeAxis.labels(Self.end.addingTimeInterval(-60), in: Self.window))
        #expect(ChartTimeAxis.labels(Self.window.lowerBound, in: Self.window))
    }

    /// The popup's narrow chart drops labels further from the edge.
    @Test func aCompactChartLeavesMoreRoom() {
        let tick = Self.end.addingTimeInterval(-25)
        #expect(ChartTimeAxis.labels(tick, in: Self.window))
        #expect(!ChartTimeAxis.labels(tick, in: Self.window, compact: true))
    }

    @Test func anEmptyWindowLabelsItsOnlyTick() {
        #expect(ChartTimeAxis.labels(Self.end, in: Self.end...Self.end))
    }
}
