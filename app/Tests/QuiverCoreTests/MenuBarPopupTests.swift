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

    @Test func aGivenOrderWinsOverTheReadingsOrderAndKeepsHarnessesWithNoReadings() {
        // Readings come most used first, so a busy harness can lead them.
        let readings = [reading("cursor", "cursor", "auto"), reading("codex", "nus", "7d"),
                        reading("claude", "personal", "5h"), reading("droid", "droid", "")]
        let groups = MenuBarPopup.harnesses(in: readings, order: ["claude", "codex", "devin", "cursor"])
        #expect(groups.map(\.harness) == ["claude", "codex", "devin", "cursor", "droid"])
        #expect(groups.first { $0.harness == "devin" }?.windows == [])
        #expect(groups.first?.windows == ["5h"])
        #expect(groups.first { $0.harness == "devin" }?.windows == [])
    }

    @Test func anEmptyDraggedOrderLeavesTheGivenOrderAlone() {
        let popup = MenuBarPopup(defaults: defaults())
        #expect(popup.ordered(["claude", "codex", "cursor", "devin"]) == ["claude", "codex", "cursor", "devin"])
    }

    @Test func draggingAHarnessBeforeAnotherReordersThePopup() {
        var popup = MenuBarPopup(defaults: defaults())
        let shown = ["claude", "codex", "cursor", "devin"]
        popup.move("devin", before: "claude", in: shown)
        #expect(popup.ordered(shown) == ["devin", "claude", "codex", "cursor"])
    }

    @Test func droppingBelowTheLastRowMovesAHarnessToTheEnd() {
        var popup = MenuBarPopup(defaults: defaults())
        let shown = ["claude", "codex", "cursor", "devin"]
        popup.moveToEnd("claude", in: shown)
        #expect(popup.ordered(shown) == ["codex", "cursor", "devin", "claude"])
    }

    @Test func anUnstarredHarnessKeepsItsPlaceForWhenItReturns() {
        var popup = MenuBarPopup(defaults: defaults())
        let shown = ["claude", "codex", "cursor", "devin"]
        popup.move("devin", before: "claude", in: shown)
        // devin unstarred: it leaves the list but keeps its slot.
        #expect(popup.ordered(["claude", "codex", "cursor"]) == ["claude", "codex", "cursor"])
        #expect(popup.ordered(shown) == ["devin", "claude", "codex", "cursor"])
    }

    @Test func aHarnessNewToTheListGoesAfterTheOrderedOnes() {
        var popup = MenuBarPopup(defaults: defaults())
        popup.move("devin", before: "claude", in: ["claude", "codex", "cursor", "devin"])
        #expect(popup.ordered(["claude", "codex", "cursor", "pi"]).last == "pi")
    }

    @Test func idsNotInTheListCannotBeMoved() {
        var popup = MenuBarPopup(defaults: defaults())
        popup.move("x", before: "claude", in: ["claude", "codex", "cursor", "devin"])
        #expect(popup.harnessOrder.isEmpty)
    }

    @Test func theDraggedOrderSurvivesARelaunch() {
        let store = defaults()
        var popup = MenuBarPopup(defaults: store)
        popup.move("devin", before: "claude", in: ["claude", "codex", "cursor", "devin"])
        popup.save(to: store)
        let back = MenuBarPopup(defaults: store)
        #expect(back.harnessOrder == popup.harnessOrder)
    }

    @Test func orderedHarnessesFollowsTheDraggedOrder() {
        var popup = MenuBarPopup(defaults: defaults())
        let readings = [reading("claude", "personal", "5h"), reading("devin", "devin", "7d")]
        popup.move("devin", before: "claude", in: ["claude", "devin"])
        let groups = popup.orderedHarnesses(in: readings, order: ["claude", "devin"])
        #expect(groups.map(\.harness) == ["devin", "claude"])
        #expect(groups[0].windows == ["7d"])
        #expect(groups[1].windows == ["5h"])
    }
}
