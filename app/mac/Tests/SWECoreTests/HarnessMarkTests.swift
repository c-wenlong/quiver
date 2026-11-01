import Testing
@testable import SWECore

@Suite struct HarnessMarkTests {
    @Test func claudeIsTheCLIAndClaudeDesktopIsTheApp() {
        // swe's mcp map: "claude" reads ~/.claude.json (Claude Code, the CLI);
        // "claude-desktop" reads Claude Desktop's own config.
        #expect(HarnessMark("claude") == HarnessMark(icon: "claude", word: "CLI", name: "Claude Code"))
        #expect(HarnessMark("claude-desktop") == HarnessMark(icon: "claude", word: "Desktop", name: "Claude Desktop"))
    }

    @Test func aHarnessWithItsOwnLogoNeedsNoWord() {
        #expect(HarnessMark("codex") == HarnessMark(icon: "codex", word: nil, name: "Codex"))
        #expect(HarnessMark("droid") == HarnessMark(icon: "droid", word: nil, name: "Factory Droid"))
        #expect(HarnessMark("qwen-code") == HarnessMark(icon: "qwen-code", word: nil, name: "Qwen Code"))
    }

    @Test func aSecondAccountShowsItsLogoAndSuffix() {
        #expect(HarnessMark("codex-acc2") == HarnessMark(icon: "codex", word: "acc2", name: "Codex (acc2)"))
        #expect(HarnessMark("claude-work") == HarnessMark(icon: "claude", word: "work", name: "Claude Code (work)"))
    }

    @Test func aHarnessWithNoLogoKeepsItsName() {
        #expect(HarnessMark("lmstudio") == HarnessMark(icon: nil, word: "LM Studio", name: "LM Studio"))
        #expect(HarnessMark("zed-agent") == HarnessMark(icon: nil, word: "Zed-agent", name: "Zed-agent"))
    }
}
