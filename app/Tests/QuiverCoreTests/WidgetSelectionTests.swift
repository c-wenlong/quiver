import Foundation
import Testing
@testable import QuiverCore

@Suite struct SourceSelectionTests {
    let defaults = UserDefaults(suiteName: "SourceSelectionTests-\(UUID().uuidString)")!
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
}

@Suite struct WidgetSelectionTests {
    let defaults = UserDefaults(suiteName: "WidgetSelectionTests-\(UUID().uuidString)")!

    @Test func aHiddenHarnessLeavesTheOtherWidgets() {
        var selection = WidgetSelection()
        selection.setShown(false, harness: "codex", in: .summary)
        #expect(!selection.shows(harness: "codex", in: .summary))
        #expect(selection.shows(harness: "codex", in: .heatmap))
        #expect(selection.shows(harness: "codex", in: .models))
    }

    @Test func aHarnessAddedLaterShows() {
        let selection = WidgetSelection(defaults: defaults)
        #expect(selection.shows(harness: "some-new-harness", in: .summary))
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

        let back = WidgetSelection(defaults: defaults)
        #expect(!back.shows(harness: "codex", in: .models))
        #expect(!back.shows(harness: "cursor", in: .heatmap))
        #expect(back.shows(harness: "codex", in: .summary))
        #expect(!back.limits.shows(source: "claude-work"))
    }
}
