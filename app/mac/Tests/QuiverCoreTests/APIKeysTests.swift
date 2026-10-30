import Foundation
import Testing
@testable import QuiverCore

/// Synthetic keys only: nothing here is a real credential.
let sampleKeysFile = """
# Keys for every harness
# ANTHROPIC
export ANTHROPIC_API_KEY="sk-ant-test-0000000000000000abcd"

# OPENAI
export OPENAI_API_KEY='sk-proj-test-111111111111wxyz'
export OPENAI_ORG=org-test
alias ll='ls -l'

# GEMINI
EXPORT GEMINI_API_KEY="AIza-test"
"""

/// A retired key left commented out above its replacement, as editors do.
/// Synthetic values only.
let commentedKeysFile = """
# MiMo (Xiaomi)
# export MIMO_API_KEY="sk-old-000000000000000000000000dead"
export MIMO_API_KEY="sk-new-000000000000000000000000beef"
"""

@Suite struct APIKeysCommentedOutTests {
    @Test func aCommentedOutExportIsNotAHeading() {
        let file = APIKeysFile(text: commentedKeysFile)
        #expect(file.entries.map(\.provider) == ["MiMo (Xiaomi)"])
        #expect(file.entries.map(\.name) == ["MIMO_API_KEY"])
    }

    @Test func aCommentedOutBareAssignmentIsNotAHeading() {
        let file = APIKeysFile(text: "# DeepSeek\n#DEEPSEEK_API_KEY=sk-old-1111\nexport DEEPSEEK_API_KEY=sk-new-2222\n")
        #expect(file.entries.map(\.provider) == ["DeepSeek"])
    }

    @Test func aCommentedOutKeyWithNoHeadingLeavesTheProviderEmpty() {
        let file = APIKeysFile(text: "# export OLD_KEY=sk-old-3333\nexport NEW_KEY=sk-new-4444\n")
        #expect(file.entries.map(\.provider) == [""])
    }

    @Test func anUppercaseCommentedOutExportIsNotAHeading() {
        let file = APIKeysFile(text: "# EXPORT MIMO_API_KEY=\"sk-old-5555\"\nexport MIMO_API_KEY=sk-new-6666\n")
        #expect(file.entries.map(\.provider) == [""])
        #expect(file.problems.map(\.reason) == ["a commented-out key still holds a value; delete the line"])
        #expect(!file.problems.contains { $0.text.contains("sk-old") })
    }

    @Test func aCommentWithAnAssignmentAnywhereIsNotAHeading() {
        let file = APIKeysFile(text: "# old: MIMO_API_KEY=sk-old-7777\nexport MIMO_API_KEY=sk-new-8888\n")
        #expect(file.entries.map(\.provider) == [""])
    }

    @Test func noProviderEverHoldsAnAssignment() {
        let texts = [commentedKeysFile, sampleKeysFile,
                     "#  export   A_KEY = x\n# B_KEY=y # note\n#export C_KEY='z'\n# Export E_KEY=v\n# was F_KEY = u\nexport D_KEY=w\n"]
        for text in texts {
            for entry in APIKeysFile(text: text).entries {
                #expect(!entry.provider.contains("="), "provider \(entry.provider) holds an assignment")
            }
        }
    }

    @Test func aCommentedOutKeyIsReportedWithItsValueHidden() {
        let file = APIKeysFile(text: commentedKeysFile)
        #expect(file.problems == [.init(line: 2, text: "# export MIMO_API_KEY=…",
                                        reason: "a commented-out key still holds a value; delete the line")])
        #expect(!file.problems.contains { $0.text.contains("sk-old") })
    }

    @Test func removingTheLastKeyKeepsACommentedOutKeyLine() throws {
        var file = APIKeysFile(text: commentedKeysFile)
        try file.remove(name: "MIMO_API_KEY")
        #expect(file.text.contains("# export MIMO_API_KEY="))
        #expect(!file.text.contains("sk-new"))
    }
}

/// A synthetic key: SECRET marks the part that must never be shown.
let fakeKey = "sk-ant-api03-SECRET0000000000000000abcd"

@Suite struct APIKeysRedactionTests {
    @Test func aLineWithoutEqualsShowsOnlyItsName() {
        #expect(APIKeysFile.redacted("export ANTHROPIC_API_KEY \(fakeKey)") == "export ANTHROPIC_API_KEY…")
        #expect(APIKeysFile.redacted("EXPORT FOO \(fakeKey)") == "EXPORT FOO…")
        #expect(APIKeysFile.redacted("  export FOO\t\(fakeKey)") == "  export FOO…")
    }

    @Test func nothingBeforeALaterEqualsIsKept() {
        #expect(APIKeysFile.redacted("export FOO sk-a=b") == "export FOO…")
        #expect(APIKeysFile.redacted("export FOO+=\"\(fakeKey)\"") == "export FOO…")
    }

    @Test func aNameThatIsItselfAKeyIsHidden() {
        #expect(APIKeysFile.redacted("export sk_live_SECRET0000000000000") == "export …")
        #expect(APIKeysFile.redacted("export AKIASECRET7EXAMPLE00") == "export …")
        #expect(APIKeysFile.redacted("# \(fakeKey)") == "# sk…")
    }

    @Test func aKeyWithNoDigitsIsStillHidden() {
        #expect(APIKeysFile.redacted("export ghp_SECRETabcdefghijklmnopqrstuvwxyz") == "export …")
        #expect(APIKeysFile.redacted("export sk_abcdefghijklmnopqrstuvwxyz") == "export …")
        let file = APIKeysFile(text: "# Anthropic\n# ghp_SECRETabcdefghijklmnopqrstuvwxyz\nexport ghp_SECRETabcdefghijklmnop\n")
        #expect(file.problems.map(\.text) == ["# …", "export …"])
        #expect(file.entries.isEmpty)
    }

    @Test func anUpperCaseNameWithUnderscoresIsKept() {
        #expect(APIKeysFile.redacted("export CLOUDFLARE_R2_ACCESS_KEY_ID x") == "export CLOUDFLARE_R2_ACCESS_KEY_ID…")
        #expect(APIKeysFile.redacted("export ANTHROPIC_API_KEY_BACKUP x") == "export ANTHROPIC_API_KEY_BACKUP…")
    }

    @Test func aLineWithNoNameKeepsOnlyItsKeyword() {
        #expect(APIKeysFile.redacted("export \"\(fakeKey)\"") == "export …")
        #expect(APIKeysFile.redacted(fakeKey) == "sk…")
    }

    @Test func problemLinesWithoutEqualsNeverShowTheValue() {
        let file = APIKeysFile(text: "export ANTHROPIC_API_KEY \(fakeKey)\nEXPORT FOO \(fakeKey)\nexport BAR sk-a=b\n")
        #expect(file.problems.map(\.text) == ["export ANTHROPIC_API_KEY…", "EXPORT FOO…", "export BAR…"])
        #expect(!file.problems.contains { $0.text.contains("SECRET") || $0.text.contains("sk-a") })
    }
}

@Suite struct APIKeysHeadingTests {
    @Test func realHeadingsStillNameTheirKeys() {
        for label in ["Anthropic", "OPENAI", "MiMo (Xiaomi)", "Kimi (Moonshot AI)", "ZAI (01.ai)", "xAi (Grok)",
                      "Keys for every harness", "Google / Vertex, AI & Co."] {
            #expect(APIKeysFile.heading("# \(label)") == label)
            #expect(APIKeysFile.heading("  ##  \(label)  ") == label)
        }
    }

    /// Commented-out keys that aren't `NAME=value` still hold a value.
    static let commentedKeyShapes = [
        "# export FOO \(fakeKey)",
        "# FOO: \(fakeKey)",
        "# \(fakeKey)",
        "# export FOO+=\"\(fakeKey)\"",
        "# FOO[0]=\(fakeKey)",
        "# old key was \(fakeKey), rotated",
        "# export FOO sk-short",
    ]

    @Test(arguments: commentedKeyShapes)
    func aCommentedOutKeyIsNeverAHeading(shape: String) {
        #expect(APIKeysFile.heading(shape) == nil)
        let file = APIKeysFile(text: "# Anthropic\n\(shape)\nexport ANTHROPIC_API_KEY=x\n")
        #expect(file.entries.map(\.provider) == ["Anthropic"])
        #expect(file.problems.map(\.line) == [2])
        #expect(file.problems.map(\.reason) == ["a commented-out key still holds a value; delete the line"])
        #expect(!file.problems.contains { $0.text.contains("SECRET") || $0.text.contains("short") })
    }

    @Test func commentsThatDoNotLookLikeALabelAreNotHeadings() {
        let words = Array(repeating: "Abcdefghij", count: 5).joined(separator: " ")
        #expect(APIKeysFile.heading("# \(words) Abcde") != nil)
        for comment in ["# \(words) Abcdef", "# ----------", "# 2024", "# OPENAI_COMPAT", "#!/bin/zsh",
                        "# see https://console.example.com/keys", "# Export keys below",
                        "# Abcdefghijklmnopqrst"] {
            #expect(APIKeysFile.heading(comment) == nil, "\(comment) became a heading")
        }
    }

    @Test func anOrdinaryCommentIsNotAProblem() {
        let file = APIKeysFile(text: """
            # Keys for every harness
            # see https://console.example.com/settings/keys
            # export these before running the tests
            # OPENAI_COMPAT
            # Anthropic
            export ANTHROPIC_API_KEY=x
            """)
        #expect(file.problems.isEmpty)
    }

    @Test func noProviderEverHoldsAKey() {
        let text = Self.commentedKeyShapes.joined(separator: "\n") + "\nexport A=1\n"
        for entry in APIKeysFile(text: text).entries {
            #expect(!entry.provider.contains("SECRET") && !entry.provider.contains("sk-"))
        }
    }

    @Test func aNewProviderHasToReadBackAsAHeading() throws {
        var file = APIKeysFile(text: "")
        for provider in ["Google: Gemini", "OPENAI_COMPAT", fakeKey, "export FOO bar",
                         String(repeating: "Ab ", count: 30)] {
            #expect(throws: APIKeysError.invalidProvider) { try file.set(name: "A", value: "1", provider: provider) }
        }
        try file.set(name: "KIMI_API_KEY", value: "k", provider: "Kimi (Moonshot AI)")
        #expect(APIKeysFile(text: file.text).entries.map(\.provider) == ["Kimi (Moonshot AI)"])
    }
}

@Suite struct APIKeysFileTests {
    @Test func entriesAreGroupedUnderTheCommentAboveThem() throws {
        let file = APIKeysFile(text: sampleKeysFile)
        #expect(file.entries.map(\.name) == ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_ORG"])
        #expect(file.entries.map(\.provider) == ["ANTHROPIC", "OPENAI", "OPENAI"])
        #expect(file.entries.map(\.value) == ["sk-ant-test-0000000000000000abcd", "sk-proj-test-111111111111wxyz", "org-test"])
    }

    @Test func linesItCannotUseAreReportedNotDropped() {
        let file = APIKeysFile(text: sampleKeysFile)
        #expect(file.problems == [.init(line: 11, text: "EXPORT GEMINI_API_KEY=…",
                                        reason: "zsh only accepts lowercase export, so this line sets nothing")])
        #expect(file.text == sampleKeysFile)
    }

    @Test func editingAValueRewritesOnlyThatLine() throws {
        var file = APIKeysFile(text: sampleKeysFile)
        try file.set(name: "OPENAI_ORG", value: "org-new", provider: "OPENAI")
        let lines = file.text.components(separatedBy: "\n")
        let before = sampleKeysFile.components(separatedBy: "\n")
        #expect(lines.count == before.count)
        #expect(lines[6] == "export OPENAI_ORG=\"org-new\"")
        #expect(Array(lines[..<6]) == Array(before[..<6]))
        #expect(Array(lines[7...]) == Array(before[7...]))
    }

    @Test func aNewKeyJoinsItsProviderOrStartsOne() throws {
        var file = APIKeysFile(text: sampleKeysFile)
        try file.set(name: "OPENAI_PROJECT", value: "p", provider: "OPENAI")
        try file.set(name: "MISTRAL_API_KEY", value: "m", provider: "MISTRAL")
        let lines = file.text.components(separatedBy: "\n")
        #expect(lines[7] == "export OPENAI_PROJECT=\"p\"")
        #expect(lines.suffix(3) == ["", "# MISTRAL", "export MISTRAL_API_KEY=\"m\""])
        #expect(APIKeysFile(text: file.text).entries.map(\.name).contains("MISTRAL_API_KEY"))
    }

    @Test func valuesAreQuotedSoTheShellReadsThemBack() throws {
        var file = APIKeysFile(text: "")
        try file.set(name: "TRICKY", value: #"a"b$c`d\e"#, provider: "TEST")
        #expect(file.text.contains(#"export TRICKY="a\"b\$c\`d\\e""#))
        #expect(APIKeysFile(text: file.text).entries.first?.value == #"a"b$c`d\e"#)
    }

    @Test func badNamesAndMultilineValuesAreRefused() {
        var file = APIKeysFile(text: "")
        #expect(throws: APIKeysError.invalidName("1BAD")) { try file.set(name: "1BAD", value: "x", provider: "P") }
        #expect(throws: APIKeysError.multilineValue) { try file.set(name: "OK", value: "a\nb", provider: "P") }
    }

    @Test func warningLinesNeverShowTheirValue() {
        let file = APIKeysFile(text: "  EXPORT API_KEY=\"secret-value\"\nexport B=\"x\"; rm -rf /tmp/nothing\n")
        #expect(file.problems.map(\.text) == ["  EXPORT API_KEY=…", "export B=…"])
        #expect(!file.problems.contains { $0.text.contains("secret-value") })
    }

    @Test func extraShellTextAfterAValueMakesTheLineReadOnly() {
        let file = APIKeysFile(text: """
            export A="one"; export B="two"
            export C="prefix""suffix"
            export D="fine"   # a trailing comment is fine
            export E=bare more
            """)
        #expect(file.entries.map(\.name) == ["D"])
        #expect(file.entries.first?.value == "fine")
        #expect(file.problems.map(\.line) == [1, 2, 4])
    }

    @Test func aKeyExportedTwiceIsFlaggedAndNotEdited() {
        var file = APIKeysFile(text: "export A=\"old\"\nexport A=\"current\"\n")
        #expect(file.problems.map(\.line) == [1])
        #expect(file.problems.first?.reason == "A is exported again on line 2, which wins; remove one of them")
        // The row is the one the shell ends up with.
        #expect(file.keys.map(\.value) == ["current"])
        #expect(throws: APIKeysError.duplicate("A")) { try file.set(name: "A", value: "new", provider: "P") }
        #expect(throws: APIKeysError.duplicate("A")) { try file.remove(name: "A") }
    }

    @Test func aProviderCannotAddShellLines() {
        var file = APIKeysFile(text: "")
        #expect(throws: APIKeysError.invalidProvider) {
            try file.set(name: "A", value: "1", provider: "Vendor\nexport OTHER=unexpected")
        }
    }

    @Test func removingTheLastKeyOfAProviderRemovesItsHeading() throws {
        var file = APIKeysFile(text: sampleKeysFile)
        try file.remove(name: "ANTHROPIC_API_KEY")
        #expect(!file.text.contains("ANTHROPIC"))
        #expect(file.text.hasPrefix("# Keys for every harness\n# OPENAI\n") || file.text.hasPrefix("# Keys for every harness\n\n# OPENAI\n"))
        try file.remove(name: "OPENAI_ORG")
        #expect(file.text.contains("# OPENAI"))
    }

    /// Two groups for the rename and move tests.
    static let moveFile = """
    # Notes above the groups
    # FIRST
    export ALPHA_KEY="a1"
    export BETA_KEY="b2"

    # SECOND
    export GAMMA_KEY='g3'
    """

    @Test func renamingAKeyKeepsItsValueAndPlace() throws {
        var file = APIKeysFile(text: Self.moveFile)
        try file.rename(name: "BETA_KEY", to: "DELTA_KEY")
        #expect(file.text == Self.moveFile.replacingOccurrences(
            of: "export BETA_KEY=\"b2\"", with: "export DELTA_KEY=\"b2\""))
        let renamed = file.text
        try file.rename(name: "DELTA_KEY", to: "DELTA_KEY") // a no-op, not an error
        #expect(file.text == renamed)
    }

    @Test func aRenameNeedsAFreeValidName() throws {
        var file = APIKeysFile(text: Self.moveFile)
        #expect(throws: APIKeysError.nameTaken("ALPHA_KEY")) {
            try file.rename(name: "BETA_KEY", to: "ALPHA_KEY")
        }
        #expect(throws: APIKeysError.invalidName("1BAD")) {
            try file.rename(name: "BETA_KEY", to: "1BAD")
        }
        #expect(throws: APIKeysError.notFound("NO_SUCH_KEY")) {
            try file.rename(name: "NO_SUCH_KEY", to: "NEW_KEY")
        }
        #expect(file.text == Self.moveFile)
    }

    @Test func aRenameTouchesOnlyTheName() throws {
        var file = APIKeysFile(text: "export OLD=\"a\\$b\" # note\n")
        try file.rename(name: "OLD", to: "NEW")
        #expect(file.text == "export NEW=\"a\\$b\" # note\n")
        var indented = APIKeysFile(text: "  export   OLD='v'  # kept\n")
        try indented.rename(name: "OLD", to: "NEW")
        #expect(indented.text == "  export   NEW='v'  # kept\n")
    }

    /// A double-quoted `$` or backtick expands in the shell, so the text
    /// isn't the key's value: the line is left alone, never rewritten with
    /// the reference escaped into a literal.
    @Test func aValueTheShellExpandsIsNotEditable() throws {
        for line in [#"export OLD="$TOKEN""#, #"export OLD="pre-${B}""#,
                     #"export OLD="$(cat f)""#, "export OLD=\"`cat f`\""] {
            var file = APIKeysFile(text: line + "\n")
            #expect(file.entries.isEmpty, "\(line)")
            #expect(file.problems.map(\.reason) == ["Quiver can't edit this line safely, so it leaves it alone"])
            #expect(throws: APIKeysError.notFound("OLD")) { try file.rename(name: "OLD", to: "NEW") }
            #expect(file.text == line + "\n")
        }
        #expect(APIKeysFile.unquote(#""a\$b\`c""#) == "a$b`c")
        #expect(APIKeysFile.unquote("'$OTHER'") == "$OTHER")
    }

    /// A name an unsafe line also sets can't be edited into a second
    /// assignment, and a later unsafe line is the one that wins.
    @Test func anUnsafeLineStillCountsAsAnAssignment() throws {
        let later = "export A=\"old\"\nexport A=\"$OTHER\"\n"
        var file = APIKeysFile(text: later)
        #expect(file.keys.isEmpty)
        #expect(file.problems.contains { $0.line == 1 && $0.reason.contains("exported again on line 2") })
        #expect(throws: APIKeysError.unsafeLine("A")) { try file.set(name: "A", value: "new", provider: "") }
        #expect(throws: APIKeysError.unsafeLine("A")) { try file.remove(name: "A") }
        #expect(throws: APIKeysError.unsafeLine("A")) { try file.rename(name: "A", to: "B") }
        #expect(throws: APIKeysError.unsafeLine("A")) { try file.move(name: "A", toProvider: "P") }
        #expect(file.text == later)
        // Only the unsafe line: a new one would be a second assignment.
        var only = APIKeysFile(text: "export A=\"$OTHER\"\nexport B=\"b\"\n")
        #expect(throws: APIKeysError.unsafeLine("A")) { try only.set(name: "A", value: "new", provider: "") }
        #expect(throws: APIKeysError.nameTaken("A")) { try only.rename(name: "B", to: "A") }
        // An editable line after the unsafe one wins, and sets in place.
        var earlier = APIKeysFile(text: "export A=\"$OTHER\"\nexport A=\"old\"\n")
        #expect(earlier.keys.map(\.value) == ["old"])
        try earlier.set(name: "A", value: "new", provider: "")
        #expect(earlier.text == "export A=\"$OTHER\"\nexport A=\"new\"\n")
    }

    @Test func aMoveLandsAtTheEndOfTheTargetGroup() throws {
        var file = APIKeysFile(text: Self.moveFile)
        try file.move(name: "ALPHA_KEY", toProvider: "SECOND")
        #expect(file.text == """
        # Notes above the groups
        # FIRST
        export BETA_KEY="b2"

        # SECOND
        export GAMMA_KEY='g3'
        export ALPHA_KEY="a1"
        """)
    }

    @Test func aMoveToANewProviderStartsAGroupAfterTheOldOne() throws {
        var file = APIKeysFile(text: Self.moveFile)
        try file.move(name: "ALPHA_KEY", toProvider: "MISTRAL")
        #expect(file.text == """
        # Notes above the groups
        # FIRST
        export BETA_KEY="b2"

        # MISTRAL
        export ALPHA_KEY="a1"

        # SECOND
        export GAMMA_KEY='g3'
        """)
    }

    @Test func aMoveToANewProviderRenewsASingleKeyGroupInPlace() throws {
        var file = APIKeysFile(text: """
        # FIRST
        export ALPHA_KEY="a1"

        # SECOND
        export GAMMA_KEY='g3'
        """)
        try file.move(name: "ALPHA_KEY", toProvider: "MISTRAL")
        #expect(file.text == """
        # MISTRAL
        export ALPHA_KEY="a1"

        # SECOND
        export GAMMA_KEY='g3'
        """)
    }

    @Test func aMoveIntoAnEmptyGroupFillsIt() throws {
        var file = APIKeysFile(text: """
        # FIRST
        export ALPHA_KEY="a1"

        # SECOND
        """)
        try file.move(name: "ALPHA_KEY", toProvider: "SECOND")
        #expect(file.text == """
        # SECOND
        export ALPHA_KEY="a1"
        """)
    }

    @Test func anUngroupedKeyMovedToANewProviderKeepsTheBlocksEnd() throws {
        var file = APIKeysFile(text: """
        # set in ~/.zshenv
        export LOOSE_ONE="l1"
        export LOOSE_TWO="l2"

        # FIRST
        export ALPHA_KEY="a1"
        """)
        try file.move(name: "LOOSE_TWO", toProvider: "MISTRAL")
        #expect(file.text == """
        # set in ~/.zshenv
        export LOOSE_ONE="l1"

        # MISTRAL
        export LOOSE_TWO="l2"

        # FIRST
        export ALPHA_KEY="a1"
        """)
    }

    @Test func theOnlyUngroupedKeyMovedToANewProviderTakesTheBlocksPlace() throws {
        var file = APIKeysFile(text: """
        # set in ~/.zshenv
        export LOOSE_KEY="l1"

        # FIRST
        export ALPHA_KEY="a1"
        """)
        try file.move(name: "LOOSE_KEY", toProvider: "MISTRAL")
        #expect(file.text == """
        # set in ~/.zshenv

        # MISTRAL
        export LOOSE_KEY="l1"

        # FIRST
        export ALPHA_KEY="a1"
        """)
    }

    @Test func aRefusedMoveLeavesTheFileAlone() throws {
        var file = APIKeysFile(text: Self.moveFile)
        #expect(throws: APIKeysError.invalidProvider) {
            try file.move(name: "ALPHA_KEY", toProvider: "export A=1")
        }
        #expect(file.text == Self.moveFile)
    }

    @Test func aMoveOutOfTheLastKeyRemovesItsHeading() throws {
        var file = APIKeysFile(text: """
        # Notes above the groups
        # ONE
        export ONLY_KEY="o1"

        # TWO
        export OTHER_KEY="o2"
        """)
        try file.move(name: "ONLY_KEY", toProvider: "TWO")
        #expect(file.text == """
        # Notes above the groups

        # TWO
        export OTHER_KEY="o2"
        export ONLY_KEY="o1"
        """)
    }

    @Test func aMoveToItsOwnProviderDoesNothing() throws {
        var file = APIKeysFile(text: Self.moveFile)
        try file.move(name: "ALPHA_KEY", toProvider: "FIRST")
        #expect(file.text == Self.moveFile)
    }

    @Test func renamingAProviderRewritesOnlyItsHeading() throws {
        var file = APIKeysFile(text: Self.moveFile)
        try file.renameProvider(from: "FIRST", to: "RENAMED")
        #expect(file.text == Self.moveFile.replacingOccurrences(of: "# FIRST", with: "# RENAMED"))
        #expect(file.entries.map(\.provider) == ["RENAMED", "RENAMED", "SECOND"])
    }

    @Test func aProviderRenameNeedsAFreeValidLabel() throws {
        var file = APIKeysFile(text: Self.moveFile)
        #expect(throws: APIKeysError.providerTaken("SECOND")) {
            try file.renameProvider(from: "FIRST", to: "SECOND")
        }
        #expect(throws: APIKeysError.invalidProvider) {
            try file.renameProvider(from: "FIRST", to: "export A=1")
        }
        #expect(throws: APIKeysError.notFound("ABSENT")) {
            try file.renameProvider(from: "ABSENT", to: "NEW")
        }
        #expect(file.text == Self.moveFile)
    }
}

@Suite struct APIKeyMaskTests {
    @Test func aKeyShowsItsPrefixAndLastFour() {
        #expect(APIKeyMask.mask("sk-ant-api03-abcdefghijklmnop3f9a") == "sk-ant-••••••••3f9a")
        #expect(APIKeyMask.mask("sk-proj-0123456789012345a21c") == "sk-proj-••••••••a21c")
    }

    @Test func aShortValueIsFullyHidden() {
        #expect(APIKeyMask.mask("org-test") == "••••••••")
        #expect(APIKeyMask.mask("") == "")
    }

    @Test func anUnprefixedKeyShowsOnlyItsEnd() {
        #expect(APIKeyMask.mask("AIzaSyD0123456789abcdefghijklmnopq") == "••••••••nopq")
    }
}

@Suite struct APIKeysStoreTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    func temp() throws -> URL {
        let dir = scratch.url.appending(path: "keys-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    @Test func savingKeepsTheFilePrivateAndBacksUpTheOldCopy() throws {
        let dir = try temp()
        let url = dir.appending(path: ".api_keys")
        try "export A=\"1\"\n".write(to: url, atomically: true, encoding: .utf8)
        let store = APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups"))
        var (file, stamp) = try store.load()
        try file.set(name: "A", value: "2", provider: "P")
        try store.save(file, over: stamp)
        #expect(try String(contentsOf: url, encoding: .utf8) == "export A=\"2\"\n")
        let mode = try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? Int
        #expect(mode == 0o600)
        let backup = dir.appending(path: "backups/api_keys.bak")
        #expect(try String(contentsOf: backup, encoding: .utf8) == "export A=\"1\"\n")
        #expect(try FileManager.default.attributesOfItem(atPath: backup.path)[.posixPermissions] as? Int == 0o600)
    }

    @Test func anExistingBackupFolderIsMadePrivate() throws {
        let dir = try temp()
        let backups = dir.appending(path: "backups")
        try FileManager.default.createDirectory(at: backups, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o755])
        let url = dir.appending(path: ".api_keys")
        try "export A=\"1\"\n".write(to: url, atomically: true, encoding: .utf8)
        let store = APIKeysStore(url: url, backupDirectory: backups)
        var (file, stamp) = try store.load()
        try file.set(name: "A", value: "2", provider: "P")
        try store.save(file, over: stamp)
        #expect(try FileManager.default.attributesOfItem(atPath: backups.path)[.posixPermissions] as? Int == 0o700)
    }

    @Test func aFileChangedSinceLoadingIsNotOverwritten() throws {
        let dir = try temp()
        let url = dir.appending(path: ".api_keys")
        try "export A=\"1\"\n".write(to: url, atomically: true, encoding: .utf8)
        let store = APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups"))
        var (file, stamp) = try store.load()
        try "export A=\"edited in vim\"\n".write(to: url, atomically: true, encoding: .utf8)
        try file.set(name: "A", value: "2", provider: "P")
        #expect(throws: APIKeysError.changedOnDisk) { try store.save(file, over: stamp) }
        #expect(try String(contentsOf: url, encoding: .utf8) == "export A=\"edited in vim\"\n")
    }

    @Test func aSymlinkedFileIsWrittenThroughNotReplaced() throws {
        let dir = try temp()
        let real = dir.appending(path: "dotfiles/api_keys")
        try FileManager.default.createDirectory(at: real.deletingLastPathComponent(), withIntermediateDirectories: true)
        try "export A=\"1\"\n".write(to: real, atomically: true, encoding: .utf8)
        let link = dir.appending(path: ".api_keys")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: real)
        let store = APIKeysStore(url: link, backupDirectory: dir.appending(path: "backups"))
        var (file, stamp) = try store.load()
        try file.set(name: "A", value: "2", provider: "P")
        try store.save(file, over: stamp)
        #expect(try FileManager.default.destinationOfSymbolicLink(atPath: link.path) == real.path)
        #expect(try String(contentsOf: real, encoding: .utf8) == "export A=\"2\"\n")
    }

    @Test func theDefaultFileSitsUnderQuiverSecrets() throws {
        let home = try temp()
        let current = home.appending(path: ".quiver/secrets/providers/.api_keys")
        #expect(APIKeysStore.defaultURL(home: home) == current)
        // An unmigrated machine keeps editing the file it already has.
        let legacy = home.appending(path: ".api_keys")
        try "export A=\"1\"\n".write(to: legacy, atomically: true, encoding: .utf8)
        #expect(APIKeysStore.defaultURL(home: home) == legacy)
        try FileManager.default.createDirectory(at: current.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "export A=\"1\"\n".write(to: current, atomically: true, encoding: .utf8)
        #expect(APIKeysStore.defaultURL(home: home) == current)
    }

    @Test func aLegacyDirectoryIsNotTheStore() throws {
        // ~/.api_keys as a directory of per-provider files is a CLI-only
        // layout; the app reads the file, not the directory.
        let home = try temp()
        let current = home.appending(path: ".quiver/secrets/providers/.api_keys")
        let legacy = home.appending(path: ".api_keys")
        try FileManager.default.createDirectory(at: legacy, withIntermediateDirectories: true)
        #expect(APIKeysStore.defaultURL(home: home) == current)
    }

    @Test func aFirstSaveCreatesItsFolderPrivatelyAndSealsIt() throws {
        let home = try temp()
        let url = APIKeysStore.defaultURL(home: home)
        let secrets = APIKeysStore.secretsFolder(home: home)
        let store = APIKeysStore(url: url, backupDirectory: home.appending(path: "backups"), sealedFolder: secrets)
        var (file, stamp) = try store.load()
        try file.set(name: "A", value: "1", provider: "P")
        try store.save(file, over: stamp)
        for folder in [secrets, url.deletingLastPathComponent()] {
            let mode = try FileManager.default.attributesOfItem(atPath: folder.path)[.posixPermissions]
            #expect((mode as? NSNumber)?.intValue == 0o700)
        }
        // An old hub's root .gitignore may not cover secrets/; this one does.
        #expect(try String(contentsOf: secrets.appending(path: ".gitignore"), encoding: .utf8) == APIKeysStore.sealBody)
    }

    @Test func aFileOutsideTheSealedFolderLeavesItAlone() throws {
        let home = try temp()
        let url = home.appending(path: "dotfiles/api_keys")
        let secrets = APIKeysStore.secretsFolder(home: home)
        let store = APIKeysStore(url: url, backupDirectory: home.appending(path: "backups"), sealedFolder: secrets)
        var (file, stamp) = try store.load()
        try file.set(name: "A", value: "1", provider: "P")
        try store.save(file, over: stamp)
        #expect(!FileManager.default.fileExists(atPath: secrets.path))
    }

    @Test func aFileThatIsNotUTF8IsNotRewritten() throws {
        let dir = try temp()
        let url = dir.appending(path: ".api_keys")
        try Data([0x23, 0x20, 0xff, 0xfe, 0x0a]).write(to: url)
        let store = APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups"))
        #expect(throws: APIKeysError.unreadable("the file isn't valid UTF-8, so Quiver won't rewrite it")) { try store.load() }
    }

    @Test func anEditDuringTheSaveIsNotOverwritten() throws {
        let dir = try temp()
        let url = dir.appending(path: ".api_keys")
        try "export A=\"1\"\n".write(to: url, atomically: true, encoding: .utf8)
        var store = APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups"))
        // An editor saves after the first check, while Quiver prepares its files.
        store.beforeReplace = { try? "export A=\"vim\"\n".write(to: url, atomically: true, encoding: .utf8) }
        var (file, stamp) = try store.load()
        try file.set(name: "A", value: "2", provider: "P")
        #expect(throws: APIKeysError.changedOnDisk) { try store.save(file, over: stamp) }
        #expect(try String(contentsOf: url, encoding: .utf8) == "export A=\"vim\"\n")
        let leftovers = try FileManager.default.contentsOfDirectory(atPath: dir.path).filter { $0.contains("quiver-") }
        #expect(leftovers.isEmpty)
    }

    @Test func aMissingFileLoadsEmptyAndIsCreatedPrivate() throws {
        let dir = try temp()
        let url = dir.appending(path: ".api_keys")
        let store = APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups"))
        var (file, stamp) = try store.load()
        #expect(file.entries.isEmpty)
        try file.set(name: "A", value: "1", provider: "P")
        try store.save(file, over: stamp)
        #expect(try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? Int == 0o600)
    }
}

@Suite struct ShellEnvironmentTests {
    @Test func envOutputBecomesADictionary() {
        let env = LoginShell.parse(envOutput: "PATH=/usr/bin\nA=1=2\nEMPTY=\n")
        #expect(env == ["PATH": "/usr/bin", "A": "1=2", "EMPTY": ""])
    }
}

/// A shell that answers only when released. Reads park in arrival
/// order so overlapping requests can be let through one at a time.
actor GatedShell: ShellEnvironmentReading {
    private var gates: [CheckedContinuation<Void, Never>] = []
    private var nextID = 0
    private var parkers: [Int: CheckedContinuation<Bool, Never>] = [:]
    /// What a read reports once released.
    private var reply: [String: String]? = ["OPENAI_ORG": "org-test"]

    /// What the next released read reports.
    func reply(_ environment: [String: String]?) { reply = environment }

    func freshEnvironment() async -> [String: String]? {
        let parked = parkers
        parkers = [:]
        for parker in parked.values { parker.resume(returning: true) }
        await withCheckedContinuation { gates.append($0) }
        return reply
    }

    /// True once a read is parked, false after `gateTimeout`. Checking
    /// the park and parking the caller happen in one actor step, so
    /// the park can never be missed and the caller stranded until the
    /// timeout.
    func waitForPark() async -> Bool {
        await withCheckedContinuation { cont in
            if !gates.isEmpty {
                cont.resume(returning: true)
                return
            }
            nextID += 1
            let id = nextID
            parkers[id] = cont
            Task { await self.expire(id) }
        }
    }

    /// The timeout resumes only its own waiter, and only if a read has
    /// not already parked.
    private func expire(_ id: Int) async {
        try? await Task.sleep(for: gateTimeout)
        parkers.removeValue(forKey: id)?.resume(returning: false)
    }

    /// Lets every parked read through.
    func release() {
        for gate in gates { gate.resume() }
        gates = []
    }

    /// Lets only the longest-parked read through; the rest stay waiting.
    func releaseNext() {
        if !gates.isEmpty { gates.removeFirst().resume() }
    }
}

/// A shell that reports a fixed environment.
struct FakeShell: ShellEnvironmentReading {
    var environment: [String: String]?
    func freshEnvironment() async -> [String: String]? { environment }
}

@MainActor
@Suite struct APIKeysSourcingTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    /// A model against a temp home: keys file and backup dir inside it,
    /// shell name and environment fixed.
    private func model(shell: String, environment: [String: String] = [:]) throws
        -> (APIKeysModel, URL, URL) {
        let home = scratch.url
            .appending(path: "keysource-\(UUID().uuidString)")
        let keys = home.appending(path: ".quiver/secrets/providers/.api_keys")
        try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "export A_KEY=v1\n".write(to: keys, atomically: true, encoding: .utf8)
        let store = APIKeysStore(url: keys, backupDirectory: home.appending(path: "backups"))
        let model = APIKeysModel(store: store, shell: FakeShell(environment: [:]),
                                 checkCache: APIKeyCheckStore(url: home.appending(path: "checks.json")),
                                 defaults: TestDefaults("keysource"),
                                 shellName: shell, home: home, environment: environment)
        return (model, home, home.appending(path: ".zshrc"))
    }

    @Test func anRcWithoutTheLineIsNotSourced() async throws {
        let (model, _, rc) = try model(shell: "/bin/zsh")
        await model.load()
        #expect(model.sourcing == .notSourced(file: rc))
    }

    @Test func addSourceLineMakesItSourced() async throws {
        let (model, home, rc) = try model(shell: "/bin/zsh")
        await model.load()
        let result = try await model.addSourceLine()
        #expect(model.sourcing == .sourced)
        #expect(result.file == rc)
        #expect(result.backup == nil)  // the rc did not exist, so nothing was backed up
        #expect(try String(contentsOf: rc, encoding: .utf8).contains(".api_keys"))
        _ = home
    }

    @Test func anUnsupportedShellReportsItsName() async throws {
        let (model, _, _) = try model(shell: "/bin/fish")
        await model.load()
        #expect(model.sourcing == .unsupportedShell("/bin/fish"))
    }

    @Test func zdotdirWinsForTheRcPath() async throws {
        let home = scratch.url
            .appending(path: "keysource-\(UUID().uuidString)")
        let keys = home.appending(path: ".quiver/secrets/providers/.api_keys")
        try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "export A_KEY=v1\n".write(to: keys, atomically: true, encoding: .utf8)
        let model = APIKeysModel(
            store: APIKeysStore(url: keys, backupDirectory: home.appending(path: "backups")),
            // ZDOTDIR in the login shell's environment wins over the
            // process's, as it will on a Finder-launched app.
            shell: FakeShell(environment: ["ZDOTDIR": home.appending(path: "dots").path]),
            checkCache: APIKeyCheckStore(url: home.appending(path: "checks.json")),
            defaults: TestDefaults("keysource"),
            shellName: "/bin/zsh", home: home, environment: [:])
        await model.load()
        #expect(model.sourcing == .notSourced(file: home.appending(path: "dots/.zshrc")))
        // The write lands in the same ZDOTDIR file the prompt showed.
        _ = try await model.addSourceLine()
        #expect(model.sourcing == .sourced)
        #expect(FileManager.default.fileExists(atPath: home.appending(path: "dots/.zshrc").path))
    }

    /// A save mid-load drops the load's read, so the sourcing check it
    /// runs afterwards sees no shell environment at all; when the
    /// save's own read lands with ZDOTDIR in it, the check reruns
    /// against the file that environment actually names.
    @Test func sourcingFollowsTheLandingReadsEnvironment() async throws {
        let home = scratch.url
            .appending(path: "keysource-\(UUID().uuidString)")
        let keys = home.appending(path: ".quiver/secrets/providers/.api_keys")
        try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "export A_KEY=v1\n".write(to: keys, atomically: true, encoding: .utf8)
        // ZDOTDIR's .zshrc sources the file; the plain ~/.zshrc doesn't.
        let dots = home.appending(path: "dots")
        try FileManager.default.createDirectory(at: dots, withIntermediateDirectories: true)
        try (ShellStartup.sourceLine(for: keys, home: home) + "\n")
            .write(to: dots.appending(path: ".zshrc"), atomically: true, encoding: .utf8)
        try "export OTHER=1\n".write(to: home.appending(path: ".zshrc"),
                                    atomically: true, encoding: .utf8)
        let shell = GatedShell()
        await shell.reply(["A_KEY": "v1", "ZDOTDIR": dots.path])
        let model = APIKeysModel(
            store: APIKeysStore(url: keys, backupDirectory: home.appending(path: "backups")),
            shell: shell,
            checkCache: APIKeyCheckStore(url: home.appending(path: "checks.json")),
            defaults: TestDefaults("keysource"),
            shellName: "/bin/zsh", home: home, environment: [:])
        let load = Task { await model.load() }
        #expect(await shell.waitForPark())

        try await model.set(name: "B_KEY", value: "v2", provider: "")
        await shell.releaseNext()
        await load.value
        // The dropped read leaves no shell env, so the check ran on the
        // wrong file; the save's own read puts it right when it lands.
        #expect(model.sourcing == .notSourced(file: home.appending(path: ".zshrc")))
        #expect(await shell.waitForPark())
        await shell.reply(["A_KEY": "v1", "B_KEY": "v2", "ZDOTDIR": dots.path])
        await shell.release()
        await model.shellReread?.value
        #expect(model.sourcing == .sourced)
    }
}

@MainActor
@Suite struct APIKeysModelTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    func model(text: String, shell: [String: String]? = [:]) throws -> (APIKeysModel, URL) {
        let dir = scratch.url.appending(path: "keysmodel-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appending(path: ".api_keys")
        try text.write(to: url, atomically: true, encoding: .utf8)
        let store = APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups"))
        return (APIKeysModel.isolated(store: store, shell: FakeShell(environment: shell),
                             checkCache: APIKeyCheckStore(url: dir.appending(path: "checks.json")),
                             home: dir), url)
    }

    @Test func rowsCarryTheirShellStatus() async throws {
        let (model, _) = try model(text: sampleKeysFile, shell: [
            "ANTHROPIC_API_KEY": "sk-ant-test-0000000000000000abcd",
            "OPENAI_API_KEY": "something else",
        ])
        await model.load()
        #expect(model.rows.map(\.status) == [.loaded, .overridden, .notLoaded])
        #expect(model.providers == ["ANTHROPIC", "OPENAI"])
        #expect(model.problems.count == 1)
    }

    @Test func rowsShowBeforeTheShellAnswers() async throws {
        let dir = scratch.url.appending(path: "keysgate-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appending(path: ".api_keys")
        try sampleKeysFile.write(to: url, atomically: true, encoding: .utf8)
        let shell = GatedShell()
        let model = APIKeysModel.isolated(store: APIKeysStore(url: url, backupDirectory: dir), shell: shell,
                                 checkCache: APIKeyCheckStore(url: dir.appending(path: "checks.json")),
                                 home: dir)
        let load = Task { await model.load() }
        #expect(await shell.waitForPark())
        #expect(model.rows.count == 3)
        #expect(model.rows.allSatisfy { $0.status == .checking })
        await shell.release()
        await load.value
        #expect(model.rows.map(\.status) == [.notLoaded, .notLoaded, .loaded])
    }

    /// A save changes the file a new terminal sources, so the shell is
    /// read again: the row shows the stale verdict first, then settles
    /// on what a new terminal would now get.
    @Test func aSaveRereadsTheShell() async throws {
        let dir = scratch.url.appending(path: "keysreread-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appending(path: ".api_keys")
        try "export A_KEY=v1\n".write(to: url, atomically: true, encoding: .utf8)
        let shell = GatedShell()
        await shell.reply(["A_KEY": "v1"])
        let model = APIKeysModel.isolated(store: APIKeysStore(url: url, backupDirectory: dir), shell: shell,
                                 checkCache: APIKeyCheckStore(url: dir.appending(path: "checks.json")),
                                 home: dir)
        let load = Task { await model.load() }
        #expect(await shell.waitForPark())
        await shell.release()
        await load.value
        #expect(model.rows.first?.status == .loaded)

        try await model.set(name: "B_KEY", value: "v2", provider: "")
        // The stale read still stands while the reread is parked.
        #expect(model.rows.last?.status == .notLoaded)
        #expect(await shell.waitForPark())
        await shell.reply(["A_KEY": "v1", "B_KEY": "v2"])
        await shell.release()
        await model.shellReread?.value
        #expect(model.rows.map(\.status) == [.loaded, .loaded])
    }

    /// A save while load() is still waiting on the shell gets a read of
    /// its own: the load's answer predates the write and is dropped, so
    /// the saved key doesn't stay Not loaded on the stale answer.
    @Test func aSaveDuringLoadGetsItsOwnShellRead() async throws {
        let dir = scratch.url.appending(path: "keysoverlap-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appending(path: ".api_keys")
        try "export A_KEY=v1\n".write(to: url, atomically: true, encoding: .utf8)
        let shell = GatedShell()
        await shell.reply(["A_KEY": "v1"])
        let model = APIKeysModel.isolated(store: APIKeysStore(url: url, backupDirectory: dir), shell: shell,
                                 checkCache: APIKeyCheckStore(url: dir.appending(path: "checks.json")),
                                 home: dir)
        let load = Task { await model.load() }
        #expect(await shell.waitForPark())

        // The write lands while the load's read is still parked, so
        // that read can only describe the file before the save.
        try await model.set(name: "B_KEY", value: "v2", provider: "")
        await shell.releaseNext()
        await load.value
        #expect(model.rows.last?.status == .checking)

        // The save's own read is the one that settles the rows.
        #expect(await shell.waitForPark())
        await shell.reply(["A_KEY": "v1", "B_KEY": "v2"])
        await shell.release()
        await model.shellReread?.value
        #expect(model.rows.map(\.status) == [.loaded, .loaded])
    }

    @Test func anUnreadableShellLeavesTheStatusUnknown() async throws {
        let (model, _) = try model(text: sampleKeysFile, shell: nil)
        await model.load()
        #expect(model.rows.allSatisfy { $0.status == .unknown })
    }

    @Test func searchMatchesProviderAndName() async throws {
        let (model, _) = try model(text: sampleKeysFile)
        await model.load()
        model.search = "org"
        #expect(model.visible.map(\.name) == ["OPENAI_ORG"])
        model.search = "anthropic"
        #expect(model.visible.map(\.name) == ["ANTHROPIC_API_KEY"])
    }

    @Test func savingWritesTheFileAndReloads() async throws {
        let (model, url) = try model(text: sampleKeysFile)
        await model.load()
        try await model.set(name: "MISTRAL_API_KEY", value: "m-1", provider: "MISTRAL")
        #expect(try String(contentsOf: url, encoding: .utf8).contains("export MISTRAL_API_KEY=\"m-1\""))
        #expect(model.rows.map(\.name).contains("MISTRAL_API_KEY"))
        try await model.remove(name: "MISTRAL_API_KEY")
        #expect(!model.rows.map(\.name).contains("MISTRAL_API_KEY"))
    }

    @Test func anEditOverAnOutsideChangeIsRefused() async throws {
        let (model, url) = try model(text: sampleKeysFile)
        await model.load()
        try "export X=\"1\"\n".write(to: url, atomically: true, encoding: .utf8)
        await #expect(throws: APIKeysError.changedOnDisk) {
            try await model.set(name: "OPENAI_ORG", value: "o", provider: "OPENAI")
        }
    }

    @Test func updateRenamesMovesAndWritesInOneSave() async throws {
        let (model, url) = try model(text: """
            # FIRST
            export OLD_KEY="v1"
            export STAYS_KEY="s1"

            # SECOND
            export OTHER_KEY="o1"
            """)
        await model.load()
        try await model.update(name: "OLD_KEY", newName: "NEW_KEY", provider: "SECOND", value: "v2")
        #expect(try String(contentsOf: url, encoding: .utf8) == """
            # FIRST
            export STAYS_KEY="s1"

            # SECOND
            export OTHER_KEY="o1"
            export NEW_KEY="v2"
            """)
        let row = model.rows.first { $0.name == "NEW_KEY" }
        #expect(row?.provider == "SECOND")
        #expect(row?.value == "v2")
    }

    @Test func anUngroupedKeyStaysUngroupedThroughAnUpdate() async throws {
        let (model, url) = try model(text: """
            export LOOSE_KEY="v1"

            # FIRST
            export GROUPED_KEY="g1"
            """)
        await model.load()
        try #require(model.rows.first { $0.name == "LOOSE_KEY" }?.provider == "")
        try await model.update(name: "LOOSE_KEY", newName: "LOOSE_KEY", provider: "", value: "v2")
        #expect(try String(contentsOf: url, encoding: .utf8) == """
            export LOOSE_KEY="v2"

            # FIRST
            export GROUPED_KEY="g1"
            """)
    }

    @Test func aRenamedKeyKeepsItsCheckResult() async throws {
        let dir = scratch.url.appending(path: "keyscheck-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appending(path: ".api_keys")
        try "# FIRST\nexport OLD_KEY=\"v1\"\n".write(to: url, atomically: true, encoding: .utf8)
        let defaults = TestDefaults("keyscheck")
        let salt = APIKeyCheckStore.salt(from: defaults)
        let cache = APIKeyCheckStore(url: dir.appending(path: "checks.json"))
        let check = APIKeyCheck(result: .noCheck("no probe for this key"),
                                checkedAt: Date(), value: "v1", salt: salt)
        cache.save(["OLD_KEY": check], for: url, currentNames: ["OLD_KEY"])
        let model = APIKeysModel.isolated(store: APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups")),
                                 shell: FakeShell(environment: [:]), checkCache: cache, defaults: defaults, home: dir)
        await model.load()
        #expect(model.checks["OLD_KEY"] == check)
        try await model.update(name: "OLD_KEY", newName: "NEW_KEY", provider: "FIRST", value: "v1")
        #expect(model.checks["OLD_KEY"] == nil)
        #expect(model.checks["NEW_KEY"] == check)
        #expect(cache.load(for: url)["NEW_KEY"] == check)
        #expect(cache.load(for: url)["OLD_KEY"] == nil)
    }

    /// A model whose checks go to `transport`, so a verdict is chosen.
    private func model(text: String, transport: FakeTransport) throws -> (APIKeysModel, URL) {
        let dir = scratch.url.appending(path: "keystest-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appending(path: ".api_keys")
        try text.write(to: url, atomically: true, encoding: .utf8)
        return (APIKeysModel.isolated(store: APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups")),
                             shell: FakeShell(environment: [:]),
                             checker: APIKeyChecker(transport: transport),
                             checkCache: APIKeyCheckStore(url: dir.appending(path: "checks.json")),
                             home: dir), url)
    }

    @Test func aTestReturnsTheVerdictAndPersistsNothing() async throws {
        let transport = FakeTransport()
        transport.reply(401, body: Data(#"{"error":{"message":"bad key"}}"#.utf8))
        let (model, url) = try model(text: "", transport: transport)
        await model.load()
        let result = await model.test(name: "OPENAI_API_KEY", value: "sk-fake-TEST-9999")
        guard case .rejected(let status, _) = result else {
            Issue.record("expected rejected, got \(result)")
            return
        }
        #expect(status == 401)
        #expect(model.checks.isEmpty)
        #expect(try String(contentsOf: url, encoding: .utf8) == "")
    }

    @Test func aTestWithoutAProbeIsNoCheck() async throws {
        let transport = FakeTransport()
        let (model, _) = try model(text: "", transport: transport)
        await model.load()
        let result = await model.test(name: "SWEBENCH_API_KEY", value: "v")
        #expect(result == .noCheck("Quiver doesn't know how to check this key."))
        #expect(transport.requests.isEmpty)
    }

    @Test func savingWithAVerdictKeepsItCurrentForTheValue() async throws {
        let transport = FakeTransport()
        let (model, _) = try model(text: "", transport: transport)
        await model.load()
        try await model.set(name: "OPENAI_API_KEY", value: "sk-saved-TEST-1", provider: "OPENAI",
                            tested: .works)
        let row = try #require(model.rows.first { $0.name == "OPENAI_API_KEY" })
        #expect(model.check(for: row)?.result == .works)
        // A different value must not inherit the verdict.
        try await model.set(name: "OPENAI_API_KEY", value: "sk-saved-TEST-2", provider: "OPENAI")
        let changed = try #require(model.rows.first { $0.name == "OPENAI_API_KEY" })
        #expect(model.check(for: changed) == nil)
    }

    @Test func anUpdateWithARenameStoresTheVerdictUnderTheNewName() async throws {
        let transport = FakeTransport()
        let (model, _) = try model(text: "export OLD_KEY=\"v1\"\n", transport: transport)
        await model.load()
        try await model.update(name: "OLD_KEY", newName: "OPENAI_API_KEY",
                               provider: "OPENAI", value: "sk-new-TEST-1", tested: .works)
        #expect(model.checks["OLD_KEY"] == nil)
        let row = try #require(model.rows.first { $0.name == "OPENAI_API_KEY" })
        #expect(model.check(for: row)?.result == .works)
    }
}

@Suite struct APIKeyProviderLogoTests {
    @Test func headingsMatchOnWordsIgnoringCase() {
        #expect(APIKeyProviderLogo.logoID(for: "ANTHROPIC") == "claude")
        #expect(APIKeyProviderLogo.logoID(for: "Claude") == "claude")
        #expect(APIKeyProviderLogo.logoID(for: "OpenAI") == "openai")
        #expect(APIKeyProviderLogo.logoID(for: "Moonshot / Kimi") == "kimi")
        #expect(APIKeyProviderLogo.logoID(for: "Google AI Studio") == "gemini")
        #expect(APIKeyProviderLogo.logoID(for: "GEMINI") == "gemini")
        #expect(APIKeyProviderLogo.logoID(for: "xAI") == "grok")
        #expect(APIKeyProviderLogo.logoID(for: "x.ai") == "grok")
        #expect(APIKeyProviderLogo.logoID(for: "Grok") == "grok")
        #expect(APIKeyProviderLogo.logoID(for: "Mistral") == "mistral-vibe")
        #expect(APIKeyProviderLogo.logoID(for: "Qwen") == "qwen-code")
        #expect(APIKeyProviderLogo.logoID(for: "Alibaba Cloud") == "qwen-code")
    }

    /// The headings in a real keys file, as the app shows them.
    @Test func theUsersHeadingsAllResolve() {
        let headings: [(String, String?)] = [
            ("OpenAI", "openai"),
            ("Anthropic", "claude"),
            ("Google Gemini", "gemini"),
            ("DeepSeek", "deepseek"),
            ("ZAI (01.ai)", "zai"),
            ("MiniMax", "minimax"),
            ("Kimi (Moonshot AI)", "kimi"),
            ("Qwen (Alibaba)", "qwen-code"),
            ("MiMo (Xiaomi)", "xiaomimimo"),
            ("xAI (Grok)", "grok"),
            ("StepFun", "stepfun"),
            ("Groq", "groq"),
            ("Upstage", "upstage"),
            ("Cerebras", "cerebras"),
            ("Mistral", "mistral-vibe"),
            ("Routing.Run", "routing-run"),
            ("Opencode Zen", "opencode"),
            ("OpenRouter", "openrouter"),
            ("Together AI", "together"),
            ("Fireworks AI", "fireworks"),
            ("Vercel Gateway", "vercel"),
            ("Nebius Token Factory", "nebius"),
            ("Featherless AI", "featherless"),
            ("NVIDIA", "nvidia"),
        ]
        for (heading, id) in headings {
            #expect(APIKeyProviderLogo.logoID(for: heading) == id, Comment(rawValue: heading))
        }
    }

    /// A name only inside another word is no match, and other providers get no logo.
    @Test func otherProvidersHaveNoLogo() {
        #expect(APIKeyProviderLogo.logoID(for: "Keys for every harness") == nil)
        #expect(APIKeyProviderLogo.logoID(for: "Stripe") == nil)
        #expect(APIKeyProviderLogo.logoID(for: "") == nil)
    }
}

/// The custom-provider path: several variables under one heading, a
/// `{base}/models` probe for keys whose BASE_URL sits beside them, and
/// the picker's model fetch.
@MainActor
@Suite struct APIKeysCustomProviderTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    private func model(text: String, transport: FakeTransport) throws -> (APIKeysModel, URL) {
        let dir = scratch.url.appending(path: "keyscustom-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appending(path: ".api_keys")
        try text.write(to: url, atomically: true, encoding: .utf8)
        return (APIKeysModel.isolated(store: APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups")),
                             shell: FakeShell(environment: [:]),
                             checker: APIKeyChecker(transport: transport),
                             checkCache: APIKeyCheckStore(url: dir.appending(path: "checks.json")),
                             home: dir), url)
    }

    @Test func setAllWritesEveryVariableUnderOneHeading() async throws {
        let (model, url) = try model(text: "# OPENAI\nexport OPENAI_API_KEY=\"o\"\n",
                                     transport: FakeTransport())
        await model.load()
        try await model.setAll(CustomProvider.entries(
            prefix: "SOCLAAS", baseURL: "https://soclaas.test/v1", key: "clsk-x", model: "m1"),
            provider: "SoCLaaS", tested: .works, testedName: "SOCLAAS_API_KEY")
        let text = try String(contentsOf: url, encoding: .utf8)
        #expect(text == """
            # OPENAI
            export OPENAI_API_KEY="o"

            # SoCLaaS
            export SOCLAAS_BASE_URL="https://soclaas.test/v1"
            export SOCLAAS_API_KEY="clsk-x"
            export SOCLAAS_MODEL="m1"
            """ + "\n")
        let key = try #require(model.rows.first { $0.name == "SOCLAAS_API_KEY" })
        #expect(key.provider == "SoCLaaS")
        #expect(model.check(for: key)?.result == .works)
    }

    @Test func aCustomKeyChecksAgainstItsBaseURL() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: Data(#"{"data":[]}"#.utf8))
        let (model, _) = try model(text: """
            # SoCLaaS
            export SOCLAAS_BASE_URL="https://soclaas.test/v1"
            export SOCLAAS_API_KEY="clsk-x"
            """, transport: transport)
        await model.load()
        await model.check(name: "SOCLAAS_API_KEY")
        let request = try #require(transport.requests.first)
        #expect(request.url?.absoluteString == "https://soclaas.test/v1/models")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer clsk-x")
        #expect(model.checks["SOCLAAS_API_KEY"]?.result == .works)
    }

    @Test func aBaseURLOnItsOwnHasNoCheck() async throws {
        let transport = FakeTransport()
        let (model, _) = try model(text: """
            # SoCLaaS
            export SOCLAAS_BASE_URL="https://soclaas.test/v1"
            """, transport: transport)
        await model.load()
        await model.check(name: "SOCLAAS_BASE_URL")
        #expect(transport.requests.isEmpty)
        guard case .noCheck = model.checks["SOCLAAS_BASE_URL"]?.result else {
            Issue.record("expected noCheck, got \(String(describing: model.checks["SOCLAAS_BASE_URL"]))")
            return
        }
    }

    @Test func aSavedCustomKeyTestsAgainstItsBaseURL() async throws {
        let transport = FakeTransport()
        transport.reply(401, body: Data(#"{"error":{"message":"bad key"}}"#.utf8))
        let (model, _) = try model(text: """
            export SOCLAAS_BASE_URL="https://soclaas.test/v1"
            """, transport: transport)
        await model.load()
        let result = await model.test(name: "SOCLAAS_API_KEY", value: "clsk-bad")
        guard case .rejected(let status, _) = result else {
            Issue.record("expected rejected, got \(result)")
            return
        }
        #expect(status == 401)
        #expect(transport.requests.first?.url?.absoluteString == "https://soclaas.test/v1/models")
    }

    @Test func testWithABaseURLNeedsNoSavedSibling() async throws {
        let transport = FakeTransport()
        transport.reply(200)
        let (model, _) = try model(text: "", transport: transport)
        await model.load()
        let result = await model.test(key: "clsk-x", baseURL: "https://soclaas.test/v1")
        #expect(result == .works)
        #expect(transport.requests.first?.url?.absoluteString == "https://soclaas.test/v1/models")
    }

    /// A custom provider may reuse a catalogue name: "Anthropic" writes
    /// ANTHROPIC_API_KEY, and its sibling ANTHROPIC_BASE_URL must own
    /// the check. Picking the catalogue probe would send the key to
    /// api.anthropic.com, a host it was never meant for; and the custom
    /// endpoint answers the same bearer request the pre-save test sent,
    /// not x-api-key.
    @Test func aCatalogueNameWithABaseURLChecksTheCustomEndpoint() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: Data(#"{"data":[]}"#.utf8))
        let (model, _) = try model(text: """
            # Custom
            export ANTHROPIC_BASE_URL="https://proxy.test/v1"
            export ANTHROPIC_API_KEY="clsk-x"
            """, transport: transport)
        await model.load()
        await model.check(name: "ANTHROPIC_API_KEY")
        let request = try #require(transport.requests.first)
        #expect(request.url?.absoluteString == "https://proxy.test/v1/models")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer clsk-x")
        #expect(request.value(forHTTPHeaderField: "x-api-key") == nil)
    }

    /// A sibling base URL that restates the catalogue's own host keeps
    /// the catalogue probe: OpenRouter's docs publish OPENROUTER_BASE_URL
    /// for its canonical endpoint, and the catalogue asks /key — which
    /// validates the key — while the generic /models is public and would
    /// pass even a bad key.
    @Test func aBaseURLOnTheCatalogueHostKeepsTheCatalogueCheck() async throws {
        let transport = FakeTransport()
        transport.reply(401, body: Data(#"{"error":{"message":"bad key"}}"#.utf8))
        let (model, _) = try model(text: """
            export OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"
            export OPENROUTER_API_KEY="sk-or-bad"
            """, transport: transport)
        await model.load()
        await model.check(name: "OPENROUTER_API_KEY")
        #expect(transport.requests.first?.url?.absoluteString == "https://openrouter.ai/api/v1/key")
        guard case .rejected = model.checks["OPENROUTER_API_KEY"]?.result else {
            Issue.record("expected rejected, got \(String(describing: model.checks["OPENROUTER_API_KEY"]))")
            return
        }
    }

    /// A catalogue probe that lists several regional endpoints narrows
    /// to the one the base URL names: a China Dashscope base must not
    /// let the international endpoint's answer stand for it.
    @Test func aBaseURLOnACatalogueHostNarrowsTheCheckToIt() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: Data(#"{"data":[]}"#.utf8))
        let (model, _) = try model(text: """
            export DASHSCOPE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
            export DASHSCOPE_API_KEY="sk-x"
            """, transport: transport)
        await model.load()
        await model.check(name: "DASHSCOPE_API_KEY")
        #expect(transport.requests.map { $0.url?.absoluteString }
                == ["https://dashscope.aliyuncs.com/compatible-mode/v1/models"])
    }

    /// A sibling base URL that can't parse is no check at all; falling
    /// back to the catalogue would leak the key the same way.
    @Test func aBrokenBaseURLKeepsTheKeyFromTheCatalogueHost() async throws {
        let transport = FakeTransport()
        let (model, _) = try model(text: """
            export OPENAI_BASE_URL="junk"
            export OPENAI_API_KEY="clsk-x"
            """, transport: transport)
        await model.load()
        await model.check(name: "OPENAI_API_KEY")
        #expect(transport.requests.isEmpty)
        guard case .noCheck = model.checks["OPENAI_API_KEY"]?.result else {
            Issue.record("expected noCheck, got \(String(describing: model.checks["OPENAI_API_KEY"]))")
            return
        }
    }

    /// The verdict a check saved belongs to the endpoint it ran against:
    /// repointing the base URL retires it even though the key is the same.
    @Test func aBaseURLEditRetiresTheKeysCheck() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: Data(#"{"data":[]}"#.utf8))
        let (model, _) = try model(text: """
            # SoCLaaS
            export SOCLAAS_BASE_URL="https://soclaas.test/v1"
            export SOCLAAS_API_KEY="clsk-x"
            """, transport: transport)
        await model.load()
        await model.check(name: "SOCLAAS_API_KEY")
        var key = try #require(model.rows.first { $0.name == "SOCLAAS_API_KEY" })
        #expect(model.check(for: key)?.result == .works)
        try await model.set(name: "SOCLAAS_BASE_URL", value: "https://elsewhere.test/v1",
                            provider: "SoCLaaS")
        key = try #require(model.rows.first { $0.name == "SOCLAAS_API_KEY" })
        #expect(model.check(for: key) == nil)
    }

    @Test func modelsFetchesAndParsesTheList() async throws {
        let transport = FakeTransport()
        transport.reply(200, body: Data(#"{"data":[{"id":"m1"},{"id":"m2","display_name":"M Two"}]}"#.utf8))
        let (model, _) = try model(text: "", transport: transport)
        await model.load()
        let models = try await model.models(baseURL: "https://soclaas.test/v1", key: "clsk-x")
        #expect(models.map(\.id) == ["m1", "m2"])
        #expect(models[1].label == "M Two")
        let request = try #require(transport.requests.first)
        #expect(request.url?.absoluteString == "https://soclaas.test/v1/models")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer clsk-x")
    }

    @Test func modelsSurfacesTheEndpointsAnswer() async throws {
        let transport = FakeTransport()
        transport.reply(401, body: Data(#"{"error":{"message":"nope"}}"#.utf8))
        let (model, _) = try model(text: "", transport: transport)
        await model.load()
        await #expect(throws: CustomProvider.FetchError.http(401, "nope")) {
            try await model.models(baseURL: "https://soclaas.test/v1", key: "clsk-x")
        }
    }

    @Test func modelsRefusesABadBaseURL() async throws {
        let (model, _) = try model(text: "", transport: FakeTransport())
        await model.load()
        await #expect(throws: CustomProvider.FetchError.badBaseURL) {
            try await model.models(baseURL: "not a url", key: "k")
        }
    }
}
