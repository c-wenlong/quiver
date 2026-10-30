import Foundation

/// The code a transcript shows — shell commands, tool arguments, file
/// bodies and fenced blocks — tokenised by a small hand-rolled scanner
/// (no highlighter dependency, matching the web app's no-build rule).
/// A port of the `hl()` machinery in `src/quiver/webapp/static/pages/chat.js`;
/// keep the two in step. Token
/// colours live in `Theme.swift` (the web app's `--tk-*` variables carry
/// the same values).
public struct SyntaxToken: Equatable, Sendable {
    public enum Kind: Sendable {
        case plain, comment, string, number, keyword, builtin, variable, meta
        /// Diff rows: a whole added or removed line, so one token carries
        /// the row's tint instead of the code's own colouring.
        case added, removed
    }

    public var kind: Kind
    public var text: String

    public init(kind: Kind, text: String) {
        self.kind = kind
        self.text = text
    }
}

/// A comment or multiline string left open at a chunk's end, picked up
/// by the next `tokens` call — diff rows and numbered output scan one
/// line at a time, and an open token resumes inside the next chunk the
/// way it would have without the break.
public struct ScanState: Sendable {
    var kind: SyntaxToken.Kind = .plain
    var close = ""
    var escapable = false
}

public enum SyntaxHighlight {
    /// `code` split into styled runs for `tag` (a fence label or a
    /// language id); unknown and empty tags stay one plain run. `diff`
    /// colours by line sigil the way the patch view does.
    public static func tokens(_ code: String, language tag: String) -> [SyntaxToken] {
        var state: ScanState?
        return tokens(code, language: tag, state: &state)
    }

    /// Same, threading multiline state between calls (the diff rows).
    public static func tokens(_ code: String, language tag: String, state: inout ScanState?) -> [SyntaxToken] {
        guard !code.isEmpty, let name = language(tag: tag), name != "text" else {
            state = nil
            return code.isEmpty ? [] : [SyntaxToken(kind: .plain, text: code)]
        }
        if name == "diff" { state = nil; return diffTokens(code) }
        return scan(code, langs[name] ?? Spec(), numbered: isNumbered(code), state: &state)
    }

    /// A fence tag or extension onto the language table (`"py"` →
    /// `"python"`); `"text"`-family and unknown tags return nil.
    public static func language(tag: String) -> String? {
        var t = tag.trimmingCharacters(in: .whitespaces).lowercased()
        if t.hasPrefix(".") { t.removeFirst() }
        t = t.components(separatedBy: .whitespaces).first ?? t
        guard !t.isEmpty else { return nil }
        if let alias = aliases[t] { return alias }
        return langs.keys.contains(t) ? t : nil
    }

    /// A file path onto the language table by basename, then extension.
    public static func language(path: String) -> String? {
        let base = (path as NSString).lastPathComponent.lowercased()
        if let name = pathLangs[base] { return name }
        guard let dot = base.lastIndex(of: "."), dot < base.endIndex else { return nil }
        return language(tag: String(base[base.index(after: dot)...]))
    }

    // MARK: scanner

    /// One language: comment/string delimiters, word sets and extra
    /// patterns. At each position the scanner tries, in order: line-number
    /// prefix, line comment, block comment, string, patterns, number,
    /// keyword-ish word. Anything unmatched is plain.
    private struct Spec {
        var line: [String] = []
        var block: [(String, String)] = []
        var strings: [String] = ["'", "\""]
        var multiline: Set<String> = []   // delimiters allowed to span newlines
        var noEscape: Set<String> = []    // delimiters where \ does not escape
        var prefixStrings = false         // python's r"" f'' runs before a delimiter
        var keyAfter = false              // a string followed by `:` is a key (JSON)
        var number: NSRegularExpression? = Spec.defaultNumber
        var keywords: Set<String> = []
        var builtins: Set<String> = []
        var constants: Set<String> = []
        var variables: Set<String> = []
        var patterns: [Pat] = []          // tried before words; `b` needs a break before
        var call = false                  // name( → builtin (a call site)
        var caps = false                  // Capitalised word → builtin (a type)
        var fold = false                  // word sets match case-insensitively (SQL, dockerfile)

        /// A sticky-at-position pattern like the JS side's /…/y.
        struct Pat {
            var kind: SyntaxToken.Kind
            var re: NSRegularExpression
            var boundary = false
        }

        static let defaultNumber = rx(
            #"0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:[0-9][0-9_]*\.?[0-9_]*|\.[0-9][0-9_]*)(?:[eE][+-]?[0-9]+)?"#)
    }

    private static func rx(_ src: String, _ opts: NSRegularExpression.Options = []) -> NSRegularExpression {
        // Every pattern here is a compile-time literal.
        try! NSRegularExpression(pattern: src, options: opts)
    }

    private static func pat(_ kind: SyntaxToken.Kind, _ src: String,
                            boundary: Bool = false,
                            _ opts: NSRegularExpression.Options = []) -> Spec.Pat {
        Spec.Pat(kind: kind, re: rx(src, opts), boundary: boundary)
    }

    private static func words(_ s: String) -> Set<String> {
        Set(s.split(separator: " ").map(String.init))
    }

    private static func spec(_ over: (inout Spec) -> Void) -> Spec {
        var s = Spec()
        over(&s)
        // fold lowercases the word before lookup; the sets fold too, so
        // SQL's lowercase tables and dockerfile's uppercase both work.
        if s.fold {
            s.keywords = Set(s.keywords.map { $0.lowercased() })
            s.builtins = Set(s.builtins.map { $0.lowercased() })
            s.constants = Set(s.constants.map { $0.lowercased() })
            s.variables = Set(s.variables.map { $0.lowercased() })
        }
        return s
    }

    /// The C family's shared shape: line and block comments, strings,
    /// words with keyword/constant sets, call sites and Capitalised types.
    private static func cLang(_ kws: String, _ over: ((inout Spec) -> Void)? = nil) -> Spec {
        var s = Spec()
        s.line = ["//"]
        s.block = [("/*", "*/")]
        s.keywords = words(kws)
        s.constants = words("true false null nil nullptr None TRUE FALSE")
        s.call = true
        s.caps = true
        s.patterns = [
            pat(.meta, #"@[A-Za-z_][A-Za-z0-9_.]*"#, boundary: true),   // decorators, annotations
            pat(.meta, #"#[A-Za-z_][A-Za-z0-9_!]*"#, boundary: true),   // C #include, rust attrs
            pat(.variable, #"\$[A-Za-z0-9_]+"#, boundary: true),        // php
        ]
        over?(&s)
        return s
    }

    private static let jsCommon: Spec = {
        var s = Spec()
        s.line = ["//"]
        s.block = [("/*", "*/")]
        s.strings = ["'", "\"", "`"]
        s.multiline = ["`"]
        s.constants = words("true false null undefined NaN Infinity")
        s.builtins = words("console window document global globalThis process require module exports fetch Promise Symbol Math JSON Object Array String Number Boolean BigInt Map Set WeakMap WeakSet Reflect Proxy Date RegExp Error TypeError RangeError SyntaxError parseInt parseFloat isNaN isFinite setTimeout setInterval clearTimeout clearInterval queueMicrotask structuredClone Intl")
        s.call = true
        s.caps = true
        s.patterns = [pat(.meta, #"@[A-Za-z_][A-Za-z0-9_.]*"#, boundary: true)]
        return s
    }()

    private static let langs: [String: Spec] = {
        var l: [String: Spec] = [:]

        l["bash"] = spec {
            $0.line = ["#"]
            $0.multiline = ["'", "\""]
            $0.noEscape = ["'"]
            $0.keywords = words("if then else elif fi for while until do done case esac function select in time coproc break continue return exit")
            $0.builtins = words("cd echo printf read test source eval exec export local declare typeset readonly set unset shift trap wait alias unalias umask ulimit getopts command builtin type hash dirs pushd popd jobs fg bg kill disown suspend logout true false let mapfile readarray enable help caller sudo doas env xargs nohup nice install")
            $0.patterns = [
                pat(.variable, #"\$\{(?:[^}"'\\]|\\.)*\}|\$[A-Za-z_][A-Za-z0-9_]*|\$[@#?$!*0-9_-]+"#),
                pat(.builtin, #"\[(?=\s)"#, boundary: true),
                pat(.meta, #"-{1,2}[A-Za-z][A-Za-z0-9_=-]*"#, boundary: true),
            ]
        }
        l["dockerfile"] = spec {
            $0.line = ["#"]
            $0.keywords = words("FROM RUN CMD ENTRYPOINT COPY ADD WORKDIR EXPOSE ENV ARG LABEL USER VOLUME SHELL HEALTHCHECK ONBUILD STOPSIGNAL AS")
            $0.fold = true
            $0.patterns = [pat(.variable, #"\$\{?[A-Za-z0-9_]+\}?"#)]
        }
        l["python"] = spec {
            $0.line = ["#"]
            $0.strings = ["'''", "\"\"\"", "'", "\""]
            $0.multiline = ["'''", "\"\"\""]
            $0.prefixStrings = true
            $0.keywords = words("and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case")
            $0.builtins = words("print len range str int float bool bytes list dict set tuple frozenset object type isinstance issubclass enumerate zip map filter sorted reversed min max sum any all abs round divmod pow input open super property staticmethod classmethod getattr setattr hasattr delattr callable iter next repr format chr ord hex bin oct id vars dir exec eval compile globals locals breakpoint hash slice memoryview bytearray complex")
            $0.constants = words("True False None NotImplemented Ellipsis")
            $0.variables = words("self cls")
            $0.patterns = [
                pat(.meta, #"@[A-Za-z_][A-Za-z0-9_.]*"#, boundary: true),
                pat(.builtin, #"__[A-Za-z0-9_]+__"#),
            ]
            $0.number = rx(#"0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:[0-9][0-9_]*\.?[0-9_]*|\.[0-9][0-9_]*)(?:[eE][+-]?[0-9]+)?[jJ]?"#)
        }
        l["javascript"] = spec {
            $0 = jsCommon
            $0.keywords = words("await async break case catch class const continue debugger default delete do else export extends finally for from function get if import in instanceof let new of return set static super switch this throw try typeof var void while with yield")
        }
        l["typescript"] = spec {
            $0 = jsCommon
            $0.keywords = words("await async abstract as break case catch class const continue debugger declare default delete do else enum export extends finally for from function get if implements import in instanceof infer interface is keyof let namespace new of private protected public readonly return satisfies set static super switch this throw try type typeof using var void while with yield")
        }
        l["json"] = spec {
            $0.strings = ["\""]
            $0.keyAfter = true
            $0.keywords = words("true false null")
        }
        l["css"] = spec {
            $0.block = [("/*", "*/")]
            $0.number = rx(#"[0-9][0-9.]*(?:px|em|rem|%|vh|vw|vmin|vmax|s|ms|fr|deg|turn|ch|ex|lh|cm|mm|in|pt|pc|dppx)?"#)
            $0.patterns = [
                pat(.keyword, #"@[A-Za-z0-9_-]+"#),                        // @media and friends
                pat(.builtin, #"--[A-Za-z0-9_-]+"#),                       // --custom-property
                // [^{};\s] keeps whitespace positions O(1): retried at
                // each space, an unbounded run would rescan to the next
                // { } ; — quadratic on a block with a long indent.
                pat(.meta, #"[^{};\s][^{};]{0,300}(?=\s*\{)"#, boundary: true), // a selector before {
                pat(.number, #"#[0-9a-fA-F]{3,8}\b"#),                     // #rrggbb
                pat(.builtin, #"[a-zA-Z-][A-Za-z0-9_-]*(?=\s*:)"#, boundary: true), // a property before :
                pat(.variable, #"[.#:][A-Za-z0-9_-]+"#),                   // .class, #id, :pseudo
                pat(.keyword, #"![A-Za-z0-9_]+"#),                         // !important
            ]
        }
        l["html"] = spec {
            $0.block = [("<!--", "-->")]
            $0.number = nil
            $0.patterns = [
                pat(.keyword, #"<![A-Za-z]+|<\?[A-Za-z0-9_:-]*|\?>"#),
                pat(.keyword, #"<\/?[A-Za-z][A-Za-z0-9_:-]*|\/?>"#),
                pat(.builtin, #"[A-Za-z0-9_:-]+(?==)"#),
                pat(.number, #"&[A-Za-z0-9_#]+;"#),
            ]
        }
        l["yaml"] = spec {
            $0.line = ["#"]
            $0.keywords = words("true false yes no on off null")
            $0.patterns = [
                pat(.builtin, #"[A-Za-z0-9_."'\[\]-]+(?=\s*:(?:\s|$))"#, boundary: true),
                pat(.variable, #"[&*][A-Za-z0-9_-]+"#, boundary: true),
            ]
        }
        l["toml"] = spec {
            $0.line = ["#"]
            $0.strings = ["\"\"\"", "'''", "\"", "'"]
            $0.multiline = ["\"\"\"", "'''"]
            $0.keywords = words("true false")
            $0.number = rx(#"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:?[0-9]{2})?|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?|0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|[+-]?(?:[0-9][0-9_]*\.?[0-9_]*|\.[0-9][0-9_]*)(?:[eE][+-]?[0-9]+)?|\b(?:inf|nan)\b"#)
            $0.patterns = [
                pat(.keyword, #"^[ \t]*\[{1,2}[^\]\n]+\]{1,2}"#, .anchorsMatchLines),
                pat(.builtin, #"[A-Za-z0-9_.-]+(?=\s*=)"#, boundary: true),
            ]
        }
        l["sql"] = spec {
            $0.line = ["--"]
            $0.block = [("/*", "*/")]
            $0.strings = ["'", "\"", "`"]
            $0.keywords = words("select from where and or not in is null like between join inner left right outer full cross on group by order having limit offset insert into values update set delete create alter drop table index view trigger procedure function if exists unique primary key foreign references default check constraint union all distinct as case when then else end cast with recursive returning begin commit rollback transaction grant revoke vacuum explain analyze asc desc fetch first next only")
            $0.builtins = words("count sum avg min max coalesce now date datetime substr substring length upper lower trim abs round concat replace nullif ifnull current_timestamp current_date current_user row_number rank dense_rank over partition")
            $0.constants = words("true false")
            $0.call = true
            $0.fold = true
        }
        l["ruby"] = spec {
            $0.line = ["#"]
            $0.block = [("=begin", "=end")]
            $0.strings = ["'", "\"", "`"]
            $0.keywords = words("def end class module if else elsif unless while until do begin rescue ensure raise return yield lambda proc require include extend attr_accessor attr_reader attr_writer puts print p gets self nil true false super private protected public initialize new each for in case when then break next redo retry loop and or not alias undef")
            $0.caps = true
            $0.patterns = [
                pat(.variable, #"@{1,2}[A-Za-z0-9_]+|\$[A-Za-z0-9_]+"#),
                pat(.variable, #":[a-zA-Z_][A-Za-z0-9_]*[!?=]?"#),
            ]
        }
        l["php"] = spec {
            $0.line = ["//", "#"]
            $0.block = [("/*", "*/")]
            $0.keywords = words("function echo if else elseif foreach while do for switch case break continue return class extends implements interface trait use namespace new public private protected static const var global fn try catch finally throw instanceof clone yield from match readonly enum abstract final print include require list unset isset empty array or and xor as declare")
            $0.constants = words("true false null TRUE FALSE NULL")
            $0.call = true
            $0.caps = true
            $0.patterns = [
                pat(.keyword, #"<\?(?:php|=)?|\?>"#),
                pat(.variable, #"\$[A-Za-z0-9_]+"#),
            ]
        }
        l["c"] = cLang("auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while bool true false")
        l["cpp"] = cLang("alignas alignof and asm auto bool break case catch char char8_t char16_t char32_t class compl concept const consteval constexpr constinit const_cast continue co_await co_return co_yield decltype default delete do double dynamic_cast else enum explicit export extern false float for friend goto if inline int long mutable namespace new noexcept nullptr operator or private protected public register reinterpret_cast requires return short signed sizeof static static_assert static_cast struct switch template this thread_local throw true try typedef typeid typename union unsigned using virtual void volatile wchar_t while xor override final")
        l["java"] = cLang("abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public record return sealed short static strictfp super switch synchronized this throw throws transient try void volatile while yield permits var true false null")
        l["kotlin"] = cLang("as break class continue do else false for fun if in interface is null object package return super this throw true try typealias val var when while by catch companion constructor data delegate enum external finally get import init inline inner internal lateinit noinline open operator out override private protected public reified sealed suspend tailrec vararg where crossinline actual expect const abstract annotation")
        l["swift"] = cLang("associatedtype associativity async await actor break case catch class continue convenience default defer deinit didSet do dynamic else enum extension fallthrough fileprivate final for func get guard if import in indirect infix init inout internal is isolated lazy let mutating nil nonisolated open operator optional override postfix precedencegroup prefix private protocol public repeat required rethrows return self Self set some static struct subscript super switch throw throws try typealias unowned var weak where while willSet true false")
        l["rust"] = cLang("as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true try type unsafe use where while abstract become box do final macro override priv typeof unsized virtual yield") { s in
            s.strings = ["\""]
            s.patterns = [
                pat(.string, #"'(?:\\.|[^\\'])'"#),
                pat(.variable, #"'[a-zA-Z_][A-Za-z0-9_]*"#),
                pat(.meta, #"#!?\[[^\]\n]*\]|#[A-Za-z0-9_]+"#),
                pat(.builtin, #"[a-zA-Z_][A-Za-z0-9_]*!"#),
            ]
        }
        l["go"] = cLang("break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var true false iota nil") { s in
            s.strings = ["'", "\"", "`"]
            s.multiline = ["`"]
            s.builtins = words("string int int8 int16 int32 int64 uint uint8 uint16 uint32 uint64 uintptr byte rune float32 float64 complex64 complex128 bool error any comparable append cap close complex copy delete imag len make new panic print println real recover")
        }
        l["csharp"] = cLang("abstract as base bool break byte case catch char checked class const continue decimal default delegate do double else enum event explicit extern false finally fixed float for foreach goto if implicit in int interface internal is lock long namespace new null object operator out override params private protected public readonly record ref required return sbyte sealed short sizeof stackalloc static string struct switch this throw true try typeof uint ulong unchecked unsafe ushort using var virtual void volatile while init file")
        return l
    }()

    /// Fence tags and file extensions onto the language table. Anything
    /// not listed renders plain ("text" is an explicit opt-out).
    private static let aliases: [String: String] = [
        "sh": "bash", "shell": "bash", "zsh": "bash", "fish": "bash", "ksh": "bash",
        "bashrc": "bash", "zshrc": "bash", "shellscript": "bash",
        "py": "python", "python3": "python",
        "js": "javascript", "jsx": "javascript", "node": "javascript", "mjs": "javascript", "cjs": "javascript",
        "ts": "typescript", "tsx": "typescript", "mts": "typescript", "cts": "typescript",
        "json5": "json", "jsonc": "json", "jsonl": "json",
        "scss": "css", "less": "css",
        "htm": "html", "svg": "html", "xhtml": "html", "xml": "html", "xsl": "html", "plist": "html", "vue": "html", "svelte": "html",
        "yml": "yaml",
        "rb": "ruby", "rake": "ruby", "gemfile": "ruby",
        "c++": "cpp", "cplusplus": "cpp", "cc": "cpp", "cxx": "cpp", "hpp": "cpp", "hh": "cpp", "h": "cpp",
        "c#": "csharp", "cs": "csharp",
        "kt": "kotlin", "kts": "kotlin",
        "rs": "rust",
        "golang": "go",
        "diff": "diff", "patch": "diff",
        "containerfile": "dockerfile",
        "makefile": "bash", "gnumakefile": "bash", "mk": "bash",
        "ini": "bash", "env": "bash", "cfg": "bash", "conf": "bash",
        "md": "text", "markdown": "text", "plaintext": "text", "txt": "text", "log": "text",
    ]

    /// Basenames that carry a language without an extension.
    private static let pathLangs: [String: String] = [
        "dockerfile": "dockerfile", "containerfile": "dockerfile",
        "makefile": "bash", "gnumakefile": "bash", "rakefile": "ruby", "gemfile": "ruby",
        ".bashrc": "bash", ".zshrc": "bash", ".bash_profile": "bash", ".zprofile": "bash",
        ".profile": "bash", ".env": "bash", ".gitignore": "bash", ".dockerignore": "bash",
    ]

    // MARK: line numbers

    /// Read/View output carries the file's own line numbers (`  12→code`,
    /// or cat -n's `12<TAB>code`): when nearly every line starts with one,
    /// each number becomes a meta token and the rest is the file's code.
    private static func isNumbered(_ code: String) -> Bool {
        let lines = code.components(separatedBy: "\n")
            .filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        guard lines.count >= 3 else { return false }
        let hits = lines.filter { numberedPrefix(Array($0.utf16), 0, cap: false) > 0 }.count
        return Double(hits) / Double(lines.count) >= 0.8
    }

    /// The `/^[ \t]*\d+[→|\t]/` run at `i`, or 0. `cap` is the JS side's
    /// `slice(i, i + 16)` bound, on while scanning.
    private static func numberedPrefix(_ u: [UInt16], _ i: Int, cap: Bool) -> Int {
        var j = i
        let end = cap ? min(i + 16, u.count) : u.count
        while j < end, u[j] == 0x20 || u[j] == 0x09 { j += 1 }
        let digits = j
        while j < end, isDigit(u[j]) { j += 1 }
        guard j > digits, j < end else { return 0 }
        return u[j] == 0x2192 || u[j] == 0x7C || u[j] == 0x09 ? j + 1 - i : 0
    }

    private static func isDigit(_ u: UInt16) -> Bool { (0x30...0x39).contains(u) }
    private static func isWordStart(_ u: UInt16) -> Bool {
        (0x41...0x5A).contains(u) || (0x61...0x7A).contains(u) || u == 0x5F || u == 0x24
    }
    private static func isWord(_ u: UInt16) -> Bool { isWordStart(u) || isDigit(u) }

    /// The string starting at i: its end index (prefix included),
    /// whether its delimiter was found, and the delimiter itself; or
    /// nil. A 1-char delimiter unclosed by end of line stops before the
    /// newline unless the language lets it wrap (bash's, js's backtick).
    private static func stringAt(_ u: [UInt16], _ i: Int, _ sp: Spec)
        -> (end: Int, closed: Bool, d: String, multi: Bool, escapable: Bool)? {
        var d = ""
        var pre = 0
        for q in sp.strings where has(u, i, q) { d = q; break }
        if d.isEmpty, sp.prefixStrings {
            var k = i
            while k < min(i + 3, u.count), isStrPrefixChar(u[k]) { k += 1 }
            if k > i {
                for q in sp.strings where has(u, k, q) { d = q; pre = k - i; break }
            }
        }
        guard !d.isEmpty else { return nil }
        let multi = sp.multiline.contains(d)
        let escapable = !sp.noEscape.contains(d)
        var j = i + pre + d.utf16.count
        while j < u.count {
            if escapable, u[j] == 0x5C { j += 2; continue }   // backslash
            if has(u, j, d) { return (j + d.utf16.count, true, d, multi, escapable) }
            if u[j] == 0x0A, !multi { return (j, false, d, multi, escapable) }   // newline
            j += 1
        }
        return (u.count, false, d, multi, escapable)
    }

    private static func isStrPrefixChar(_ u: UInt16) -> Bool {
        // python's [rRfFbBuU] before a quote
        u == 0x72 || u == 0x52 || u == 0x66 || u == 0x46 || u == 0x62 || u == 0x42 || u == 0x75 || u == 0x55
    }

    /// `u` starts with `prefix` at `i`, both in UTF-16.
    private static func has(_ u: [UInt16], _ i: Int, _ prefix: String) -> Bool {
        var j = i
        for unit in prefix.utf16 {
            guard j < u.count, u[j] == unit else { return false }
            j += 1
        }
        return true
    }

    /// The first index ≥ i where `needle` starts, or nil.
    private static func find(_ u: [UInt16], _ i: Int, _ needle: String) -> Int? {
        let p = Array(needle.utf16)
        guard !p.isEmpty, i < u.count else { return nil }
        var j = i
        while j + p.count <= u.count {
            if has(u, j, needle) { return j }
            j += 1
        }
        return nil
    }

    /// The regex's anchored match length at `i`, or 0. `i` is a UTF-16
    /// offset, so ranges land on the same units `u` is indexed by.
    private static func match(_ re: NSRegularExpression, _ code: String, _ i: Int, _ n: Int) -> Int {
        guard i < n,
              let m = re.firstMatch(in: code, options: [.anchored], range: NSRange(location: i, length: n - i)),
              m.range.length > 0 else { return 0 }
        return m.range.length
    }

    /// The break characters a boundary pattern needs before it, so
    /// `foo-bar` is not a flag and `input:hover` not a YAML key.
    private static let breakChars: Set<UInt16> = Set(Array(" \t\n;|&(){}<>=,".utf16))

    /// Whole-input scan. Numbered output splits at each line so a line
    /// number is never swallowed by a multiline token reaching across
    /// it — the number peels off first, then the open token resumes.
    private static func scan(_ code: String, _ sp: Spec, numbered: Bool,
                             state: inout ScanState?) -> [SyntaxToken] {
        guard numbered else { return scanBlock(code, sp, &state) }
        var toks: [SyntaxToken] = []
        var first = true
        for line in code.components(separatedBy: "\n") {
            if first { first = false } else { toks.append(SyntaxToken(kind: .plain, text: "\n")) }
            var rest = line
            let u = Array(line.utf16)
            let len = numberedPrefix(u, 0, cap: true)
            if len > 0 {
                toks.append(SyntaxToken(kind: .meta, text: String(decoding: u[0..<len], as: UTF16.self)))
                rest = String(decoding: u[len...], as: UTF16.self)
            }
            toks.append(contentsOf: scanBlock(rest, sp, &state))
        }
        return toks
    }

    /// [SyntaxToken] covering `code` end to end. `state` carries an
    /// unfinished comment or multiline string between calls — diff rows
    /// and numbered output scan one line at a time.
    private static func scanBlock(_ code: String, _ sp: Spec,
                                  _ state: inout ScanState?) -> [SyntaxToken] {
        let u = Array(code.utf16)
        let n = u.count
        var toks: [SyntaxToken] = []
        var plain: [UInt16] = []   // units, so a surrogate pair survives
        var i = 0

        func flushPlain() {
            if !plain.isEmpty {
                toks.append(SyntaxToken(kind: .plain, text: String(decoding: plain, as: UTF16.self)))
                plain.removeAll(keepingCapacity: true)
            }
        }
        func emit(_ kind: SyntaxToken.Kind, _ j: Int) {
            if kind == .plain {
                plain.append(contentsOf: u[i..<j])
            } else {
                flushPlain()
                toks.append(SyntaxToken(kind: kind, text: String(decoding: u[i..<j], as: UTF16.self)))
            }
            i = j
        }

        // Resume a token left open by the previous chunk: run to its
        // delimiter (escapes skipped inside strings) or the chunk's end.
        if let s0 = state {
            var j = 0
            while j < n {
                if s0.escapable, u[j] == 0x5C { j += 2; continue }
                if has(u, j, s0.close) { j += s0.close.utf16.count; state = nil; break }
                j += 1
            }
            // A backslash ending the chunk skips past it.
            emit(s0.kind, min(j, n))
        }

        while i < n {
            var hit = false
            for c in sp.line where has(u, i, c) {
                emit(.comment, find(u, i, "\n") ?? n)
                hit = true
                break
            }
            if hit { continue }
            for pair in sp.block where has(u, i, pair.0) {
                if let end = find(u, i + pair.0.count, pair.1) {
                    emit(.comment, end + pair.1.count)
                } else {
                    emit(.comment, n)
                    state = ScanState(kind: .comment, close: pair.1, escapable: false)
                }
                hit = true
                break
            }
            if hit { continue }
            if let sa = stringAt(u, i, sp) {
                var kind = SyntaxToken.Kind.string
                if sp.keyAfter {
                    var j = sa.end
                    while j < n, u[j] == 0x20 || u[j] == 0x09 { j += 1 }
                    if j < n, u[j] == 0x3A { kind = .builtin }   // ":"
                }
                if !sa.closed, sa.multi {
                    state = ScanState(kind: kind, close: sa.d, escapable: sa.escapable)
                }
                emit(kind, sa.end)
                continue
            }
            for p in sp.patterns where !p.boundary || i == 0 || breakChars.contains(u[i - 1]) {
                let len = match(p.re, code, i, n)
                if len > 0 { emit(p.kind, i + len); hit = true; break }
            }
            if hit { continue }
            if let re = sp.number {
                let len = match(re, code, i, n)
                if len > 0 { emit(.number, i + len); continue }
            }
            if isWordStart(u[i]) {
                var j = i + 1
                while j < n, isWord(u[j]) { j += 1 }
                let w = String(decoding: u[i..<j], as: UTF16.self)
                let key = sp.fold ? w.lowercased() : w
                var kind = SyntaxToken.Kind.plain
                if sp.keywords.contains(key) { kind = .keyword }
                else if sp.constants.contains(key) { kind = .number }
                else if sp.variables.contains(key) { kind = .variable }
                else if sp.builtins.contains(key) { kind = .builtin }
                else if sp.call, j < n, u[j] == 0x28 { kind = .builtin }   // "("
                else if sp.caps, (0x41...0x5A).contains(u[i]) { kind = .builtin }
                emit(kind, j)
                continue
            }
            plain.append(u[i])
            i += 1
        }
        flushPlain()
        return toks
    }

    /// A ```diff fence: each line's sigil picks its row kind, the same
    /// classes the web `hl()`'s diff branch emits.
    private static func diffTokens(_ code: String) -> [SyntaxToken] {
        var toks: [SyntaxToken] = []
        for line in code.components(separatedBy: "\n") {
            let kind: SyntaxToken.Kind =
                line.hasPrefix("+") && !line.hasPrefix("+++") ? .added
                : line.hasPrefix("-") && !line.hasPrefix("---") ? .removed
                : line.hasPrefix("@@") || line.hasPrefix("***")
                    || line.hasPrefix("diff ") || line.hasPrefix("index ")
                    || line.hasPrefix("--- ") || line.hasPrefix("+++ ") ? .meta
                : .plain
            toks.append(SyntaxToken(kind: kind, text: line + "\n"))
        }
        if toks.last?.text.hasSuffix("\n") == true {
            toks[toks.count - 1].text.removeLast()
        }
        return toks
    }
}
