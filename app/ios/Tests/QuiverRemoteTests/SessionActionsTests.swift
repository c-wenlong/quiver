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
        XCTAssertEqual(SessionAction.leading(watching), [.attach, .open])
        XCTAssertEqual(SessionAction.trailing(watching), [.pin, .rename, .fork])

        // Busy and only watched: nothing to take over yet, but the swipe
        // still opens the chat.
        XCTAssertEqual(SessionAction.leading(row("b", status: "active")), [.open])
        XCTAssertEqual(SessionAction.leading(row("n", attachable: false)), [.open])

        let attached = row("a", status: "active", ownership: "attached")
        XCTAssertEqual(SessionAction.leading(attached), [.detach, .open])

        let pinnedNoFork = row("p", canFork: nil, pinnedAt: 5)
        XCTAssertEqual(SessionAction.trailing(pinnedNoFork), [.unpin, .rename], "no can_fork, no Fork")
        XCTAssertEqual(SessionAction.menu(attached), [.detach, .open, .pin, .rename, .fork])
    }

    func testEveryActionHasASymbolThatExists() {
        for action in [SessionAction.attach, .detach, .open, .pin, .unpin, .rename, .fork, .clear] {
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

    func testASwipeDetachNeverCutsATurnAndAWorkingRowAsks() {
        // The row's status can be a poll old, so even an idle-looking row
        // detaches with when_idle, which the hub runs at once if it's idle.
        XCTAssertEqual(DetachPlan.of(row("i", ownership: "attached")), .detach(.whenIdle))
        XCTAssertEqual(DetachPlan.of(row("f", status: "followup", ownership: "attached")), .detach(.whenIdle))
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

    func testRenameCountsCodePointsAsTheHubDoes() {
        // A flag is two code points; "e" plus a combining acute is two.
        let flag = "\u{1F1F8}\u{1F1EC}"
        let accent = "e\u{0301}"
        XCTAssertEqual(RenameRules.length(flag), 2)
        XCTAssertEqual(RenameRules.length(accent), 2)
        let flags = String(repeating: flag, count: 70)
        XCTAssertEqual(flags.count, 70, "70 characters")
        XCTAssertEqual(RenameRules.length(RenameRules.clamp(flags)), 120, "but cut at 120 code points")
        XCTAssertEqual(RenameRules.clamp(flags), String(repeating: flag, count: 60))
        let accents = String(repeating: accent, count: 61)
        XCTAssertEqual(RenameRules.normalized(accents).unicodeScalars.count, 120)
        // Cut where the hub cuts, then trailing space dropped like rstrip.
        let spaced = String(repeating: "a", count: 119) + " b"
        XCTAssertEqual(RenameRules.normalized(spaced), String(repeating: "a", count: 119))
    }

    func testPinChoicesOutliveStaleListsAndStaleReplies() {
        func list(_ pinned: Bool) -> [SessionRow] { [row("k", pinnedAt: pinned ? 1 : nil)] }
        var ledger = PinLedger()

        // A list fetched before the pin was answered keeps the pin, even
        // when it lands after the answer.
        let before = ledger.mark
        let v1 = ledger.choose("k", pinned: true, now: 50)
        XCTAssertTrue(ledger.answered("k", version: v1))
        XCTAssertTrue(ledger.overlay(list(false), fetchedFrom: before)[0].isPinned)
        // One fetched after the answer is the hub's word, and the choice is forgotten.
        let after = ledger.mark
        XCTAssertFalse(ledger.overlay(list(false), fetchedFrom: after)[0].isPinned)
        XCTAssertNil(ledger.choices["k"])

        // Pin, then unpin before the pin's reply: the pin's reply is stale.
        let pin = ledger.choose("k", pinned: true, now: 60)
        let unpin = ledger.choose("k", pinned: false, now: 61)
        XCTAssertFalse(ledger.answered("k", version: pin), "an older reply is not applied")
        XCTAssertFalse(ledger.refused("k", version: pin), "nor does an older refusal revert the row")
        XCTAssertFalse(ledger.overlay(list(true), fetchedFrom: ledger.mark)[0].isPinned, "the unpin still holds")
        XCTAssertEqual(ledger.overlay(SessionMeta(title: "k", harnessTitle: "k", pinned: true, pinnedAt: 60), key: "k").pinned,
                       false, "an event can't undo a choice still out")
        XCTAssertTrue(ledger.refused("k", version: unpin))
        XCTAssertNil(ledger.choices["k"])
    }

    @MainActor
    func testAReplyFromAHubThisPhoneLeftIsDropped() async throws {
        let cred = Credential(deviceId: "d", credential: "c", name: nil)
        let app = AppModel(hub: PairedHub(baseURL: URL(string: "http://hub-a.test:8477")!, credential: cred))
        app.revokeOnHub = { _, _ in }
        let same = try await app.onSameHub { _ in 1 }
        XCTAssertEqual(same, 1)
        let moved = try await app.onSameHub { _ -> Int in
            app.unpair()
            return 2
        }
        XCTAssertNil(moved, "paired elsewhere while it was out")

        let other = AppModel(hub: PairedHub(baseURL: URL(string: "http://hub-b.test:8477")!, credential: cred))
        other.revokeOnHub = { _, _ in }
        let failed = try await other.onSameHub { _ -> Int in
            other.unpair()
            throw HubError(status: 409, code: "x", message: "old hub")
        }
        XCTAssertNil(failed, "nor is its error shown")
    }

    @MainActor
    func testAStartedLaunchCantBeStartedAgain() async {
        let flow = LaunchFlow()
        var posts = 0
        let post: () async throws -> LaunchStatus = {
            posts += 1
            return LaunchStatus(launchId: "l1", state: "started", key: "claude:fork", note: nil, queued: nil)
        }
        let fetch: (String) async throws -> LaunchStatus = { _ in
            LaunchStatus(launchId: "l1", state: "started", key: "claude:fork", note: nil, queued: nil)
        }
        let key = await flow.run(post: post, fetch: fetch, interval: .zero)
        XCTAssertEqual(key, "claude:fork")
        XCTAssertTrue(flow.busy, "still busy while the sheet refreshes and closes")
        XCTAssertTrue(flow.opening)
        let again = await flow.run(post: post, fetch: fetch, interval: .zero)
        XCTAssertNil(again)
        XCTAssertEqual(posts, 1, "a second Start posts nothing")
    }

    func testPinnedRowsNameTheirMachineWhenThereAreSeveral() {
        let a = row("a", cwd: "/Users/demo/projects/api")
        var b = row("b", cwd: "/Users/demo/projects/web")
        let machines = [Machine(id: "m1", name: "studio.local", os: nil, online: true, lastSeenAt: nil, harnesses: nil),
                        Machine(id: "m2", name: "laptop", os: nil, online: true, lastSeenAt: nil, harnesses: nil)]
        XCTAssertEqual(SessionGrouping.pinnedPlace(a, rows: [a, b], machines: machines), "projects/api")
        b = SessionRow(key: "b", harness: "claude", agent: nil, cwd: "/Users/demo/projects/web", title: "b", lastActiveAt: 1,
                       status: "done", machineId: "m2", ownership: "observed", attachable: true)
        XCTAssertEqual(SessionGrouping.pinnedPlace(a, rows: [a, b], machines: machines), "studio · projects/api")
        XCTAssertEqual(SessionGrouping.pinnedPlace(b, rows: [a, b], machines: machines), "laptop · projects/web")
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
