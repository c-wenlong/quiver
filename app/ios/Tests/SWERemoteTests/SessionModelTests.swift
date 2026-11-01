import XCTest
@testable import SWERemote

/// A session row's `model` (#643): decoded when the hub sends it, shown
/// short, and absent rather than a placeholder when it doesn't.
final class SessionModelTests: XCTestCase {
    func testDecodesTheModelAndToleratesItsAbsence() throws {
        let json = #"""
        {"sessions":[{"key":"claude:1","harness":"claude","status":"done","ownership":"observed",
          "attachable":true,"model":"claude-opus-4-5-20251101"},
          {"key":"codex:2","harness":"codex","status":"done","ownership":"observed","attachable":true}]}
        """#
        let rows = try Hub.decoder.decode(SessionsResponse.self, from: Data(json.utf8)).sessions
        XCTAssertEqual(rows[0].model, "claude-opus-4-5-20251101")
        XCTAssertEqual(rows[0].modelLabel, "claude-opus-4-5")
        XCTAssertNil(rows[1].model)
        XCTAssertNil(rows[1].modelLabel, "an older hub, or an unknown model, shows nothing")
    }

    func testShortLabelDropsProviderPathAndDateStamp() {
        XCTAssertEqual(ModelLabel.short("claude-opus-4-5-20251101"), "claude-opus-4-5")
        XCTAssertEqual(ModelLabel.short("anthropic/claude-opus-4-5"), "claude-opus-4-5")
        XCTAssertEqual(ModelLabel.short("openrouter/nvidia/nemotron-3:free"), "nemotron-3:free")
        XCTAssertEqual(ModelLabel.short("gpt-5.1-codex"), "gpt-5.1-codex", "a version is not a date stamp")
        XCTAssertEqual(ModelLabel.short("swe-2-max"), "swe-2-max")
        XCTAssertEqual(ModelLabel.short("model-2025110"), "model-2025110", "seven digits are kept")
        XCTAssertEqual(ModelLabel.short("-20251101"), "-20251101", "never shortened to nothing")
        XCTAssertNil(ModelLabel.short(nil))
        XCTAssertNil(ModelLabel.short("  "))
    }
}
