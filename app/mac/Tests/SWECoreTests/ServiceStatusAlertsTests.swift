import Foundation
import Testing
@testable import SWECore

/// The notification rule: one `started` per provider incident, one
/// `resolved` when the provider leaves the affected set, nothing for
/// maintenance or other groups, and a first poll that only seeds.
@Suite struct ServiceStatusAlertsTests {
    private func service(_ id: String) throws -> StatusService {
        try #require(StatusService.all.first { $0.id == id })
    }

    private func incident(_ id: String, _ impact: ServiceHealth = .minor, maintenance: Bool = false,
                          update: String? = nil) -> ServiceIncident {
        ServiceIncident(id: id, name: "Incident \(id)", impact: impact, stage: nil, startedAt: nil,
                        latestUpdate: update, updatedAt: nil, url: nil, isMaintenance: maintenance)
    }

    private func status(_ id: String, _ health: ServiceHealth?,
                        _ incidents: [ServiceIncident] = []) throws -> ServiceStatus {
        let report = health.map { StatusReport(health: $0, summary: nil, components: [], incidents: incidents) }
        return ServiceStatus(service: try service(id), report: report, checkedAt: usageEpoch,
                             problem: nil, isStale: false)
    }

    private func step(_ state: inout ServiceStatusAlertState, _ statuses: ServiceStatus...,
                      notify: Bool = true) -> [ServiceStatusAlert] {
        ServiceStatusAlerts.step(&state, statuses: statuses, notify: notify)
    }

    /// A seeded state, as after the first poll on a quiet day.
    private func primed() throws -> ServiceStatusAlertState {
        var state = ServiceStatusAlertState()
        _ = ServiceStatusAlerts.step(&state, statuses: try StatusService.all.map {
            try status($0.id, .operational)
        }, notify: true)
        return state
    }

    @Test func startUpdateResolve() throws {
        var state = try primed()
        let start = step(&state, try status("claude", .minor, [incident("a")]))
        #expect(start.map(\.kind) == [.started])
        #expect(ServiceStatusAlerts.title(start[0]) == "Claude: minor incident")
        #expect(ServiceStatusAlerts.body(start[0]) == "Incident a")
        // A new update or a worse impact on the same incident is not news.
        #expect(step(&state, try status("claude", .major, [incident("a", .major, update: "Monitoring")])).isEmpty)
        let resolved = step(&state, try status("claude", .operational))
        #expect(resolved.map(\.kind) == [.resolved])
        #expect(ServiceStatusAlerts.title(resolved[0]) == "Claude is back to normal")
        #expect(ServiceStatusAlerts.body(resolved[0]) == "Resolved: Incident a")
        #expect(step(&state, try status("claude", .operational)).isEmpty)
    }

    /// A second incident on a provider already down is its own banner.
    @Test func newIncidentWhileDown() throws {
        var state = try primed()
        _ = step(&state, try status("claude", .minor, [incident("a")]))
        let more = step(&state, try status("claude", .minor, [incident("a"), incident("b")]))
        #expect(more.map(\.incident) == ["Incident b"])
    }

    /// Two incidents opening between polls are two banners.
    @Test func twoIncidentsAtOnce() throws {
        var state = try primed()
        let both = step(&state, try status("claude", .major, [incident("a"), incident("b")]))
        #expect(both.map(\.incident) == ["Incident a", "Incident b"])
    }

    /// A page with no reading at the first poll seeds on its first answer.
    @Test func latePageSeedsOnItsFirstReading() throws {
        var state = ServiceStatusAlertState()
        _ = step(&state, try status("claude", .operational), try status("openai", nil))
        #expect(step(&state, try status("claude", .operational),
                     try status("openai", .minor, [incident("a")])).isEmpty)
        #expect(step(&state, try status("claude", .operational),
                     try status("openai", .minor, [incident("a"), incident("b")])).map(\.incident) == ["Incident b"])
    }

    /// The same incident flapping in and out banners once each way.
    @Test func flappingIncidentIsQuiet() throws {
        var state = try primed()
        let down = try status("claude", .minor, [incident("a")])
        let up = try status("claude", .operational)
        var kinds: [ServiceStatusAlert.Kind] = []
        for reading in [down, up, down, up, down] { kinds += step(&state, reading).map(\.kind) }
        #expect(kinds == [.started, .resolved])
    }

    @Test func maintenanceAndOtherGroupsNeverNotify() throws {
        var state = try primed()
        let alerts = step(&state,
                          try status("claude", .maintenance, [incident("m", .maintenance, maintenance: true)]),
                          try status("vercel", .critical, [incident("v", .critical)]),
                          try status("npm", .major, [incident("n", .major)]))
        #expect(alerts.isEmpty)
    }

    /// A page with maintenance under way beside an incident keys on the
    /// incident only.
    @Test func maintenanceBesideAnIncident() throws {
        var state = try primed()
        let alerts = step(&state, try status(
            "openai", .minor, [incident("m", .maintenance, maintenance: true), incident("i")]))
        #expect(alerts.map(\.incident) == ["Incident i"])
        #expect(!state.delivered.contains("openai@m"))
    }

    /// No incident named: keyed per service, and cleared when it recovers
    /// so the next blip banners again.
    @Test func incidentlessOutage() throws {
        var state = try primed()
        let down = try status("cursor", .minor)
        #expect(step(&state, down).map(\.incident) == [nil])
        #expect(step(&state, down).isEmpty)
        let resolved = step(&state, try status("cursor", .operational))
        #expect(ServiceStatusAlerts.body(resolved[0]) == "The status page reports no open incident.")
        #expect(step(&state, down).count == 1)
    }

    @Test func firstPollOnlySeeds() throws {
        var state = ServiceStatusAlertState()
        let down = try status("claude", .minor, [incident("a")])
        #expect(step(&state, down).isEmpty)
        #expect(state.seeded == ["claude https://status.claude.com/api/v2/summary.json"])
        #expect(step(&state, down).isEmpty)
        // It never said "down", so the recovery is quiet too.
        #expect(step(&state, try status("claude", .operational)).isEmpty)
    }

    /// An unreadable page neither starts nor resolves anything.
    @Test func noReadingLeavesStateAlone() throws {
        var state = try primed()
        _ = step(&state, try status("claude", .minor, [incident("a")]))
        #expect(step(&state, try status("claude", nil)).isEmpty)
        #expect(state.down["claude"] == "Incident a")
    }

    /// Switched off: still recorded, nothing announced, and switching on
    /// mid-incident stays quiet.
    @Test func switchedOffRecordsSilently() throws {
        var state = try primed()
        let down = try status("claude", .minor, [incident("a")])
        #expect(step(&state, down, notify: false).isEmpty)
        #expect(step(&state, down).isEmpty)
        #expect(step(&state, try status("claude", .operational)).isEmpty)
    }

    @Test func untrackedServiceIsDropped() throws {
        var state = try primed()
        _ = step(&state, try status("claude", .minor, [incident("a")]))
        _ = step(&state, try status("openai", .operational))
        #expect(state.down.isEmpty)
    }

    @Test func deliveredIsCapped() throws {
        var state = try primed()
        _ = step(&state, try status("claude", .minor, (0..<ServiceStatusAlertState.cap).map { incident("o\($0)") }))
        _ = step(&state, try status("claude", .minor, (0..<10).map { incident("i\($0)") }))
        #expect(state.delivered.count == ServiceStatusAlertState.cap)
        #expect(state.delivered.last == "claude@i9")
        #expect(!state.delivered.contains("claude@o0"))
    }

    /// The cap never forgets an incident that is still open.
    @Test func capKeepsOpenIncidents() throws {
        var state = try primed()
        let many = try status("claude", .minor, (0..<(ServiceStatusAlertState.cap + 10)).map { incident("i\($0)") })
        #expect(step(&state, many).count == ServiceStatusAlertState.cap + 10)
        #expect(step(&state, many).isEmpty)
    }

    /// A card edited to read another feed is a new page: its first
    /// reading seeds, and the old page's incident owes no recovery.
    @Test func movedFeedIsANewPage() throws {
        var state = try primed()
        _ = step(&state, try status("claude", .minor, [incident("a")]))
        var moved = try status("claude", .minor, [incident("b")])
        moved.service.feed = .statuspage(URL(string: "https://example.com/api/v2/summary.json")!)
        #expect(step(&state, moved).isEmpty)
        #expect(state.down.isEmpty)
        var up = try status("claude", .operational)
        up.service = moved.service
        #expect(step(&state, up).isEmpty)
    }
}
