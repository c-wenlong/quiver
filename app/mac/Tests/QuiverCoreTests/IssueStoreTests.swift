import Foundation
import Testing
@testable import QuiverCore

/// `IssueStore`: the dedup, re-notify and auto-resolve rules the toasts,
/// badge and Doctor rows all read from `~/.quiver/data/issues.json`.
@Suite struct IssueStoreTests {
    private func tempStore(clock: TestClock? = nil) -> IssueStore {
        let clock = clock ?? TestClock()
        var store = IssueStore(url: FileManager.default.temporaryDirectory
            .appending(path: "issues-\(UUID().uuidString).json"))
        store.now = { clock.now }
        return store
    }

    private func remedy(_ summary: String = "Refresh Cursor's session") -> UsageRemedy {
        UsageRemedy(kind: "run-command", title: "Refresh the session",
                    summary: summary, steps: ["Run cursor-agent once."],
                    command: "cursor-agent status")
    }

    @Test func theSameFailureDeduplicatesAcrossPolls() {
        let store = tempStore()
        let first = store.record(harness: "cursor", status: .authRequired,
                                 remedy: remedy())
        #expect(first.shouldNotify)
        // The next three polls find the same failure: one issue, no
        // re-announce, and the occurrence count grows.
        for _ in 0..<3 {
            let again = store.record(harness: "cursor", status: .authRequired,
                                     remedy: remedy())
            #expect(!again.shouldNotify)
        }
        let all = store.all()
        #expect(all.count == 1)
        #expect(all[0].id == "cursor/auth-required")
        #expect(all[0].occurrences == 4)
        #expect(!all[0].resolved)
    }

    @Test func aToldFailureStaysToldAcrossRestarts() {
        let url = FileManager.default.temporaryDirectory
            .appending(path: "issues-\(UUID().uuidString).json")
        var store = IssueStore(url: url)
        let epoch = Date(timeIntervalSince1970: 1_791_720_000)
        store.now = { epoch }
        let outcome = store.record(harness: "devin", status: .permissionRequired,
                                   remedy: nil)
        #expect(outcome.shouldNotify)
        store.markNotified(outcome.issue.id)
        // A new store on the same file is the next launch: the marker on
        // disk keeps the same episode from toasting again.
        var reopened = IssueStore(url: url)
        reopened.now = { epoch.addingTimeInterval(300) }
        let again = reopened.record(harness: "devin", status: .permissionRequired,
                                    remedy: nil)
        #expect(!again.shouldNotify)
        #expect(again.issue.lastNotified == epoch)
    }

    @Test func aSuccessfulPollResolvesAndARecurrenceRetells() {
        let store = tempStore()
        _ = store.record(harness: "cursor", status: .authRequired,
                         remedy: remedy())
        store.resolve(harness: "cursor")
        #expect(store.open().isEmpty)
        #expect(store.all().filter(\.resolved).count == 1)
        // It came back: a new episode that notifies again.
        let back = store.record(harness: "cursor", status: .authRequired,
                                remedy: remedy())
        #expect(back.shouldNotify)
        #expect(!back.issue.resolved)
        #expect(store.open().map(\.id) == ["cursor/auth-required"])
    }

    @Test func aDifferentFailureRetiresTheOldOne() {
        let store = tempStore()
        _ = store.record(harness: "cursor", status: .unavailable, remedy: nil)
        // The outage became a stale login: the open slot moves to the new
        // status and the old issue closes instead of stacking.
        let next = store.record(harness: "cursor", status: .authRequired,
                                remedy: remedy())
        #expect(next.shouldNotify)
        #expect(store.open().map(\.id) == ["cursor/auth-required"])
        let retired = store.all().first { $0.id == "cursor/unavailable" }
        #expect(retired?.resolved == true)
    }

    @Test func extraAccountsGetTheirOwnSlots() {
        let store = tempStore()
        _ = store.record(harness: "cursor", status: .authRequired, remedy: remedy(),
                         account: "Work", accountKey: "work")
        _ = store.record(harness: "cursor", status: .authRequired, remedy: nil,
                         account: "Side", accountKey: "side")
        // One resolves alone: the other's issue is untouched.
        store.resolve(harness: "cursor", accountKey: "work")
        #expect(store.open().map(\.id) == ["cursor/side/auth-required"])
        // And the harness's own row is a third, independent slot.
        _ = store.record(harness: "cursor", status: .signedOut, remedy: nil)
        #expect(store.open().count == 2)
    }

    @Test func theFileMatchesWhatSweDoctorReads() throws {
        let store = tempStore()
        _ = store.record(harness: "cursor", status: .authRequired,
                         remedy: remedy(), account: "Work", accountKey: "work")
        let data = try Data(contentsOf: store.url)
        let raw = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(raw["version"] as? Int == 1)
        let issues = try #require(raw["issues"] as? [String: Any])
        let entry = try #require(issues["cursor/work/auth-required"] as? [String: Any])
        #expect(entry["harness"] as? String == "cursor")
        #expect(entry["status"] as? String == "auth-required")
        #expect(entry["account"] as? String == "Work")
        #expect(entry["occurrences"] as? Int == 1)
        #expect((entry["remedy"] as? [String: Any])?["command"] as? String == "cursor-agent status")
    }
}

/// `IssuesModel`: polls feed the store, toasts fire once per episode,
/// outages stay card-only and Doctor's rows come from the same list.
@MainActor
@Suite struct IssuesModelTests {
    private func model() -> (IssuesModel, IssueStore) {
        let store = IssueStore(url: FileManager.default.temporaryDirectory
            .appending(path: "issues-\(UUID().uuidString).json"))
        return (IssuesModel(store: store), store)
    }

    @Test func anActionableFailureToastsOnce() {
        let (issues, _) = model()
        issues.record(harness: "cursor", status: .authRequired,
                      remedy: nil)
        #expect(issues.toast?.id == "cursor/auth-required")
        #expect(issues.actionableCount == 1)
        issues.dismissToast()
        // The same failure on the next poll does not toast again.
        issues.record(harness: "cursor", status: .authRequired,
                      remedy: nil)
        #expect(issues.toast == nil)
    }

    @Test func anOutageIsACardStateNotAToast() {
        let (issues, _) = model()
        issues.record(harness: "devin", status: .unavailable, remedy: nil)
        #expect(issues.toast == nil)
        // Transient failures still count for Doctor, not the badge.
        #expect(issues.actionableCount == 0)
        #expect(issues.open.count == 1)
    }

    @Test func okResolvesTheOpenIssuesForThatSlot() {
        let (issues, _) = model()
        issues.record(harness: "devin", status: .signedOut, remedy: nil,
                      account: "Work", accountKey: "work")
        #expect(issues.actionableCount == 1)
        issues.record(harness: "devin", status: .ok, remedy: nil,
                      account: "Work", accountKey: "work")
        #expect(issues.open.isEmpty)
        #expect(issues.actionableCount == 0)
    }

    @Test func doctorRowsComeFromTheStore() {
        let (issues, _) = model()
        issues.record(harness: "cursor", status: .authRequired,
                      remedy: UsageRemedy(kind: "run-command", title: "Refresh",
                                          summary: "Cursor's stored login is stale",
                                          steps: ["Run cursor-agent once."],
                                          command: "cursor-agent status"))
        let finding = issues.findings.first
        #expect(finding?.check == "usage.cursor/auth-required")
        #expect(finding?.severity == .warning)
        #expect(finding?.harness == "cursor")
        #expect(finding?.fix == "cursor-agent status")
    }
}
