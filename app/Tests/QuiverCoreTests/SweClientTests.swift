import Foundation
import Testing
@testable import QuiverCore

/// Stands in for the `swe` process: records what it was asked and replies
/// with canned output.
final class FakeRunner: CommandRunning, @unchecked Sendable {
    var reply: CommandResult
    private(set) var calls: [[String]] = []

    init(_ reply: CommandResult) { self.reply = reply }

    func run(_ arguments: [String]) async throws -> CommandResult {
        calls.append(arguments)
        return reply
    }
}

func fixture(_ name: String) throws -> Data {
    let url = try #require(Bundle.module.url(
        forResource: name, withExtension: "json", subdirectory: "Fixtures"))
    return try Data(contentsOf: url)
}

@Suite struct SweClientTests {
    @Test func listDecodesHarnessesAndTheirLimits() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: try fixture("list-usage"), stderr: ""))
        let harnesses = try await SweClient(runner: runner).harnesses(withUsage: true)

        #expect(harnesses.map(\.name) == ["claude", "codex", "gemini"])
        let claude = try #require(harnesses.first)
        #expect(claude.state == .starred)
        #expect(claude.aliases == ["cc"])
        let limit = try #require(claude.rateLimit)
        #expect(limit.status == .ok)
        #expect(limit.usedPercent == 28)
        #expect(limit.remainingPercent == 72)
        #expect(limit.window == "5h")
        #expect(limit.resetAt == Date(timeIntervalSince1970: 1_791_718_000))
        #expect(limit.fetchedAt != nil)
        #expect(harnesses[1].rateLimit == nil)
        #expect(harnesses[2].state == .active)
    }

    @Test func usageIsOnlyRequestedWhenAsked() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: try fixture("list-usage"), stderr: ""))
        let client = SweClient(runner: runner)
        _ = try await client.harnesses(withUsage: false)
        _ = try await client.harnesses(withUsage: true)
        #expect(runner.calls == [["list", "--json"], ["list", "--usage", "--json"]])
    }

    @Test func aNewerSchemaIsRefusedRatherThanMisread() async throws {
        let body = Data(#"{"schema": 2, "data": {"harnesses": []}}"#.utf8)
        let client = SweClient(runner: FakeRunner(.init(status: 0, stdout: body, stderr: "")))
        await #expect(throws: SweError.unsupportedSchema(2)) {
            _ = try await client.harnesses(withUsage: false)
        }
    }

    @Test func aFailingCommandSurfacesItsStderr() async throws {
        let client = SweClient(runner: FakeRunner(.init(status: 1, stdout: Data(), stderr: "boom\n")))
        await #expect(throws: SweError.commandFailed(status: 1, message: "boom")) {
            _ = try await client.harnesses(withUsage: false)
        }
    }

    @Test func anOlderSweWithoutJSONIsNamedAsSuch() async throws {
        // A swe from before --json existed rejects the flag in plain text.
        let old = Data("\u{1B}[31m  Unexpected args: --json\u{1B}[0m\n".utf8)
        let client = SweClient(runner: FakeRunner(.init(status: 0, stdout: old, stderr: "")))
        await #expect(throws: SweError.jsonUnsupported) {
            _ = try await client.harnesses(withUsage: false)
        }
        let viaStderr = SweClient(runner: FakeRunner(.init(status: 1, stdout: Data(), stderr: "Unknown flag: --json")))
        await #expect(throws: SweError.jsonUnsupported) {
            _ = try await viaStderr.harnesses(withUsage: false)
        }
    }

    @Test func aFailureWithOnlyStdoutStillSaysWhy() async throws {
        // Older swe printed refusals to stdout.
        let client = SweClient(runner: FakeRunner(.init(status: 1, stdout: Data("\u{1B}[31mUnknown flag: --bogus\u{1B}[0m\n".utf8), stderr: "")))
        await #expect(throws: SweError.commandFailed(status: 1, message: "Unknown flag: --bogus")) {
            _ = try await client.harnesses(withUsage: false)
        }
    }

    @Test func errorMessagesCarryNoTerminalColourCodes() async throws {
        let client = SweClient(runner: FakeRunner(.init(status: 1, stdout: Data(), stderr: "\u{1B}[31mboom\u{1B}[0m")))
        await #expect(throws: SweError.commandFailed(status: 1, message: "boom")) {
            _ = try await client.harnesses(withUsage: false)
        }
    }

    @Test func nonJSONOutputIsReportedAsSuch() async throws {
        let client = SweClient(runner: FakeRunner(.init(status: 0, stdout: Data("Unknown flag".utf8), stderr: "")))
        await #expect(throws: SweError.self) {
            _ = try await client.harnesses(withUsage: false)
        }
    }
}

@Suite struct SweLocationTests {
    @Test func aChosenPathWinsOverTheBundledOneAndPATH() {
        #expect(SweLocation.executable(override: "/opt/quiver/bin/swe", bundled: "/A/swe")
            == "/opt/quiver/bin/swe")
        #expect(SweLocation.executable(override: "/opt/quiver/bin/swe", bundled: nil)
            == "/opt/quiver/bin/swe")
    }

    @Test func blankOrMissingOverrideMeansTheBundledSwe() {
        #expect(SweLocation.executable(override: nil, bundled: "/A/swe") == "/A/swe")
        #expect(SweLocation.executable(override: "  ", bundled: "/A/swe") == "/A/swe")
    }

    @Test func withoutABundledSweItIsWhateverPATHFinds() {
        #expect(SweLocation.executable(override: nil, bundled: nil) == "swe")
        #expect(SweLocation.executable(override: "  ", bundled: nil) == "swe")
    }

    @Test func theBundledSweIsFoundOnlyWhenItIsExecutable() throws {
        let app = FileManager.default.temporaryDirectory
            .appending(path: "SweLocationTests-\(UUID().uuidString).app")
        defer { try? FileManager.default.removeItem(at: app) }
        let bin = app.appending(path: "Contents/Resources/swe/bin")
        try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
        try Data("""
            <?xml version="1.0" encoding="UTF-8"?>
            <plist version="1.0"><dict>
            <key>CFBundleIdentifier</key><string>com.example.swe-location-tests</string>
            </dict></plist>
            """.utf8).write(to: app.appending(path: "Contents/Info.plist"))
        let shim = bin.appending(path: "swe")
        try Data("#!/bin/sh\n".utf8).write(to: shim)
        let bundle = try #require(Bundle(url: app))
        #expect(SweLocation.bundled(in: bundle) == nil)

        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: shim.path)
        #expect(SweLocation.bundled(in: bundle) == shim.path)
    }
}

@Suite struct LoginShellPathTests {
    @Test func pathIsReadFromTheLoginShellEnvironment() {
        let env = "HOME=/Users/k\nPATH=/opt/homebrew/bin:/usr/bin\nSHELL=/bin/zsh\n"
        #expect(LoginShell.path(fromEnvOutput: env) == "/opt/homebrew/bin:/usr/bin")
    }

    @Test func missingPathFallsBackToNil() {
        #expect(LoginShell.path(fromEnvOutput: "HOME=/Users/k\n") == nil)
    }
}
