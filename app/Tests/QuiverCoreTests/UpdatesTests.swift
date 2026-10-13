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

private func okReply(_ text: String) -> CommandResult {
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
                       builtCommit: String? = built) async -> LocalBuildCheck.Result {
        await LocalBuildCheck(runner: GitFake(replies))
            .check(path: "/tmp/checkout", builtCommit: builtCommit)
    }

    @Test func upToDate() async {
        let result = await check([
            "rev-parse HEAD": okReply(head + "\n"),
            "cat-file -e \(built)^{commit}": okReply(""),
            "rev-list --left-right --count \(built)...HEAD": okReply("0\t0\n"),
            "status --porcelain": okReply(""),
        ])
        #expect(result.state == .upToDate)
        #expect(!result.checkoutDirty)
    }

    @Test func behind() async {
        let result = await check([
            "rev-parse HEAD": okReply(head + "\n"),
            "cat-file -e \(built)^{commit}": okReply(""),
            "rev-list --left-right --count \(built)...HEAD": okReply("0\t3\n"),
            "status --porcelain": okReply(""),
        ])
        #expect(result.state == .behind(3))
    }

    @Test func diverged() async {
        let result = await check([
            "rev-parse HEAD": okReply(head + "\n"),
            "cat-file -e \(built)^{commit}": okReply(""),
            "rev-list --left-right --count \(built)...HEAD": okReply("2\t5\n"),
            "status --porcelain": okReply(""),
        ])
        #expect(result.state == .diverged(buildOnly: 2, headOnly: 5))
    }

    @Test func notARepo() async {
        let result = await check([:])
        #expect(result.state == .notARepo)
    }

    @Test func unknownBuild() async {
        let result = await check(["rev-parse HEAD": okReply(head + "\n")],
                                 builtCommit: nil)
        #expect(result.state == .unknownBuild)
    }

    @Test func builtCommitMissing() async {
        let result = await check(["rev-parse HEAD": okReply(head + "\n")])
        #expect(result.state == .builtCommitMissing)
    }

    @Test func dirtyCheckout() async {
        let result = await check([
            "rev-parse HEAD": okReply(head + "\n"),
            "cat-file -e \(built)^{commit}": okReply(""),
            "rev-list --left-right --count \(built)...HEAD": okReply("0\t0\n"),
            "status --porcelain": okReply(" M file.swift\n"),
        ])
        #expect(result.checkoutDirty)
    }

    @Test func upstreamBehind() async {
        let result = await check([
            "rev-parse HEAD": okReply(head + "\n"),
            "cat-file -e \(built)^{commit}": okReply(""),
            "rev-list --left-right --count \(built)...HEAD": okReply("0\t0\n"),
            "status --porcelain": okReply(""),
            "rev-list --count HEAD..@{upstream}": okReply("7\n"),
        ])
        #expect(result.upstreamBehind == 7)
    }

    @Test func upstreamFallsBackToOriginHEAD() async {
        let result = await check([
            "rev-parse HEAD": okReply(head + "\n"),
            "cat-file -e \(built)^{commit}": okReply(""),
            "rev-list --left-right --count \(built)...HEAD": okReply("0\t0\n"),
            "status --porcelain": okReply(""),
            "rev-list --count HEAD..origin/HEAD": okReply("4\n"),
        ])
        #expect(result.upstreamBehind == 4)
    }

    @Test func upstreamIsNilWhenNeitherRefWorks() async {
        let result = await check([
            "rev-parse HEAD": okReply(head + "\n"),
            "cat-file -e \(built)^{commit}": okReply(""),
            "rev-list --left-right --count \(built)...HEAD": okReply("0\t0\n"),
            "status --porcelain": okReply(""),
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
        #expect(ReleaseCheck.compare(version: "1.0.0", to: newer) == .available(newer))
        #expect(ReleaseCheck.compare(version: "1.0.0", to: same) == .upToDate(same))
        #expect(ReleaseCheck.compare(version: "1.0", to: same) == .upToDate(same))
        #expect(ReleaseCheck.compare(version: "1.0.9",
                                   to: .init(tag: "v1.0.10", url: nil, publishedAt: nil))
              == .available(.init(tag: "v1.0.10", url: nil, publishedAt: nil)))
        if case .unknown(_, let release) = ReleaseCheck.compare(version: "1.0.0", to: weird) {
            #expect(release == weird)
        } else {
            Issue.record("malformed tag should read as unknown")
        }
    }

    @Test func fetchesDecodesAndCaches() async throws {
        var transport = FakeTransport()
        transport.answer = .success((Data(Self.payload.utf8), 200))
        let check = ReleaseCheck(transport: transport, cacheURL: tempCache())
        let outcome = await check.check(current: "1.0.0", force: false)
        guard case .available(let release) = outcome else {
            Issue.record("expected an available release, got \(outcome)")
            return
        }
        #expect(release.tag == "v1.1.0")
        #expect(release.url?.absoluteString.contains("/tag/v1.1.0") == true)
        #expect(release.publishedAt != nil)
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
        guard case .available = await cold.check(current: "1.0.0", force: false) else {
            Issue.record("warm cache should skip the network")
            return
        }
        // Forced, the same dead transport surfaces its error but keeps the
        // cached release.
        guard case .unknown(let message, let release) =
                await cold.check(current: "1.0.0", force: true) else {
            Issue.record("forced check on a dead transport should be unknown")
            return
        }
        #expect(message != nil)
        #expect(release?.tag == "v1.1.0")
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
