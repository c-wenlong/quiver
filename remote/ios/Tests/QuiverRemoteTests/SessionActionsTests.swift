import XCTest
import UIKit
@testable import QuiverRemote

final class SessionActionsTests: XCTestCase {
    private func row(_ key: String, status: String? = "done", ownership: String = "observed", attachable: Bool = true,
                     canFork: Bool? = true, pinnedAt: Double? = nil, ago: Double = 60, cwd: String = "/Users/demo/projects/api",
                     now: Double = 1_000_000) -> SessionRow {
        var r = SessionRow(key: key, harness: "claude", agent: "Claude Code", cwd: cwd, title: key, lastActiveAt: now - ago,
                           status: status, machineId: "m1", ownership: ownership, attachable: attachable)
        r.canFork = canFork
        r.pinned = pinnedAt != nil
        r.pinnedAt = pinnedAt
        return r
    }

    func testWhichActionsShowForEachState() {
        let watching = row("w")
        XCTAssertEqual(SessionAction.leading(watching), [.attach])
        XCTAssertEqual(SessionAction.trailing(watching), [.pin, .rename, .fork])

        // Busy and only watched: nothing to take over yet.
        XCTAssertEqual(SessionAction.leading(row("b", status: "active")), [])
        XCTAssertEqual(SessionAction.leading(row("n", attachable: false)), [])

        let attached = row("a", status: "active", ownership: "attached")
        XCTAssertEqual(SessionAction.leading(attached), [.detach])

        let pinnedNoFork = row("p", canFork: nil, pinnedAt: 5)
        XCTAssertEqual(SessionAction.trailing(pinnedNoFork), [.unpin, .rename], "no can_fork, no Fork")
        XCTAssertEqual(SessionAction.menu(attached), [.detach, .pin, .rename, .fork])
    }

    func testEveryActionHasASymbolThatExists() {
        for action in [SessionAction.attach, .detach, .pin, .unpin, .rename, .fork] {
            XCTAssertNotNil(UIImage(systemName: action.symbol), action.symbol)
        }
    }

    func testPinnedSectionComesFirstInPinOrderAndLeavesItsFolder() {
        let now = 1_000_000.0
        let groups = SessionGrouping.groupsWithPins([
            row("a", now: now),
            row("late", pinnedAt: 900, ago: 30, now: now),
            row("early", pinnedAt: 100, ago: 5 * 86_400, cwd: "/Users/demo/old", now: now),
            row("b", ago: 120, now: now),
        ], machines: [], now: now)
        XCTAssertEqual(groups.map(\.title), ["Pinned", "projects/api"])
        XCTAssertEqual(groups[0].rows.map(\.key), ["early", "late"], "ordered by pinned_at, an old one too")
        XCTAssertEqual(groups[1].rows.map(\.key), ["a", "b"])

        let unpinned = SessionGrouping.groupsWithPins([row("a", now: now)], machines: [], now: now)
        XCTAssertEqual(unpinned.map(\.id), [unpinned[0].id])
        XCTAssertNotEqual(unpinned[0].id, "pinned")
    }

    func testIdleDetachesAtOnceAndWorkingAsks() {
        XCTAssertEqual(DetachPlan.of(row("i", ownership: "attached")), .immediately)
        XCTAssertEqual(DetachPlan.of(row("f", status: "followup", ownership: "attached")), .immediately)
        XCTAssertEqual(DetachPlan.of(row("a", status: "active", ownership: "attached")), .ask)
        XCTAssertEqual(DetachPlan.of(row("q", status: "approval", ownership: "attached")), .ask)
        XCTAssertEqual(DetachPlan.choices.map(\.rawValue), ["when_idle", "keep", "now"], "the default first")
    }

    func testRenameRules() {
        XCTAssertEqual(RenameRules.clamp(String(repeating: "x", count: 130)).count, 120)
        XCTAssertEqual(RenameRules.clamp("one\ntwo"), "one two")
        XCTAssertEqual(RenameRules.normalized("  Fix   the\tbuild "), "Fix the build")
        XCTAssertFalse(RenameRules.canSave("   ", current: "Old"), "clearing goes through Use the original name")
        XCTAssertFalse(RenameRules.canSave(" Old ", current: "Old"), "no change")
        XCTAssertTrue(RenameRules.canSave("New name", current: "Old"))
        XCTAssertTrue(RenameRules.canSave("New name", current: nil))
    }

    func testCustomTitleIsAnOverrideOfTheHarnessTitle() {
        var r = row("k")
        XCTAssertFalse(r.hasCustomTitle, "an old hub sends no harness_title")
        r.apply(SessionMeta(title: "Mine", harnessTitle: "k", pinned: true, pinnedAt: 7))
        XCTAssertTrue(r.hasCustomTitle)
        XCTAssertEqual(r.displayTitle, "Mine")
        XCTAssertEqual(r.pinnedAt, 7)
        r.apply(SessionMeta(title: "k", harnessTitle: "k", pinned: false, pinnedAt: 7))
        XCTAssertFalse(r.hasCustomTitle)
        XCTAssertFalse(r.isPinned)
        XCTAssertNil(r.pinnedAt, "an unpinned row keeps no pin time")
    }

    func testForkErrorsReadPlainly() {
        func error(_ code: String, _ status: Int = 409) -> HubError {
            HubClient.error(status: status, body: Data(#"{"error":{"code":"\#(code)","message":"hub words"}}"#.utf8))
        }
        XCTAssertTrue(error("fork_pending").message.hasPrefix("Another fork in this folder is still starting"))
        XCTAssertTrue(error("fork_unsupported").message.contains("can't be forked"))
        XCTAssertEqual(error("too_many_pins").message, "Too many sessions are pinned. Unpin one first.")
        XCTAssertEqual(error("control_failed").message, "hub words", "a refused keep says what the hub said")
    }

    func testDecodesTheNewRowFields() throws {
        let json = #"""
        {"sessions":[{"key":"claude:1","harness":"claude","title":"My name","harness_title":"Harness name",
          "status":"done","ownership":"observed","attachable":true,"pinned":true,"pinned_at":1700000000.5,
          "can_fork":true,"last_active_at":5},
          {"key":"codex:2","harness":"codex","title":"Plain","status":"done","ownership":"observed","attachable":true}]}
        """#
        let rows = try Hub.decoder.decode(SessionsResponse.self, from: Data(json.utf8)).sessions
        XCTAssertEqual(rows[0].harnessTitle, "Harness name")
        XCTAssertTrue(rows[0].isPinned)
        XCTAssertEqual(rows[0].pinnedAt, 1_700_000_000.5)
        XCTAssertEqual(rows[0].canFork, true)
        XCTAssertTrue(rows[0].hasCustomTitle)
        XCTAssertFalse(rows[1].isPinned)
        XCTAssertNil(rows[1].canFork)

        let meta = try Hub.decoder.decode(SessionMeta.self, from: Data(
            #"{"ok":true,"title":"","harness_title":"H","pinned":false,"pinned_at":null}"#.utf8))
        XCTAssertEqual(meta, SessionMeta(title: "", harnessTitle: "H", pinned: false, pinnedAt: nil))
    }

    func testSwipeReleaseOpensRunsOrCloses() {
        let m = SwipeMath(width: 400, leadingCount: 1, trailingCount: 3)
        XCTAssertEqual(m.release(offset: -50, velocity: 0), .close)
        XCTAssertEqual(m.release(offset: -130, velocity: 0), .openTrailing)
        XCTAssertEqual(m.release(offset: -60, velocity: -1200), .openTrailing, "a flick opens it")
        XCTAssertEqual(m.release(offset: -130, velocity: 1500), .close, "a flick back closes it")
        XCTAssertEqual(m.release(offset: -300, velocity: 0), .runTrailing, "a full swipe runs the edge action")
        XCTAssertEqual(m.release(offset: 50, velocity: 0), .openLeading)
        XCTAssertEqual(m.release(offset: 240, velocity: 0), .runLeading)
        let noLeading = SwipeMath(width: 400, leadingCount: 0, trailingCount: 2)
        XCTAssertLessThan(noLeading.offset(200), 30, "a side with no actions barely moves")
        XCTAssertEqual(noLeading.release(offset: noLeading.offset(200), velocity: 0), .close)
        XCTAssertEqual(m.offset(-200), -200)
        XCTAssertGreaterThan(m.offset(-600), -600, "past the row it rubber-bands")
    }
}
