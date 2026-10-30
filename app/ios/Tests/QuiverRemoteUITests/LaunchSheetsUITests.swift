import XCTest

/// Live UI verification for the launch Model menu (#914): the menu shows
/// on a machine row that reports `model_at_launch`, starts on the
/// harness's newest recent model, re-defaults on a harness change while
/// untouched, keeps a picked Harness default and a typed Other value
/// across switches, and disables with a note for a harness that can't
/// take a pick.
///
/// Needs a paired hub in the simulator's store (pair once with
/// `simctl openurl <device> 'quiver://pair?…'` before running); the
/// assertions wait rather than assume, so a slow hub still passes.
/// The default cases also need real ledger data on that hub — a harness
/// that has never launched has no recent model to start on — and the
/// resolve-sheet case needs the hub to see c-wenlong/quiver issues. What
/// the hub lacks (an unpaired simulator, no second harness, no Kilo, the
/// issue out of reach) skips the case instead of failing it.
final class LaunchSheetsUITests: XCTestCase {

    private var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launch()
    }

    /// The landing button only exists when the app is paired; an
    /// unpaired simulator skips rather than fails, so these can stay in
    /// the scheme's default test action.
    private func openNewSessionSheet() throws {
        let newSession = app.buttons["New session"]
        try XCTSkipUnless(newSession.waitForExistence(timeout: 15),
                          "no paired hub on this simulator — pair once with a quiver://pair link")
        newSession.tap()
    }

    /// The Model menu's button; its accessibility value is the label of
    /// the current choice.
    private var modelMenu: XCUIElement {
        app.buttons["model-menu"]
    }

    private var otherField: XCUIElement {
        app.textFields["model-other"]
    }

    private func menuValue() -> String {
        modelMenu.value as? String ?? ""
    }

    private func chip(_ name: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label == %@", name)).firstMatch
    }

    /// A harness chip that is not the current selection, so tapping it
    /// always switches. nil when every candidate is missing.
    private func otherChip(than names: [String]) -> XCUIElement? {
        names.map(chip).first { $0.exists && !$0.isSelected }
    }

    /// Opens the menu and taps the entry labelled `label`.
    private func pick(_ label: String) {
        modelMenu.tap()
        let item = app.buttons.matching(NSPredicate(format: "label == %@", label)).firstMatch
        XCTAssertTrue(item.waitForExistence(timeout: 5), "no \(label) entry in the Model menu")
        item.tap()
    }

    func testModelMenuDefaultsFollowsAndKeepsAPick() throws {
        try openNewSessionSheet()

        XCTAssertTrue(modelMenu.waitForExistence(timeout: 10), "Model menu missing on the New session sheet")
        let harnesses = ["Codex", "Claude Code", "Claude", "Gemini"]

        // Default: the harness's newest recent model, not Harness default.
        let initial = menuValue()
        XCTAssertNotEqual(initial, "Harness default", "Model menu did not start on a recent model")

        // The menu lists Harness default, the recents, then Other.
        modelMenu.tap()
        for label in ["Harness default", initial, "Other…"] {
            XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label == %@", label)).firstMatch.waitForExistence(timeout: 5),
                          "Model menu has no \(label) entry")
        }
        // Close it without a pick: a tap on the navigation title.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.08)).tap()

        // An untouched menu follows a harness switch to the new
        // harness's newest model. A hub offering only one of these
        // harnesses has nothing to switch to: its setup, not a menu bug.
        guard let first = otherChip(than: harnesses) else {
            throw XCTSkip("the hub offers no alternate harness to switch to")
        }
        first.tap()
        XCTAssertNotEqual(menuValue(), initial, "Model menu kept another harness's model across a switch")

        // An explicit Harness default survives the next switch.
        pick("Harness default")
        XCTAssertEqual(menuValue(), "Harness default")
        guard let second = otherChip(than: harnesses) else {
            throw XCTSkip("the hub offers no second harness to switch to")
        }
        second.tap()
        XCTAssertEqual(menuValue(), "Harness default", "a picked Harness default was re-defaulted by a switch")

        // Other opens a field; what is typed there survives a switch.
        pick("Other…")
        XCTAssertTrue(otherField.waitForExistence(timeout: 5), "Other did not open the model field")
        otherField.tap()
        otherField.typeText("my-model")
        first.tap()
        XCTAssertEqual(menuValue(), "Other…", "Other was re-defaulted by a switch")
        XCTAssertEqual(otherField.value as? String, "my-model", "typed model was lost on a switch")
    }

    func testAnUnsupportedHarnessDisablesTheMenu() throws {
        try openNewSessionSheet()

        let kilo = chip("Kilo")
        try XCTSkipUnless(kilo.waitForExistence(timeout: 10), "Kilo isn't installed on the hub")
        kilo.tap()

        XCTAssertTrue(modelMenu.waitForExistence(timeout: 5), "Model row missing entirely — the disabled variant should still show")
        XCTAssertFalse(modelMenu.isEnabled, "Model menu stayed usable for a harness that can't take a pick")
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "can't take a model")).firstMatch.waitForExistence(timeout: 3),
                      "the disabled note did not render")
    }

    /// The resolve sheet on a GitHub issue gets the same Model menu.
    func testIssueResolveSheetShowsTheMenu() throws {
        // Deep links need a paired app on screen first.
        try XCTSkipUnless(app.buttons["New session"].waitForExistence(timeout: 15),
                          "no paired hub on this simulator — pair once with a quiver://pair link")
        XCUIDevice.shared.system.open(URL(string: "quiver://issue/c-wenlong/quiver/914")!)
        let resolve = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", "Resolve with agent")).firstMatch
        try XCTSkipUnless(resolve.waitForExistence(timeout: 15),
                          "the hub can't show c-wenlong/quiver#914 (not pinned, or gh signed out)")
        resolve.tap()
        XCTAssertTrue(modelMenu.waitForExistence(timeout: 10), "Model menu missing on the resolve sheet")
        XCTAssertNotEqual(menuValue(), "Harness default", "resolve-sheet Model menu did not start on a recent model")
    }
}
