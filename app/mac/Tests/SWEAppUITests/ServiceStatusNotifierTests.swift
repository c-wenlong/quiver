import Foundation
import Testing
@testable import SWEApp
@testable import SWECore

/// `ServiceStatusNotifier`: the switch, the state file under
/// `~/.quiver/cache/`, and the issue's acceptance run. `post` captures the
/// alerts instead of reaching `UNUserNotificationCenter`.
@Suite(.serialized)
@MainActor
struct ServiceStatusNotifierTests {
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    final class Posts: @unchecked Sendable {
        var alerts: [ServiceStatusAlert] = []
    }

    private func claude(_ health: ServiceHealth, incident: String? = nil) throws -> [ServiceStatus] {
        let service = try #require(StatusService.all.first { $0.id == "claude" })
        let incidents = incident.map {
            [ServiceIncident(id: $0, name: "Elevated errors", impact: health, stage: "Investigating",
                             startedAt: nil, latestUpdate: nil, updatedAt: nil, url: nil, isMaintenance: false)]
        } ?? []
        let report = StatusReport(health: health, summary: nil, components: [], incidents: incidents)
        return [ServiceStatus(service: service, report: report, checkedAt: Date(), problem: nil, isStale: false)]
    }

    private func notifier(_ posts: Posts, defaults: UserDefaults = TestDefaults(),
                          allowed: @escaping @Sendable () -> Bool = { true }) -> ServiceStatusNotifier {
        ServiceStatusNotifier(quiver: scratch.url, defaults: defaults, authorized: { allowed() }) {
            posts.alerts += $0
        }
    }

    final class Allowed: @unchecked Sendable { var value = false }

    /// Banners blocked when the incident starts: no banner, and none for
    /// its recovery after they are allowed again.
    @Test func blockedBannersOweNoRecovery() async throws {
        let posts = Posts()
        let allowed = Allowed()
        let notifier = notifier(posts, allowed: { allowed.value })
        notifier.bannersEnabled = true
        await notifier.observe(try claude(.operational))
        await notifier.observe(try claude(.minor, incident: "a"))
        allowed.value = true
        await notifier.observe(try claude(.operational))
        #expect(posts.alerts.isEmpty)
    }

    @Test func offByDefault() async throws {
        let posts = Posts()
        let notifier = notifier(posts)
        #expect(!notifier.bannersEnabled)
        await notifier.observe(try claude(.operational))
        await notifier.observe(try claude(.minor, incident: "a"))
        await notifier.observe(try claude(.operational))
        #expect(posts.alerts.isEmpty)
    }

    @Test func switchPersists() {
        let defaults = TestDefaults()
        notifier(Posts(), defaults: defaults).bannersEnabled = true
        #expect(notifier(Posts(), defaults: defaults).bannersEnabled)
    }

    /// The issue's acceptance: one banner when Claude goes down, none on
    /// the next poll, one when it resolves; a relaunch mid-incident is quiet.
    @Test func acceptance() async throws {
        let posts = Posts()
        let first = notifier(posts)
        first.bannersEnabled = true
        await first.observe(try claude(.operational))
        await first.observe(try claude(.minor, incident: "a"))
        #expect(posts.alerts.map(\.kind) == [.started])
        await first.observe(try claude(.minor, incident: "a"))
        #expect(posts.alerts.count == 1)

        let relaunched = notifier(posts)
        relaunched.bannersEnabled = true
        await relaunched.observe(try claude(.minor, incident: "a"))
        #expect(posts.alerts.count == 1)
        await relaunched.observe(try claude(.operational))
        #expect(posts.alerts.map(\.kind) == [.started, .resolved])
        #expect(FileManager.default.fileExists(
            atPath: scratch.url.appending(path: "cache/service-status-notified.json").path))
    }
}
