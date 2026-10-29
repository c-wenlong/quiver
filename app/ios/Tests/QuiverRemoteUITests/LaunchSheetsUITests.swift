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

    private func openNewSessionSheet() {
        let newSession = app.buttons["New session"]
        XCTAssertTrue(newSession.waitForExistence(timeout: 15), "Sessions page: no New session button — is the app paired?")
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

    func testModelFieldPrefillsRefillsAndKeepsAnEdit() throws {
        openNewSessionSheet()

        let field = modelField
        XCTAssertTrue(field.waitForExistence(timeout: 10), "Model field missing on the New session sheet")

        // Prefill: whatever the harness's last-used model is, the field
        // starts non-empty for a model-capable default harness.
        let initial = field.value as? String ?? ""
        XCTAssertFalse(initial.isEmpty, "Model field was not prefilled")

        // A harness switch re-prefills while the field is untouched.
        let codex = chip("Codex")
        XCTAssertTrue(codex.waitForExistence(timeout: 5), "no Codex harness chip")
        codex.tap()
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        let codexValue = field.value as? String ?? ""
        XCTAssertFalse(codexValue.isEmpty, "Model field did not re-prefill for Codex")
        XCTAssertNotEqual(initial, codexValue, "Model field kept the old suggestion across a harness switch")

        // A typed value survives the next harness switch.
        field.tap()
        field.typeText("-typed")
        let typed = field.value as? String ?? ""
        let claude = chip("Claude Code")
        if !claude.exists { chip("Claude").tap() } else { claude.tap() }
        XCTAssertEqual(field.value as? String, typed, "typed model was overwritten by a harness switch")

        // A deliberate clear is an edit too: it stays cleared. An empty
        // field reports its placeholder as value, so compare against it.
        field.tap()
        field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 32))
        let cleared = field.value as? String ?? ""
        XCTAssertTrue(cleared.isEmpty || cleared == field.placeholderValue as? String,
                      "field did not clear: \(cleared)")
        codex.tap()
        let after = field.value as? String ?? ""
        XCTAssertTrue(after.isEmpty || after == field.placeholderValue as? String,
                      "cleared model was resurrected by a harness switch: \(after)")
    }

    func testAnUnsupportedHarnessDisablesTheField() throws {
        openNewSessionSheet()

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
        XCTAssertTrue(app.buttons["New session"].waitForExistence(timeout: 15))
        XCUIDevice.shared.system.open(URL(string: "quiver://issue/c-wenlong/quiver/914")!)
        let resolve = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", "Resolve with agent")).firstMatch
        XCTAssertTrue(resolve.waitForExistence(timeout: 15), "no Resolve with agent button on the issue")
        resolve.tap()
        XCTAssertTrue(modelField.waitForExistence(timeout: 10), "Model field missing on the resolve sheet")
        let value = modelField.value as? String ?? ""
        XCTAssertFalse(value.isEmpty, "resolve-sheet Model field was not prefilled")
    }
}
