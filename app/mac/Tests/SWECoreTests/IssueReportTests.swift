import Foundation
import Testing
@testable import SWECore

/// The Report a Bug links prefill GitHub's issue forms by field id, and the
/// diagnostics text lands in a public issue, so it must name no one.
@Suite struct IssueReportTests {
    private func query(_ url: URL) -> [String: String] {
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        return Dictionary(uniqueKeysWithValues: items.map { ($0.name, $0.value ?? "") })
    }

    @Test func bugLinkPrefillsAreaVersionAndOS() {
        let url = IssueReport.newIssueURL(.bug, version: "SWE 1.1.1 · swe 1.1.1", os: "macOS 26.1")
        #expect(url.absoluteString.hasPrefix("https://github.com/c-wenlong/swe/issues/new?"))
        #expect(query(url) == ["template": "bug_report.yml", "area": "Mac app",
                               "version": "SWE 1.1.1 · swe 1.1.1", "os": "macOS 26.1"])
    }

    @Test func featureLinkCarriesOnlyTheArea() {
        let url = IssueReport.newIssueURL(.feature, version: "SWE 1.1.1", os: "macOS 26.1")
        #expect(query(url) == ["template": "feature_request.yml", "area": "Mac app"])
    }

    @Test func plusSignsSurviveTheQuery() {
        let url = IssueReport.newIssueURL(.bug, version: "1.2+dev", os: "macOS 26.1")
        #expect(url.absoluteString.contains("1.2%2Bdev"))
    }

    @Test func osLineDropsAZeroPatch() {
        #expect(IssueReport.osLine(OperatingSystemVersion(majorVersion: 26, minorVersion: 1, patchVersion: 0))
                == "macOS 26.1")
        #expect(IssueReport.osLine(OperatingSystemVersion(majorVersion: 15, minorVersion: 6, patchVersion: 1))
                == "macOS 15.6.1")
    }

    @Test func diagnosticsListFindingsWithHomeAndEmailsMasked() {
        let findings: [DoctorReport.Finding] = [
            .init(check: "links", severity: .warning,
                  message: "/Users/ana/.claude/skills is not linked for ana@example.com"),
        ]
        let text = IssueReport.diagnostics(version: "SWE 1.1.1", os: "macOS 26.1",
                                           findings: findings, home: "/Users/ana/", user: "ana")
        #expect(text == """
            Version: SWE 1.1.1
            OS: macOS 26.1
            swe doctor:
              warning links: ~/.claude/skills is not linked for <email>
            """)
    }

    @Test func diagnosticsSayWhenDoctorIsCleanOrHasNotRun() {
        let clean = IssueReport.diagnostics(version: "v", os: "o", findings: [])
        #expect(clean.hasSuffix("swe doctor: everything checks out"))
        let none = IssueReport.diagnostics(version: "v", os: "o", findings: nil)
        #expect(none.hasSuffix("swe doctor: not run yet"))
    }

    @Test func theUserNameIsMaskedOnlyAsAPathComponent() {
        let text = IssueReport.redact(
            "which npm is /etc/profiles/per-user/ana/bin/npm; /var/analytics and banana stay",
            home: "/Users/ana", user: "ana")
        #expect(text == "which npm is /etc/profiles/per-user/<user>/bin/npm; /var/analytics and banana stay")
    }

    @Test func accountNamesAreMaskedInChecksAndMessages() {
        let findings: [DoctorReport.Finding] = [
            .init(check: "usage.cursor/work/signed-out", severity: .warning,
                  message: "Cursor (work) is signed out; claude-work and homework stay"),
        ]
        let text = IssueReport.diagnostics(version: "v", os: "o", findings: findings,
                                           accounts: ["work", "claude-work", "ab"], home: "/Users/x", user: "x")
        #expect(text.hasSuffix(
            "warning usage.cursor/<account>/signed-out: Cursor (<account>) is signed out; <account> and homework stay"))
    }

    @Test func mergedPutsLiveIssuesFirstOncePerCheck() {
        let live = DoctorReport.Finding(check: "usage.cursor/signed-out", severity: .warning, message: "live")
        let report = DoctorReport(ok: false, findings: [
            .init(check: "usage.cursor/signed-out", severity: .warning, message: "stale"),
            .init(check: "npm.preferred", severity: .info, message: "npm"),
        ])
        #expect(IssueReport.merged(issues: [live], report: report)?.map(\.message) == ["live", "npm"])
        #expect(IssueReport.merged(issues: [live], report: nil) == nil)
    }

    @Test func theHomeIsMaskedOnlyAsAWholePathAtItsStart() {
        let text = IssueReport.redact(
            "home /Users/ana, ~ at (/Users/ana/x) and \"/Users/ana\"; /Users/anabel/y and /backup/Users/ana/z stay",
            home: "/Users/ana/")
        #expect(text == "home ~, ~ at (~/x) and \"~\"; /Users/anabel/y and /backup/Users/ana/z stay")
    }

    @Test func aRootHomeIsNotMasked() {
        #expect(IssueReport.redact("/etc/hosts", home: "/") == "/etc/hosts")
    }
}
