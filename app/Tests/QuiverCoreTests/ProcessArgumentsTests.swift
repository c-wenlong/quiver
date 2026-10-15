import Darwin
import Foundation
import Testing
@testable import QuiverCore

@Suite struct ProcessArgumentsTests {
    /// A KERN_PROCARGS2 buffer by hand: int32 argc, exec path, padding, then
    /// the argv strings.
    private func buffer(argc: Int32, exec: String, arguments: [String]) -> [UInt8] {
        var out = withUnsafeBytes(of: argc) { [UInt8]($0) }
        out += exec.utf8 + [0]
        out += [0, 0, 0]
        for argument in arguments { out += argument.utf8 + [0] }
        return out
    }

    @Test func parseReadsArgvPastTheExecPathAndPadding() {
        let bytes = buffer(
            argc: 3, exec: "/Applications/Claude.app/Contents/MacOS/Claude",
            arguments: ["/Applications/Claude.app/Contents/MacOS/Claude",
                        "--user-data-dir=/Users/me/.quiver/accounts/claude/claude-cortex/desktop",
                        "--foo"])
        #expect(ProcessArguments.parse(bytes) == [
            "/Applications/Claude.app/Contents/MacOS/Claude",
            "--user-data-dir=/Users/me/.quiver/accounts/claude/claude-cortex/desktop",
            "--foo",
        ])
    }

    @Test func parseStopsAtTheBufferEndAndAtArgc() {
        #expect(ProcessArguments.parse(buffer(argc: 1, exec: "/bin/x", arguments: ["/bin/x", "extra"])) == ["/bin/x"])
        let truncated = Array(buffer(argc: 4, exec: "/bin/x", arguments: ["/bin/x", "a", "b"]).dropLast(2))
        #expect(ProcessArguments.parse(truncated).count <= 4)
    }

    @Test func parseRejectsATooSmallBuffer() {
        #expect(ProcessArguments.parse([1, 0]) == [])
        #expect(ProcessArguments.parse([]) == [])
    }
}
