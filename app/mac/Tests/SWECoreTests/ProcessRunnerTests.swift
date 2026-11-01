import Foundation
import Testing
@testable import SWECore

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
        let temp = try TempDir()
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let task = Task { try await runner.run(["-c", "touch '\(temp.path("up").path)'; exec sleep 30"]) }

        // Cancel only once the child is up, not after a guessed delay.
        let childStarted = await fileAppears("up", in: temp.url)
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
        let temp = try TempDir()
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        // The sleep outlasts the runner's 120 s default bound, so neither
        // its own exit nor the bound can stand in for a working cancel.
        let script = "(trap '' TERM; touch '\(temp.path("trapped").path)'; exec sleep 300) & "
            + writePid("$!", to: "child.pid", in: temp) + "; wait"
        let task = Task { try await runner.run(["-c", script]) }
        // Cancel only once the trap is in place and the pid is written.
        let trapped = await fileAppears("trapped", in: temp.url)
        let grandchild = await pid(in: "child.pid", of: temp)
        task.cancel()
        let cancelledAt = Date()
        try #require(trapped)
        let pid = try #require(grandchild)

        _ = try? await task.value
        // The SIGKILL pass comes two seconds after the cancel; the bound
        // only catches a call left waiting on the pipes.
        #expect(Date().timeIntervalSince(cancelledAt) < 60)
        #expect(await processEnds(pid))
    }

    @Test func aCancelBeforeLaunchNeverStartsTheChild() async throws {
        // The task cancels itself before calling run, so the cancel handler
        // fires before the launch every time, with no timing involved. A
        // launch that ignored it would start the child, which touches the
        // marker, and run would return its exit instead of throwing.
        let temp = try TempDir()
        let marker = temp.path("started")
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

    /// Runs the real wall-clock deadline. The killed child's exit used to
    /// race the timeout report, and about one run in a hundred answered
    /// with the SIGTERM status instead of throwing.
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
        let temp = try TempDir()
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let started = Date()
        await #expect(throws: SweError.timedOut) {
            try await runner.run(["-c", "sleep 30 & " + writePid("$!", to: "sleep.pid", in: temp) + "; exit 0"],
                                 input: nil, timeout: .milliseconds(500))
        }
        #expect(Date().timeIntervalSince(started) < 20)
        // sh had exited, so nothing swept the sleep: end it here rather
        // than leave it running for half a minute after the suite.
        if let pid = await pid(in: "sleep.pid", of: temp), isLive(pid) { kill(pid, SIGKILL) }
    }

    @Test func aTimeoutStopsTheWholeCommandTree() async throws {
        // The child reports the pid of a sleep it spawned, then waits on
        // it. Stopping only the direct child would leave the sleep
        // running — the rebuild case is `bash` under a timed-out
        // `build-app.sh` leaving `swift build` alive. The sleep ignores
        // SIGTERM, so only the tree walk's SIGKILL pass can end it (the
        // terminate already reaches the child's process group).
        let temp = try TempDir()
        var runner = ProcessRunner(executable: "/bin/sh", environment: env)
        // The deadline fires once the trap is in place and the pid is
        // written, however long a busy machine takes to get there.
        runner.deadlineTimer = .once(["trapped", "child.pid"], appearIn: temp)
        let script = "(trap '' TERM; touch '\(temp.path("trapped").path)'; exec sleep 60) & "
            + writePid("$!", to: "child.pid", in: temp) + "; wait"
        await #expect(throws: SweError.timedOut) {
            try await runner.run(["-c", script], input: nil, timeout: .seconds(1))
        }
        let grandchild = try #require(await pid(in: "child.pid", of: temp))
        #expect(await processEnds(grandchild))
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
        let temp = try TempDir()
        let shell = temp.path("slow-shell")
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

    @Test func aShellThatIgnoresSIGTERMIsKilledAtTheTimeout() async throws {
        // An interactive zsh ignores SIGTERM. A terminate alone left the
        // shell and its startup file's child running after the fallback.
        let temp = try TempDir()
        let shell = temp.path("stubborn-shell")
        try "#!/bin/sh\ntrap '' TERM\nsleep 30 &\n\(writePid("$$ $!", to: "pids", in: temp))\nwait\n"
            .write(to: shell, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: shell.path)

        // The deadline fires once the shell has started its child and
        // said so; on a wall clock a busy machine could fire it first,
        // and the pids were never written.
        let env = await LoginShell.resolve(shell: shell.path, base: ["PATH": "/usr/bin:/bin"],
                                           timeout: .seconds(1), deadline: .once(["pids"], appearIn: temp))
        #expect(env["PATH"] == "/usr/bin:/bin")
        let pids = ((try? String(contentsOf: temp.path("pids"), encoding: .utf8)) ?? "")
            .split(whereSeparator: \.isWhitespace).compactMap { pid_t($0) }
        try #require(pids.count == 2)
        // The shell and the child its startup file was waiting on.
        for pid in pids { #expect(await processEnds(pid)) }
    }

    @Test func aTimedOutShellsNestedCommandsAreKilledToo() async throws {
        // The shell waits on a helper that waits on its own command. Killing
        // the helper before listing its children let the grandchild
        // reparent to launchd and outlive the timeout.
        let temp = try TempDir()
        let shell = temp.path("nested-shell")
        let inner = "sleep 30 & " + writePid("\\$!", to: "grandchild.pid", in: temp) + "; wait"
        try "#!/bin/sh\ntrap '' TERM\n/bin/sh -c \"\(inner)\" &\nwait\n"
            .write(to: shell, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: shell.path)

        // The deadline fires once the grandchild exists. On a wall clock
        // a busy machine fired it before the helper had started its
        // command, so no pid was written and the test failed.
        _ = await LoginShell.resolve(shell: shell.path, base: ["PATH": "/usr/bin:/bin"],
                                     timeout: .seconds(1), deadline: .once(["grandchild.pid"], appearIn: temp))
        let pid = try #require(await pid(in: "grandchild.pid", of: temp))
        #expect(await processEnds(pid))
    }

    @Test func aWorkingShellSuppliesItsPATH() async throws {
        let temp = try TempDir()
        let shell = temp.path("fake-shell")
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
        let temp = try TempDir()
        let shell = temp.path("fake-shell")
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

/// A login shell that didn't answer in time must not leave the whole
/// launch on launchd's bare PATH, and must not stall every call either.
@Suite struct ShellEnvironmentCacheTests {
    final class Reads: @unchecked Sendable {
        private let lock = NSLock()
        private var answers: [[String: String]?]
        private(set) var count = 0
        init(_ answers: [[String: String]?]) { self.answers = answers }
        func next() -> [String: String]? {
            lock.withLock {
                count += 1
                return answers.isEmpty ? nil : answers.removeFirst()
            }
        }
        var calls: Int { lock.withLock { count } }
    }

    @Test func aFailedReadIsRetriedAndASuccessIsKept() async {
        let reads = Reads([nil, ["PATH": "/login/bin"]])
        let cache = ShellEnvironmentCache(retryAfter: .zero) {
            reads.next()
        } fallback: {
            ["PATH": "/usr/bin"]
        }
        // The first call waits for the first read, which failed.
        #expect(await cache.environment() == ["PATH": "/usr/bin"])
        #expect(reads.calls == 1)
        // The next call answers at once and starts a retry behind it.
        #expect(await cache.environment() == ["PATH": "/usr/bin"])
        await cache.settle()
        #expect(await cache.environment() == ["PATH": "/login/bin"])
        #expect(reads.calls == 2)
    }

    @Test func noRetryInsideTheWindow() async {
        let reads = Reads([nil, ["PATH": "/login/bin"]])
        let cache = ShellEnvironmentCache(retryAfter: .seconds(3600)) {
            reads.next()
        } fallback: {
            ["PATH": "/usr/bin"]
        }
        for _ in 0..<5 { _ = await cache.environment() }
        #expect(reads.calls == 1)
    }

    @Test func earlyCallersShareTheFirstRead() async {
        let reads = Reads([["PATH": "/login/bin"]])
        let cache = ShellEnvironmentCache(retryAfter: .zero) {
            try? await Task.sleep(for: .milliseconds(100))
            return reads.next()
        } fallback: {
            ["PATH": "/usr/bin"]
        }
        async let a = cache.environment()
        async let b = cache.environment()
        let results = await [a, b]
        #expect(results == [["PATH": "/login/bin"], ["PATH": "/login/bin"]])
        #expect(reads.calls == 1)
    }
}

/// A shell command that writes `value` to `name` in `temp` all at once
/// (a temp file renamed into place), so a reader that sees the file
/// never sees half a pid.
private func writePid(_ value: String, to name: String, in temp: TempDir) -> String {
    let path = temp.path(name).path
    return "echo \(value) > '\(path).tmp' && mv '\(path).tmp' '\(path)'"
}

/// The pid written by `writePid`, once the file appears; nil if it never
/// does or holds no pid.
private func pid(in name: String, of temp: TempDir) async -> pid_t? {
    guard await fileAppears(name, in: temp.url),
          let text = try? String(contentsOf: temp.path(name), encoding: .utf8),
          let pid = pid_t(text.trimmingCharacters(in: .whitespacesAndNewlines)), pid > 0
    else { return nil }
    return pid
}

extension DeadlineTimer {
    /// Fires once every file in `names` exists in `temp`, whatever the
    /// timeout says: the child writes them when the tree the test means
    /// to stop is in place. Fires anyway after `gateTimeout`, so a
    /// broken setup fails the test instead of hanging it.
    static func once(_ names: [String], appearIn temp: TempDir) -> DeadlineTimer {
        /// The work item crosses into the task; only `DispatchQueue`
        /// ever runs it.
        final class Fire: @unchecked Sendable {
            let item: DispatchWorkItem
            init(_ item: DispatchWorkItem) { self.item = item }
        }
        return DeadlineTimer { _, item in
            let fire = Fire(item)
            Task {
                for name in names { _ = await fileAppears(name, in: temp.url) }
                DispatchQueue.global().async(execute: fire.item)
            }
        }
    }
}
