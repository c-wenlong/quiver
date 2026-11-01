import Foundation
import Testing
@testable import SWECore

@Suite struct SourceSelectionTests {
    let defaults = TestDefaults("SourceSelectionTests")
    let keys = (sources: "widgetLimitsHiddenSources", windows: "widgetLimitsHiddenSourceWindows")

    @Test func anEmptySelectionShowsEverything() {
        let selection = SourceSelection(defaults: defaults, hiddenSourcesKey: keys.sources,
                                        hiddenWindowsKey: keys.windows)
        #expect(selection.shows(source: "claude-work"))
        #expect(selection.shows(window: "5h", source: "claude-work"))
    }

    @Test func aHiddenSourceDisappearsWithItsWindows() {
        var selection = SourceSelection(hiddenSourcesKey: keys.sources, hiddenWindowsKey: keys.windows)
        selection.setShown(false, source: "claude-work")
        #expect(!selection.shows(source: "claude-work"))
        #expect(!selection.shows(window: "5h", source: "claude-work"))
        #expect(selection.shows(window: "5h", source: "codex"))
        selection.setShown(true, source: "claude-work")
        #expect(selection.shows(source: "claude-work"))
    }

    @Test func aHiddenWindowLeavesTheSource() {
        var selection = SourceSelection(hiddenSourcesKey: keys.sources, hiddenWindowsKey: keys.windows)
        selection.setShown(false, window: "5h", source: "claude-work")
        #expect(selection.shows(source: "claude-work"))
        #expect(!selection.shows(window: "5h", source: "claude-work"))
        #expect(selection.shows(window: "7d", source: "claude-work"))
    }

    @Test func choicesSurviveAReload() {
        var selection = SourceSelection(hiddenSourcesKey: keys.sources, hiddenWindowsKey: keys.windows)
        selection.setShown(false, source: "claude-work")
        selection.setShown(false, window: "5h", source: "codex")
        selection.save(to: defaults)

        let back = SourceSelection(defaults: defaults, hiddenSourcesKey: keys.sources,
                                   hiddenWindowsKey: keys.windows)
        #expect(!back.shows(source: "claude-work"))
        #expect(!back.shows(window: "5h", source: "codex"))
        #expect(back.shows(window: "7d", source: "codex"))
    }

    @Test func filterKeepsShownSourcesAndWindows() {
        var selection = SourceSelection(hiddenSourcesKey: keys.sources, hiddenWindowsKey: keys.windows)
        selection.setShown(false, source: "hidden")
        selection.setShown(false, window: "5h", source: "codex")
        let readings = [reading("hidden", used: 1), reading("codex", "5h", used: 2),
                        reading("codex", "7d", used: 3)]
        #expect(selection.filter(readings).map(\.id) == ["codex|7d"])
    }

    @Test func anEmptyOrderPassesTheGivenOrderThrough() {
        let selection = SourceSelection(hiddenSourcesKey: keys.sources, hiddenWindowsKey: keys.windows)
        #expect(selection.ordered(["a", "b", "c"]) == ["a", "b", "c"])
    }

    @Test func movingBeforeATargetWritesTheOrder() {
        var selection = SourceSelection(hiddenSourcesKey: keys.sources, hiddenWindowsKey: keys.windows)
        selection.move("c", before: "a", in: ["a", "b", "c"])
        #expect(selection.ordered(["a", "b", "c"]) == ["c", "a", "b"])
        // The first move freezes the shown order.
        #expect(selection.sourceOrder == ["c", "a", "b"])
    }

    @Test func movingToTheEndDropsBelowTheLastShownRow() {
        var selection = SourceSelection(hiddenSourcesKey: keys.sources, hiddenWindowsKey: keys.windows)
        selection.moveToEnd("a", in: ["a", "b", "c"])
        #expect(selection.ordered(["a", "b", "c"]) == ["b", "c", "a"])
    }

    @Test func unknownIdsAreIgnored() {
        var selection = SourceSelection(hiddenSourcesKey: keys.sources, hiddenWindowsKey: keys.windows)
        selection.move("ghost", before: "a", in: ["a", "b"])
        selection.move("a", before: "ghost", in: ["a", "b"])
        selection.moveToEnd("ghost", in: ["a", "b"])
        #expect(selection.sourceOrder.isEmpty)
    }

    @Test func hidingAWindowOrSourceNeverMovesItsRank() {
        var selection = SourceSelection(hiddenSourcesKey: keys.sources, hiddenWindowsKey: keys.windows)
        let readings = [reading("a", "5h", used: 90), reading("b", "5h", used: 80),
                        reading("c", "7d", used: 5)]
        #expect(selection.defaultRank(readings) == ["a", "b", "c"])
        // a's heaviest window, and b's only one, hidden: rows stay put.
        selection.setShown(false, window: "5h", source: "a")
        selection.setShown(false, window: "5h", source: "b")
        selection.setShown(false, source: "c")
        #expect(selection.defaultRank(readings) == ["a", "b", "c"])
    }

    @Test func theOrderSurvivesAReloadThroughTheWidgetKey() {
        var selection = SourceSelection(defaults: defaults,
                                        hiddenSourcesKey: keys.sources, hiddenWindowsKey: keys.windows,
                                        sourceOrderKey: SourceSelection.widgetSourceOrderKey)
        selection.move("codex", before: "claude", in: ["claude", "codex", "devin"])
        selection.save(to: defaults)

        let back = SourceSelection(defaults: defaults,
                                   hiddenSourcesKey: keys.sources, hiddenWindowsKey: keys.windows,
                                   sourceOrderKey: SourceSelection.widgetSourceOrderKey)
        #expect(back.sourceOrder == ["codex", "claude", "devin"])
        #expect(back.ordered(["claude", "codex", "devin"]) == ["codex", "claude", "devin"])
    }
}

@Suite struct WidgetSelectionTests {
    let defaults = TestDefaults("WidgetSelectionTests")

    @Test func aHiddenHarnessLeavesTheOtherWidgets() {
        var selection = WidgetSelection()
        selection.setShown(false, harness: "codex", in: .summary)
        #expect(!selection.shows(harness: "codex", in: .summary))
        #expect(selection.shows(harness: "codex", in: .heatmap))
        #expect(selection.shows(harness: "codex", in: .models))
        #expect(selection.shows(harness: "codex", in: .compute))
        selection.setShown(false, harness: "claude", in: .compute)
        #expect(!selection.shows(harness: "claude", in: .compute))
        #expect(selection.shows(harness: "claude", in: .summary))
    }

    @Test func aHarnessAddedLaterShows() {
        let selection = WidgetSelection(defaults: defaults)
        #expect(selection.shows(harness: "some-new-harness", in: .summary))
    }

    @Test func aHiddenHarnessStaysListedWithoutUsage() {
        var selection = WidgetSelection()
        selection.setShown(false, harness: "codex", in: .summary)
        // Usage left the range, but the switch must stay to undo it.
        #expect(selection.listedHarnesses(withUsage: ["claude"], for: .summary) == ["claude", "codex"])
        #expect(selection.listedHarnesses(withUsage: ["claude"], for: .models) == ["claude"])
        selection.setShown(true, harness: "codex", in: .summary)
        #expect(selection.listedHarnesses(withUsage: ["claude"], for: .summary) == ["claude"])
    }

    @Test func harnessChoicesSaveUnderTheirOwnKeys() {
        var selection = WidgetSelection()
        selection.setShown(false, harness: "codex", in: .models)
        selection.setShown(false, harness: "cursor", in: .heatmap)
        selection.limits.setShown(false, source: "claude-work")
        selection.save(to: defaults)

        #expect(defaults.stringArray(forKey: "widgetHarnesses.models") == ["codex"])
        #expect(defaults.stringArray(forKey: "widgetHarnesses.heatmap") == ["cursor"])
        #expect(defaults.stringArray(forKey: "widgetHarnesses.summary") == [])
        #expect(defaults.stringArray(forKey: "widgetLimitsHiddenSources") == ["claude-work"])

        selection.summaryShowsTotal = false
        selection.save(to: defaults)
        #expect(defaults.bool(forKey: "widgetSummaryShowsTotal") == false)
        let reloaded = WidgetSelection(defaults: defaults)
        #expect(!reloaded.summaryShowsTotal)

        let back = WidgetSelection(defaults: defaults)
        #expect(!back.shows(harness: "codex", in: .models))
        #expect(!back.shows(harness: "cursor", in: .heatmap))
        #expect(back.shows(harness: "codex", in: .summary))
        #expect(!back.limits.shows(source: "claude-work"))
    }
}
