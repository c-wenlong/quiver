import XCTest
@testable import QuiverRemote

/// The chat's Model menu (#643): where it shows, what it lists, what a
/// typed id may be, and when a sent switch stops showing as pending.
final class ModelSwitchTests: XCTestCase {
    private func row(harness: String = "claude", ownership: String = "attached", mode: String? = "tmux",
                     modes: [String]? = ["tmux"], model: String? = "claude-opus-4-5") -> SessionRow {
        var r = SessionRow(key: "\(harness):1", harness: harness, agent: nil, cwd: "/tmp", title: "t", lastActiveAt: 0,
                           status: "done", machineId: "m1", ownership: ownership, attachable: true)
        r.mode = mode
        r.modelModes = modes
        r.model = model
        return r
    }

    private func machine(recent: [String: [String]]?) -> Machine {
        var m = Machine(id: "m1", name: "mac", os: "darwin", online: true, lastSeenAt: nil, harnesses: ["claude"])
        m.recentModels = recent
        return m
    }

    func testMenuShowsOnlyOnAnAttachedRowWhoseChannelSwitches() {
        XCTAssertTrue(row().canSwitchModel)
        XCTAssertFalse(row(ownership: "observed").canSwitchModel, "watching only")
        XCTAssertFalse(row(mode: "headless").canSwitchModel, "headless answers unsupported_control")
        XCTAssertFalse(row(mode: nil).canSwitchModel)
        XCTAssertFalse(row(harness: "opencode", modes: []).canSwitchModel, "opencode's TUI has no /model <id>")
        XCTAssertFalse(row(modes: nil).canSwitchModel, "an older hub can't say, so no menu")
    }

    func testDecodesModeModelModesAndTheOwnershipEventsMode() throws {
        let json = #"""
        {"sessions":[{"key":"claude:1","harness":"claude","status":"done","ownership":"attached","attachable":true,
          "mode":"tmux","tmux":"qv-1","model":"claude-opus-4-5","model_modes":["tmux"]}]}
        """#
        let r = try Hub.decoder.decode(SessionsResponse.self, from: Data(json.utf8)).sessions[0]
        XCTAssertEqual(r.mode, "tmux")
        XCTAssertEqual(r.modelModes, ["tmux"])
        XCTAssertTrue(r.canSwitchModel)

        let event = try Hub.decoder.decode(OwnershipData.self, from: Data(
            #"{"ownership":"attached","tmux":"qv-1","mode":"tmux","kept":false}"#.utf8))
        XCTAssertEqual(event.mode, "tmux")
        let old = try Hub.decoder.decode(OwnershipData.self, from: Data(#"{"ownership":"observed"}"#.utf8))
        XCTAssertNil(old.mode)
    }

    func testChoicesAreTheRecentModelsWithTheCurrentOneAlwaysListed() {
        let m = machine(recent: ["claude": ["claude-sonnet-4-5", "claude-opus-4-5"], "codex": ["gpt-5.2"]])
        XCTAssertEqual(ModelSwitchOptions.choices(for: row(), on: m), ["claude-sonnet-4-5", "claude-opus-4-5"])
        XCTAssertEqual(ModelSwitchOptions.choices(for: row(model: "claude-haiku-4-5"), on: m),
                       ["claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-5"], "the running model leads when not recent")
        XCTAssertEqual(ModelSwitchOptions.choices(for: row(model: nil), on: nil), [])
        XCTAssertEqual(ModelSwitchOptions.choices(for: row(), on: nil), ["claude-opus-4-5"])
    }

    func testTypedIdMustBeOneToken() {
        XCTAssertEqual(ModelSwitchOptions.validValue("  sonnet \n"), "sonnet")
        XCTAssertEqual(ModelSwitchOptions.validValue("anthropic/claude-opus-4-5"), "anthropic/claude-opus-4-5")
        XCTAssertEqual(ModelSwitchOptions.validValue("gpt-5.1-codex:high"), "gpt-5.1-codex:high")
        XCTAssertNil(ModelSwitchOptions.validValue(""))
        XCTAssertNil(ModelSwitchOptions.validValue("opus please"), "a space would type prompt text")
        XCTAssertNil(ModelSwitchOptions.validValue("opus\n/clear"), "a newline would send a second command")
        XCTAssertNil(ModelSwitchOptions.validValue("-opus"))
        XCTAssertNil(ModelSwitchOptions.validValue("ópus"))
        XCTAssertNil(ModelSwitchOptions.validValue(String(repeating: "a", count: 129)))
        XCTAssertNotNil(ModelSwitchOptions.validValue(String(repeating: "a", count: 128)))
    }

    func testPendingSettlesWhenTheRowsModelMoves() {
        let sent = Date(timeIntervalSince1970: 1_000)
        let s = ModelSwitch(requested: "claude-sonnet-4-5", from: "claude-opus-4-5", sentAt: sent)
        XCTAssertFalse(s.settled(currentModel: "claude-opus-4-5", listRefreshedAt: sent.addingTimeInterval(60)),
                       "no reply yet: still asked for, and true")
        XCTAssertTrue(s.settled(currentModel: "claude-sonnet-4-5-20250929", listRefreshedAt: nil),
                      "the transcript names a new model, whatever its spelling")
    }

    func testPendingSettlesOnceAReplyHasHadTimeToReachTheRow() {
        let sent = Date(timeIntervalSince1970: 1_000)
        var s = ModelSwitch(requested: "sonnet", from: "claude-opus-4-5", sentAt: sent)
        let reply = sent.addingTimeInterval(20)
        s.noteReply(at: reply, messageTs: reply.timeIntervalSince1970)
        XCTAssertFalse(s.settled(currentModel: "claude-opus-4-5", listRefreshedAt: reply.addingTimeInterval(5)),
                       "the hub may not have read the reply yet")
        XCTAssertTrue(s.settled(currentModel: "claude-opus-4-5", listRefreshedAt: reply.addingTimeInterval(13)),
                      "the reply ran on opus: the switch didn't take, so the header says so")
        // A later reply doesn't move the clock forward.
        s.noteReply(at: reply.addingTimeInterval(100), messageTs: nil)
        XCTAssertEqual(s.replyAt, reply)
    }

    func testOnlyThisSwitchsOwnFailureCancelsIt() throws {
        let s = ModelSwitch(requested: "sonnet", from: "opus", sentAt: Date(), clientId: "c-1")
        func result(_ json: String) throws -> CommandResult {
            try Hub.decoder.decode(CommandResult.self, from: Data(json.utf8))
        }
        XCTAssertTrue(s.failed(by: try result(#"{"op":"control","ok":false,"client_id":"c-1","note":"pane unreachable"}"#)))
        XCTAssertFalse(s.failed(by: try result(#"{"op":"control","ok":false,"note":"an interrupt failed"}"#)),
                       "another control's failure, or one from an older hub that drops the id")
        XCTAssertFalse(s.failed(by: try result(#"{"op":"control","ok":false,"client_id":"c-2"}"#)))
        XCTAssertFalse(s.failed(by: try result(#"{"op":"control","ok":true,"client_id":"c-1"}"#)))
    }

    func testRepliesFromBeforeTheSwitchDontCount() {
        let sent = Date(timeIntervalSince1970: 10_000)
        var s = ModelSwitch(requested: "sonnet", from: "opus", sentAt: sent)
        s.noteReply(at: sent.addingTimeInterval(-1), messageTs: nil)
        XCTAssertNil(s.replyAt, "arrived before the send")
        s.noteReply(at: sent.addingTimeInterval(5), messageTs: 1_000)
        XCTAssertNil(s.replyAt, "a replayed message from long before the send")
        s.noteReply(at: sent.addingTimeInterval(5), messageTs: 9_990)
        XCTAssertNotNil(s.replyAt, "clock skew between phone and machine is tolerated")
    }
}
