import Foundation
import Testing
@testable import QuiverCore

/// The Report a Bug links prefill GitHub's issue forms by field id, and the
/// diagnostics text lands in a public issue, so it must name no one.
@Suite struct IssueReportTests {
    private func query(_ url: URL) -> [String: String] {
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        return Dictionary(uniqueKeysWithValues: items.map { ($0.name, $0.value ?? "") })
    }

    @Test func bugLinkPrefillsAreaVersionAndOS() {
        let url = IssueReport.newIssueURL(.bug, version: "Quiver 1.1.1 · swe 1.1.1", os: "macOS 26.1")
        #expect(url.absoluteString.hasPrefix("https://github.com/c-wenlong/quiver/issues/new?"))
        #expect(query(url) == ["template": "bug_report.yml", "area": "Mac app",
                               "version": "Quiver 1.1.1 · swe 1.1.1", "os": "macOS 26.1"])
    }

    @Test func featureLinkCarriesOnlyTheArea() {
        let url = IssueReport.newIssueURL(.feature, version: "Quiver 1.1.1", os: "macOS 26.1")
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
        let report = DoctorReport(ok: false, findings: [
            .init(check: "links", severity: .warning,
                  message: "/Users/ana/.claude/skills is not linked for ana@example.com"),
        ])
        let text = IssueReport.diagnostics(version: "Quiver 1.1.1", os: "macOS 26.1",
                                           doctor: report, home: "/Users/ana/", user: "ana")
        #expect(text == """
            Version: Quiver 1.1.1
            OS: macOS 26.1
            swe doctor:
              warning links: ~/.claude/skills is not linked for <email>
            """)
    }

    @Test func diagnosticsSayWhenDoctorIsCleanOrHasNotRun() {
        let clean = IssueReport.diagnostics(version: "v", os: "o", doctor: DoctorReport(ok: true, findings: []))
        #expect(clean.hasSuffix("swe doctor: everything checks out"))
        let none = IssueReport.diagnostics(version: "v", os: "o", doctor: nil)
        #expect(none.hasSuffix("swe doctor: not run yet"))
    }

    @Test func theUserNameIsMaskedOnlyAsAPathComponent() {
        let text = IssueReport.redact(
            "which npm is /etc/profiles/per-user/ana/bin/npm; /var/analytics and banana stay",
            home: "/Users/ana", user: "ana")
        #expect(text == "which npm is /etc/profiles/per-user/<user>/bin/npm; /var/analytics and banana stay")
    }

    @Test func aRootHomeIsNotMasked() {
        #expect(IssueReport.redact("/etc/hosts", home: "/") == "/etc/hosts")
    }
}
