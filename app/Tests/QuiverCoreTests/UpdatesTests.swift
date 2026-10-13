import Foundation
import Testing
@testable import QuiverCore

/// Stands in for git: maps each argv (after `-C <path>`) to a canned
/// result, in order of a lookup table with a wildcard fallback.
private final class GitFake: CommandRunning, @unchecked Sendable {
    var replies: [String: CommandResult]
    private(set) var calls: [[String]] = []

    init(_ replies: [String: CommandResult]) { self.replies = replies }

    func run(_ arguments: [String]) async throws -> CommandResult {
        calls.append(arguments)
        let key = arguments.dropFirst(2).joined(separator: " ")
        return replies[key] ?? CommandResult(status: 128, stdout: Data(), stderr: "unknown")
    }
}

private func gitOk(_ text: String) -> CommandResult {
    CommandResult(status: 0, stdout: Data(text.utf8), stderr: "")
}

private let head = "aabbccddee00112233445566778899aabbccdd"
private let built = "ffeeddccbbaa99887766554433221100ffeeddcc"

@Suite("Build info")
struct BuildInfoTests {
    @Test func allKeys() {
        let info = BuildInfo(infoDictionary: [
            "QuiverChannel": "prod",
            "QuiverCommit": "abc123",
            "QuiverSourcePath": "/tmp/checkout",
            "QuiverDirty": true,
            "CFBundleShortVersionString": "1.2.3",
            "QuiverVersion": "v1.2.3-4-gabc123",
        ])
        #expect(info.channel == .prod)
        #expect(info.commit == "abc123")
        #expect(info.sourcePath == "/tmp/checkout")
        #expect(info.dirty)
        #expect(info.version == "1.2.3")
        #expect(info.describe == "v1.2.3-4-gabc123")
    }

    @Test func missingAndEmptyReadAsDev() {
        let info = BuildInfo(infoDictionary: nil)
        #expect(info.channel == .dev)
        #expect(info.commit == nil)
        #expect(info.sourcePath == nil)
        #expect(!info.dirty)
        #expect(info.version == "0.0.0")

        let empty = BuildInfo(infoDictionary: [
            "QuiverChannel": "bogus",
            "QuiverCommit": "",
            "QuiverSourcePath": "",
            "CFBundleShortVersionString": "",
        ])
        #expect(empty.channel == .dev)
        #expect(empty.commit == nil)
        #expect(empty.sourcePath == nil)
        #expect(empty.version == "0.0.0")
        #expect(empty.describe == nil)
    }
}

@Suite("Update preferences")
struct UpdatePreferencesTests {
    private func freshDefaults() throws -> UserDefaults {
        let defaults = try #require(UserDefaults(suiteName: "UpdatePreferencesTests.\(UUID().uuidString)"))
        defaults.removePersistentDomain(forName: defaults.description)
        return defaults
    }

    @Test func storedChannelWins() {
        let build = BuildInfo(channel: .dev)
        let prefs = UpdatePreferences(defaults: try! freshDefaults())
        #expect(prefs.channel(build: build) == .dev)
        prefs.channelOverride = "prod"
        #expect(prefs.channel(build: build) == .prod)
        prefs.channelOverride = "nonsense"
        #expect(prefs.channel(build: build) == .dev)
    }

    @Test func storedCheckoutWinsAndExpandsTilde() {
        let build = BuildInfo(sourcePath: "/nonexistent/path")
        let prefs = UpdatePreferences(defaults: try! freshDefaults())
        prefs.checkoutOverride = "~/quiver"
        #expect(prefs.checkout(build: build) == (NSHomeDirectory() + "/quiver"))
    }

    @Test func stampedPathIsUsedWhenItExists() throws {
        let dir = FileManager.default.temporaryDirectory
            .appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let prefs = UpdatePreferences(defaults: try! freshDefaults())
        #expect(prefs.checkout(build: BuildInfo(sourcePath: dir.path)) == dir.path)
        #expect(prefs.checkout(build: BuildInfo(sourcePath: nil)) == nil)
        #expect(prefs.checkout(build: BuildInfo(sourcePath: "/definitely/not/here")) == nil)
    }
}

@Suite("Local build check")
struct LocalBuildCheckTests {
    private func check(_ replies: [String: CommandResult],
                       builtCommit: String? = built,
                       builtDirty: Bool = false) async -> LocalBuildCheck.Result {
        await LocalBuildCheck(runner: GitFake(replies))
            .check(path: "/tmp/checkout", builtCommit: builtCommit, builtDirty: builtDirty)
    }

    @Test func upToDate() async {
        let result = await check([
            "rev-parse HEAD": gitOk(head + "\n"),
            "cat-file -e \(built)^{commit}": gitOk(""),
            "rev-list --left-right --count \(built)...HEAD": gitOk("0\t0\n"),
            "status --porcelain": gitOk(""),
        ])
        #expect(result.state == .upToDate)
        #expect(!result.checkoutDirty)
    }

    @Test func behind() async {
        let result = await check([
            "rev-parse HEAD": gitOk(head + "\n"),
            "cat-file -e \(built)^{commit}": gitOk(""),
            "rev-list --left-right --count \(built)...HEAD": gitOk("0\t3\n"),
            "status --porcelain": gitOk(""),
        ])
        #expect(result.state == .behind(3))
    }

    @Test func diverged() async {
        let result = await check([
            "rev-parse HEAD": gitOk(head + "\n"),
            "cat-file -e \(built)^{commit}": gitOk(""),
            "rev-list --left-right --count \(built)...HEAD": gitOk("2\t5\n"),
            "status --porcelain": gitOk(""),
        ])
        #expect(result.state == .diverged(buildOnly: 2, headOnly: 5))
    }

    @Test func notARepo() async {
        let result = await check([:])
        #expect(result.state == .notARepo)
    }

    @Test func unknownBuild() async {
        let result = await check(["rev-parse HEAD": gitOk(head + "\n")],
                                 builtCommit: nil)
        #expect(result.state == .unknownBuild)
    }

    @Test func builtCommitMissing() async {
        let result = await check(["rev-parse HEAD": gitOk(head + "\n")])
        #expect(result.state == .builtCommitMissing)
    }

    @Test func dirtyCheckout() async {
        let result = await check([
            "rev-parse HEAD": gitOk(head + "\n"),
            "cat-file -e \(built)^{commit}": gitOk(""),
            "rev-list --left-right --count \(built)...HEAD": gitOk("0\t0\n"),
            "status --porcelain": gitOk(" M file.swift\n"),
        ])
        #expect(result.checkoutDirty)
    }

    @Test func upstreamBehind() async {
        let result = await check([
            "rev-parse HEAD": gitOk(head + "\n"),
            "cat-file -e \(built)^{commit}": gitOk(""),
            "rev-list --left-right --count \(built)...HEAD": gitOk("0\t0\n"),
            "status --porcelain": gitOk(""),
            "rev-list --count HEAD..@{upstream}": gitOk("7\n"),
        ])
        #expect(result.upstreamBehind == 7)
    }

    @Test func upstreamFallsBackToOriginHEAD() async {
        let result = await check([
            "rev-parse HEAD": gitOk(head + "\n"),
            "cat-file -e \(built)^{commit}": gitOk(""),
            "rev-list --left-right --count \(built)...HEAD": gitOk("0\t0\n"),
            "status --porcelain": gitOk(""),
            "rev-list --count HEAD..origin/HEAD": gitOk("4\n"),
        ])
        #expect(result.upstreamBehind == 4)
    }

    @Test func aDirtyBuildIsReported() async {
        let result = await check([
            "rev-parse HEAD": gitOk(head + "\n"),
            "cat-file -e \(built)^{commit}": gitOk(""),
            "rev-list --left-right --count \(built)...HEAD": gitOk("0\t0\n"),
            "status --porcelain": gitOk(""),
        ], builtDirty: true)
        #expect(result.state == .upToDate)
        #expect(result.buildDirty)
    }

    @Test func upstreamIsNilWhenNeitherRefWorks() async {
        let result = await check([
            "rev-parse HEAD": gitOk(head + "\n"),
            "cat-file -e \(built)^{commit}": gitOk(""),
            "rev-list --left-right --count \(built)...HEAD": gitOk("0\t0\n"),
            "status --porcelain": gitOk(""),
        ])
        #expect(result.upstreamBehind == nil)
    }
}

@Suite("Release check")
struct ReleaseCheckTests {
    private static let payload = """
        {"tag_name": "v1.1.0",
         "html_url": "https://github.com/c-wenlong/quiver/releases/tag/v1.1.0",
         "published_at": "2026-10-01T00:00:00Z"}
        """

    private struct FakeTransport: HTTPTransport {
        var answer: Result<(Data, Int), Error> = .failure(URLError(.notConnectedToInternet))
        var calls = 0
        func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
            var copy = self
            copy.calls += 1
            let response = HTTPURLResponse(
                url: request.url!, statusCode: try answer.get().1, httpVersion: nil,
                headerFields: nil)!
            return (try answer.get().0, response)
        }
    }

    private func tempCache() -> URL {
        FileManager.default.temporaryDirectory
            .appending(path: UUID().uuidString)
            .appending(path: "latest-release.json")
    }

    @Test func comparesDottedNumbers() {
        let newer = ReleaseCheck.Release(tag: "v1.1.0", url: nil, publishedAt: nil)
        let same = ReleaseCheck.Release(tag: "v1.0.0", url: nil, publishedAt: nil)
        let weird = ReleaseCheck.Release(tag: "next", url: nil, publishedAt: nil)
        #expect(ReleaseCheck.compare(version: "1.0.0", to: newer) == .available)
        #expect(ReleaseCheck.compare(version: "1.0.0", to: same) == .upToDate)
        #expect(ReleaseCheck.compare(version: "1.0", to: same) == .upToDate)
        #expect(ReleaseCheck.compare(version: "1.0.9",
                                   to: .init(tag: "v1.0.10", url: nil, publishedAt: nil))
              == .available)
        #expect(ReleaseCheck.compare(version: "1.0.0", to: weird) == .incomparable)
    }

    @Test func fetchesDecodesAndCaches() async throws {
        var transport = FakeTransport()
        transport.answer = .success((Data(Self.payload.utf8), 200))
        let check = ReleaseCheck(transport: transport, cacheURL: tempCache())
        let report = await check.check(current: "1.0.0", force: false)
        #expect(report.comparison == .available)
        #expect(report.release?.tag == "v1.1.0")
        #expect(report.release?.url?.absoluteString.contains("/tag/v1.1.0") == true)
        #expect(report.release?.publishedAt != nil)
        #expect(report.error == nil)
    }

    @Test func freshCacheSkipsTheNetworkAndForceBypasses() async throws {
        let cacheURL = tempCache()
        var transport = FakeTransport()
        transport.answer = .success((Data(Self.payload.utf8), 200))
        let check = ReleaseCheck(transport: transport, cacheURL: cacheURL)
        _ = await check.check(current: "1.0.0", force: false)
        // A second checker with a failing transport still answers from the
        // warm cache.
        let cold = ReleaseCheck(transport: FakeTransport(), cacheURL: cacheURL)
        let warmed = await cold.check(current: "1.0.0", force: false)
        #expect(warmed.comparison == .available)
        #expect(warmed.error == nil)
        // Forced, the same dead transport surfaces its error but the cached
        // newer release still compares as available.
        let forced = await cold.check(current: "1.0.0", force: true)
        #expect(forced.error != nil)
        #expect(forced.release?.tag == "v1.1.0")
        #expect(forced.comparison == .available)
    }
}

@Suite("swe version")
struct SweVersionTests {
    @Test func envelopeDecodesTheVersion() async throws {
        let stdout = Data("""
            {"schema": 1, "data": {"name": "swe", "version": "1.0.0", "python": "3.15.0"}}
            """.utf8)
        let swe = SweClient(runner: FakeRunner(.init(status: 0, stdout: stdout, stderr: "")))
        struct Version: Decodable { let version: String }
        let reply: Version = try await swe.call(["version", "--json"])
        #expect(reply.version == "1.0.0")
    }

    @Test func anOlderSweAnswersNil() async {
        let swe = SweClient(runner: FakeRunner(
            .init(status: 1, stdout: Data(), stderr: "Unknown command: 'version'")))
        struct Version: Decodable { let version: String }
        let reply: Version? = try? await swe.call(["version", "--json"])
        #expect(reply == nil)
    }
}

/// Runs whatever it was asked, immediately; argv is captured for checks.
private final class RecordingRunner: CommandRunning, @unchecked Sendable {
    var result: CommandResult
    private(set) var calls: [[String]] = []

    init(_ result: CommandResult) { self.result = result }

    func run(_ arguments: [String]) async throws -> CommandResult {
        calls.append(arguments)
        return result
    }
}

/// Blocks inside run until finishCall, so a test can change the checkout
/// while a build is in flight.
private final class GateRunner: CommandRunning, @unchecked Sendable {
    private(set) var calls: [[String]] = []
    private var finish: CheckedContinuation<CommandResult, Never>?
    private var waiter: CheckedContinuation<Void, Never>?
    private var called = false

    func run(_ arguments: [String]) async throws -> CommandResult {
        calls.append(arguments)
        called = true
        waiter?.resume()
        waiter = nil
        return await withCheckedContinuation { finish = $0 }
    }

    func waitUntilCalled() async {
        if called { return }
        await withCheckedContinuation { waiter = $0 }
    }

    func finishCall(_ result: CommandResult) {
        finish?.resume(returning: result)
        finish = nil
    }
}

private struct DeadTransport: HTTPTransport {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        throw URLError(.notConnectedToInternet)
    }
}

@Suite("Updates model")
@MainActor
struct UpdatesModelTests {
    private func tempDir(_ name: String = UUID().uuidString) -> URL {
        let url = FileManager.default.temporaryDirectory.appending(path: name)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private func makeModel(checkout: String,
                           rebuildRunner: any CommandRunning,
                           rebuildDir: URL) -> UpdatesModel {
        let defaults = UserDefaults(suiteName: "UpdatesModelTests.\(UUID().uuidString)")!
        let prefs = UpdatePreferences(defaults: defaults)
        prefs.checkoutOverride = checkout
        return UpdatesModel(
            build: BuildInfo(commit: built),
            preferences: prefs,
            localCheck: LocalBuildCheck(runner: GitFake([
                "rev-parse HEAD": gitOk(head + "\n"),
                "cat-file -e \(built)^{commit}": gitOk(""),
                "rev-list --left-right --count \(built)...HEAD": gitOk("0\t0\n"),
                "status --porcelain": gitOk(""),
            ])),
            releaseCheck: ReleaseCheck(
                transport: DeadTransport(),
                cacheURL: tempDir().appending(path: "latest-release.json")),
            rebuildRunner: rebuildRunner,
            rebuildDir: rebuildDir)
    }

    @Test func changingTheCheckoutDropsStaleAnswersAndBuilds() async throws {
        let dir = tempDir()
        let rebuildDir = tempDir()
        let model = makeModel(
            checkout: dir.path,
            rebuildRunner: RecordingRunner(gitOk("")),
            rebuildDir: rebuildDir)
        await model.refresh(force: false)
        #expect(model.local != nil)
        await model.rebuild()
        #expect(model.rebuildState == .built(rebuildDir.appending(path: "Quiver.app")))

        model.setCheckout("")
        #expect(model.checkout == nil)
        #expect(model.local == nil)
        #expect(model.rebuildState == .idle)
    }

    @Test func aBuildThatOutlivesItsCheckoutIsDiscarded() async throws {
        let dir = tempDir()
        let gate = GateRunner()
        let model = makeModel(
            checkout: dir.path, rebuildRunner: gate, rebuildDir: tempDir())
        async let build: Void = model.rebuild()
        await gate.waitUntilCalled()
        model.setCheckout("")
        gate.finishCall(gitOk(""))
        await build
        #expect(model.rebuildState == .idle)
    }

    @Test func rebuildRunsTheScriptIntoTheRebuildCache() async throws {
        let dir = tempDir()
        let rebuildDir = tempDir()
        let runner = RecordingRunner(gitOk(""))
        let model = makeModel(checkout: dir.path, rebuildRunner: runner,
                              rebuildDir: rebuildDir)
        await model.rebuild()
        #expect(runner.calls == [[
            "QUIVER_BUILD_DIR=\(rebuildDir.path)", "bash",
            dir.path + "/app/script/build-app.sh",
        ]])
        #expect(model.rebuildState == .built(rebuildDir.appending(path: "Quiver.app")))
    }

    @Test func aFailedRebuildKeepsTheLastLines() async throws {
        let dir = tempDir()
        let runner = RecordingRunner(CommandResult(
            status: 1,
            stdout: Data("l1\nl2\nl3\nl4\nl5\nl6\nl7\nl8\n".utf8), stderr: ""))
        let model = makeModel(checkout: dir.path, rebuildRunner: runner,
                              rebuildDir: tempDir())
        await model.rebuild()
        guard case .failed(let message) = model.rebuildState else {
            Issue.record("expected a failed rebuild, got \(model.rebuildState)")
            return
        }
        #expect(message == "l3\nl4\nl5\nl6\nl7\nl8")
    }
}
