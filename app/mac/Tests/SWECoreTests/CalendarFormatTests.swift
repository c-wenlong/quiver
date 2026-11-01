import Foundation
import Testing
@testable import SWECore

/// `CalendarFormat`: the one set of rules every block, card, row and
/// sheet prints times, durations and session labels with.
@Suite struct CalendarFormatTests {
    private func clock(_ date: Date) -> String {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f.string(from: date)
    }

    @Test func aBlockInsideOneMinutePrintsOneTime() {
        let start = utc("2026-10-13T11:02:00Z")
        let end = utc("2026-10-13T11:02:45Z")
        #expect(CalendarFormat.range(start: start, end: end) == clock(start))
    }

    @Test func aRangeSpanningMinutesPrintsBothTimes() {
        let start = utc("2026-10-13T11:02:00Z")
        let end = utc("2026-10-13T11:47:30Z")
        #expect(CalendarFormat.range(start: start, end: end)
                == "\(clock(start))–\(clock(end))")
    }

    @Test func matchingClockTimesAcrossDaysStayARange() {
        // 11:02 today to 11:02 tomorrow is not one moment.
        let start = utc("2026-10-13T11:02:00Z")
        let end = utc("2026-10-14T11:02:00Z")
        #expect(CalendarFormat.range(start: start, end: end)
                == "\(clock(start))–\(clock(end))")
    }

    @Test func activeTimeUnderAMinuteIsLessThanOne() {
        #expect(CalendarFormat.duration(0) == "<1m")
        #expect(CalendarFormat.duration(37) == "<1m")
        #expect(CalendarFormat.duration(60) == "1m")
        #expect(CalendarFormat.duration(59 * 60 + 30) == "59m")
        #expect(CalendarFormat.duration(2 * 3600 + 5 * 60) == "2h 5m")
    }

    @Test func aMissingTitleKeepsAReadableSessionId() {
        #expect(CalendarFormat.sessionTitle(nil, sessionId: "natural-riverbed")
                == "natural-riverbed")
        #expect(CalendarFormat.sessionTitle("", sessionId: "s") == "s")
    }

    /// A UUID-like id, or any id past 24 characters, is cut to its first
    /// eight; a short readable slug stays whole.
    @Test func aMissingTitleShortensUUIDLikeAndLongIds() {
        let uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        let packed = "a1b2c3d4e5f67890abcdef1234567890"
        #expect(CalendarFormat.sessionTitle(nil, sessionId: uuid) == "a1b2c3d4…")
        #expect(CalendarFormat.sessionTitle(nil, sessionId: packed) == "a1b2c3d4…")
        let long = "natural-riverbed-shared-kayak-more"
        #expect(CalendarFormat.sessionTitle(nil, sessionId: long) == "natural-…")
        #expect(CalendarFormat.sessionTitle("Real title", sessionId: packed)
                == "Real title")
    }
}
