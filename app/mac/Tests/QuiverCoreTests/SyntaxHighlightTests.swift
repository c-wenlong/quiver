import Testing
@testable import QuiverCore

/// `SyntaxHighlight` is a port of the web app's `hl()` machinery — the
/// tests mirror the smoke cases the JS side exercises.
@Suite struct SyntaxHighlightTests {

    // MARK: language resolution

    @Test func fenceAliases() {
        #expect(SyntaxHighlight.language(tag: "py") == "python")
        #expect(SyntaxHighlight.language(tag: "sh") == "bash")
        #expect(SyntaxHighlight.language(tag: "TS") == "typescript")
        #expect(SyntaxHighlight.language(tag: "c++") == "cpp")
        #expect(SyntaxHighlight.language(tag: "yml") == "yaml")
        #expect(SyntaxHighlight.language(tag: "rs") == "rust")
        #expect(SyntaxHighlight.language(tag: "diff") == "diff")
    }

    @Test func unknownFencesStayPlain() {
        #expect(SyntaxHighlight.language(tag: "") == nil)
        #expect(SyntaxHighlight.language(tag: "notalang") == nil)
        #expect(SyntaxHighlight.tokens("a b", language: "notalang")
            == [SyntaxToken(kind: .plain, text: "a b")])
        #expect(SyntaxHighlight.tokens("x", language: "text")
            == [SyntaxToken(kind: .plain, text: "x")])
    }

    @Test func pathsCarryLanguages() {
        #expect(SyntaxHighlight.language(path: "/a/b/main.swift") == "swift")
        #expect(SyntaxHighlight.language(path: "x/Component.TSX") == "typescript")
        #expect(SyntaxHighlight.language(path: "proj/Dockerfile") == "dockerfile")
        #expect(SyntaxHighlight.language(path: ".env") == "bash")
        #expect(SyntaxHighlight.language(path: "Makefile") == "bash")
        #expect(SyntaxHighlight.language(path: "no-extension-here") == nil)
    }

    // MARK: scanning

    private func kinds(_ code: String, _ lang: String) -> [(SyntaxToken.Kind, String)] {
        SyntaxHighlight.tokens(code, language: lang).map { ($0.kind, $0.text) }
    }

    private func kindOf(_ code: String, _ lang: String, _ needle: String) -> SyntaxToken.Kind? {
        SyntaxHighlight.tokens(code, language: lang)
            .first { $0.text.contains(needle) }?.kind
    }

    @Test func bash() {
        let toks = kinds(#"if [ -f x ]; then sudo apt-get install -y foo # done"#, "bash")
        #expect(toks.contains { $0.0 == .keyword && $0.1 == "if" })
        #expect(toks.contains { $0.0 == .builtin && $0.1 == "[" })
        #expect(toks.contains { $0.0 == .meta && $0.1 == "-f" })
        #expect(toks.contains { $0.0 == .builtin && $0.1 == "sudo" })
        #expect(toks.contains { $0.0 == .meta && $0.1 == "-y" })
        #expect(toks.contains { $0.0 == .comment && $0.1 == "# done" })
    }

    @Test func bashVariablesAndFlags() {
        let toks = kinds(#"echo "$HOME/x" --color=auto ./f -la"#, "bash")
        #expect(toks.contains { $0.0 == .builtin && $0.1 == "echo" })
        #expect(toks.contains { $0.0 == .string && $0.1.hasPrefix("\"") })
        #expect(toks.contains { $0.0 == .meta && $0.1 == "--color=auto" })
        #expect(toks.contains { $0.0 == .meta && $0.1 == "-la" })
        // `foo-bar` is a bare word, not a flag: the dash needs a break before it.
        #expect(!kinds("foo-bar", "bash").contains { $0.0 == .meta })
    }

    @Test func python() {
        let code = "def f(x):\n    return f\"v={x}\"  # c\nn = 0x1f\n"
        let toks = kinds(code, "python")
        #expect(toks.contains { $0.0 == .keyword && $0.1 == "def" })
        #expect(toks.contains { $0.0 == .keyword && $0.1 == "return" })
        #expect(toks.contains { $0.0 == .string && $0.1.contains("v=") })
        #expect(toks.contains { $0.0 == .comment && $0.1 == "# c" })
        #expect(toks.contains { $0.0 == .number && $0.1 == "0x1f" })
        #expect(kindOf("len(items)", "python", "len") == .builtin)
        #expect(kindOf("self.x = 1", "python", "self") == .variable)
    }

    @Test func jsonKeysAndScalars() {
        let toks = kinds(#"{"a": 1, "b": [true, null], "c": "s"}"#, "json")
        #expect(toks.contains { $0.0 == .builtin && $0.1 == "\"a\"" })
        #expect(toks.contains { $0.0 == .number && $0.1 == "1" })
        #expect(toks.contains { $0.0 == .keyword && $0.1 == "true" })
        #expect(toks.contains { $0.0 == .keyword && $0.1 == "null" })
        // A quoted run before `:` is a key; a value string stays a string.
        #expect(toks.contains { $0.0 == .builtin && $0.1 == "\"c\"" })
        #expect(toks.contains { $0.0 == .string && $0.1 == "\"s\"" })
    }

    @Test func javascript() {
        let toks = kinds("const r = await fetch(\"x\"); // go", "javascript")
        #expect(toks.contains { $0.0 == .keyword && $0.1 == "const" })
        #expect(toks.contains { $0.0 == .keyword && $0.1 == "await" })
        #expect(toks.contains { $0.0 == .builtin && $0.1 == "fetch" })
        #expect(toks.contains { $0.0 == .comment && $0.1 == "// go" })
    }

    @Test func css() {
        let toks = kinds(".a { margin: 4px; color: #fff; }", "css")
        // A run of selector text before `{` is meta.
        #expect(toks.contains { $0.0 == .meta && $0.1 == ".a " })
        #expect(toks.contains { $0.0 == .builtin && $0.1 == "margin" })
        #expect(toks.contains { $0.0 == .number && $0.1 == "4px" })
        #expect(toks.contains { $0.0 == .number && $0.1 == "#fff" })
    }

    @Test func html() {
        let toks = kinds(#"<div class="x">&amp;</div>"#, "html")
        #expect(toks.contains { $0.0 == .keyword && $0.1 == "<div" })
        #expect(toks.contains { $0.0 == .builtin && $0.1 == "class" })
        #expect(toks.contains { $0.0 == .string && $0.1 == "\"x\"" })
        #expect(toks.contains { $0.0 == .number && $0.1 == "&amp;" })
        #expect(toks.contains { $0.0 == .keyword && $0.1 == "</div" })
    }

    @Test func yaml() {
        let toks = kinds("name: app\nport: 8080\nenabled: on", "yaml")
        // A word before `:` is the key; a bare `on` value is a keyword.
        #expect(toks.contains { $0.0 == .builtin && $0.1 == "name" })
        #expect(toks.contains { $0.0 == .builtin && $0.1 == "port" })
        #expect(toks.contains { $0.0 == .number && $0.1 == "8080" })
        #expect(toks.contains { $0.0 == .keyword && $0.1 == "on" })
    }

    @Test func toml() {
        let toks = kinds("[server]\nport = 8080\nhost = \"x\"", "toml")
        #expect(toks.contains { $0.0 == .keyword && $0.1 == "[server]" })
        #expect(toks.contains { $0.0 == .builtin && $0.1 == "port" })
        #expect(toks.contains { $0.0 == .string && $0.1 == "\"x\"" })
    }

    @Test func sql() {
        let toks = kinds("select name, count(*) from t where id > 3 -- x", "sql")
        #expect(toks.contains { $0.0 == .keyword && $0.1 == "select" })
        #expect(toks.contains { $0.0 == .builtin && $0.1 == "count" })
        #expect(toks.contains { $0.0 == .number && $0.1 == "3" })
        #expect(toks.contains { $0.0 == .comment && $0.1 == "-- x" })
    }

    @Test func rustAndGo() {
        #expect(kindOf("fn main() { let x = 1; }", "rust", "fn") == .keyword)
        #expect(kindOf("func main() { fmt.Println(1) }", "go", "func") == .keyword)
        #expect(kindOf("func main() { fmt.Println(1) }", "go", "Println") == .builtin)
        #expect(kindOf("struct Foo;", "rust", "Foo") == .builtin)
    }

    @Test func numberedOutputKeepsCode() {
        let code = "  1→import os\n  2→x = 1\n  3→print(x)"
        let toks = kinds(code, "python")
        #expect(toks.contains { $0.0 == .meta && $0.1 == "  1→" })
        #expect(toks.contains { $0.0 == .keyword && $0.1 == "import" })
        #expect(toks.contains { $0.0 == .number && $0.1 == "1" })
    }

    @Test func diffFences() {
        let toks = kinds("+added line\n-removed line\n@@ -1 +1 @@\n kept", "diff")
        #expect(toks.contains { $0.0 == .added && $0.1.hasPrefix("+added") })
        #expect(toks.contains { $0.0 == .removed && $0.1.hasPrefix("-removed") })
        #expect(toks.contains { $0.0 == .meta && $0.1.hasPrefix("@@") })
        #expect(toks.contains { $0.0 == .plain && $0.1.hasPrefix(" kept") })
    }

    @Test func joinedTokensReproduceInput() {
        for (code, lang) in [
            ("if x; then echo \"$v\"; fi", "bash"),
            ("<a href=\"u\">t</a>", "html"),
            ("{\"k\": [1, \"s\"]}", "json"),
            ("def f():\n    return 'unclosed\nnext", "python"),
        ] {
            let joined = SyntaxHighlight.tokens(code, language: lang).map(\.text).joined()
            #expect(joined == code)
        }
    }

    // MARK: ToolDisplay

    @Test func readableCommandIsBash() {
        let call = HubToolCall(name: "Bash", argsFull: #"{"command":"ls -la","description":"x"}"#)
        let readable = ToolDisplay.readable(call)
        #expect(readable?.text == "ls -la")
        #expect(readable?.language == "bash")
    }

    @Test func readableArrayCommand() {
        let call = HubToolCall(name: "Bash", argsFull: #"{"command":["git","status"]}"#)
        #expect(ToolDisplay.readable(call)?.text == "git status")
    }

    @Test func readableFileContentTakesFileLanguage() {
        let call = HubToolCall(name: "Write",
                               argsFull: #"{"file_path":"/a/x.py","content":"print(1)\n"}"#)
        let readable = ToolDisplay.readable(call)
        #expect(readable?.text == "print(1)\n")
        #expect(readable?.language == "python")
    }

    @Test func readableOtherArgsArePrettyJson() {
        let call = HubToolCall(name: "WebFetch", argsFull: #"{"url":"https://x"}"#)
        let readable = ToolDisplay.readable(call)
        #expect(readable?.language == "json")
        #expect(readable?.text.contains("\n") == true)
    }

    @Test func outputLanguageFollowsReadPath() {
        let call = HubToolCall(name: "Read", argsFull: #"{"file_path":"/a/x.css"}"#)
        #expect(ToolDisplay.outputLanguage(call, result: "body { }") == "css")
        let bash = HubToolCall(name: "Bash", argsFull: #"{"command":"ls"}"#)
        #expect(ToolDisplay.outputLanguage(bash, result: #"{"ok":true}"#) == "json")
        #expect(ToolDisplay.outputLanguage(bash, result: "plain text") == nil)
        // An edit's result is prose, not the file.
        let edit = HubToolCall(name: "Edit", argsFull: #"{"file_path":"/a/x.css","old_string":"a","new_string":"b"}"#)
        #expect(ToolDisplay.outputLanguage(edit, result: "file updated") == nil)
    }

    // MARK: ToolDiff

    @Test func editArgsBecomeDiff() {
        let call = HubToolCall(name: "Edit", argsFull: #"{"file_path":"/a/x.py","old_string":"a = 1","new_string":"a = 2"}"#)
        let diff = ToolDiff.from(call)
        #expect(diff?.file == "/a/x.py")
        #expect(diff?.language == "python")
        #expect(diff?.added == 1 && diff?.removed == 1)
        #expect(diff?.sections.first?.contains { $0.kind == .removed && $0.text == "a = 1" } == true)
        #expect(diff?.sections.first?.contains { $0.kind == .added && $0.text == "a = 2" } == true)
    }

    @Test func writeIsAllAdded() {
        let call = HubToolCall(name: "Write", argsFull: #"{"file_path":"/a/x.txt","content":"l1\nl2"}"#)
        let diff = ToolDiff.from(call)
        #expect(diff?.added == 2)
        #expect(diff?.sections.first?.allSatisfy { $0.kind == .added } == true)
    }

    @Test func stringPatchArgs() {
        // Codex apply_patch: args_full is the patch text as a JSON string.
        let raw = #""*** Begin Patch\n*** Update File: x.py\n@@\n+a = 2\n-a = 1\n*** End Patch""#
        let call = HubToolCall(name: "apply_patch", argsFull: raw)
        let diff = ToolDiff.from(call)
        #expect(diff?.file == "x.py")
        #expect(diff?.added == 1 && diff?.removed == 1)
        #expect(diff?.sections.first?.contains { $0.kind == .meta } == true)
    }

    @Test func nonEditToolsHaveNoDiff() {
        #expect(ToolDiff.from(HubToolCall(name: "Bash", argsFull: #"{"command":"ls"}"#)) == nil)
        #expect(ToolDiff.from(HubToolCall(name: "Edit", argsFull: "not json")) == nil)
        #expect(ToolDiff.from(HubToolCall(name: "Edit", argsFull: #"{"file_path":"/a/x"}"#)) == nil)
    }
}
