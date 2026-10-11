import Foundation
import Testing
@testable import QuiverCore

private func reading(_ harness: String, _ source: String, _ window: String) -> LimitReading {
    LimitReading(source: source, harness: harness, title: source, window: window, usedPercent: 10,
                 resetsAt: nil, fetchedAt: nil, isStale: false)
}

@Suite struct MenuBarPopupTests {
    private func defaults() -> UserDefaults {
        let name = "MenuBarPopupTests.\(UUID().uuidString)"
        let suite = UserDefaults(suiteName: name)!
        suite.removePersistentDomain(forName: name)
        return suite
    }

    @Test func byDefaultEverythingShows() {
        let popup = MenuBarPopup(defaults: defaults())
        #expect(popup.showIncidents)
        #expect(popup.showStats)
        #expect(popup.shows(harness: "claude"))
        #expect(popup.shows(window: "5h", of: "claude"))
    }

    @Test func choicesSurviveARelaunch() {
        let store = defaults()
        var popup = MenuBarPopup(defaults: store)
        popup.showIncidents = false
        popup.showStats = false
        popup.setShown(false, harness: "devin")
        popup.setShown(false, window: "7d Fable", of: "claude")
        popup.save(to: store)
        let back = MenuBarPopup(defaults: store)
        #expect(back == popup)
        #expect(!back.shows(harness: "devin"))
        #expect(!back.shows(window: "7d Fable", of: "claude"))
        #expect(back.shows(window: "5h", of: "claude"))
    }

    @Test func hidingAWindowIsPerHarness() {
        var popup = MenuBarPopup(defaults: defaults())
        popup.setShown(false, window: "7d", of: "codex")
        #expect(!popup.shows(window: "7d", of: "codex"))
        #expect(popup.shows(window: "7d", of: "claude"))
        popup.setShown(true, window: "7d", of: "codex")
        #expect(popup.shows(window: "7d", of: "codex"))
        #expect(popup.hiddenWindows.isEmpty)
    }

    @Test func aHiddenHarnessHidesItsWindowsToo() {
        var popup = MenuBarPopup(defaults: defaults())
        popup.setShown(false, harness: "claude")
        #expect(!popup.shows(window: "5h", of: "claude"))
    }

    @Test func harnessesAndTheirWindowsComeFromTheReadingsInOrder() {
        let readings = [
            reading("claude", "personal", "5h"), reading("claude", "personal", "7d"),
            reading("claude", "work", "5h"), reading("claude", "work", "7d Fable"),
            reading("codex", "nus", "7d"), reading("devin", "devin", ""), reading("cursor", "cursor", "auto"),
        ]
        let groups = MenuBarPopup.harnesses(in: readings)
        #expect(groups.map(\.harness) == ["claude", "codex", "devin", "cursor"])
        #expect(groups.first?.windows == ["5h", "7d", "7d Fable"])
        // A harness that reports one unnamed window has nothing to pick.
        #expect(groups.first { $0.harness == "devin" }?.windows == [])
    }
}
