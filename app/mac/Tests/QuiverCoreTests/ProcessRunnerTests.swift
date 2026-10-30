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
        // No wall-clock bound: a starved CI runner made any fixed bound
        // flaky. The child marks when it is running, then execs into the
        // sleep so the signal reaches it. A working cancel ends it with
        // SIGTERM; a broken one lets the sleep exit 0.
        let marker = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: marker) }
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let task = Task { try await runner.run(["-c", "touch '\(marker.path)'; exec sleep 30"]) }

        // Cancel only once the child is up, not after a guessed delay.
        let deadline = Date().addingTimeInterval(20)
        while !FileManager.default.fileExists(atPath: marker.path), Date() < deadline {
            try await Task.sleep(for: .milliseconds(50))
        }
        let childStarted = FileManager.default.fileExists(atPath: marker.path)
        // Cancel before the check, so a failed check leaves no child behind.
        task.cancel()
        try #require(childStarted)

        let result = try await task.value
        #expect(result.status == SIGTERM)
    }

    @Test func cancellingTheCallerStopsTheWholeCommandTree() async throws {
        // The child reports the pid of a sleep it spawned, then waits on
        // it. The sleep ignores SIGTERM, as a build tool mid-write may:
        // a terminate alone (which reaches the child's process group)
        // left it running, holding the pipes, after its page went away.
        let dir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let pidFile = dir.appending(path: "child.pid")
        let ready = dir.appending(path: "ready")
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        // The sleep outlasts the runner's 120 s default bound, so neither
        // its own exit nor the bound can stand in for a working cancel.
        let script = "(trap '' TERM; touch '\(ready.path)'; exec sleep 300) & "
            + "echo $! > '\(pidFile.path)'; wait"
        let task = Task { try await runner.run(["-c", script]) }
        // Cancel only once the trap is in place and the pid is written.
        let startDeadline = Date().addingTimeInterval(20)
        while !FileManager.default.fileExists(atPath: ready.path)
                || (try? String(contentsOf: pidFile, encoding: .utf8))?.hasSuffix("\n") != true,
              Date() < startDeadline {
            try await Task.sleep(for: .milliseconds(25))
        }
        task.cancel()
        let cancelledAt = Date()
        let grandchild = pid_t(
            (try? String(contentsOf: pidFile, encoding: .utf8))?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? "")
        try #require(FileManager.default.fileExists(atPath: ready.path))
        try #require(grandchild != nil && grandchild! > 0)

        _ = try? await task.value
        // The SIGKILL pass comes two seconds after the cancel; the bound
        // only catches a call left waiting on the pipes.
        #expect(Date().timeIntervalSince(cancelledAt) < 60)
        // Dead already, but reaped on launchd's schedule: poll the pid.
        let reapDeadline = Date().addingTimeInterval(10)
        while kill(grandchild!, 0) == 0, Date() < reapDeadline {
            try await Task.sleep(for: .milliseconds(50))
        }
        #expect(kill(grandchild!, 0) != 0)
    }

    @Test func aCancelBeforeLaunchNeverStartsTheChild() async throws {
        // The task cancels itself before calling run, so the cancel handler
        // fires before the launch every time, with no timing involved. A
        // launch that ignored it would start the child, which touches the
        // marker, and run would return its exit instead of throwing.
        let marker = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: marker) }
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let task = Task {
            withUnsafeCurrentTask { $0?.cancel() }
            return try await runner.run(["-c", "touch '\(marker.path)'; exec sleep 30"])
        }

        await #expect(throws: CancellationError.self) { try await task.value }
        #expect(!FileManager.default.fileExists(atPath: marker.path))
    }
}

@Suite struct ProcessRunnerTimeoutTests {
    let env = ["PATH": "/usr/bin:/bin"]

    @Test func aRunThatNeverFinishesIsStoppedAtTheDeadline() async throws {
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let started = Date()
        await #expect(throws: SweError.timedOut) {
            try await runner.run(["-c", "exec sleep 30"], input: nil,
                                 timeout: .milliseconds(500))
        }
        // The bound catches a hang, not latency: it sits under the
        // child's 30s sleep, so a broken timeout still fails while a
        // starved CI runner cannot trip it.
        #expect(Date().timeIntervalSince(started) < 20)
    }

    @Test func aGrandchildHoldingThePipeStillTimesOut() async throws {
        // sh exits at once, but the sleep it spawned keeps the stdout
        // pipe open, so the readers would wait on EOF forever. The
        // deadline resumes the call anyway.
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let started = Date()
        await #expect(throws: SweError.timedOut) {
            try await runner.run(["-c", "sleep 30 & exit 0"], input: nil,
                                 timeout: .milliseconds(500))
        }
        #expect(Date().timeIntervalSince(started) < 20)
    }

    @Test func aTimeoutStopsTheWholeCommandTree() async throws {
        // The child reports the pid of a sleep it spawned, then waits on
        // it. Stopping only the direct child would leave the sleep
        // running — the rebuild case is `bash` under a timed-out
        // `build-app.sh` leaving `swift build` alive. The sleep ignores
        // SIGTERM, so only the tree walk's SIGKILL pass can end it (the
        // terminate already reaches the child's process group).
        let dir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let pidFile = dir.appending(path: "child.pid")
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let task = Task {
            try await runner.run(
                ["-c", "(trap '' TERM; exec sleep 60) & echo $! > '\(pidFile.path)'; wait"],
                // Two seconds, so the trap is in place well before the
                // deadline's SIGTERM even on a busy runner.
                input: nil, timeout: .seconds(2))
        }
        // Poll rather than guess when the grandchild exists.
        let startDeadline = Date().addingTimeInterval(10)
        while !FileManager.default.fileExists(atPath: pidFile.path), Date() < startDeadline {
            try await Task.sleep(for: .milliseconds(25))
        }
        let grandchild = pid_t(
            try String(contentsOf: pidFile, encoding: .utf8)
                .trimmingCharacters(in: .whitespacesAndNewlines))
        try #require(grandchild != nil && grandchild! > 0)

        await #expect(throws: SweError.timedOut) { try await task.value }

        // The orphan is dead already, but launchd reaps it on its own
        // schedule — poll the pid instead of sleeping a guessed delay.
        let reapDeadline = Date().addingTimeInterval(10)
        while kill(grandchild!, 0) == 0, Date() < reapDeadline {
            try await Task.sleep(for: .milliseconds(50))
        }
        #expect(kill(grandchild!, 0) != 0)
    }

    @Test func aQuickRunInsideItsDeadlineAnswers() async throws {
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let result = try await runner.run(["-c", "echo hi"], input: nil,
                                          timeout: .seconds(10))
        #expect(String(decoding: result.stdout, as: UTF8.self) == "hi\n")
    }

    @Test func aTimeoutRidesThroughTheProtocolOverload() async throws {
        // The two-argument requirement forwards to the bounded one, so
        // every existing call site is covered without naming a deadline.
        let runner = ProcessRunner(executable: "/bin/sh", environment: env,
                                   defaultTimeout: .milliseconds(500))
        let started = Date()
        await #expect(throws: SweError.timedOut) {
            try await runner.run(["-c", "exec sleep 30"], input: nil)
        }
        #expect(Date().timeIntervalSince(started) < 20)
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
        // The bound catches a hang, not latency: it sits under the
        // shell's 30s sleep, so a broken timeout still fails while a
        // starved CI runner cannot trip it.
        #expect(Date().timeIntervalSince(started) < 20)
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
