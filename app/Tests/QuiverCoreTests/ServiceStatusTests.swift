import Foundation
import Synchronization
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

    /// The services the line names, in its order, for their logos.
    @Test func affectedListsTheProvidersTheLineNames() throws {
        let statuses = [try status("claude", .minor), try status("cloudflare", .major), try status("openai", .major),
                        try status("cursor", .minor), try status("github", .maintenance)]
        #expect(ServiceStatusSummary.affected(statuses).map(\.service.id) == ["openai", "claude", "cursor"])
        #expect(ServiceStatusSummary.affected([try status("claude", .operational)]).isEmpty)
    }

    /// Pages filed under a harness's or provider's id draw its logo,
    /// infrastructure pages included.
    @Test func logoIDsMapPagesToHarnessLogos() throws {
        #expect(try service("claude").logoID == "claude")
        #expect(try service("openai").logoID == "codex")
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
            group: .providers, isCustom: true)
        #expect(openRouter.logoID == "openrouter")
        let acme = StatusService(
            id: "custom-acme", name: "Acme", pageURL: URL(string: "https://status.acme.test")!,
            feed: .statuspage(URL(string: "https://status.acme.test/api/v2/summary.json")!),
            group: .infrastructure, isCustom: true)
        #expect(acme.logoID == nil)
    }
}

/// The status pages the user adds: slugs, storage, the model's add/remove
/// and the sheet's Check button.
@Suite struct CustomStatusTests {
    private func customService(_ id: String, _ name: String, _ group: StatusService.Group = .providers)
        -> StatusService {
        let page = URL(string: "https://status.example.com")!
        return StatusService(id: id, name: name, pageURL: page,
                             feed: .statuspage(page.appending(path: "api/v2/summary.json")),
                             group: group, isCustom: true)
    }

    private func probeService(_ transport: FakeTransport,
                              resolve: @escaping @Sendable (URL) async -> URL? = { $0 })
        -> ServiceStatusService {
        let dir = FileManager.default.temporaryDirectory.appending(path: "status-\(UUID().uuidString)")
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
        let suite = "quiver-test-\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = CustomStatusServices(defaults: defaults)
        #expect(store.load().isEmpty)
        let acme = customService("custom-acme", "Acme", .infrastructure)
        store.save([acme])
        #expect(try #require(store.load().first) == acme)
        // Only customs are ever written; a built-in passed in is dropped.
        store.save([acme, try service("claude")])
        #expect(store.load().map(\.id) == ["custom-acme"])
    }

    @Test func badStoreDataReadsAsEmpty() throws {
        let suite = "quiver-test-\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = CustomStatusServices(defaults: defaults)
        defaults.set(Data("<not json".utf8), forKey: CustomStatusServices.defaultsKey)
        #expect(store.load().isEmpty)
        defaults.set(Data(#"[{"id":"custom-x","name":"X","pageURL":"https://x.test","group":"bogus"}]"#.utf8),
                     forKey: CustomStatusServices.defaultsKey)
        #expect(store.load().isEmpty)
    }

    /// Adding puts the card in its group as unknown until the load fills
    /// it in, persists it, and lets the poll floors fetch it right away.
    @MainActor @Test func addStoresFetchesAndGroupsTheService() async throws {
        let clock = TestClock()
        let dir = FileManager.default.temporaryDirectory.appending(path: "status-\(UUID().uuidString)")
        let memory = MemoryStatusStore()
        let model = ServiceStatusModel(
            service: ServiceStatusService(transport: StubStatusTransport(body: try statusFixture("statuspage-github")),
                                        cacheURL: dir.appending(path: "service-status.json"),
                                        now: { clock.now }),
            store: memory)
        let added = await model.add(name: "OpenRouter",
                                    pageURL: URL(string: "https://status.openrouter.ai")!,
                                    group: .providers)
        #expect(added.id.hasPrefix("custom-openrouter-"))
        #expect(added.isCustom)
        #expect(added.feedURL.absoluteString == "https://status.openrouter.ai/api/v2/summary.json")
        #expect(model.services.last == added)
        #expect(memory.saved.map(\.id) == [added.id])
        #expect(model.groups.first { $0.group == .providers }?.statuses.last?.service == added)
        // The fetch ran: the card is filled, not left unknown.
        #expect(model.statuses.last?.report?.health == .operational)
        #expect(model.statuses.last?.checkedAt == usageEpoch)
    }

    /// Only customs can go; a built-in id is left alone.
    @MainActor @Test func removeDropsOnlyCustomServices() async throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: "status-\(UUID().uuidString)")
        let memory = MemoryStatusStore()
        let model = ServiceStatusModel(
            service: ServiceStatusService(transport: StubStatusTransport(body: try statusFixture("statuspage-github")),
                                        cacheURL: dir.appending(path: "service-status.json")),
            store: memory)
        let added = await model.add(name: "Acme", pageURL: URL(string: "https://status.acme.test")!,
                                    group: .infrastructure)
        await model.remove(id: "claude")
        #expect(model.services.contains { $0.id == "claude" })
        #expect(memory.saved.map(\.id) == [added.id])
        await model.remove(id: added.id)
        #expect(!model.services.contains { $0.id == added.id })
        #expect(!model.statuses.contains { $0.service.id == added.id })
        #expect(memory.saved.isEmpty)
    }

    /// A load parked mid-flight cannot bring a page removed while it was
    /// out back into the list or the menu bar.
    @MainActor @Test func aRemovedPageStaysRemovedWhenAParkedLoadLands() async throws {
        let transport = GatedTransport(body: try statusFixture("statuspage-claude"))
        let dir = FileManager.default.temporaryDirectory.appending(path: "status-\(UUID().uuidString)")
        let memory = MemoryStatusStore()
        memory.saved = [customService("custom-acme-aaaa0001", "Acme")]
        let model = ServiceStatusModel(
            service: ServiceStatusService(transport: transport,
                                        cacheURL: dir.appending(path: "service-status.json")),
            store: memory)
        let loading = Task { await model.load(force: true) }
        while transport.inFlight < model.services.count {
            try await Task.sleep(for: .milliseconds(5))
        }
        await model.remove(id: "custom-acme-aaaa0001")
        transport.release()
        await loading.value
        #expect(!model.statuses.contains { $0.service.id == "custom-acme-aaaa0001" })
        #expect(!model.affected.map(\.id).contains("custom-acme-aaaa0001"))
    }

    /// After a remove, the id is gone from memory and the cache file, so a
    /// same-named page added later starts unknown instead of inheriting
    /// the old reading.
    @MainActor @Test func aReAddedPageStartsUnknown() async throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: "status-\(UUID().uuidString)")
        let cacheFile = dir.appending(path: "service-status.json")
        let statusService = ServiceStatusService(transport: StubStatusTransport(
            body: try statusFixture("statuspage-claude")), cacheURL: cacheFile)
        let model = ServiceStatusModel(service: statusService, store: MemoryStatusStore())
        let first = await model.add(name: "Acme", pageURL: URL(string: "https://status.acme.test")!,
                                    group: .providers)
        #expect(model.statuses.last?.report?.health == .minor)
        await model.remove(id: first.id)
        // The cache file has no entry for the removed id.
        let file = try #require(
            try JSONSerialization.jsonObject(with: Data(contentsOf: cacheFile)) as? [String: Any])
        #expect((file["services"] as? [String: Any])?[first.id] == nil)
        // And a cached read for it is unknown, as for a page never seen.
        #expect(await statusService.cached(for: [first]).first?.report == nil)
        let second = await model.add(name: "Acme", pageURL: URL(string: "https://status.acme.test")!,
                                     group: .providers)
        #expect(second.id != first.id)
    }

    @Test func probeReadsAStatuspageFeed() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: try statusFixture("statuspage-github"))
        let result = await probeService(transport).probe("status.example.com/some/path?x=1")
        guard case .ok(let pageURL, let report) = result else {
            Issue.record("expected ok, got \(result)")
            return
        }
        #expect(pageURL == URL(string: "https://status.example.com/"))
        #expect(report.health == .operational)
        #expect(transport.requests.first?.url?.absoluteString
            == "https://status.example.com/api/v2/summary.json")
    }

    /// The polling transport follows no redirects, so the probe asks the
    /// resolver and reads the host the page actually answers at.
    @Test func probeFollowsTheRedirectToTheFinalHost() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: try statusFixture("statuspage-github"))
        let result = await probeService(transport) { _ in URL(string: "https://real.example.org/deep/path") }
            .probe("https://status.example.com/")
        guard case .ok(let pageURL, _) = result else {
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
            let result = await probeService(transport).probe("status.example.com")
            #expect(result == .failure("Quiver can read Atlassian Statuspage and incident.io pages, "
                + "which serve /api/v2/summary.json. status.example.com doesn't."))
        }
    }

    @Test func probeReportsAnUnreachableHost() async {
        let transport = FakeTransport()
        transport.fail()
        let result = await probeService(transport).probe("status.example.com")
        #expect(result == .failure("Couldn't reach status.example.com."))
    }
}

/// Where the model's customs go, in memory.
private final class MemoryStatusStore: CustomStatusStoring, @unchecked Sendable {
    var saved: [StatusService] = []
    func load() -> [StatusService] { saved }
    func save(_ services: [StatusService]) { saved = services }
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

/// Parks every request at the gate until `release`, so a test can remove a
/// service while its load is still out.
private final class GatedTransport: HTTPTransport {
    private struct State {
        var waiting: [CheckedContinuation<Void, Never>] = []
        var inFlight = 0
        var open = false
    }

    private let state = Mutex(State())
    let body: Data
    init(body: Data) { self.body = body }

    var inFlight: Int { state.withLock { $0.inFlight } }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        await withCheckedContinuation { continuation in
            state.withLock { s in
                s.inFlight += 1
                if s.open { continuation.resume() } else { s.waiting.append(continuation) }
            }
        }
        return (body, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1",
                                      headerFields: nil)!)
    }

    func release() {
        state.withLock { s in
            s.open = true
            for continuation in s.waiting { continuation.resume() }
            s.waiting.removeAll()
        }
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
