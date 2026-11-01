import Foundation
import Testing
@testable import SWECore

/// `IssueStore`: the dedup, re-notify and auto-resolve rules the toasts,
/// badge and Doctor rows all read from `~/.swe/data/issues.json`.
@Suite struct IssueStoreTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    private func tempStore(clock: TestClock? = nil) -> IssueStore {
        let clock = clock ?? TestClock()
        var store = IssueStore(url: scratch.url
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
        let url = scratch.url
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
        // swe doctor prefers this stored fix for an extra account.
        #expect(entry["accountKey"] as? String == "work")
        #expect((entry["remedy"] as? [String: Any])?["command"] as? String == "cursor-agent status")
    }

    @Test func aRecurrenceCountsItsPollsFromOne() {
        let clock = TestClock()
        let store = tempStore(clock: clock)
        for _ in 0..<3 { _ = store.record(harness: "devin", status: .unavailable, remedy: nil) }
        store.resolve(harness: "devin")
        clock.advance(600)
        // A new episode: Doctor's "persistent" count starts over, and it
        // dates from now, not the first outage.
        let back = store.record(harness: "devin", status: .unavailable, remedy: nil)
        #expect(back.issue.occurrences == 1)
        #expect(back.issue.firstSeen == clock.now)
    }

    @Test func anOutageThatChangesKindKeepsCounting() {
        let store = tempStore()
        // timeout, unavailable, timeout: one outage three polls long, so
        // Doctor's persistence threshold is met despite the flapping.
        _ = store.record(harness: "devin", status: .timeout, remedy: nil)
        _ = store.record(harness: "devin", status: .unavailable, remedy: nil)
        let third = store.record(harness: "devin", status: .timeout, remedy: nil)
        #expect(third.issue.occurrences == 3)
        #expect(store.open().map(\.id) == ["devin/timeout"])
        // A login problem in between is a different story: counting
        // starts over once it is an outage again.
        _ = store.record(harness: "devin", status: .authRequired, remedy: nil)
        let after = store.record(harness: "devin", status: .unavailable, remedy: nil)
        #expect(after.issue.occurrences == 1)
    }

    @Test func aSuccessfulReadMarksTheSlotHealthy() {
        let store = tempStore()
        // Never read: a sign-out is an account that was never set up.
        let first = store.record(harness: "cursor", status: .signedOut, remedy: nil)
        #expect(!first.wasHealthy)
        store.resolve(harness: "cursor")
        // It worked once, so signing out again is a regression.
        let again = store.record(harness: "cursor", status: .signedOut, remedy: nil)
        #expect(again.shouldNotify)
        #expect(again.wasHealthy)
        // Another account's slot is its own.
        let other = store.record(harness: "cursor", status: .signedOut, remedy: nil,
                                 account: "Work", accountKey: "work")
        #expect(!other.wasHealthy)
    }

    @Test func unpolledSlotsClose() {
        let store = tempStore()
        _ = store.record(harness: "cursor", status: .authRequired, remedy: nil)
        _ = store.record(harness: "devin", status: .authRequired, remedy: nil,
                         account: "Work", accountKey: "work")
        _ = store.record(harness: "devin", status: .authRequired, remedy: nil,
                         account: "Side", accountKey: "side")
        // Cursor was unstarred and Devin's "work" account removed: neither
        // is polled again, so neither can recover. "side" stays open.
        store.resolveMissing(starred: ["devin"], accounts: ["devin": ["side"]])
        #expect(store.open().map(\.id) == ["devin/side/auth-required"])
    }

    @Test func aRenamedAccountsLabelFollows() {
        let store = tempStore()
        _ = store.record(harness: "devin", status: .authRequired, remedy: nil,
                         account: "Work", accountKey: "work")
        let renamed = store.record(harness: "devin", status: .authRequired, remedy: nil,
                                   account: "Day job", accountKey: "work")
        #expect(!renamed.shouldNotify)
        #expect(renamed.issue.message.hasSuffix("(Day job)"))
    }

    /// A file this build can't read (cut off, from a newer build, or with
    /// a status it doesn't know) reads empty, and no write replaces it:
    /// writing back the empty list would lose every issue.
    @Test(arguments: [
        #"{"version": 1, "issues": {"cursor/auth-required": {"id": "cursor/auth-requ"#,
        #"{"version": 2, "issues": {}, "added_later": true}"#,
        #"{"version": 1, "issues": {"x/new": {"id": "x/new", "harness": "x", "status": "brand-new", "firstSeen": 1, "lastSeen": 1, "occurrences": 1, "resolved": false}}}"#,
    ])
    func aFileThisBuildCantReadIsNeverOverwritten(_ text: String) throws {
        let store = tempStore()
        try Data(text.utf8).write(to: store.url)
        #expect(store.all().isEmpty)
        let outcome = store.record(harness: "cursor", status: .authRequired, remedy: remedy())
        #expect(!outcome.shouldNotify)
        #expect(outcome.issue.id == "cursor/auth-required")
        store.markNotified("cursor/auth-required")
        store.resolve(harness: "cursor")
        store.resolveMissing(starred: [], accounts: [:])
        #expect(try String(contentsOf: store.url, encoding: .utf8) == text)
    }

    @Test func aBlankFileReadsEmptyAndTakesTheFirstWrite() throws {
        let store = tempStore()
        try Data("\n".utf8).write(to: store.url)
        #expect(store.record(harness: "cursor", status: .authRequired, remedy: nil).shouldNotify)
        #expect(store.open().map(\.id) == ["cursor/auth-required"])
    }
}

/// What each surface makes of a status: the card's "Action needed" line
/// and the Fix sheet's buttons.
@Suite struct IssueSurfaceTests {
    private let settings = "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders"

    @Test func onlyLoginAndPermissionProblemsNeedAction() {
        let needing = IssueStatus.allCases.filter(\.needsAction)
        #expect(needing == [.authRequired, .permissionRequired])
        #expect(IssueStatus.allCases.filter(\.isOutage) == [.unavailable, .timeout])
    }

    @Test func theCardLineIsTheRemedySummaryOrAPlainReading() {
        let named = HarnessIssue(status: .authRequired, remedy: UsageRemedy(
            kind: "run-command", title: "Refresh Cursor's session",
            summary: "Cursor's stored login is stale", steps: ["Run cursor-agent once."]))
        #expect(named.actionNeededLine == "Cursor's stored login is stale")
        #expect(HarnessIssue(status: .permissionRequired).actionNeededLine
                == "macOS won't let SWE read its credentials")
        // Signed out and outages keep the card's own wording (Sign in,
        // Refresh), so there is no Action needed line.
        for status in [IssueStatus.signedOut, .unavailable, .timeout, .ok] {
            #expect(HarnessIssue(status: status).actionNeededLine == nil)
        }
    }

    @Test func theFixSheetOffersTheCommandThenTheLink() throws {
        let both = UsageRemedy(kind: "grant-permission", title: "t", summary: "s",
                               steps: ["a"], command: "cursor-agent status", url: settings)
        let link = try #require(URL(string: settings))
        #expect(both.actions == [.command("cursor-agent status"),
                                 .open(link, label: "Open System Settings")])
        let web = UsageRemedy(kind: "relogin", title: "t", summary: "s", steps: [],
                              url: "https://cursor.com/settings")
        #expect(web.actions.map { if case .open(_, let label) = $0 { label } else { "" } }
                == ["Open Link"])
        #expect(UsageRemedy(kind: "retry", title: "t", summary: "s", steps: []).actions.isEmpty)
    }

    @Test func aPermissionRemedyDecodesItsLink() throws {
        let json = """
        {"status": "permission-required",
         "remedy": {"kind": "grant-permission", "title": "Let SWE read Cursor's data",
                    "summary": "macOS is blocking Cursor's session files",
                    "steps": ["Open System Settings."], "url": "\(settings)"}}
        """
        let issue = try JSONDecoder().decode(HarnessIssue.self, from: Data(json.utf8))
        #expect(issue.remedy?.url == settings)
        #expect(issue.remedy?.actions.count == 1)
    }
}

/// `IssuesModel`: polls feed the store, toasts fire once per episode,
/// outages stay card-only and Doctor's rows come from the same list.
@MainActor
@Suite struct IssuesModelTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    private func model() -> (IssuesModel, IssueStore) {
        let store = IssueStore(url: scratch.url
            .appending(path: "issues-\(UUID().uuidString).json"))
        return (IssuesModel(store: store), store)
    }

    @Test func aStaleLoginToastsOnceAndBadges() async {
        let (issues, _) = model()
        await issues.record(harness: "cursor", status: .authRequired, remedy: nil)
        #expect(issues.toast?.id == "cursor/auth-required")
        #expect(issues.badgeCount == 1)
        issues.dismissToast()
        // The same failure on the next poll does not toast again.
        await issues.record(harness: "cursor", status: .authRequired, remedy: nil)
        #expect(issues.toast == nil)
    }

    @Test func aPermissionProblemToastsBadgesAndOffersTheSettingsPane() async {
        let (issues, _) = model()
        let pane = "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders"
        await issues.record(harness: "devin", status: .permissionRequired,
                            remedy: UsageRemedy(kind: "grant-permission",
                                                title: "Let SWE read Devin's credentials",
                                                summary: "macOS is blocking Devin's credentials",
                                                steps: ["Open System Settings."], url: pane))
        #expect(issues.toast?.id == "devin/permission-required")
        #expect(issues.badgeCount == 1)
        // No command to run: Doctor's fix is the pane to open.
        #expect(issues.findings.first?.fix == pane)
    }

    @Test func anOutageIsACardStateUntilItPersists() async {
        let (issues, _) = model()
        for poll in 1...IssuesModel.persistentPolls {
            await issues.record(harness: "devin", status: .unavailable, remedy: nil)
            // No toast and no badge, ever; Doctor only once it persists.
            #expect(issues.toast == nil)
            #expect(issues.badgeCount == 0)
            #expect(issues.findings.isEmpty == (poll < IssuesModel.persistentPolls))
        }
        #expect(issues.findings.first?.severity == .info)
    }

    @Test func aSignOutToastsOnlyWhereAReadUsedToWork() async {
        let (issues, _) = model()
        // Starred but never signed in: Doctor lists it, nothing toasts.
        await issues.record(harness: "cursor", status: .signedOut, remedy: nil)
        #expect(issues.toast == nil)
        #expect(issues.badgeCount == 0)
        #expect(issues.findings.first?.severity == .warning)
        // It signs in, works, then signs out: that regression toasts, but
        // the badge stays for problems only the user can fix by hand.
        await issues.record(harness: "cursor", status: .ok, remedy: nil)
        await issues.record(harness: "cursor", status: .signedOut, remedy: nil)
        #expect(issues.toast?.id == "cursor/signed-out")
        #expect(issues.badgeCount == 0)
    }

    @Test func everyFailureInOnePollGetsItsTurn() async {
        let (issues, _) = model()
        await issues.record([
            .init(harness: "cursor", status: .authRequired),
            .init(harness: "devin", status: .permissionRequired),
        ])
        #expect(issues.toast?.id == "cursor/auth-required")
        issues.dismissToast()
        #expect(issues.toast?.id == "devin/permission-required")
        issues.dismissToast()
        #expect(issues.toast == nil)
    }

    @Test func okResolvesTheOpenIssuesForThatSlot() async {
        let (issues, _) = model()
        await issues.record(harness: "devin", status: .authRequired, remedy: nil,
                            account: "Work", accountKey: "work")
        #expect(issues.badgeCount == 1)
        await issues.record(harness: "devin", status: .ok, remedy: nil,
                            account: "Work", accountKey: "work")
        #expect(issues.open.isEmpty)
        #expect(issues.badgeCount == 0)
    }

    @Test func anUnstarredHarnessLeavesTheBadge() async {
        let (issues, _) = model()
        await issues.record([.init(harness: "cursor", status: .authRequired)],
                            starred: ["cursor"], accounts: [:])
        #expect(issues.badgeCount == 1)
        // The next poll no longer stars Cursor: it can't recover, so it
        // must not hold the badge forever.
        await issues.record([], starred: [], accounts: [:])
        #expect(issues.open.isEmpty)
    }

    @Test func aRelaunchReadsWhatTheLastRunLeft() async {
        let (issues, store) = model()
        await issues.record(harness: "cursor", status: .authRequired, remedy: nil)
        let relaunched = IssuesModel(store: store)
        await relaunched.reload()
        #expect(relaunched.open.map(\.id) == ["cursor/auth-required"])
        // Told last run: not raised again.
        #expect(relaunched.toast == nil)
    }

    @Test func doctorRowsComeFromTheStore() async {
        let (issues, _) = model()
        await issues.record(harness: "cursor", status: .authRequired,
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
