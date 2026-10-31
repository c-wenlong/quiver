import Foundation
import Testing
@testable import QuiverCore

/// The real runner's stdin path, against /bin/sh. MCP secret values and
/// GitHub comment bodies travel this way, so they must arrive whole, the
/// pipe must close after them, and they must never reach the child's argv
/// (which `ps` shows to every process on the Mac) or an error message.
@Suite struct ProcessRunnerStdinTests {
    let env = ["PATH": "/usr/bin:/bin"]
    /// A bound on runs that would otherwise wait on stdin forever: a
    /// runner that never closes the pipe fails the test instead of
    /// hanging it.
    let bound: Duration = .seconds(60)

    @Test func theInputArrivesOnStdinByteForByte() async throws {
        // Newlines, a NUL, a quote and non-ASCII text: nothing is
        // re-encoded or cut at a line or a zero byte.
        var input = Data("line one\nsecond 'line' \u{00e9}\u{1F511}\n".utf8)
        input.append(0)
        input.append(contentsOf: Data("after the nul\n".utf8))
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let result = try await runner.run(["-c", "cat"], input: input, timeout: bound)
        #expect(result.status == 0)
        #expect(result.stdout == input)
    }

    @Test func inputLargerThanThePipeBufferArrivesWhole() async throws {
        // Many times the 64 KB pipe buffer, so the write has to wait on
        // the child reading, from a queue that isn't the caller's.
        let input = Data((0..<1_000_000).map { UInt8(truncatingIfNeeded: $0 &* 31) })
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let result = try await runner.run(["-c", "cat"], input: input, timeout: bound)
        #expect(result.status == 0)
        #expect(result.stdout.count == input.count)
        #expect(result.stdout == input)
    }

    @Test func stdinClosesAfterAnEmptyInput() async throws {
        // An empty value still gets a pipe, closed at once, so a child
        // reading to EOF finishes rather than waiting on the app.
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let result = try await runner.run(["-c", "cat; echo done"], input: Data(), timeout: bound)
        #expect(result.status == 0)
        #expect(String(decoding: result.stdout, as: UTF8.self) == "done\n")
    }

    @Test func noInputMeansAnEmptyStdin() async throws {
        // Without input the child reads /dev/null, never the app's own
        // stdin, which would hang it or hand it the test runner's input.
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let result = try await runner.run(["-c", "wc -c | tr -d ' '"], input: nil, timeout: bound)
        #expect(String(decoding: result.stdout, as: UTF8.self) == "0\n")
    }

    @Test func theInputNeverReachesTheChildsArgv() async throws {
        // `ps` reads the argv the kernel holds for the child, which is
        // what any other process on the Mac can see.
        let secret = "sk-fixture-\(UUID().uuidString)"
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let script = "ps -ww -o command= -p $$; printf '%s\\n' \"$0\" \"$@\"; cat >/dev/null"
        let result = try await runner.run(["-c", script, "sh", "set", "NAME"],
                                          input: Data((secret + "\n").utf8), timeout: bound)
        let argv = String(decoding: result.stdout, as: UTF8.self)
        #expect(result.status == 0)
        // The listing is there, so the check below looked at something.
        #expect(argv.contains("NAME"))
        #expect(!argv.contains(secret))
    }

    @Test func aChildThatExitsWithoutReadingLeavesTheCallerStanding() async throws {
        // An older swe that lacks the command exits before reading. The
        // write then hits a closed pipe: without F_SETNOSIGPIPE that is a
        // SIGPIPE, which would take the whole app (here, the test run)
        // down. The run answers with the child's own status.
        let input = Data(count: 4_000_000)
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        for _ in 0..<3 {
            let result = try await runner.run(["-c", "echo nope >&2; exit 7"],
                                              input: input, timeout: bound)
            #expect(result.status == 7)
            #expect(result.stderr == "nope\n")
        }
    }

    @Test func aChildThatReadsPartOfTheInputThenExitsAnswers() async throws {
        // `head` stops reading after one line, so most of the write lands
        // on a pipe nobody reads any more.
        let input = Data(("first\n" + String(repeating: "x", count: 2_000_000)).utf8)
        let runner = ProcessRunner(executable: "/bin/sh", environment: env)
        let result = try await runner.run(["-c", "head -n 1"], input: input, timeout: bound)
        #expect(result.status == 0)
        #expect(String(decoding: result.stdout, as: UTF8.self) == "first\n")
    }
}

/// `SweClient` over the real runner and a stand-in `swe` script, for the
/// two callers that send a secret or private text: `mcp secret set` and
/// `gh issue comment`. The script records its argv (both as given and as
/// `ps` sees it) and its stdin, so the test checks the actual process.
@Suite struct SweStdinSecretsTests {
    /// A `swe` that records what it got into `$RECORD_DIR`, then answers
    /// with `reply` on stdout, `stderr` on stderr and exit `status`.
    private func fakeSwe(in temp: TempDir, reply: String, stderr: String = "",
                         status: Int32 = 0) throws -> ProcessRunner {
        let script = temp.path("swe")
        let body = """
        #!/bin/sh
        printf '%s\\n' "$@" > "$RECORD_DIR/argv"
        ps -ww -o command= -p $$ >> "$RECORD_DIR/argv"
        cat > "$RECORD_DIR/stdin"
        printf '%s' '\(reply)'
        printf '%s' '\(stderr)' >&2
        exit \(status)
        """
        try body.write(to: script, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: script.path)
        return ProcessRunner(executable: script.path,
                             environment: ["PATH": "/usr/bin:/bin", "RECORD_DIR": temp.url.path])
    }

    private func recorded(_ name: String, in temp: TempDir) throws -> Data {
        try Data(contentsOf: temp.path(name))
    }

    @Test func aSecretValueGoesOnStdinWithANewlineAndNowhereElse() async throws {
        let temp = try TempDir()
        let secret = "s3cr3t-\(UUID().uuidString)"
        let runner = try fakeSwe(in: temp, reply:
            #"{"schema":1,"data":{"name":"MY_TOKEN","path":"/x/.api_keys","created":true,"replaced":false}}"#)
        let result = try await SweClient(runner: runner).mcpSecretSet(name: "MY_TOKEN", value: secret)
        #expect(result.name == "MY_TOKEN")
        #expect(try recorded("stdin", in: temp) == Data((secret + "\n").utf8))
        let argv = String(decoding: try recorded("argv", in: temp), as: UTF8.self)
        #expect(argv.contains("MY_TOKEN"))
        #expect(!argv.contains(secret))
    }

    @Test func aRefusedSecretSetNamesTheRefusalButNotTheValue() async throws {
        let temp = try TempDir()
        let secret = "s3cr3t-\(UUID().uuidString)"
        let runner = try fakeSwe(in: temp, reply: "", stderr: "refused: bad name", status: 2)
        do {
            try await SweClient(runner: runner).mcpSecretSet(name: "bad name", value: secret)
            Issue.record("a refusal must throw")
        } catch let error as SweError {
            #expect(error == .commandFailed(status: 2, message: "refused: bad name"))
            #expect(!(error.errorDescription ?? "").contains(secret))
            #expect(!String(describing: error).contains(secret))
        }
        // The value still went in on stdin only.
        #expect(try recorded("stdin", in: temp) == Data((secret + "\n").utf8))
        #expect(!String(decoding: try recorded("argv", in: temp), as: UTF8.self).contains(secret))
    }

    @Test func aCommentBodyGoesOnStdinIntact() async throws {
        // A comment is multi-line markdown that may start with a dash or
        // hold shell metacharacters; on stdin none of that matters.
        let temp = try TempDir()
        let body = "--looks like a flag\n\n```sh\n$(rm -rf ~) `x` 'q' \"d\"\n```\n\u{2014} done \u{1F44D}"
        let runner = try fakeSwe(in: temp, reply:
            #"{"schema":1,"data":{"repo":"o/r","status":"ok","issue":7,"error":null}}"#)
        let reply = try await SweClient(runner: runner).ghIssueComment(repo: "o/r", number: 7, body: body)
        #expect(reply.status == "ok")
        #expect(try recorded("stdin", in: temp) == Data(body.utf8))
        let argv = String(decoding: try recorded("argv", in: temp), as: UTF8.self)
        #expect(argv.contains("--body-file=-"))
        #expect(!argv.contains("looks like a flag"))
        #expect(!argv.contains("rm -rf"))
    }
}
