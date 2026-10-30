import Foundation
import Synchronization
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
        TestDefaults("UpdatePreferencesTests")
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

    @Test func aProdBuildIgnoresAStoredDevOverride() {
        let build = BuildInfo(channel: .prod)
        let prefs = UpdatePreferences(defaults: try! freshDefaults())
        prefs.channelOverride = "dev"
        #expect(prefs.channel(build: build) == .prod)
        prefs.channelOverride = "prod"
        #expect(prefs.channel(build: build) == .prod)
        prefs.channelOverride = ""
        #expect(prefs.channel(build: build) == .prod)
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
    /// Every temp folder lives in here, removed when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

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
        scratch.url
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

/// Holds run at a gate until finishCall or failCall, so a test can change
/// the checkout while a build is in flight. The test waits with
/// `gate.arrived()`, which gives up after `gateTimeout` instead of hanging.
private final class GateRunner: CommandRunning, Sendable {
    let gate = HoldGate()
    private let outcome = Mutex<Result<CommandResult, any Error>?>(nil)

    func run(_ arguments: [String]) async throws -> CommandResult {
        try await gate.pass()
        guard let outcome = outcome.withLock({ $0 }) else { throw CancellationError() }
        return try outcome.get()
    }

    func finishCall(_ result: CommandResult) {
        outcome.withLock { $0 = .success(result) }
        gate.open()
    }

    func failCall(_ error: any Error) {
        outcome.withLock { $0 = .failure(error) }
        gate.open()
    }
}

/// Throws `SweError.timedOut` the way a run past its deadline does on the
/// first call, then answers like a finished build, so a test can see the
/// rebuild state recover instead of sticking at .building.
private final class FlakyRunner: CommandRunning, @unchecked Sendable {
    private var failures: Int

    init(failures: Int = 1) { self.failures = failures }

    func run(_ arguments: [String]) async throws -> CommandResult {
        if failures > 0 {
            failures -= 1
            throw SweError.timedOut
        }
        return CommandResult(status: 0, stdout: Data(), stderr: "")
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
    /// Every temp folder lives in here, removed when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    private func tempDir(_ name: String = UUID().uuidString) -> URL {
        let url = scratch.url.appending(path: name)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private func makeModel(checkout: String,
                           rebuildRunner: any CommandRunning,
                           rebuildDir: URL) -> UpdatesModel {
        let defaults = TestDefaults("UpdatesModelTests")
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

    /// The dev controls (channel picker, Source checkout, Rebuild) are a
    /// dev build's; a prod build hides them even with a dev override saved.
    @Test func aProdBuildHasNoDevControls() {
        let defaults = TestDefaults("UpdatesModelTests")
        let prefs = UpdatePreferences(defaults: defaults)
        prefs.channelOverride = "dev"
        let model = UpdatesModel(
            build: BuildInfo(channel: .prod, commit: built),
            preferences: prefs,
            releaseCheck: ReleaseCheck(
                transport: DeadTransport(),
                cacheURL: tempDir().appending(path: "latest-release.json")))
        #expect(model.channel == .prod)
        #expect(!model.showsDevControls)
        // setChannel can still be called; on a prod build it stays prod.
        model.setChannel(.dev)
        #expect(model.channel == .prod)
    }

    @Test func aDevBuildShowsDevControls() {
        let defaults = TestDefaults("UpdatesModelTests")
        let model = UpdatesModel(
            build: BuildInfo(channel: .dev, commit: built),
            preferences: UpdatePreferences(defaults: defaults),
            releaseCheck: ReleaseCheck(
                transport: DeadTransport(),
                cacheURL: tempDir().appending(path: "latest-release.json")))
        #expect(model.channel == .dev)
        #expect(model.showsDevControls)
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
        guard case .built(let url) = model.rebuildState else {
            Issue.record("expected a built rebuild, got \(model.rebuildState)")
            return
        }
        let buildDir = url.deletingLastPathComponent()
        #expect(url.lastPathComponent == "Quiver.app")
        #expect(buildDir.deletingLastPathComponent().path == rebuildDir.path)
        #expect(FileManager.default.fileExists(atPath: buildDir.path))

        model.setCheckout("")
        #expect(model.checkout == nil)
        #expect(model.local == nil)
        #expect(model.rebuildState == .idle)
        #expect(!FileManager.default.fileExists(atPath: buildDir.path))
    }

    @Test func aBuildThatOutlivesItsCheckoutIsDiscarded() async throws {
        let dir = tempDir()
        let runner = GateRunner()
        let model = makeModel(
            checkout: dir.path, rebuildRunner: runner, rebuildDir: tempDir())
        async let build: Void = model.rebuild()
        #expect(await runner.gate.arrived())
        model.setCheckout("")
        runner.finishCall(gitOk(""))
        await build
        #expect(model.rebuildState == .idle)
    }

    @Test func aBuildFailureThatOutlivesItsCheckoutIsDiscarded() async throws {
        // Same race on the error path: a timed-out or failed build from
        // the old checkout must not paint .failed on the new row.
        let dir = tempDir()
        let runner = GateRunner()
        let model = makeModel(
            checkout: dir.path, rebuildRunner: runner, rebuildDir: tempDir())
        async let build: Void = model.rebuild()
        #expect(await runner.gate.arrived())
        model.setCheckout("")
        runner.failCall(SweError.timedOut)
        await build
        #expect(model.rebuildState == .idle)
    }

    /// Puts an empty build script at `relative` inside `checkout`, so the
    /// rebuild sees the layout of a real checkout.
    private func placeScript(_ relative: String, in checkout: URL) throws {
        let script = checkout.appending(path: relative)
        try FileManager.default.createDirectory(at: script.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try Data().write(to: script)
    }

    @Test func rebuildRunsTheScriptIntoTheRebuildCache() async throws {
        let dir = tempDir()
        try placeScript("app/mac/script/build-app.sh", in: dir)
        let rebuildDir = tempDir()
        let runner = RecordingRunner(gitOk(""))
        let model = makeModel(checkout: dir.path, rebuildRunner: runner,
                              rebuildDir: rebuildDir)
        await model.rebuild()
        #expect(runner.calls.count == 1)
        let argv = try #require(runner.calls.first)
        #expect(argv.count == 3)
        #expect(argv[1] == "bash")
        #expect(argv[2] == dir.path + "/app/mac/script/build-app.sh")
        let buildDir = String(argv[0].dropFirst("QUIVER_BUILD_DIR=".count))
        #expect(buildDir.hasPrefix(rebuildDir.path + "/"))
        #expect(buildDir != rebuildDir.path)
        guard case .built(let url) = model.rebuildState else {
            Issue.record("expected a built rebuild, got \(model.rebuildState)")
            return
        }
        #expect(url == URL(fileURLWithPath: buildDir)
            .appending(path: "Quiver.app"))

        // A second rebuild gets its own folder and clears the first.
        model.resetRebuild()
        await model.rebuild()
        let second = try #require(runner.calls.last)[0]
            .dropFirst("QUIVER_BUILD_DIR=".count)
        #expect(String(second) != buildDir)
        #expect(!FileManager.default.fileExists(atPath: buildDir))
    }

    @Test func aCheckoutFromBeforeTheMoveBuildsWithTheOldScript() async throws {
        let dir = tempDir()
        try placeScript("app/script/build-app.sh", in: dir)
        let runner = RecordingRunner(gitOk(""))
        let model = makeModel(checkout: dir.path, rebuildRunner: runner,
                              rebuildDir: tempDir())
        await model.rebuild()
        let argv = try #require(runner.calls.first)
        #expect(argv[2] == dir.path + "/app/script/build-app.sh")
    }

    @Test func pruneRemovesOnlyOldFoldersInsideTheCache() async throws {
        let dir = tempDir()
        let rebuildDir = tempDir()
        let model = makeModel(checkout: dir.path,
                              rebuildRunner: RecordingRunner(gitOk("")),
                              rebuildDir: rebuildDir)
        let old = rebuildDir.appending(path: "old-build")
        let fresh = rebuildDir.appending(path: "fresh-build")
        let outside = tempDir().appending(path: "keep-me")
        for url in [old, fresh] {
            try FileManager.default.createDirectory(at: url,
                                                    withIntermediateDirectories: true)
        }
        try "x".write(to: outside, atomically: true, encoding: .utf8)
        let twoDaysAgo = Date().addingTimeInterval(-2 * 24 * 3600)
        try FileManager.default.setAttributes(
            [.modificationDate: twoDaysAgo], ofItemAtPath: old.path)

        model.pruneStaleBuilds()

        #expect(!FileManager.default.fileExists(atPath: old.path))
        #expect(FileManager.default.fileExists(atPath: fresh.path))
        #expect(FileManager.default.fileExists(atPath: outside.path))
    }

    @Test func pruneKeepsAFolderWhoseOwnerIsAlive() async throws {
        let dir = tempDir()
        let rebuildDir = tempDir()
        let model = makeModel(checkout: dir.path,
                              rebuildRunner: RecordingRunner(gitOk("")),
                              rebuildDir: rebuildDir)
        func oldFolder(_ name: String, owner: String?) throws -> URL {
            let folder = rebuildDir.appending(path: name)
            try FileManager.default.createDirectory(at: folder,
                                                    withIntermediateDirectories: true)
            if let owner {
                try owner.write(to: folder.appending(path: "owner.pid"),
                                atomically: true, encoding: .utf8)
            }
            // Backdating goes last: the marker write touches the folder's
            // own mtime.
            try FileManager.default.setAttributes(
                [.modificationDate: Date().addingTimeInterval(-2 * 24 * 3600)],
                ofItemAtPath: folder.path)
            return folder
        }
        let live = try oldFolder("live", owner: "\(ProcessInfo.processInfo.processIdentifier)")
        let dead = try oldFolder("dead", owner: "999999")
        #expect(kill(999999, 0) != 0)
        let unmarked = try oldFolder("unmarked", owner: nil)

        model.pruneStaleBuilds()

        #expect(FileManager.default.fileExists(atPath: live.path))
        #expect(!FileManager.default.fileExists(atPath: dead.path))
        #expect(!FileManager.default.fileExists(atPath: unmarked.path))
    }

    @Test func relaunchOnACleanedUpBuildExplainsItself() async throws {
        let dir = tempDir()
        let model = makeModel(checkout: dir.path,
                              rebuildRunner: RecordingRunner(gitOk("")),
                              rebuildDir: tempDir())
        await model.rebuild()
        guard case .built(let url) = model.rebuildState else {
            Issue.record("expected a built rebuild, got \(model.rebuildState)")
            return
        }
        try FileManager.default.removeItem(at: url.deletingLastPathComponent())

        #expect(model.relaunchURL() == nil)
        #expect(model.rebuildState == .idle)
        #expect(model.rebuildNotice == "The build was cleaned up. Rebuild to try again.")
    }

    @Test func aTimedOutBuildFailsAndTheNextBuildCanRun() async throws {
        let dir = tempDir()
        let model = makeModel(checkout: dir.path, rebuildRunner: FlakyRunner(),
                              rebuildDir: tempDir())
        await model.rebuild()
        guard case .failed(let message) = model.rebuildState else {
            Issue.record("expected a failed rebuild, got \(model.rebuildState)")
            return
        }
        #expect(message.contains("timed out"))
        // The wedge would have left .building and blocked this rebuild.
        model.resetRebuild()
        #expect(model.rebuildState == .idle)
        await model.rebuild()
        guard case .built = model.rebuildState else {
            Issue.record("expected a built rebuild, got \(model.rebuildState)")
            return
        }
    }

    @Test func aFailedRebuildKeepsTheLastLinesAndWritesTheLog() async throws {
        let dir = tempDir()
        let rebuildDir = tempDir()
        let runner = RecordingRunner(CommandResult(
            status: 1,
            stdout: Data("l1\nl2\nl3\nl4\nl5\nl6\nl7\nl8\n".utf8), stderr: ""))
        let model = makeModel(checkout: dir.path, rebuildRunner: runner,
                              rebuildDir: rebuildDir)
        await model.rebuild()
        guard case .failed(let message) = model.rebuildState else {
            Issue.record("expected a failed rebuild, got \(model.rebuildState)")
            return
        }
        #expect(message.hasPrefix("l3\nl4\nl5\nl6\nl7\nl8\nFull log: "))
        let log = rebuildDir.appending(path: "last-build.log")
        let text = try String(contentsOf: log, encoding: .utf8)
        #expect(text.contains("=== stdout ==="))
        #expect(text.contains("l8"))
    }
}

@Suite("Build failure summary")
struct FailureSummaryTests {
    private func summary(stderr: String = "", stdout: String = "",
                         log: URL? = nil, limit: Int = 6) -> String {
        UpdatesModel.failureSummary(stderr: stderr, stdout: Data(stdout.utf8),
                                    log: log, limit: limit)
    }

    /// A log that names no errors keeps showing its own tail.
    @Test func noErrorLinesKeepsTheTail() {
        let out = (1...8).map { "l\($0)" }.joined(separator: "\n")
        #expect(summary(stdout: out) == "l3\nl4\nl5\nl6\nl7\nl8")
    }

    /// A build's last lines are progress counters; the error named
    /// earlier must surface instead.
    @Test func errorLinesBeatTheProgressTail() {
        let msg = summary(
            stderr: "app.swift:1:5: error: cannot assign to property\n",
            stdout: "[161/172]\n[163/172]\n[167/172]\n")
        #expect(msg.contains("error: cannot assign to property"))
        #expect(!msg.contains("[167/172]"))
    }

    /// More errors than the row takes: the first few, the count left
    /// out, and the last one.
    @Test func manyErrorsShowFirstAndLast() {
        let errors = (1...8).map { "f\($0).swift:1:1: error: problem \($0)" }
        let msg = summary(stderr: errors.joined(separator: "\n"))
        #expect(msg.contains("problem 1"))
        #expect(msg.contains("problem 5"))
        #expect(!msg.contains("problem 6"))
        #expect(msg.contains("… 2 more error lines"))
        #expect(msg.contains("problem 8"))
    }

    /// The build scripts' own messages name the failure even without
    /// an "error:" marker.
    @Test func scriptMessagesCountAsFailures() {
        #expect(summary(stderr: "step done\nbuild-app: Sparkle.framework missing\n")
            == "build-app: Sparkle.framework missing")
    }

    /// Script status chatter ("downloading …") wears the same prefix
    /// as diagnostics: a real failure line beats it.
    @Test func aStatusLineDoesNotHideAnUnmarkedFailure() {
        let msg = summary(
            stderr: "bundle-swe: downloading Python 3.13.16 (aarch64-apple-darwin)\n"
                + "tar: python.tar.zst: No space left on device\n")
        #expect(msg.contains("No space left on device"))
        #expect(!msg.contains("downloading Python"))
    }

    /// A killed step (OOM, timeout) names the failure too.
    @Test func killedStepsCountAsFailures() {
        let msg = summary(
            stderr: "bundle-swe: downloading Python 3.13.16\n"
                + "bash: line 40: 8123 Killed: 9               tar -xzf python.tar.zst\n")
        #expect(msg.contains("Killed: 9"))
    }

    /// Styling and hyperlinks a log may carry must not paint literal
    /// escapes in the row.
    @Test func ansiStylingIsStripped() {
        #expect(summary(stderr: "\u{1B}[1;31merror: boom\u{1B}[0m\n")
            == "error: boom")
    }

    /// A failed command echoes every file it was given: the line is cut
    /// rather than eating the row.
    @Test func aGiantLineIsCut() {
        let msg = summary(stderr: "error: " + String(repeating: "x", count: 500))
        #expect(msg.count == 201)
        #expect(msg.hasPrefix("error: "))
        #expect(msg.hasSuffix("…"))
    }

    /// A build that died silently still tells the user something.
    @Test func silenceStillSaysSomething() {
        #expect(summary(stderr: "  \n\n") == "The build failed without saying why.")
    }

    /// The log the row points at is named in the message.
    @Test func theLogIsNamed() {
        let log = URL(fileURLWithPath: "/tmp/x/last-build.log")
        #expect(summary(stderr: "error: x", log: log)
            .hasSuffix("\nFull log: /tmp/x/last-build.log"))
    }
}
