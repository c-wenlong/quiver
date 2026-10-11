import Foundation
import Testing
@testable import QuiverCore

/// The fixtures under Fixtures/status are real responses recorded on
/// 2026-10-11. Cloudflare's is trimmed to three regions (the live page lists
/// 481 components) and Google Cloud's to one Gemini and one unrelated incident.
private func statusFixture(_ name: String) throws -> Data {
    let url = try #require(Bundle.module.url(forResource: "Fixtures/status/\(name)", withExtension: "json"))
    return try Data(contentsOf: url)
}

private func service(_ id: String) throws -> StatusService {
    try #require(StatusService.all.first { $0.id == id })
}

/// The recorded Google Cloud file with the Gemini incident still open.
private func openGeminiIncident() throws -> Data {
    var incidents = try #require(
        try JSONSerialization.jsonObject(with: statusFixture("gcloud-incidents")) as? [[String: Any]])
    incidents[0]["end"] = NSNull()
    incidents[0]["severity"] = "high"
    return try JSONSerialization.data(withJSONObject: incidents)
}

@Suite struct StatusParserTests {
    @Test func statuspageWithAnIncident() throws {
        let report = try #require(StatusParser.statuspage(try statusFixture("statuspage-claude"), now: usageEpoch))
        #expect(report.health == .minor)
        #expect(report.summary == "Partially Degraded Service")
        #expect(report.components == [AffectedComponent(name: "Claude Console (platform.claude.com)", state: .degraded)])
        let incident = try #require(report.incidents.first)
        #expect(report.incidents.count == 1)
        #expect(incident.name == "Elevated errors on platform.claude.com")
        #expect(incident.impact == .major)
        #expect(incident.stage == "Monitoring")
        #expect(incident.startedAt == UsageJSON.iso8601("2026-10-07T13:25:04.155Z"))
        #expect(incident.latestUpdate?.hasPrefix("We've mitigated the errors loading usage data") == true)
        #expect(incident.updatedAt == UsageJSON.iso8601("2026-10-07T21:44:41.466Z"))
        #expect(incident.url == URL(string: "https://stspg.io/sjkw7njwf0mt"))
        #expect(!incident.isMaintenance)
    }

    /// OpenAI's page is incident.io serving the Statuspage shape, without
    /// `started_at`, incident components or a maintenance list.
    @Test func incidentIoFlavour() throws {
        let report = try #require(StatusParser.statuspage(try statusFixture("statuspage-openai"), now: usageEpoch))
        #expect(report.health == .minor)
        #expect(report.components == [AffectedComponent(name: "Compliance API", state: .degraded)])
        let incident = try #require(report.incidents.first)
        #expect(incident.name == "Delayed Costs data in the Compliance API")
        #expect(incident.impact == .minor)
        #expect(incident.startedAt == UsageJSON.iso8601("2026-10-09T17:23:18Z"))
        #expect(incident.latestUpdate?.hasPrefix("We have implemented a mitigation") == true)
        #expect(incident.url == nil)
    }

    @Test func allOperational() throws {
        let report = try #require(StatusParser.statuspage(try statusFixture("statuspage-github"), now: usageEpoch))
        #expect(report.health == .operational)
        #expect(report.components.isEmpty)
        #expect(report.incidents.isEmpty)
    }

    /// Groups are not listed when their own members are, the worst state
    /// comes first, and a maintenance that has not started is left out.
    @Test func componentGroupsAndMaintenance() throws {
        let report = try #require(StatusParser.statuspage(try statusFixture("statuspage-cloudflare"), now: usageEpoch))
        #expect(report.health == .major)
        #expect(report.components.count == 8)
        #expect(report.components.first == AffectedComponent(name: "Annaba, Algeria - (AAE)", state: .majorOutage))
        #expect(!report.components.contains { $0.name == "Africa" || $0.name == "Cloudflare Sites and Services" })
        #expect(report.incidents.count == 3)
        #expect(!report.incidents.contains { $0.isMaintenance })
    }

    @Test func maintenanceInProgressIsListed() throws {
        var object = try #require(
            try JSONSerialization.jsonObject(with: statusFixture("statuspage-cloudflare")) as? [String: Any])
        var maintenances = try #require(object["scheduled_maintenances"] as? [[String: Any]])
        maintenances[0]["status"] = "in_progress"
        object["scheduled_maintenances"] = maintenances
        object["incidents"] = []
        object["status"] = ["indicator": "maintenance", "description": "Service Under Maintenance"]
        let report = try #require(StatusParser.statuspage(try JSONSerialization.data(withJSONObject: object), now: usageEpoch))
        #expect(report.health == .maintenance)
        let maintenance = try #require(report.incidents.first)
        #expect(maintenance.isMaintenance)
        #expect(maintenance.stage == "In progress")
        #expect(maintenance.name == "ZRH (Zurich) on 2026-10-12")
        // When it is scheduled for, not when it was announced (2026-10-08).
        #expect(maintenance.startedAt == UsageJSON.iso8601("2026-10-12T00:00:00.000Z"))
    }

    @Test func notAStatusPage() {
        #expect(StatusParser.statuspage(Data("<html>".utf8), now: usageEpoch) == nil)
        #expect(StatusParser.statuspage(Data(#"{"page":{"status":"UP"}}"#.utf8), now: usageEpoch) == nil)
        #expect(StatusParser.googleCloud(Data("{}".utf8), products: ["Vertex Gemini API"], now: usageEpoch) == nil)
    }

    @Test func googleCloudWithNothingOpen() throws {
        let report = try #require(StatusParser.googleCloud(
            try statusFixture("gcloud-incidents"), products: ["Vertex Gemini API"], now: usageEpoch))
        #expect(report.health == .operational)
        #expect(report.incidents.isEmpty)
        #expect(report.components.isEmpty)
    }

    @Test func googleCloudOpenGeminiIncident() throws {
        let report = try #require(StatusParser.googleCloud(
            try openGeminiIncident(), products: ["Vertex Gemini API"], now: usageEpoch))
        #expect(report.health == .critical)
        #expect(report.components == [AffectedComponent(name: "Gemini on Agent Platform", state: .majorOutage)])
        let incident = try #require(report.incidents.first)
        #expect(report.incidents.count == 1)
        #expect(incident.name.hasPrefix("Vertex AI Gemini API customers experienced increased error rates"))
        #expect(incident.startedAt == UsageJSON.iso8601("2026-02-27T12:37:00+00:00"))
        #expect(incident.url == URL(string: "https://status.cloud.google.com/incidents/41E5S3mkTGDfkZuJZH5k"))
        // Markdown headings and bold markers are dropped from the update.
        let update = try #require(incident.latestUpdate)
        #expect(!update.contains("#") && !update.contains("**"))
        #expect(update.hasPrefix("On Friday, 27 February 2026"))
    }

    /// Two open incidents on one product: the component takes the worse
    /// state whichever comes first in the feed.
    @Test(arguments: [false, true]) func googleCloudWorstStatePerProduct(reversed: Bool) throws {
        var incidents = try #require(
            try JSONSerialization.jsonObject(with: openGeminiIncident()) as? [[String: Any]])
        var minor = incidents[0]
        minor["id"] = "minor-one"
        minor["severity"] = "low"
        incidents = reversed ? [incidents[0], minor] : [minor, incidents[0]]
        let report = try #require(StatusParser.googleCloud(
            try JSONSerialization.data(withJSONObject: incidents), products: ["Vertex Gemini API"], now: usageEpoch))
        #expect(report.health == .critical)
        #expect(report.components == [AffectedComponent(name: "Gemini on Agent Platform", state: .majorOutage)])
        #expect(report.incidents.map(\.id).last == "minor-one")
    }

    /// An incident for another product never counts against Gemini.
    @Test func googleCloudOtherProduct() throws {
        let report = try #require(StatusParser.googleCloud(
            try openGeminiIncident(), products: ["Google Cloud Storage"], now: usageEpoch))
        #expect(report.health == .operational)
    }
}

@Suite struct StatusCatalogTests {
    @Test func everyServiceHasAJsonFeedOnHttps() {
        #expect(Set(StatusService.all.map(\.id)).count == StatusService.all.count)
        for service in StatusService.all {
            #expect(service.feedURL.scheme == "https")
            #expect(service.pageURL.scheme == "https")
        }
        #expect(StatusService.all.filter { $0.group == .providers }.map(\.name)
            == ["Claude", "OpenAI / Codex", "GitHub / Copilot", "Cursor", "Gemini", "Devin"])
    }

    @Test func healthTitlesAreSentenceCase() {
        let titles = ServiceHealth.allCases.map(\.title)
        #expect(titles == ["Operational", "Under maintenance", "Minor incident", "Major outage", "Critical outage"])
    }
}

@Suite struct ServiceStatusServiceTests {
    private func makeService(_ transport: FakeTransport, clock: TestClock, directory: URL? = nil) -> ServiceStatusService {
        let dir = directory ?? FileManager.default.temporaryDirectory.appending(path: "status-\(UUID().uuidString)")
        return ServiceStatusService(transport: transport, cacheURL: dir.appending(path: "service-status.json"),
                                    now: { clock.now })
    }

    @Test func fetchesAndParses() async throws {
        let transport = FakeTransport()
        transport.reply(200, headers: ["ETag": "W/\"abc\""], body: try statusFixture("statuspage-claude"))
        let clock = TestClock()
        let statuses = await makeService(transport, clock: clock).statuses(for: [try service("claude")], force: false)
        let status = try #require(statuses.first)
        #expect(status.report?.health == .minor)
        #expect(status.checkedAt == usageEpoch)
        #expect(status.problem == nil)
        #expect(!status.isStale)
        let request = try #require(transport.requests.first)
        #expect(request.url?.absoluteString == "https://status.claude.com/api/v2/summary.json")
        #expect(request.value(forHTTPHeaderField: "If-None-Match") == nil)
    }

    @Test func pollsAtMostEveryFiveMinutes() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: try statusFixture("statuspage-github"))
        let clock = TestClock()
        let status = makeService(transport, clock: clock)
        let github = [try service("github")]
        _ = await status.statuses(for: github, force: false)
        clock.advance(120)
        let again = await status.statuses(for: github, force: false)
        #expect(transport.requests.count == 1)
        #expect(again.first?.checkedAt == usageEpoch)
        // A manual refresh still waits out a one-minute floor, then goes.
        clock.advance(-90)
        _ = await status.statuses(for: github, force: true)
        #expect(transport.requests.count == 1)
        clock.advance(90)
        transport.reply(200, body: try statusFixture("statuspage-github"))
        _ = await status.statuses(for: github, force: true)
        #expect(transport.requests.count == 2)
    }

    @Test func revalidatesWithTheETag() async throws {
        let transport = FakeTransport()
        transport.reply(200, headers: ["ETag": "W/\"abc\"", "Last-Modified": "Sun, 11 Oct 2026 06:45:55 GMT"],
                        body: try statusFixture("statuspage-claude"))
        let clock = TestClock()
        let status = makeService(transport, clock: clock)
        let claude = [try service("claude")]
        _ = await status.statuses(for: claude, force: false)
        clock.advance(301)
        transport.reply(304, body: Data())
        let again = try #require(await status.statuses(for: claude, force: false).first)
        let request = try #require(transport.requests.last)
        #expect(request.value(forHTTPHeaderField: "If-None-Match") == "W/\"abc\"")
        #expect(request.value(forHTTPHeaderField: "If-Modified-Since") == "Sun, 11 Oct 2026 06:45:55 GMT")
        #expect(again.report?.health == .minor)
        // The page confirmed nothing changed, so the reading is current.
        #expect(again.checkedAt == usageEpoch.addingTimeInterval(301))
        #expect(!again.isStale)
    }

    @Test func keepsTheLastReadingWhenOffline() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: try statusFixture("statuspage-claude"))
        let clock = TestClock()
        let status = makeService(transport, clock: clock)
        let claude = [try service("claude")]
        _ = await status.statuses(for: claude, force: false)
        clock.advance(301)
        transport.fail()
        let offline = try #require(await status.statuses(for: claude, force: false).first)
        #expect(offline.report?.health == .minor)
        #expect(offline.checkedAt == usageEpoch)
        #expect(offline.isStale)
        #expect(offline.problem == "Couldn't reach status.claude.com")
    }

    /// Past an hour an old reading is not shown as the state of the service.
    @Test func expiresOldReadings() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: try statusFixture("statuspage-claude"))
        let clock = TestClock()
        let status = makeService(transport, clock: clock)
        let claude = [try service("claude")]
        _ = await status.statuses(for: claude, force: false)
        clock.advance(3601)
        transport.reply(500)
        let expired = try #require(await status.statuses(for: claude, force: false).first)
        #expect(expired.report == nil)
        #expect(expired.problem == "status.claude.com answered 500")
    }

    @Test func honoursRetryAfter() async throws {
        let transport = FakeTransport()
        transport.reply(429, headers: ["Retry-After": "1200"])
        let clock = TestClock()
        let status = makeService(transport, clock: clock)
        let cursor = [try service("cursor")]
        let first = try #require(await status.statuses(for: cursor, force: false).first)
        #expect(first.problem?.hasPrefix("status.cursor.com asked us to wait until") == true)
        clock.advance(900)
        _ = await status.statuses(for: cursor, force: true)
        #expect(transport.requests.count == 1)
        clock.advance(301)
        transport.reply(200, body: try statusFixture("statuspage-github"))
        let after = try #require(await status.statuses(for: cursor, force: false).first)
        #expect(transport.requests.count == 2)
        #expect(after.problem == nil)
    }

    /// Retry-After counts from when the answer arrived, not when the
    /// request went out.
    @Test func retryAfterCountsFromTheAnswer() async throws {
        let clock = TestClock()
        let transport = ClockAdvancingTransport(clock: clock, seconds: 20)
        transport.inner.reply(429, headers: ["Retry-After": "120"])
        let dir = FileManager.default.temporaryDirectory.appending(path: "status-\(UUID().uuidString)")
        let status = ServiceStatusService(transport: transport, cacheURL: dir.appending(path: "service-status.json"),
                                          now: { clock.now })
        let cursor = [try service("cursor")]
        _ = await status.statuses(for: cursor, force: false)
        clock.advance(110)  // 130 s after the request, 110 s after the answer
        _ = await status.statuses(for: cursor, force: true)
        #expect(transport.inner.requests.count == 1)
    }

    @Test func unreadableBodyIsAProblemNotAnOutage() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: Data("<html>".utf8))
        let first = try #require(await makeService(transport, clock: TestClock())
            .statuses(for: [try service("npm")], force: false).first)
        #expect(first.report == nil)
        #expect(first.problem == "Couldn't read status.npmjs.org")
    }

    /// The cache file carries readings across launches without a new request.
    @Test func cacheSurvivesARestart() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: "status-\(UUID().uuidString)")
        let transport = FakeTransport()
        transport.reply(200, body: try statusFixture("statuspage-openai"))
        let clock = TestClock()
        let openai = [try service("openai")]
        _ = await makeService(transport, clock: clock, directory: directory).statuses(for: openai, force: false)
        clock.advance(60)
        let restarted = makeService(transport, clock: clock, directory: directory)
        let cached = try #require(await restarted.cached(for: openai).first)
        #expect(cached.report?.health == .minor)
        let polled = try #require(await restarted.statuses(for: openai, force: false).first)
        #expect(polled.checkedAt == usageEpoch)
        #expect(transport.requests.count == 1)
    }

    /// A second call while the first is waiting on the network joins its
    /// request instead of sending another.
    @Test func overlappingCallsShareOneRequest() async throws {
        let transport = DelayedTransport()
        transport.inner.reply(200, body: try statusFixture("statuspage-github"))
        let clock = TestClock()
        let dir = FileManager.default.temporaryDirectory.appending(path: "status-\(UUID().uuidString)")
        let status = ServiceStatusService(transport: transport, cacheURL: dir.appending(path: "service-status.json"),
                                          now: { clock.now })
        let github = [try service("github")]
        async let first = status.statuses(for: github, force: false)
        async let second = status.statuses(for: github, force: true)
        let (a, b) = await (first, second)
        #expect(transport.inner.requests.count == 1)
        #expect(a.first?.report?.health == .operational)
        #expect(b.first?.report?.health == .operational)
    }

    /// With a cache that cannot be written, the limits still hold in memory.
    @Test func unwritableCacheKeepsTheCooldown() async throws {
        let blocker = FileManager.default.temporaryDirectory.appending(path: "status-\(UUID().uuidString)")
        try Data().write(to: blocker)
        let transport = FakeTransport()
        transport.reply(429, headers: ["Retry-After": "1200"])
        let clock = TestClock()
        let status = ServiceStatusService(transport: transport, cacheURL: blocker.appending(path: "service-status.json"),
                                          now: { clock.now })
        let cursor = [try service("cursor")]
        _ = await status.statuses(for: cursor, force: false)
        clock.advance(600)
        let waiting = try #require(await status.statuses(for: cursor, force: true).first)
        #expect(transport.requests.count == 1)
        #expect(waiting.problem?.hasPrefix("status.cursor.com asked us to wait until") == true)
    }

    /// Another running copy's later attempt wins over this one's older entry.
    @Test func newerEntryWins() {
        var early = ServiceStatusService.Entry()
        early.attemptedAt = usageEpoch
        var late = ServiceStatusService.Entry()
        late.attemptedAt = usageEpoch.addingTimeInterval(30)
        late.cooldownUntil = usageEpoch.addingTimeInterval(900)
        #expect(ServiceStatusService.newer(early, late) == late)
        #expect(ServiceStatusService.newer(late, early) == late)
        #expect(ServiceStatusService.newer(nil, early) == early)
    }

    @Test func googleCloudFeed() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: try openGeminiIncident())
        let gemini = try #require(await makeService(transport, clock: TestClock())
            .statuses(for: [try service("gemini")], force: false).first)
        #expect(gemini.report?.health == .critical)
        #expect(transport.requests.first?.url?.absoluteString == "https://status.cloud.google.com/incidents.json")
    }
}

@Suite struct ServiceStatusSummaryTests {
    private func status(_ id: String, _ health: ServiceHealth?) throws -> ServiceStatus {
        let report = health.map { StatusReport(health: $0, summary: nil, components: [], incidents: []) }
        return ServiceStatus(service: try service(id), report: report, checkedAt: usageEpoch, problem: nil, isStale: false)
    }

    @Test func menuLineNamesDegradedProviders() throws {
        #expect(ServiceStatusSummary.menuLine([try status("claude", .operational)]) == nil)
        #expect(ServiceStatusSummary.menuLine([try status("claude", .minor)]) == "Claude: minor incident")
        #expect(ServiceStatusSummary.menuLine([try status("claude", .minor), try status("openai", .major)])
            == "OpenAI / Codex: major outage, Claude: minor incident")
        #expect(ServiceStatusSummary.menuLine([try status("claude", .minor), try status("openai", .major),
                                               try status("cursor", .minor)])
            == "OpenAI / Codex: major outage, and 2 more")
    }

    /// Infrastructure, maintenance and unknown states stay off the menu bar.
    @Test func menuLineIgnoresTheRest() throws {
        #expect(ServiceStatusSummary.menuLine([try status("cloudflare", .major), try status("github", .maintenance),
                                               try status("cursor", nil)]) == nil)
        #expect(ServiceStatusSummary.worst([try status("cloudflare", .major), try status("claude", .minor)]) == .minor)
    }
}

/// The fake network, answering only after a pause so calls can overlap.
private final class DelayedTransport: HTTPTransport {
    let inner = FakeTransport()

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        try await Task.sleep(for: .milliseconds(200))
        return try await inner.send(request)
    }
}

/// The fake network, taking `seconds` of the test clock to answer.
private final class ClockAdvancingTransport: HTTPTransport {
    let inner = FakeTransport()
    let clock: TestClock
    let seconds: TimeInterval

    init(clock: TestClock, seconds: TimeInterval) {
        self.clock = clock
        self.seconds = seconds
    }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        clock.advance(seconds)
        return try await inner.send(request)
    }
}
