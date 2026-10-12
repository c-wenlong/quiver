import Foundation
import Testing
@testable import QuiverCore

/// The real runner against /bin/sh, so pipe handling is exercised for real.
@Suite struct ProcessRunnerTests {
    let env = ["PATH": "/usr/bin:/bin"]

    @Test func returnsStatusStdoutAndStderr() async throws {
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let result = try await runner.run(["-c", "printf '{\"a\":1}'; printf oops >&2; exit 3"])
        #expect(result.status == 3)
        #expect(String(decoding: result.stdout, as: UTF8.self) == #"{"a":1}"#)
        #expect(result.stderr == "oops")
    }

    @Test func drainsOutputLargerThanThePipeBuffer() async throws {
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let result = try await runner.run(["-c", "head -c 300000 /dev/zero | tr '\\\\0' x"])
        #expect(result.status == 0)
        #expect(result.stdout.count == 300_000)
    }

    @Test func manyRunsInARowAllComplete() async throws {
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        for i in 0..<20 {
            let result = try await runner.run(["-c", "echo \(i)"])
            #expect(String(decoding: result.stdout, as: UTF8.self) == "\(i)\n")
        }
    }

    @Test func aMissingExecutableIsNotInstalled() async throws {
        let runner = ProcessRunner(executable: "/nonexistent/swe", environment: env)
        let result = try await runner.run(["--json"])
        // /usr/bin/env reports the missing program with exit 127.
        #expect(result.status == 127)
    }
}

@Suite struct ProcessRunnerOrderingTests {
    let env = ["PATH": "/usr/bin:/bin"]

    @Test func manySmallWritesArriveCompleteAndInOrder() async throws {
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let script = "i=1; while [ $i -le 3000 ]; do echo line$i; echo err$i >&2; i=$((i+1)); done"
        let expectedOut = (1...3000).map { "line\($0)\n" }.joined()
        let expectedErr = (1...3000).map { "err\($0)\n" }.joined()
        for _ in 0..<5 {
            let result = try await runner.run(["-c", script])
            #expect(String(decoding: result.stdout, as: UTF8.self) == expectedOut)
            #expect(result.stderr == expectedErr)
        }
    }

    @Test func cancellingTheCallerStopsTheChild() async throws {
        let runner = ProcessRunner(executable: "/bin/sleep", environment: env)
        let started = Date()
        let task = Task { try await runner.run(["30"]) }
        try await Task.sleep(for: .milliseconds(300))
        task.cancel()
        _ = try? await task.value
        #expect(Date().timeIntervalSince(started) < 5)
    }
}

@Suite struct LoginShellTimeoutTests {
    @Test func aShellThatHangsFallsBackWithinTheTimeLimit() async throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let shell = dir.appending(path: "slow-shell")
        try "#!/bin/sh\nsleep 30\n".write(to: shell, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: shell.path)

        let started = Date()
        let env = await LoginShell.resolve(shell: shell.path, base: ["PATH": "/usr/bin:/bin"], timeout: .milliseconds(500))
        #expect(Date().timeIntervalSince(started) < 3)
        #expect(env["PATH"] == "/usr/bin:/bin")
    }

    @Test func aWorkingShellSuppliesItsPATH() async throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let shell = dir.appending(path: "fake-shell")
        try "#!/bin/sh\necho HOME=/x\necho PATH=/custom/bin:/usr/bin\n".write(to: shell, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: shell.path)

        let env = await LoginShell.resolve(shell: shell.path, base: ["PATH": "/usr/bin"], timeout: .seconds(5))
        #expect(env["PATH"] == "/custom/bin:/usr/bin")
    }

    @Test func sweOverridesAreDroppedOnTheFallbackPath() async {
        // The shell never answered, so base comes back — minus the
        // variables swe children must not inherit.
        let env = await LoginShell.resolve(
            shell: "/nonexistent/shell",
            base: ["PATH": "/usr/bin:/bin", "SWE_RATE_LIMITS_TTL": "inf", "HOME": "/tmp"],
            timeout: .seconds(1))
        #expect(env["SWE_RATE_LIMITS_TTL"] == nil)
        #expect(env["HOME"] == "/tmp")
        #expect(env["PATH"] == "/usr/bin:/bin")
    }

    @Test func sweOverridesAreDroppedWhenTheShellAnswers() async throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let shell = dir.appending(path: "fake-shell")
        try "#!/bin/sh\necho PATH=/fake/answered/bin\necho SWE_RATE_LIMITS_TTL=inf\n"
            .write(to: shell, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: shell.path)

        let env = await LoginShell.resolve(
            shell: shell.path,
            base: ["PATH": "/usr/bin:/bin", "SWE_RATE_LIMITS_TTL": "inf", "HOME": "/tmp"],
            timeout: .seconds(5))
        #expect(env["PATH"] == "/fake/answered/bin")
        #expect(env["SWE_RATE_LIMITS_TTL"] == nil)
        #expect(env["HOME"] == "/tmp")
    }
}
