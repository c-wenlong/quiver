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
