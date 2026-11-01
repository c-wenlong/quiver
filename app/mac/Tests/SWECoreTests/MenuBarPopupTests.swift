import Foundation
import Testing
@testable import SWECore

private func reading(_ harness: String, _ source: String, _ window: String) -> LimitReading {
    LimitReading(source: source, harness: harness, title: source, window: window, usedPercent: 10,
                 resetsAt: nil, fetchedAt: nil, isStale: false)
}

@Suite struct MenuBarPopupTests {
    private func defaults() -> UserDefaults {
        let suite = TestDefaults("MenuBarPopupTests")
        return suite
    }

    /// The settings the user's app had saved before per-account choices:
    /// every rule harness-level, nothing hidden outright.
    private func userStore() -> UserDefaults {
        let store = defaults()
        store.set([], forKey: MenuBarPopup.Key.hiddenHarnesses)
        store.set(["claude": ["5h", "7d Fable"], "codex": ["5h"], "cursor": ["api", "auto"]],
                  forKey: MenuBarPopup.Key.hiddenWindows)
        store.set(["claude", "codex", "devin", "cursor"], forKey: MenuBarPopup.Key.harnessOrder)
        return store
    }

    @Test func byDefaultEverythingShows() {
        let popup = MenuBarPopup(defaults: defaults())
        #expect(popup.showIncidents)
        #expect(popup.showStats)
        #expect(popup.shows(source: "claude-default", harness: "claude"))
        #expect(popup.shows(window: "5h", source: "claude-default", harness: "claude",
                            allWindows: ["5h"]))
    }

    @Test func aLegacyHarnessRuleAppliesToEveryAccountUntilOneIsSet() {
        let popup = MenuBarPopup(defaults: userStore())
        // Personal: two of its three windows follow the Claude rule.
        #expect(!popup.shows(window: "5h", source: "claude-default", harness: "claude",
                             allWindows: ["5h", "7d", "7d Fable"]))
        #expect(popup.shows(window: "7d", source: "claude-default", harness: "claude",
                            allWindows: ["5h", "7d", "7d Fable"]))
        #expect(!popup.shows(window: "7d Fable", source: "claude-default", harness: "claude",
                             allWindows: ["5h", "7d", "7d Fable"]))
        // Cortex's only window would all go under the rule, so the rule
        // is dropped: a legacy hide must not vanish a whole account.
        #expect(popup.shows(window: "5h", source: "claude-cortex", harness: "claude",
                            allWindows: ["5h"]))
        // Codex hides its 5h in both accounts, and Cursor's API and Auto
        // follow its own name, not an SWE profile.
        #expect(!popup.shows(window: "5h", source: "codex-nus", harness: "codex",
                             allWindows: ["5h", "weekly"]))
        #expect(!popup.shows(window: "5h", source: "codex-default", harness: "codex",
                             allWindows: ["5h", "weekly"]))
        #expect(!popup.shows(window: "api", source: "cursor", harness: "cursor",
                             allWindows: ["api", "auto", "included"]))
        #expect(!popup.shows(window: "auto", source: "cursor", harness: "cursor",
                             allWindows: ["api", "auto", "included"]))
        #expect(popup.shows(window: "included", source: "cursor", harness: "cursor",
                            allWindows: ["api", "auto", "included"]))
    }

    @Test func aSetterFreezesTheEffectiveStateThenChangesOneThing() {
        var popup = MenuBarPopup(defaults: userStore())
        let windows = ["5h", "7d", "7d Fable"]
        popup.setShown(true, window: "7d Fable", source: "claude-default",
                       harness: "claude", allWindows: windows)
        // The rule became Personal's own list, then 7d Fable came back.
        #expect(popup.hiddenSourceWindows["claude-default"] == ["5h"])
        #expect(popup.shows(window: "7d Fable", source: "claude-default", harness: "claude",
                            allWindows: windows))
        #expect(!popup.shows(window: "5h", source: "claude-default", harness: "claude",
                             allWindows: windows))
        // Cortex still follows the legacy rule, untouched.
        #expect(popup.shows(window: "5h", source: "claude-cortex", harness: "claude",
                            allWindows: ["5h"]))
    }

    @Test func hidingAWindowIsPerAccount() {
        var popup = MenuBarPopup(defaults: defaults())
        popup.setShown(false, window: "7d", source: "codex-default", harness: "codex",
                       allWindows: ["7d"])
        #expect(!popup.shows(window: "7d", source: "codex-default", harness: "codex",
                             allWindows: ["7d"]))
        #expect(popup.shows(window: "7d", source: "codex-nus", harness: "codex",
                            allWindows: ["7d"]))
        popup.setShown(true, window: "7d", source: "codex-default", harness: "codex",
                       allWindows: ["7d"])
        #expect(popup.shows(window: "7d", source: "codex-default", harness: "codex",
                            allWindows: ["7d"]))
    }

    @Test func anExplicitEmptyWindowListShowsEverything() {
        var popup = MenuBarPopup(defaults: userStore())
        let windows = ["5h", "7d", "7d Fable"]
        popup.setShown(true, window: "5h", source: "claude-default",
                       harness: "claude", allWindows: windows)
        popup.setShown(true, window: "7d Fable", source: "claude-default",
                       harness: "claude", allWindows: windows)
        // The entry stays, empty: Personal no longer follows the rule.
        #expect(popup.hiddenSourceWindows["claude-default"] == [])
        #expect(popup.shows(window: "5h", source: "claude-default", harness: "claude",
                            allWindows: windows))
        #expect(popup.shows(window: "7d Fable", source: "claude-default", harness: "claude",
                            allWindows: windows))
    }

    @Test func aHiddenSourceHidesItsWindowsToo() {
        var popup = MenuBarPopup(defaults: defaults())
        popup.setShown(false, source: "claude-default", harness: "claude",
                       allWindows: ["5h"])
        #expect(!popup.shows(source: "claude-default", harness: "claude"))
        #expect(!popup.shows(window: "5h", source: "claude-default", harness: "claude",
                             allWindows: ["5h"]))
        #expect(popup.shows(source: "claude-cortex", harness: "claude"))
    }

    @Test func showingASourceUnderALegacyHarnessHideKeepsItShown() {
        let store = userStore()
        store.set(["claude"], forKey: MenuBarPopup.Key.hiddenHarnesses)
        var popup = MenuBarPopup(defaults: store)
        #expect(!popup.shows(source: "claude-cortex", harness: "claude"))
        popup.setShown(true, source: "claude-cortex", harness: "claude",
                       allWindows: ["5h"])
        #expect(popup.shows(source: "claude-cortex", harness: "claude"))
        // Personal still follows the harness-level hide.
        #expect(!popup.shows(source: "claude-default", harness: "claude"))
    }

    @Test func enablingAnAccountKeepsItsWindowChoices() {
        let store = userStore()
        store.set(["claude"], forKey: MenuBarPopup.Key.hiddenHarnesses)
        var popup = MenuBarPopup(defaults: store)
        popup.setShown(true, source: "claude-default", harness: "claude",
                       allWindows: ["5h", "7d"])
        #expect(popup.shows(source: "claude-default", harness: "claude"))
        // The legacy 5h hide is the account's effective state, so it
        // moves with it; showing the account must not show what the
        // rule hid.
        #expect(!popup.shows(window: "5h", source: "claude-default", harness: "claude",
                             allWindows: ["5h", "7d"]))
        #expect(popup.shows(window: "7d", source: "claude-default", harness: "claude",
                            allWindows: ["5h", "7d"]))
    }

    @Test func hidingASourceUnderALegacyHideMaterialisesItsWindows() {
        let store = userStore()
        store.set(["claude"], forKey: MenuBarPopup.Key.hiddenHarnesses)
        var popup = MenuBarPopup(defaults: store)
        popup.setShown(false, window: "5h", source: "claude-cortex", harness: "claude",
                       allWindows: ["5h"])
        // The source was effectively hidden, so it stays hidden rather
        // than a window entry accidentally unhiding it.
        #expect(!popup.shows(source: "claude-cortex", harness: "claude"))
    }

    @Test func rowsGroupTheWindowsPerSourceInFirstSeenOrder() {
        let readings = [
            reading("claude", "claude-default", "5h"), reading("claude", "claude-default", "7d"),
            reading("claude", "claude-cortex", "5h"),
            reading("codex", "codex-nus", "7d"), reading("devin", "devin", ""),
            reading("cursor", "cursor", "auto"),
        ]
        let sources = [
            (source: "claude-default", harness: "claude", title: "Claude: Personal"),
            (source: "claude-cortex", harness: "claude", title: "Claude: Cortex"),
            (source: "devin", harness: "devin", title: "Devin"),
        ]
        let rows = MenuBarPopup.rows(in: readings, sources: sources)
        #expect(rows.map(\.source) == ["claude-default", "claude-cortex", "devin",
                                       "codex-nus", "cursor"])
        #expect(rows.first?.windows == ["5h", "7d"])
        #expect(rows.first { $0.source == "devin" }?.windows == [])
        #expect(rows.first { $0.source == "claude-default" }?.title == "Claude: Personal")
    }

    @Test func sourcesWithoutADraggedOrderFollowTheLegacyHarnessOrder() {
        let popup = MenuBarPopup(defaults: userStore())
        let sources: [(source: String, harness: String)] = [
            ("claude-default", "claude"), ("claude-cortex", "claude"),
            ("codex-default", "codex"), ("codex-nus", "codex"),
            ("devin", "devin"), ("cursor", "cursor"),
        ]
        #expect(popup.ordered(sources) == ["claude-default", "claude-cortex",
                                           "codex-default", "codex-nus", "devin", "cursor"])
        // A new account lands after its harness's other accounts, ahead
        // of the harnesses that follow it.
        #expect(popup.ordered(sources + [("claude-new", "claude"), ("droid", "droid")])
            == ["claude-default", "claude-cortex", "claude-new",
                "codex-default", "codex-nus", "devin", "cursor", "droid"])
    }

    @Test func draggingASourceBeforeAnotherWritesTheSourceOrder() {
        var popup = MenuBarPopup(defaults: userStore())
        let shown: [(source: String, harness: String)] = [
            ("claude-default", "claude"), ("claude-cortex", "claude"),
            ("codex-default", "codex"), ("codex-nus", "codex"),
            ("devin", "devin"), ("cursor", "cursor"),
        ]
        popup.move("cursor", before: "claude-default", in: shown)
        #expect(popup.sourceOrder == ["cursor", "claude-default", "claude-cortex",
                                      "codex-default", "codex-nus", "devin"])
        #expect(popup.ordered(shown) == popup.sourceOrder)
    }

    @Test func droppingBelowTheLastRowMovesASourceToTheEnd() {
        var popup = MenuBarPopup(defaults: userStore())
        let shown: [(source: String, harness: String)] = [
            ("claude-default", "claude"), ("claude-cortex", "claude"),
            ("codex-default", "codex"), ("cursor", "cursor"),
        ]
        popup.moveToEnd("claude-default", in: shown)
        #expect(popup.ordered(shown) == ["claude-cortex", "codex-default", "cursor",
                                         "claude-default"])
    }

    @Test func anUnstarredAccountKeepsItsPlaceForWhenItReturns() {
        var popup = MenuBarPopup(defaults: userStore())
        let shown: [(source: String, harness: String)] = [
            ("claude-default", "claude"), ("codex-default", "codex"), ("cursor", "cursor"),
        ]
        popup.move("cursor", before: "claude-default", in: shown)
        // cortex unstarred: it leaves the list; the dragged order stands.
        #expect(popup.ordered(shown) == ["cursor", "claude-default", "codex-default"])
        // cortex returns with no dragged slot of its own: it lands after
        // every explicitly ordered source.
        #expect(popup.ordered([("cursor", "cursor"), ("claude-default", "claude"),
                               ("claude-cortex", "claude"), ("codex-default", "codex")])
            == ["cursor", "claude-default", "codex-default", "claude-cortex"])
    }

    @Test func choicesSurviveARelaunch() {
        let store = userStore()
        var popup = MenuBarPopup(defaults: store)
        popup.showIncidents = false
        popup.showStats = false
        popup.setShown(false, source: "devin", harness: "devin", allWindows: [])
        popup.setShown(true, window: "7d Fable", source: "claude-default",
                       harness: "claude", allWindows: ["5h", "7d", "7d Fable"])
        popup.move("cursor", before: "claude-default",
                   in: [("claude-default", "claude"), ("cursor", "cursor")])
        popup.save(to: store)
        let back = MenuBarPopup(defaults: store)
        #expect(back == popup)
        #expect(!back.shows(source: "devin", harness: "devin"))
        #expect(back.shows(window: "7d Fable", source: "claude-default", harness: "claude",
                         allWindows: ["5h", "7d", "7d Fable"]))
        #expect(back.ordered([("claude-default", "claude"), ("cursor", "cursor")])
            == ["cursor", "claude-default"])
    }

    @Test func theLegacyKeysAreNeverWritten() {
        let store = userStore()
        let harnesses = store.stringArray(forKey: MenuBarPopup.Key.hiddenHarnesses)
        let windows = store.dictionary(forKey: MenuBarPopup.Key.hiddenWindows)
        let order = store.stringArray(forKey: MenuBarPopup.Key.harnessOrder)
        var popup = MenuBarPopup(defaults: store)
        popup.setShown(false, source: "claude-cortex", harness: "claude", allWindows: [])
        popup.setShown(true, window: "5h", source: "claude-default", harness: "claude",
                       allWindows: ["5h", "7d"])
        popup.move("cursor", before: "claude-default",
                   in: [("claude-default", "claude"), ("cursor", "cursor")])
        popup.save(to: store)
        #expect(store.stringArray(forKey: MenuBarPopup.Key.hiddenHarnesses) == harnesses)
        #expect(store.dictionary(forKey: MenuBarPopup.Key.hiddenWindows)
                as? [String: [String]] == windows as? [String: [String]])
        #expect(store.stringArray(forKey: MenuBarPopup.Key.harnessOrder) == order)
    }
}
