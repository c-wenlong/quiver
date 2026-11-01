import Foundation
import Testing
@testable import SWECore

@Suite struct UsageFormatTests {
    @Test func tokensAreCompact() {
        #expect(UsageFormat.tokens(0) == "0")
        #expect(UsageFormat.tokens(950) == "950")
        #expect(UsageFormat.tokens(12_340) == "12.3K")
        #expect(UsageFormat.tokens(4_200_000) == "4.2M")
        #expect(UsageFormat.tokens(11_600_000_000) == "11.6B")
        #expect(UsageFormat.tokens(1_000_000) == "1M")
        // The unit is picked after rounding, never "1000K".
        #expect(UsageFormat.tokens(999_949) == "999.9K")
        #expect(UsageFormat.tokens(999_950) == "1M")
        #expect(UsageFormat.tokens(999_950_000) == "1B")
        #expect(UsageFormat.tokens(999_999_999_999) == "1000B")
    }

    @Test func dollarsKeepCentsOnlyWhenSmall() {
        #expect(UsageFormat.dollars(0) == "$0")
        #expect(UsageFormat.dollars(0.004) == "<$0.01")
        #expect(UsageFormat.dollars(3.456) == "$3.46")
        #expect(UsageFormat.dollars(4687.2) == "$4,687")
    }

    @Test func harnessesHaveReadableNames() {
        #expect(UsageFormat.harness("claude") == "Claude Code")
        #expect(UsageFormat.harness("codex") == "Codex")
        #expect(UsageFormat.harness("opencode") == "OpenCode")
        #expect(UsageFormat.harness("devin") == "Devin")
        #expect(UsageFormat.harness("gemini") == "Gemini")
    }
}
