import Foundation
import Testing
@testable import SWECore

/// A limit reading with only what a test cares about.
func reading(
    _ source: String, _ window: String = "5h", used: Double, resetsAt: Date? = nil,
    fetchedAt: Date? = nil, stale: Bool = false, harness: String = "claude"
) -> LimitReading {
    LimitReading(source: source, harness: harness, title: source, window: window, usedPercent: used,
                 resetsAt: resetsAt, fetchedAt: fetchedAt, isStale: stale)
}

@Suite struct LimitReadingTests {
    let now = Date(timeIntervalSince1970: 1_791_703_800)

    func card(_ id: String, vendor: Vendor, name: String, usage: AccountUsage?) -> AccountsModel.Card {
        AccountsModel.Card(profile: Profile(id: id, vendor: vendor, name: name, isDefault: false, color: "#cf6848"),
                           usage: usage)
    }

    @Test func everyAccountWindowAndEveryOtherHarnessBecomesAReading() {
        let usage = AccountUsage(
            status: .ok,
            windows: [UsageWindow(label: "5h", usedPercent: 40, resetsAt: now + 3_600),
                      UsageWindow(label: "7d", usedPercent: 71, resetsAt: now + 86_400)],
            fetchedAt: now - 60)
        let cards = [card("claude-work", vendor: .claude, name: "Work", usage: usage),
                     card("codex-default", vendor: .codex, name: "Default", usage: nil)]
        let others = [
            OtherLimitsModel.Row(name: "devin", limit: RateLimit(status: .ok, usedPercent: 55, window: "7d",
                                                                 resetAt: now + 7_200, fetchedAt: now - 30)),
            // Nothing to show for a harness that needs signing in again.
            OtherLimitsModel.Row(name: "cursor", limit: RateLimit(status: .authRequired, usedPercent: 0)),
        ]

        let readings = LimitReading.all(cards: cards, others: others, now: now)

        #expect(readings.map(\.id) == ["claude-work|5h", "claude-work|7d", "devin|7d"])
        #expect(readings[1].title == "Claude: Work")
        #expect(readings[1].harness == "claude")
        #expect(readings[1].usedPercent == 71)
        #expect(readings[2].title == "Devin")
        // A display name reads like an account's: brand, then the name.
        let named = [OtherLimitsModel.Row(name: "devin", limit: RateLimit(status: .ok, usedPercent: 55, window: "7d",
                                                                          resetAt: now + 7_200, fetchedAt: now - 30),
                                          displayName: "Cortex")]
        #expect(LimitReading.all(cards: [], others: named, now: now).first?.title == "Devin: Cortex")
        #expect(readings[2].harness == "devin")
        #expect(readings[2].fetchedAt == now - 30)
    }

    @Test func aStaleAccountReadingIsMarkedSo() {
        let usage = AccountUsage(status: .expired, windows: [UsageWindow(label: "5h", usedPercent: 99, resetsAt: nil)],
                                 fetchedAt: now - 7_200, isStale: true)
        let readings = LimitReading.all(cards: [card("claude-default", vendor: .claude, name: "Default", usage: usage)],
                                        others: [], now: now)
        #expect(readings.map(\.isStale) == [true])
    }

    @Test func anOldReadingFromSweIsStale() {
        // swe falls back to its last reading for up to a day when a provider
        // fails, still with status ok; only its age gives it away.
        let others = [
            OtherLimitsModel.Row(name: "devin", limit: RateLimit(status: .ok, usedPercent: 95, window: "7d",
                                                                 resetAt: now + 86_400, fetchedAt: now - 3 * 3_600)),
            OtherLimitsModel.Row(name: "cursor", limit: RateLimit(status: .ok, usedPercent: 10, window: "auto",
                                                                  resetAt: now + 86_400, fetchedAt: now - 300)),
            OtherLimitsModel.Row(name: "copilot", limit: RateLimit(status: .ok, usedPercent: 10, window: "30d")),
        ]
        let readings = LimitReading.all(cards: [], others: others, now: now)
        #expect(readings.map(\.isStale) == [true, false, true])
        #expect(LimitReading.tightest(readings, now: now)?.source == "cursor")
    }

    @Test func aLongerRefreshWindowAcceptsOlderSweReadings() {
        let others = [OtherLimitsModel.Row(name: "devin", limit: RateLimit(
            status: .ok, usedPercent: 40, window: "7d", fetchedAt: now - 3_600))]
        // Default window: an hour-old reading is swe's outage fallback.
        #expect(LimitReading.all(cards: [], others: others, now: now).first?.isStale == true)
        // A 120-minute "refresh only what failed" window makes it
        // legitimately fresh, so it still feeds the menu bar and alerts.
        #expect(LimitReading.all(cards: [], others: others, now: now,
                                 sweFreshFor: 120 * 60).first?.isStale == false)

        // An unavailable window counts as stale at any window size.
        let unavailable = OtherLimitsModel.Row(name: "cursor", limit: RateLimit(
            status: .ok, usedPercent: 40, window: "7d", fetchedAt: now,
            windows: [.init(window: "grok_bot", usedPercent: 10, resetAt: nil,
                            unavailable: true, asOf: now - 3_600)]))
        #expect(LimitReading.all(cards: [], others: [unavailable], now: now,
                                 sweFreshFor: 120 * 60).first?.isStale == true)
    }

    @Test func aStaleWindowIsNotAResetOne() {
        // The widget says "reset" only for a window whose reset time passed.
        #expect(reading("a", used: 80, resetsAt: now + 600, stale: true).hasReset(now: now) == false)
        #expect(reading("a", used: 80, resetsAt: now - 1).hasReset(now: now))
        #expect(reading("a", used: 80).hasReset(now: now) == false)
    }

    @Test func aReadingAgesWhenTheAppStopsRefreshing() {
        // The widget keeps drawing after SWE quits; an hours-old figure
        // must not look current.
        let fresh = reading("a", used: 50, resetsAt: now + 86_400, fetchedAt: now - 600)
        #expect(!fresh.isOld(now: now))
        #expect(fresh.isOld(now: now + 2 * 3_600))
        #expect(reading("a", used: 50, stale: true).isOld(now: now))
        // No fetch time: judged by when the snapshot was written instead.
        #expect(!reading("a", used: 50).isOld(now: now, fallback: now - 60))
        #expect(reading("a", used: 50).isOld(now: now, fallback: now - 7_200))
    }

    @Test func theTightestIsTheMostUsedFreshWindowThatHasNotReset() {
        let readings = [
            reading("a", used: 60, resetsAt: now + 600),
            // Stale: an old figure must not drive the menu bar.
            reading("b", used: 98, resetsAt: now + 600, stale: true),
            // Its window reset since the reading; 97% is no longer true.
            reading("c", used: 97, resetsAt: now - 60),
            reading("d", "7d", used: 83, resetsAt: now + 86_400),
        ]
        #expect(LimitReading.tightest(readings, now: now)?.id == "d|7d")
    }

    @Test func aTieGoesToTheWindowThatResetsLast() {
        // The one you are stuck with longer is the tighter one.
        let readings = [reading("a", used: 80, resetsAt: now + 600),
                        reading("b", used: 80, resetsAt: now + 6_000),
                        reading("c", used: 80, resetsAt: nil)]
        #expect(LimitReading.tightest(readings, now: now)?.id == "b|5h")
    }

    @Test func nothingFreshMeansNoTightest() {
        #expect(LimitReading.tightest([], now: now) == nil)
        #expect(LimitReading.tightest([reading("a", used: 50, stale: true)], now: now) == nil)
    }

    @Test func readingsSortMostUsedFirstForThePanelAndWidget() {
        let readings = [reading("a", used: 10), reading("b", used: 90), reading("c", used: 50, stale: true)]
        #expect(LimitReading.byPressure(readings, now: now).map(\.source) == ["b", "a", "c"])
    }
}

@Suite struct LimitAlerterTests {
    let now = Date(timeIntervalSince1970: 1_791_703_800)
    let reset = Date(timeIntervalSince1970: 1_791_703_800 + 3_600)

    @Test func crossingNinetyNotifiesOnce() {
        var alerter = LimitAlerter()
        #expect(alerter.evaluate([reading("a", used: 85, resetsAt: reset, fetchedAt: now)], now: now).isEmpty)
        let first = alerter.evaluate([reading("a", used: 91, resetsAt: reset, fetchedAt: now)], now: now + 60)
        #expect(first.map(\.kind) == [.crossed])
        #expect(first.first?.reading.usedPercent == 91)
        // Still above, then higher, then a wobble down and back: one crossing, one notification.
        #expect(alerter.evaluate([reading("a", used: 95, resetsAt: reset, fetchedAt: now)], now: now + 120).isEmpty)
        #expect(alerter.evaluate([reading("a", used: 88, resetsAt: reset, fetchedAt: now)], now: now + 180).isEmpty)
        #expect(alerter.evaluate([reading("a", used: 92, resetsAt: reset, fetchedAt: now)], now: now + 240).isEmpty)
    }

    @Test func eachWindowIsItsOwnCrossing() {
        var alerter = LimitAlerter()
        let alerts = alerter.evaluate([reading("a", "5h", used: 90, resetsAt: reset),
                                       reading("a", "7d", used: 93, resetsAt: reset + 86_400),
                                       reading("b", "5h", used: 40)], now: now)
        #expect(alerts.map(\.reading.id) == ["a|5h", "a|7d"])
    }

    @Test func aResetAfterACrossingNotifiesAndRearms() {
        var alerter = LimitAlerter()
        _ = alerter.evaluate([reading("a", used: 96, resetsAt: reset, fetchedAt: now)], now: now)
        // The first reading after the reset time, back near zero.
        let after = reading("a", used: 2, resetsAt: reset + 5 * 3_600, fetchedAt: reset + 120)
        let alerts = alerter.evaluate([after], now: reset + 120)
        #expect(alerts.map(\.kind) == [.reset])
        #expect(alerter.evaluate([after], now: reset + 300).isEmpty)
        // A new window that fills up notifies again.
        let full = reading("a", used: 90, resetsAt: reset + 5 * 3_600, fetchedAt: reset + 4 * 3_600)
        #expect(alerter.evaluate([full], now: reset + 4 * 3_600).map(\.kind) == [.crossed])
    }

    @Test func aResetTimeThatMovesOnCountsAsAReset() {
        // Codex reports seconds until the reset, so a fetch taken a moment
        // early can still be the new window: the later reset time says so.
        var alerter = LimitAlerter()
        _ = alerter.evaluate([reading("a", used: 100, resetsAt: reset, fetchedAt: now)], now: now)
        let alerts = alerter.evaluate([reading("a", used: 0, resetsAt: reset + 18_000, fetchedAt: reset - 5)],
                                      now: reset - 5)
        #expect(alerts.map(\.kind) == [.reset])
    }

    @Test func aFewSecondsOfJitterInTheResetTimeIsNotAReset() {
        var alerter = LimitAlerter()
        _ = alerter.evaluate([reading("a", used: 92, resetsAt: reset, fetchedAt: now)], now: now)
        #expect(alerter.evaluate([reading("a", used: 85, resetsAt: reset + 4, fetchedAt: now + 300)],
                                 now: now + 300).isEmpty)
        #expect(alerter.state.alerted["a|5h"] != nil)
    }

    @Test func noResetNotificationForAWindowThatNeverCrossed() {
        var alerter = LimitAlerter()
        _ = alerter.evaluate([reading("a", used: 70, resetsAt: reset, fetchedAt: now)], now: now)
        #expect(alerter.evaluate([reading("a", used: 1, resetsAt: reset + 18_000, fetchedAt: reset + 60)],
                                 now: reset + 60).isEmpty)
    }

    @Test func fallingWellBelowWithoutAResetRearmsQuietly() {
        // A window with no reset time that drops back: no reset to announce,
        // but the next climb past 90 is a new crossing.
        var alerter = LimitAlerter()
        _ = alerter.evaluate([reading("a", used: 91, fetchedAt: now)], now: now)
        #expect(alerter.evaluate([reading("a", used: 60, fetchedAt: now + 60)], now: now + 60).isEmpty)
        #expect(alerter.evaluate([reading("a", used: 90, fetchedAt: now + 120)], now: now + 120).map(\.kind) == [.crossed])
    }

    @Test func staleAndAlreadyResetReadingsNeverNotify() {
        var alerter = LimitAlerter()
        #expect(alerter.evaluate([reading("a", used: 99, resetsAt: reset, stale: true),
                                  reading("b", used: 99, resetsAt: now - 1, fetchedAt: now - 600)], now: now).isEmpty)
    }

    @Test func theStateSurvivesARelaunch() throws {
        var alerter = LimitAlerter()
        _ = alerter.evaluate([reading("a", used: 95, resetsAt: reset, fetchedAt: now)], now: now)
        let data = try JSONEncoder().encode(alerter.state)
        var relaunched = LimitAlerter(state: try JSONDecoder().decode(LimitAlerter.State.self, from: data))
        #expect(relaunched.evaluate([reading("a", used: 96, resetsAt: reset, fetchedAt: now)], now: now + 60).isEmpty)
    }

    @Test func oldEntriesArePruned() {
        // An account removed while over 90 must not keep its entry forever.
        var alerter = LimitAlerter()
        _ = alerter.evaluate([reading("gone", used: 95, resetsAt: reset)], now: now)
        _ = alerter.evaluate([], now: now + 31 * 86_400)
        #expect(alerter.state.alerted.isEmpty)
    }

    @Test func messagesNameTheAccountWindowAndReset() {
        var r = reading("claude-work", "7d", used: 91.6, resetsAt: now + 2 * 3_600)
        r.title = "Claude: Work"
        let crossed = LimitAlert(kind: .crossed, reading: r)
        #expect(crossed.title == "Claude: Work is at 92% of its 7d limit")
        #expect(crossed.body(now: now) == "Resets in 2 hours.")
        let noReset = LimitAlert(kind: .crossed, reading: reading("x", used: 95))
        #expect(noReset.body(now: now) == "No reset time reported.")
        var after = reading("claude-work", "7d", used: 3, resetsAt: now + 7 * 86_400)
        after.title = "Claude: Work"
        let back = LimitAlert(kind: .reset, reading: after)
        #expect(back.title == "Claude: Work: 7d limit has reset")
        #expect(back.body(now: now) == "Now at 3%.")
        var grok = reading("cursor", "grok_bot", used: 95)
        grok.title = "Cursor: Cortex"
        #expect(LimitAlert(kind: .crossed, reading: grok).title == "Cursor: Cortex is at 95% of its Grok Bot limit")
    }
}

@Suite struct ResetFormatTests {
    let now = Date(timeIntervalSince1970: 1_791_703_800)

    @Test func resetsReadLikeTokenTrackersWidget() {
        #expect(UsageFormat.resetIn(now + 12 * 60, now: now) == "in 12m")
        #expect(UsageFormat.resetIn(now + 4 * 3_600 + 28 * 60, now: now) == "in 4h 28m")
        #expect(UsageFormat.resetIn(now + 3 * 3_600, now: now) == "in 3h")
        #expect(UsageFormat.resetIn(now + 30 * 3_600, now: now) == "in 1d")
        #expect(UsageFormat.resetIn(now + 25 * 86_400, now: now) == "in 25d")
        #expect(UsageFormat.resetIn(now + 20, now: now) == "in 1m")
        #expect(UsageFormat.resetIn(now - 5, now: now) == "now")
    }

    @Test func agesReadTheSameWay() {
        #expect(UsageFormat.ago(now - 90 * 60, now: now) == "1h ago")
        #expect(UsageFormat.ago(now - 5 * 60, now: now) == "5m ago")
        #expect(UsageFormat.ago(now - 3 * 86_400, now: now) == "3d ago")
        #expect(UsageFormat.ago(now + 10, now: now) == "just now")
    }

    @Test func signInExpiryNamesTheDayOnlyWhenItIsNotToday() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try #require(TimeZone(identifier: "Asia/Singapore"))
        // 2026-10-18 14:30 in Singapore.
        let now = try #require(calendar.date(from: DateComponents(year: 2026, month: 10, day: 18, hour: 14, minute: 30)))
        func at(_ day: Int, _ hour: Int, _ minute: Int) throws -> Date {
            try #require(calendar.date(from: DateComponents(year: 2026, month: 10, day: day, hour: hour, minute: minute)))
        }
        #expect(UsageFormat.signIn(expiresAt: try at(18, 22, 41), now: now, calendar: calendar)
                == "Sign-in expires at 22:41")
        #expect(UsageFormat.signIn(expiresAt: try at(18, 0, 8), now: now, calendar: calendar)
                == "Sign-in expired at 00:08")
        #expect(UsageFormat.signIn(expiresAt: try at(17, 0, 8), now: now, calendar: calendar)
                == "Sign-in expired yesterday at 00:08")
        #expect(UsageFormat.signIn(expiresAt: try at(19, 6, 5), now: now, calendar: calendar)
                == "Sign-in expires tomorrow at 06:05")
        #expect(UsageFormat.signIn(expiresAt: try at(11, 9, 0), now: now, calendar: calendar)
                == "Sign-in expired on 11 Oct at 09:00")
    }
}

@Suite struct RefreshFreshWindowTests {
    @Test func theFieldClampsIntoTheAcceptedRange() {
        #expect(RefreshFreshWindow.minutes(0) == 1)
        #expect(RefreshFreshWindow.minutes(-4) == 1)
        #expect(RefreshFreshWindow.minutes(5) == 5)
        #expect(RefreshFreshWindow.minutes(999) == 120)
    }

    @Test func theDefaultIsFiveMinutesAndSecondsConverts() {
        #expect(RefreshFreshWindow.defaultMinutes == 5)
        #expect(RefreshFreshWindow.seconds(5) == 300)
        #expect(RefreshFreshWindow.seconds(0) == 60)
    }
}
