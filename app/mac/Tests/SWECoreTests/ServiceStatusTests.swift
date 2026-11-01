import Foundation
import Synchronization
import Testing
@testable import SWECore

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
        #expect(StatusService.all.filter { $0.group.isProvider }.map(\.name)
            == ["Claude", "OpenAI / Codex", "GitHub / Copilot", "Cursor", "Gemini", "Devin"])
    }

    @Test func healthTitlesAreSentenceCase() {
        let titles = ServiceHealth.allCases.map(\.title)
        #expect(titles == ["Operational", "Under maintenance", "Minor incident", "Major outage", "Critical outage"])
    }
}

@Suite struct StatusCatalogueTests {
    /// What the catalogue sheet promises: the built-ins first (a removed
    /// or edited one is tracked again from here), then unique https pages
    /// each serving a Statuspage summary.json.
    @Test func everyEntryIsATrackableStatuspage() {
        #expect(Array(StatusCatalogue.all.prefix(StatusService.all.count)) == StatusService.all)
        #expect(Set(StatusCatalogue.all.map(\.id)).count == StatusCatalogue.all.count)
        // Rows are matched against tracked pages by feed host, so two
        // entries on one host would both read Added after adding either.
        #expect(Set(StatusCatalogue.all.map { $0.feedURL.host()?.lowercased() }).count
            == StatusCatalogue.all.count)
        for entry in StatusCatalogue.all {
            #expect(!entry.name.isEmpty)
            #expect(entry.pageURL.scheme == "https")
            #expect(entry.feedURL.scheme == "https")
            // Gemini's built-in feed is Google Cloud's; the rest serve a
            // Statuspage summary.json (status.x.ai files it under /api/compat).
            if entry.feed != StatusService.all.first(where: { $0.id == entry.id })?.feed {
                #expect(entry.feedURL.path.hasSuffix("summary.json"))
            } else if case .statuspage = entry.feed {
                #expect(entry.feedURL.path.hasSuffix("summary.json"))
            }
        }
        // Every group chip must have rows to offer.
        for group in StatusService.Group.allCases {
            #expect(StatusCatalogue.all.contains { $0.group == group }, "no catalogue rows for \(group)")
        }
    }

    /// The sheet's mark column: every catalogue row and every built-in
    /// resolves a bundled logo. A new entry with no mark fails here rather
    /// than shipping a blank cell.
    @Test func everyEntryResolvesABundledMark() {
        for entry in StatusCatalogue.all + StatusService.all {
            let logo = entry.logoID
            #expect(logo != nil, "\(entry.id) has no bundled mark")
            #expect(logo.flatMap { HarnessIcons.icon(for: $0) } != nil,
                    "\(entry.id) names \(logo ?? "?") with no harness-*.svg")
        }
    }

    /// The sheet's search reads the name and the page's host, and the
    /// chips split on the group.
    @Test func filterMatchesGroupNameAndHost() {
        #expect(StatusCatalogue.filter(group: nil, query: "").count == StatusCatalogue.all.count)
        #expect(StatusCatalogue.filter(group: .modelProviders, query: "")
            .allSatisfy { $0.group == .modelProviders })
        #expect(StatusCatalogue.filter(group: .services, query: "")
            .allSatisfy { $0.group == .services })
        #expect(StatusCatalogue.filter(group: nil, query: " docker ")
            .map(\.id) == ["docker"])
        #expect(StatusCatalogue.filter(group: nil, query: "notion-status")
            .map(\.id) == ["notion"])
        #expect(StatusCatalogue.filter(group: .modelProviders, query: "docker").isEmpty)
        #expect(StatusCatalogue.filter(group: nil, query: "no such page").isEmpty)
    }
}

@Suite struct ServiceStatusServiceTests {
    /// Every cache folder lives in here, removed when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    private func makeService(_ transport: FakeTransport, clock: TestClock, directory: URL? = nil) -> ServiceStatusService {
        let dir = directory ?? scratch.url.appending(path: UUID().uuidString)
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
        #expect(first.problem?.hasPrefix("status.x.ai asked us to wait until") == true)
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
        let dir = scratch.url.appending(path: UUID().uuidString)
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
        let directory = scratch.url.appending(path: UUID().uuidString)
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
        let transport = HeldTransport()
        transport.inner.reply(200, body: try statusFixture("statuspage-github"))
        let clock = TestClock()
        // Armed once the first request is parked, the clock opens the gate
        // on its next read: the second call's start. The service reads the
        // clock and checks for a request under way in one actor step, so
        // by the time the first call can land the second has joined it.
        let armed = Box(false)
        let gate = transport.gate
        let dir = scratch.url.appending(path: UUID().uuidString)
        let status = ServiceStatusService(transport: transport, cacheURL: dir.appending(path: "service-status.json"),
                                          now: {
                                              if armed.value {
                                                  armed.value = false
                                                  gate.open()
                                              }
                                              return clock.now
                                          })
        let github = [try service("github")]
        let first = Task { await status.statuses(for: github, force: false) }
        #expect(await gate.arrived(1))
        armed.value = true
        let b = await status.statuses(for: github, force: true)
        let a = await first.value
        #expect(transport.inner.requests.count == 1)
        #expect(a.first?.report?.health == .operational)
        // Without the join the second call returns at once with no reading.
        #expect(b.first?.report?.health == .operational)
    }

    /// With a cache that cannot be written, the limits still hold in memory.
    @Test func unwritableCacheKeepsTheCooldown() async throws {
        let blocker = scratch.url.appending(path: UUID().uuidString)
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
        #expect(waiting.problem?.hasPrefix("status.x.ai asked us to wait until") == true)
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

    /// Minor infrastructure incidents, maintenance and unknown states stay
    /// off the menu bar.
    @Test func menuLineIgnoresTheRest() throws {
        #expect(ServiceStatusSummary.menuLine([try status("vercel", .minor), try status("github", .maintenance),
                                               try status("cursor", nil)]) == nil)
        #expect(ServiceStatusSummary.worst([try status("vercel", .minor), try status("claude", .operational)])
            == .operational)
    }

    /// A major infrastructure outage reaches the line on its own.
    @Test func menuLineNamesAMajorInfrastructureOutage() throws {
        #expect(ServiceStatusSummary.menuLine([try status("cloudflare", .major), try status("vercel", .minor)])
            == "Cloudflare: major outage")
        #expect(ServiceStatusSummary.menuLine([try status("npm", .critical)]) == "npm: critical outage")
        #expect(ServiceStatusSummary.worst([try status("cloudflare", .major), try status("claude", .minor)]) == .major)
    }

    /// The services the line names, in its order, for their logos.
    @Test func affectedListsTheServicesTheLineNames() throws {
        let statuses = [try status("claude", .minor), try status("cloudflare", .major), try status("openai", .major),
                        try status("cursor", .minor), try status("github", .maintenance), try status("vercel", .minor)]
        #expect(ServiceStatusSummary.affected(statuses).map(\.service.id)
            == ["cloudflare", "openai", "claude", "cursor"])
        #expect(ServiceStatusSummary.affected([try status("claude", .operational)]).isEmpty)
    }

    /// Pages filed under a harness's or provider's id draw its logo,
    /// infrastructure pages included.
    @Test func logoIDsMapPagesToHarnessLogos() throws {
        #expect(try service("claude").logoID == "claude")
        // A company's page draws its brand logo from ModelBrands, the one
        // every other page shows for OpenAI.
        #expect(try service("openai").logoID == "openai")
        #expect(try service("github").logoID == "copilot")
        #expect(try service("gemini").logoID == "gemini")
        #expect(try service("npm").logoID == "npm")
        #expect(try service("vercel").logoID == "vercel")
        #expect(try service("cloudflare").logoID == "cloudflare")
    }

    /// A custom page is matched on its name first, so "OpenRouter" finds
    /// the openrouter mark while "Acme" finds nothing.
    @Test func customServicesMatchLogosByName() throws {
        let openRouter = StatusService(
            id: "custom-openrouter", name: "OpenRouter",
            pageURL: URL(string: "https://status.openrouter.ai")!,
            feed: .statuspage(URL(string: "https://status.openrouter.ai/api/v2/summary.json")!),
            group: .modelProviders, isCustom: true)
        #expect(openRouter.logoID == "openrouter")
        let acme = StatusService(
            id: "custom-acme", name: "Acme", pageURL: URL(string: "https://status.acme.test")!,
            feed: .statuspage(URL(string: "https://status.acme.test/api/v2/summary.json")!),
            group: .services, isCustom: true)
        #expect(acme.logoID == nil)
    }

    /// A catalogue row's one-click add lands under a fresh custom id;
    /// the card still draws the catalogue's mark through the feed's host.
    @Test func catalogueAddsKeepTheirMarkThroughTheHost() {
        func added(_ name: String, _ host: String, _ group: StatusService.Group) -> StatusService {
            StatusService(
                id: StatusService.customID(for: name, taken: []), name: name,
                pageURL: URL(string: "https://\(host)")!,
                feed: .statuspage(URL(string: "https://\(host)/api/v2/summary.json")!),
                group: group, isCustom: true)
        }
        #expect(added("PyPI", "status.python.org", .services).logoID == "pypi")
        #expect(added("Sourcegraph", "sourcegraphstatus.com", .modelProviders).logoID == "sourcegraph")
        // The name match still wins where a brand covers it.
        #expect(added("Moonshot AI", "status.moonshot.cn", .modelProviders).logoID == "kimi")
    }
}

/// The status pages the user adds: slugs, storage, the model's add/remove
/// and the sheet's Check button.
@Suite struct CustomStatusTests {
    /// Every cache folder lives in here, removed when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    private func customService(_ id: String, _ name: String, _ group: StatusService.Group = .modelProviders)
        -> StatusService {
        let page = URL(string: "https://status.example.com")!
        return StatusService(id: id, name: name, pageURL: page,
                             feed: .statuspage(page.appending(path: "api/v2/summary.json")),
                             group: group, isCustom: true)
    }

    private func probeService(_ transport: FakeTransport,
                              resolve: @escaping @Sendable (URL) async -> URL? = { $0 })
        -> ServiceStatusService {
        let dir = scratch.url.appending(path: UUID().uuidString)
        return ServiceStatusService(transport: transport,
                                    cacheURL: dir.appending(path: "service-status.json"),
                                    redirectResolver: resolve)
    }

    /// "custom-" + the slugged name + a random suffix; the suffix is
    /// injected so the test can fix it.
    @Test func customIDsSlugTheNameAndTakeASuffix() {
        let suffix = { "aaaa0001" }
        #expect(StatusService.customID(for: "Groq Cloud", taken: [], suffix: suffix)
            == "custom-groq-cloud-aaaa0001")
        #expect(StatusService.customID(for: "ZAI (01.ai)", taken: [], suffix: suffix)
            == "custom-zai-01-ai-aaaa0001")
        // The uniqueness loop is a backstop for a suffix collision.
        #expect(StatusService.customID(for: "Acme", taken: ["custom-acme-aaaa0001"], suffix: suffix)
            == "custom-acme-aaaa0001-2")
    }

    /// Two pages with the same name get different ids, so one can never
    /// inherit the other's poll cache.
    @Test func sameNameGetsDifferentIdsEachTime() {
        let first = StatusService.customID(for: "Acme", taken: [], suffix: { "aaaa0001" })
        let second = StatusService.customID(for: "Acme", taken: [first], suffix: { "bbbb0002" })
        #expect(first == "custom-acme-aaaa0001")
        #expect(second == "custom-acme-bbbb0002")
    }

    @Test func storeRoundTripsThroughUserDefaults() throws {
        let defaults = TestDefaults("quiver-test")
        let store = CustomStatusServices(defaults: defaults)
        #expect(store.load() == StatusServicePrefs())
        var prefs = StatusServicePrefs()
        let acme = customService("custom-acme", "Acme", .services)
        prefs.custom = [acme]
        prefs.hidden = ["vercel"]
        prefs.overrides = ["cursor": .init(pageURL: URL(string: "https://status.x.ai")!,
                                           feedURL: URL(string: "https://status.x.ai/api/compat/summary.json")!)]
        store.save(prefs)
        #expect(store.load() == prefs)
        // Only customs are ever written; a built-in passed in is dropped.
        prefs.custom.append(try service("claude"))
        store.save(prefs)
        #expect(store.load().custom.map(\.id) == ["custom-acme"])
    }

    /// Records written before feeds could sit off /api/v2 still load.
    @Test func aLegacyStoreFileStillReads() throws {
        let defaults = TestDefaults("quiver-test")
        let store = CustomStatusServices(defaults: defaults)
        defaults.set(Data(#"[{"id":"custom-x","name":"X","pageURL":"https://x.test","group":"providers"}]"#.utf8),
                     forKey: CustomStatusServices.defaultsKey)
        let loaded = store.load()
        #expect(loaded.custom.map(\.id) == ["custom-x"])
        #expect(loaded.custom.first?.feedURL == URL(string: "https://x.test/api/v2/summary.json"))
        #expect(loaded.hidden.isEmpty && loaded.overrides.isEmpty)
    }

    @Test func badStoreDataReadsAsEmpty() throws {
        let defaults = TestDefaults("quiver-test")
        let store = CustomStatusServices(defaults: defaults)
        defaults.set(Data("<not json".utf8), forKey: CustomStatusServices.defaultsKey)
        #expect(store.load() == StatusServicePrefs())
        defaults.set(Data(#"[{"id":"custom-x","name":"X","pageURL":123,"group":"bogus"}]"#.utf8),
                     forKey: CustomStatusServices.defaultsKey)
        #expect(store.load() == StatusServicePrefs())
    }

    /// Records written before the group split keep their page: a group
    /// the enum no longer knows maps to the catch-all rather than
    /// failing the store's decode.
    @Test func oldAndUnknownGroupsLoadForward() throws {
        let defaults = TestDefaults("quiver-test")
        let store = CustomStatusServices(defaults: defaults)
        defaults.set(Data(#"[{"id":"custom-a","name":"A","pageURL":"https://a.test","group":"providers"},{"id":"custom-b","name":"B","pageURL":"https://b.test","group":"infrastructure"},{"id":"custom-c","name":"C","pageURL":"https://c.test","group":"bogus"},{"id":"custom-d","name":"D","pageURL":"https://status.python.org","group":"providers"}]"#.utf8),
                     forKey: CustomStatusServices.defaultsKey)
        let loaded = store.load().custom
        #expect(loaded.map(\.id) == ["custom-a", "custom-b", "custom-c", "custom-d"])
        // A catalogue page's group follows the catalogue, not the record.
        #expect(loaded.map(\.group) == [.modelProviders, .services, .services, .registries])
    }

    /// Only a pre-split group migrates: one the user picked under the
    /// new layout survives a restart even when the page is a catalogue
    /// host the catalogue groups elsewhere.
    @Test func aChosenGroupSurvivesAReload() throws {
        let defaults = TestDefaults("quiver-test")
        let store = CustomStatusServices(defaults: defaults)
        let groq = StatusService(id: "custom-g", name: "Groq",
                                 pageURL: URL(string: "https://groqstatus.com")!,
                                 feed: .statuspage(URL(string: "https://groqstatus.com/api/v2/summary.json")!),
                                 group: .services, isCustom: true)
        store.save(StatusServicePrefs(custom: [groq]))
        #expect(store.load().custom.first?.group == .services)
    }

    /// Adding puts the card in its group as unknown until the load fills
    /// it in, persists it, and lets the poll floors fetch it right away.
    @MainActor @Test func addStoresFetchesAndGroupsTheService() async throws {
        let clock = TestClock()
        let dir = scratch.url.appending(path: UUID().uuidString)
        let memory = MemoryStatusStore()
        let model = ServiceStatusModel(
            service: ServiceStatusService(transport: StubStatusTransport(body: try statusFixture("statuspage-github")),
                                        cacheURL: dir.appending(path: "service-status.json"),
                                        now: { clock.now }),
            store: memory)
        let added = await model.add(name: "OpenRouter",
                                    pageURL: URL(string: "https://status.openrouter.ai")!,
                                    feedURL: URL(string: "https://status.openrouter.ai/api/v2/summary.json")!,
                                    group: .modelProviders)
        #expect(added.id.hasPrefix("custom-openrouter-"))
        #expect(added.isCustom)
        #expect(added.feedURL.absoluteString == "https://status.openrouter.ai/api/v2/summary.json")
        #expect(model.services.last == added)
        #expect(memory.saved.custom.map(\.id) == [added.id])
        #expect(model.groups.first { $0.group == .modelProviders }?.statuses.last?.service == added)
        // The fetch ran: the card is filled, not left unknown.
        #expect(model.statuses.last?.report?.health == .operational)
        #expect(model.statuses.last?.checkedAt == usageEpoch)
    }

    /// Customs are deleted; a built-in goes hidden, its id kept free for
    /// the catalogue's Add to bring back.
    @MainActor @Test func removeDeletesCustomsAndHidesBuiltIns() async throws {
        let dir = scratch.url.appending(path: UUID().uuidString)
        let memory = MemoryStatusStore()
        let model = ServiceStatusModel(
            service: ServiceStatusService(transport: StubStatusTransport(body: try statusFixture("statuspage-github")),
                                        cacheURL: dir.appending(path: "service-status.json")),
            store: memory)
        let added = await model.add(name: "Acme", pageURL: URL(string: "https://status.acme.test")!,
                                    feedURL: URL(string: "https://status.acme.test/api/v2/summary.json")!,
                                    group: .services)
        await model.remove(id: "claude")
        #expect(!model.services.contains { $0.id == "claude" })
        #expect(memory.saved.hidden == ["claude"])
        #expect(memory.saved.custom.map(\.id) == [added.id])
        await model.remove(id: added.id)
        #expect(!model.services.contains { $0.id == added.id })
        #expect(!model.statuses.contains { $0.service.id == added.id })
        #expect(memory.saved.custom.isEmpty)
    }

    /// The catalogue's Add on a built-in row restores the catalog's
    /// entry: a removed card comes back, an edited one loses its override.
    @MainActor @Test func trackRestoresABuiltIn() async throws {
        let dir = scratch.url.appending(path: UUID().uuidString)
        let memory = MemoryStatusStore()
        let model = ServiceStatusModel(
            service: ServiceStatusService(transport: StubStatusTransport(body: try statusFixture("statuspage-github")),
                                        cacheURL: dir.appending(path: "service-status.json")),
            store: memory)
        await model.remove(id: "cursor")
        #expect(!model.services.contains { $0.id == "cursor" })
        let cursor = try #require(StatusCatalogue.all.first { $0.id == "cursor" })
        _ = await model.track(cursor)
        #expect(model.services.contains { $0.id == "cursor" })
        #expect(try model.services.first { $0.id == "cursor" } == service("cursor"))
        #expect(memory.saved.hidden.isEmpty)
        // A non-built-in row lands as a custom page.
        let groq = try #require(StatusCatalogue.all.first { $0.id == "groq" })
        let added = await model.track(groq)
        #expect(added.isCustom)
        #expect(added.feedURL == groq.feedURL)
    }

    /// The editor's Save on a built-in keeps the difference from the
    /// catalog as an override, and Reset puts the catalog's entry back.
    @MainActor @Test func updateOverridesAndResetRestoresABuiltIn() async throws {
        let dir = scratch.url.appending(path: UUID().uuidString)
        let memory = MemoryStatusStore()
        let model = ServiceStatusModel(
            service: ServiceStatusService(transport: StubStatusTransport(body: try statusFixture("statuspage-github")),
                                        cacheURL: dir.appending(path: "service-status.json")),
            store: memory)
        await model.update(id: "cursor", name: "Cursor", pageURL: URL(string: "https://status.cursor.sh")!,
                           feedURL: URL(string: "https://status.cursor.sh/api/v2/summary.json")!,
                           group: .modelProviders)
        let edited = try #require(model.services.first { $0.id == "cursor" })
        #expect(edited.pageURL == URL(string: "https://status.cursor.sh"))
        #expect(edited.feedURL == URL(string: "https://status.cursor.sh/api/v2/summary.json"))
        #expect(model.isEdited("cursor"))
        #expect(memory.saved.overrides["cursor"]?.feedURL?.host() == "status.cursor.sh")
        await model.reset(id: "cursor")
        #expect(try model.services.first { $0.id == "cursor" } == service("cursor"))
        #expect(!model.isEdited("cursor"))
        #expect(memory.saved.overrides.isEmpty)
    }

    /// An edit that returns a built-in to its default writes no override.
    @MainActor @Test func anEditBackToTheDefaultWritesNoOverride() async throws {
        let dir = scratch.url.appending(path: UUID().uuidString)
        let model = ServiceStatusModel(
            service: ServiceStatusService(transport: StubStatusTransport(body: try statusFixture("statuspage-github")),
                                        cacheURL: dir.appending(path: "service-status.json")),
            store: MemoryStatusStore())
        let claude = try service("claude")
        await model.update(id: "claude", name: claude.name, pageURL: claude.pageURL,
                           feedURL: claude.feedURL, group: claude.group)
        #expect(!model.isEdited("claude"))
        #expect(model.services.first { $0.id == "claude" } == claude)
    }

    /// A page-URL edit keeps the checked feed the probe returned; without
    /// it `applying` would derive /api/v2/summary.json, which is wrong for
    /// feeds filed elsewhere, like Cursor's /api/compat.
    @MainActor @Test func anEditKeepsTheCheckedFeed() async throws {
        let dir = scratch.url.appending(path: UUID().uuidString)
        let memory = MemoryStatusStore()
        let make = {
            ServiceStatusModel(
                service: ServiceStatusService(transport: StubStatusTransport(body: try! statusFixture("statuspage-github")),
                                            cacheURL: dir.appending(path: "service-status.json")),
                store: memory)
        }
        let model = make()
        // The probe normalizes the page to scheme://host/ — the trailing
        // slash makes this an override even though the host is unchanged.
        await model.update(id: "cursor", name: "Cursor",
                           pageURL: URL(string: "https://status.x.ai/")!,
                           feedURL: URL(string: "https://status.x.ai/api/compat/summary.json")!,
                           group: .modelProviders)
        #expect(memory.saved.overrides["cursor"]?.feedURL
            == URL(string: "https://status.x.ai/api/compat/summary.json"))
        // A reload through `applying` keeps the compat feed.
        #expect(make().services.first { $0.id == "cursor" }?.feedURL
            == URL(string: "https://status.x.ai/api/compat/summary.json"))
    }

    /// The editor's Save on a custom page rewrites its record.
    @MainActor @Test func updateRewritesACustomService() async throws {
        let dir = scratch.url.appending(path: UUID().uuidString)
        let memory = MemoryStatusStore()
        let model = ServiceStatusModel(
            service: ServiceStatusService(transport: StubStatusTransport(body: try statusFixture("statuspage-github")),
                                        cacheURL: dir.appending(path: "service-status.json")),
            store: memory)
        let added = await model.add(name: "Acme", pageURL: URL(string: "https://status.acme.test")!,
                                    feedURL: URL(string: "https://status.acme.test/api/v2/summary.json")!,
                                    group: .modelProviders)
        await model.update(id: added.id, name: "Acme Two",
                           pageURL: URL(string: "https://status.acme.example")!,
                           feedURL: URL(string: "https://status.acme.example/api/v2/summary.json")!,
                           group: .services)
        let edited = try #require(model.services.last)
        #expect(edited.name == "Acme Two")
        #expect(edited.group == .services)
        #expect(edited.feedURL.host() == "status.acme.example")
        #expect(memory.saved.custom == [edited])
    }

    /// A load parked mid-flight cannot bring a page removed while it was
    /// out back into the list or the menu bar.
    @MainActor @Test func aRemovedPageStaysRemovedWhenAParkedLoadLands() async throws {
        let transport = GatedTransport(body: try statusFixture("statuspage-claude"))
        let dir = scratch.url.appending(path: UUID().uuidString)
        let memory = MemoryStatusStore()
        memory.saved.custom = [customService("custom-acme-aaaa0001", "Acme")]
        let model = ServiceStatusModel(
            service: ServiceStatusService(transport: transport,
                                        cacheURL: dir.appending(path: "service-status.json")),
            store: memory)
        let loading = Task { await model.load(force: true) }
        #expect(await transport.gate.arrived(model.services.count))
        await model.remove(id: "custom-acme-aaaa0001")
        transport.gate.open()
        await loading.value
        #expect(!model.statuses.contains { $0.service.id == "custom-acme-aaaa0001" })
        #expect(!model.affected.map(\.id).contains("custom-acme-aaaa0001"))
    }

    /// After a remove, the id is gone from memory and the cache file, so a
    /// same-named page added later starts unknown instead of inheriting
    /// the old reading.
    @MainActor @Test func aReAddedPageStartsUnknown() async throws {
        let dir = scratch.url.appending(path: UUID().uuidString)
        let cacheFile = dir.appending(path: "service-status.json")
        let statusService = ServiceStatusService(transport: StubStatusTransport(
            body: try statusFixture("statuspage-claude")), cacheURL: cacheFile)
        let model = ServiceStatusModel(service: statusService, store: MemoryStatusStore())
        let first = await model.add(name: "Acme", pageURL: URL(string: "https://status.acme.test")!,
                                    feedURL: URL(string: "https://status.acme.test/api/v2/summary.json")!,
                                    group: .modelProviders)
        #expect(model.statuses.last?.report?.health == .minor)
        await model.remove(id: first.id)
        // The cache file has no entry for the removed id.
        let file = try #require(
            try JSONSerialization.jsonObject(with: Data(contentsOf: cacheFile)) as? [String: Any])
        #expect((file["services"] as? [String: Any])?[first.id] == nil)
        // And a cached read for it is unknown, as for a page never seen.
        #expect(await statusService.cached(for: [first]).first?.report == nil)
        let second = await model.add(name: "Acme", pageURL: URL(string: "https://status.acme.test")!,
                                     feedURL: URL(string: "https://status.acme.test/api/v2/summary.json")!,
                                     group: .modelProviders)
        #expect(second.id != first.id)
    }

    /// An id forgotten while its request is in flight — a remove, or an
    /// edit that moved the feed — does not get its old answer saved back:
    /// it would hold the new feed off for the next five minutes.
    @MainActor @Test func aForgottenPollsAnswerIsDropped() async throws {
        let transport = GatedTransport(body: try statusFixture("statuspage-github"))
        let dir = scratch.url.appending(path: UUID().uuidString)
        let status = ServiceStatusService(transport: transport,
                                          cacheURL: dir.appending(path: "service-status.json"))
        let github = [try service("github")]
        async let first: [ServiceStatus] = status.statuses(for: github, force: false)
        #expect(await transport.gate.arrived(1))
        await status.forget(id: "github")
        transport.gate.open()
        let statuses = await first
        #expect(statuses.first?.report == nil)
        #expect(await status.cached(for: github).first?.report == nil)
        let file = try? JSONSerialization.jsonObject(
            with: (try? Data(contentsOf: dir.appending(path: "service-status.json"))) ?? Data()) as? [String: Any]
        #expect((file?["services"] as? [String: Any])?["github"] == nil)
    }

    @Test func probeReadsAStatuspageFeed() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: try statusFixture("statuspage-github"))
        let result = await probeService(transport).probe("status.example.com/some/path?x=1")
        guard case .ok(let pageURL, let feedURL, let report) = result else {
            Issue.record("expected ok, got \(result)")
            return
        }
        #expect(pageURL == URL(string: "https://status.example.com/"))
        #expect(feedURL == URL(string: "https://status.example.com/api/v2/summary.json"))
        #expect(report.health == .operational)
        #expect(transport.requests.first?.url?.absoluteString
            == "https://status.example.com/api/v2/summary.json")
    }

    /// A page without the /api/v2 feed still reads when it serves the same
    /// shape under /api/compat, the way status.x.ai does.
    @Test func probeFallsBackToTheCompatFeed() async throws {
        let transport = FakeTransport()
        transport.reply(403)
        transport.reply(200, body: try statusFixture("statuspage-github"))
        let result = await probeService(transport).probe("status.example.com")
        guard case .ok(_, let feedURL, _) = result else {
            Issue.record("expected ok, got \(result)")
            return
        }
        #expect(feedURL == URL(string: "https://status.example.com/api/compat/summary.json"))
        #expect(transport.requests.map { $0.url?.path }
            == ["/api/v2/summary.json", "/api/compat/summary.json"])
    }

    /// The polling transport follows no redirects, so the probe asks the
    /// resolver and reads the host the page actually answers at.
    @Test func probeFollowsTheRedirectToTheFinalHost() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: try statusFixture("statuspage-github"))
        let result = await probeService(transport) { _ in URL(string: "https://real.example.org/deep/path") }
            .probe("https://status.example.com/")
        guard case .ok(let pageURL, _, _) = result else {
            Issue.record("expected ok, got \(result)")
            return
        }
        #expect(pageURL == URL(string: "https://real.example.org/"))
        #expect(transport.requests.first?.url?.absoluteString
            == "https://real.example.org/api/v2/summary.json")
    }

    @Test func probeRejectsPlainHttp() async {
        let transport = FakeTransport()
        let result = await probeService(transport).probe("http://status.example.com")
        #expect(result == .failure("Use the https address of the status page."))
        #expect(transport.requests.isEmpty)
    }

    @Test func probeExplainsAPageItCannotRead() async throws {
        for (status, body) in [(404, Data()), (200, Data("<html>".utf8))] {
            let transport = FakeTransport()
            transport.reply(status, body: body)
            transport.reply(status, body: body)
            let result = await probeService(transport).probe("status.example.com")
            #expect(result == .failure("SWE can read Atlassian Statuspage and incident.io pages, "
                + "which serve a JSON feed under /api. status.example.com doesn't."))
        }
    }

    @Test func probeReportsAnUnreachableHost() async {
        let transport = FakeTransport()
        transport.fail()
        let result = await probeService(transport).probe("status.example.com")
        #expect(result == .failure("Couldn't reach status.example.com."))
    }
}

/// Where the model's prefs go, in memory.
private final class MemoryStatusStore: StatusServiceStoring, @unchecked Sendable {
    var saved = StatusServicePrefs()
    func load() -> StatusServicePrefs { saved }
    func save(_ prefs: StatusServicePrefs) { saved = prefs }
}

/// Always answers 200 with the same body: `add` polls every page, and the
/// built-ins' answers are beside the point.
private struct StubStatusTransport: HTTPTransport {
    let body: Data
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        (body, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1",
                               headerFields: nil)!)
    }
}

/// Parks every request at `gate` until the test opens it, so a test can
/// remove a service while its load is still out.
private final class GatedTransport: HTTPTransport {
    let gate = HoldGate()
    let body: Data
    init(body: Data) { self.body = body }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        try await gate.pass()
        return (body, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1",
                                      headerFields: nil)!)
    }
}

/// The fake network, holding every request at `gate` until the test opens
/// it, so calls can overlap.
private final class HeldTransport: HTTPTransport {
    let inner = FakeTransport()
    let gate = HoldGate()

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        try await gate.pass()
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
