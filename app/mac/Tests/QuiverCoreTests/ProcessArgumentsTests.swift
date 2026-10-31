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
        // argc promises four strings but the buffer ends first: stop there.
        let full = buffer(argc: 4, exec: "/bin/x", arguments: ["/bin/x", "a", "b"])
        #expect(ProcessArguments.parse(Array(full.dropLast(2))) == ["/bin/x", "a"])
        // A last string cut before its NUL still counts, up to the buffer end.
        #expect(ProcessArguments.parse(Array(full.dropLast(1))) == ["/bin/x", "a", "b"])
    }

    @Test func aNegativeOrZeroArgcReadsNoArguments() {
        // 0xFFFFFFFF reads as argc -1, which used to trap in `0..<min(argc, 256)`.
        #expect(ProcessArguments.parse([0xFF, 0xFF, 0xFF, 0xFF] + Array("/bin/x\0a\0".utf8)) == [])
        #expect(ProcessArguments.parse(buffer(argc: 0, exec: "/bin/x", arguments: ["/bin/x"])) == [])
    }

    @Test func parseRejectsATooSmallBuffer() {
        #expect(ProcessArguments.parse([1, 0]) == [])
        #expect(ProcessArguments.parse([]) == [])
    }
}
