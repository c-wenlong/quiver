import Foundation
import Testing
@testable import SWECore

/// `swe sync status --json` and `swe sync --json`, against output captured
/// from real runs on throwaway repos (a bare remote, a `device/desk` clone).
@Suite struct HubSyncTests {
    @Test func statusDecodesDeviceModeAndWordsTheCard() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: try fixture("swe-sync-status"), stderr: ""))
        let status = try await SweClient(runner: runner).hubSyncStatus(fetch: true)
        #expect(runner.calls.last == ["sync", "status", "--fetch", "--json"])
        #expect(status.ready)
        #expect(status.mode == "device")
        #expect(status.branch == "device/desk")
        #expect(status.base == "origin/main")
        #expect(status.baseBehind == 1)
        #expect(status.changes?.map(\.group) == ["mcp.json", "skills"])
        let lines = status.lines
        #expect(lines.repo == "Device branch device/desk: merges origin/main in, pushes only device/desk.")
        #expect(lines.changes == "2 uncommitted files: mcp.json 1, skills 1. 1 new commit on origin/main.")
        #expect(lines.warning == nil)
    }

    @Test func aHubWithoutARepoCarriesItsProblem() {
        let status = HubSyncStatus(path: "/tmp/h/.swe", ready: false,
                                   problem: "~/.swe is not a git repository")
        #expect(status.lines.repo == "~/.swe is not a git repository")
        #expect(status.lines.warning == "~/.swe is not a git repository")
    }

    @Test func syncDecodesCommitMergeAndRelink() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: try fixture("swe-sync"), stderr: ""))
        let result = try await SweClient(runner: runner).hubSync()
        #expect(runner.calls.last == ["sync", "--json"])
        #expect(result.result == "synced")
        #expect(result.commit?.files.count == 2)
        #expect(result.merges == [.init(ref: "origin/main", commits: 1)])
        #expect(result.relinked?.changed == 2)
        #expect(result.outcome.text == "Committed 2 files, merged 1 commit, pushed device/desk, re-linked every harness.")
        #expect(!result.outcome.failed)
    }

    /// Relink problems ride on every result that relinked, a failed push
    /// included, since a retry won't relink again.
    @Test func relinkProblemsShowOnSuccessAndOnAFailedPush() throws {
        let json = #"""
        {"result": "push-failed", "dry_run": false, "mode": "single", "branch": "main",
         "commit": null, "merges": [{"ref": "origin/main", "commits": 1}], "conflicts": [],
         "held_back": [], "pushed": false, "push_error": "rejected",
         "relinked": {"changed": 1, "notes": ["1 path(s) blocked; swe init --force replaces them"],
                      "marketplaces": [{"harness": "claude", "marketplace": "mine", "error": "no claude"}]}}
        """#
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let failed = try decoder.decode(HubSyncResult.self, from: Data(json.utf8))
        #expect(failed.outcome.failed)
        #expect(failed.outcome.text == "Committed and merged, but the push failed: rejected. Sync again to retry. "
                + "1 path(s) blocked; swe init --force replaces them mine not registered in claude: no claude.")
        let synced = try decoder.decode(HubSyncResult.self, from: Data(
            json.replacingOccurrences(of: "\"push-failed\"", with: "\"synced\"")
                .replacingOccurrences(of: "\"pushed\": false", with: "\"pushed\": true").utf8))
        #expect(synced.outcome.failed)
        #expect(synced.outcome.text.hasPrefix("Merged 1 commit, pushed main, re-linked every harness. 1 path(s)"))
    }

    /// A conflict exits 1 and still prints the document.
    @Test func conflictDecodesThroughItsNonzeroExit() async throws {
        let runner = FakeRunner(.init(status: 1, stdout: try fixture("swe-sync-conflict"), stderr: ""))
        let result = try await SweClient(runner: runner).hubSync()
        #expect(result.result == "conflict")
        #expect(result.conflicts == ["AGENTS.md"])
        #expect(result.outcome.failed)
        #expect(result.outcome.text.contains("AGENTS.md"))
    }

    /// A repo sync can't work with at all exits 1 with no document.
    @Test func refusalThrowsSwesReason() async throws {
        let runner = FakeRunner(.init(status: 1, stdout: Data(),
                                      stderr: "swe sync: ~/.swe does not exist yet; run swe init first"))
        await #expect(throws: SweError.self) { try await SweClient(runner: runner).hubSync() }
    }
}
