import CoreGraphics
import Testing
@testable import QuiverApp

/// The now pill on Calendar's hour axis hides the hour label it would
/// half-cover, and only that one.
@Suite struct CalendarHourAxisTests {
    static let hour: CGFloat = 48

    static func offset(_ h: Int, _ m: Int) -> CGFloat { (CGFloat(h) + CGFloat(m) / 60) * hour }

    @Test func aPillJustAfterTheHourHidesThatHoursLabel() {
        #expect(CalendarHourAxis.pillCovers(hour: 20, nowOffset: Self.offset(20, 14), hourHeight: Self.hour))
        #expect(!CalendarHourAxis.pillCovers(hour: 21, nowOffset: Self.offset(20, 14), hourHeight: Self.hour))
        #expect(!CalendarHourAxis.pillCovers(hour: 19, nowOffset: Self.offset(20, 14), hourHeight: Self.hour))
    }

    @Test func aPillJustBeforeTheHourHidesTheComingLabel() {
        #expect(CalendarHourAxis.pillCovers(hour: 20, nowOffset: Self.offset(19, 58), hourHeight: Self.hour))
        #expect(!CalendarHourAxis.pillCovers(hour: 19, nowOffset: Self.offset(19, 58), hourHeight: Self.hour))
    }

    /// View › Zoom grows the pill and the labels but not the hours, so a
    /// label clear of the pill at 100% meets it at 150%.
    @Test func zoomWidensTheReach() {
        let now = Self.offset(20, 27)
        #expect(!CalendarHourAxis.pillCovers(hour: 20, nowOffset: now, hourHeight: Self.hour))
        #expect(CalendarHourAxis.pillCovers(hour: 20, nowOffset: now, hourHeight: Self.hour, scale: 1.5))
    }

    @Test func aPillMidHourHidesNothing() {
        let now = Self.offset(20, 30)
        #expect((0..<24).allSatisfy { !CalendarHourAxis.pillCovers(hour: $0, nowOffset: now, hourHeight: Self.hour) })
    }
}
