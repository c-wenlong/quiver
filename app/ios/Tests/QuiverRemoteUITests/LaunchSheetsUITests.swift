import XCTest

/// Live UI verification for the launch Model field (#914): the same
/// behaviour the Mac sheets and the web sheets were checked for — the
/// field shows on a machine row that reports `model_at_launch`, prefills
/// the harness's last-used model, re-prefills on a harness change while
/// untouched, keeps a typed value (a deliberate clear included), and
/// disables with a note for a harness that can't take a pick.
///
/// Needs a paired hub in the simulator's store (pair once with
/// `simctl openurl <device> 'quiver://pair?…'` before running); the
/// assertions wait rather than assume, so a slow hub still passes.
/// The prefill cases also need real ledger data on that hub — a harness
/// that has never launched has no last-used model to fill — and the
/// resolve-sheet case needs the hub to see c-wenlong/quiver issues.
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

    private var modelField: XCUIElement {
        // The ModelField's TextField carries the "Harness default"
        // placeholder as its accessibility label.
        app.textFields["Harness default"]
    }

    private func chip(_ name: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label == %@", name)).firstMatch
    }

    /// An empty TextField reports its placeholder through `value`; both
    /// mean "no model picked".
    private func isEmptyOrPlaceholder(_ field: XCUIElement) -> Bool {
        let value = field.value as? String ?? ""
        return value.isEmpty || value == field.placeholderValue as? String
    }

    /// A harness chip that is not the current selection, so tapping it
    /// always switches. nil when every candidate is missing.
    private func otherChip(than names: [String]) -> XCUIElement? {
        names.map(chip).first { $0.exists && !$0.isSelected }
    }

    func testModelFieldPrefillsRefillsAndKeepsAnEdit() throws {
        try openNewSessionSheet()

        let field = modelField
        XCTAssertTrue(field.waitForExistence(timeout: 10), "Model field missing on the New session sheet")
        let harnesses = ["Codex", "Claude Code", "Claude", "Gemini"]

        // Prefill: whatever the harness's last-used model is, the field
        // starts non-empty for a model-capable default harness.
        XCTAssertFalse(isEmptyOrPlaceholder(field), "Model field was not prefilled")
        let initial = field.value as? String ?? ""

        // A harness switch re-prefills while the field is untouched: tap
        // whichever offered chip is not the current selection.
        guard let first = otherChip(than: harnesses) else {
            XCTFail("no alternate harness chip to switch to"); return
        }
        first.tap()
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        let refilled = field.value as? String ?? ""
        XCTAssertFalse(isEmptyOrPlaceholder(field), "Model field did not re-prefill after the switch")
        XCTAssertNotEqual(initial, refilled, "Model field kept the old suggestion across a harness switch")

        // A typed value survives the next harness switch.
        field.tap()
        field.typeText("-typed")
        let typed = field.value as? String ?? ""
        guard let second = otherChip(than: harnesses) else {
            XCTFail("no second harness chip to switch to"); return
        }
        second.tap()
        XCTAssertEqual(field.value as? String, typed, "typed model was overwritten by a harness switch")

        // A deliberate clear is an edit too: it stays cleared. Delete
        // once per character present (plus margin), then compare
        // against the placeholder, which empty fields report.
        field.tap()
        let chars = (field.value as? String ?? "").count
        field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: chars + 8))
        XCTAssertTrue(isEmptyOrPlaceholder(field), "field did not clear: \(field.value ?? "")")
        first.tap()
        XCTAssertTrue(isEmptyOrPlaceholder(field),
                      "cleared model was resurrected by a harness switch: \(field.value ?? "")")
    }

    func testAnUnsupportedHarnessDisablesTheField() throws {
        try openNewSessionSheet()

        let kilo = chip("Kilo")
        XCTAssertTrue(kilo.waitForExistence(timeout: 10), "no Kilo harness chip")
        kilo.tap()

        let field = modelField
        XCTAssertTrue(field.waitForExistence(timeout: 5), "Model row missing entirely — the disabled variant should still show")
        XCTAssertFalse(field.isEnabled, "Model field stayed editable for a harness that can't take a pick")
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@", "can't take a model")).firstMatch.waitForExistence(timeout: 3),
                      "the disabled note did not render")
    }

    /// The resolve sheet on a GitHub issue gets the same Model field.
    func testIssueResolveSheetShowsTheField() throws {
        // Deep links need a paired app on screen first.
        try XCTSkipUnless(app.buttons["New session"].waitForExistence(timeout: 15),
                          "no paired hub on this simulator — pair once with a quiver://pair link")
        XCUIDevice.shared.system.open(URL(string: "quiver://issue/c-wenlong/quiver/914")!)
        let resolve = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", "Resolve with agent")).firstMatch
        XCTAssertTrue(resolve.waitForExistence(timeout: 15), "no Resolve with agent button on the issue")
        resolve.tap()
        XCTAssertTrue(modelField.waitForExistence(timeout: 10), "Model field missing on the resolve sheet")
        XCTAssertFalse(isEmptyOrPlaceholder(modelField), "resolve-sheet Model field was not prefilled")
    }
}
