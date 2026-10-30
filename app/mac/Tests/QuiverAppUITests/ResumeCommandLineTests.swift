import Testing
@testable import QuiverApp

/// The session detail's resume command must paste into a shell as the
/// argv it shows.
@Suite @MainActor struct ResumeCommandLineTests {
    @Test func plainWordsStayBare() {
        #expect(SessionDetail.shellLine(["claude", "--resume", "0b1c-2d"]) == "claude --resume 0b1c-2d")
    }

    @Test func spacesQuotesAndShellCharactersAreQuoted() {
        #expect(SessionDetail.shellLine(["codex", "resume", "my session", "it's", "$(id)", ""])
                == #"codex resume 'my session' 'it'\''s' '$(id)' ''"#)
    }
}
