import Foundation

/// Edit-ish tool calls rendered as a diff, ported from `toolDiff()` in
/// `src/swe/webapp/static/pages/chat.js` so every client shows the same
/// lines (iOS's copy lives in `app/ios/.../Hub/ToolDiff.swift`).
public struct DiffLine: Equatable, Sendable {
    public enum Kind: Sendable { case context, added, removed, meta }
    public var kind: Kind
    public var text: String

    public init(kind: Kind, text: String) {
        self.kind = kind
        self.text = text
    }
}

public struct ToolDiff: Equatable, Sendable {
    public var file: String
    /// One run of context/del/add lines per edit — a MultiEdit shows its
    /// edits back to back, the way the web app joins the per-edit blocks.
    public var sections: [[DiffLine]]

    public var added: Int { sections.flatMap { $0 }.filter { $0.kind == .added }.count }
    public var removed: Int { sections.flatMap { $0 }.filter { $0.kind == .removed }.count }
    /// The file's own language, for colouring the code inside the rows.
    public var language: String? { SyntaxHighlight.language(path: file) }

    public init(file: String, sections: [[DiffLine]]) {
        self.file = file
        self.sections = sections
    }

    static let tools: Set<String> = ["edit", "multiedit", "write", "notebookedit", "apply_patch", "patch", "file_edit"]

    public static func from(_ call: HubToolCall) -> ToolDiff? {
        let name = call.name.lowercased()
        guard tools.contains(name), let full = call.argsFull else { return nil }
        let parsed = try? JSONSerialization.jsonObject(with: Data(full.utf8), options: [.fragmentsAllowed])
        let patching = name == "apply_patch" || name == "patch"
        // Codex's apply_patch is a custom tool whose input is the patch
        // text itself, so the hub's args_full is a JSON string, not an
        // object.
        if patching, let patch = parsed as? String {
            return patch.isEmpty ? nil : ToolDiff(file: patchFile(patch), sections: [patchLines(patch)])
        }
        guard let args = parsed as? [String: Any] else { return nil }
        let file = (args["file_path"] ?? args["path"] ?? args["filename"]) as? String ?? ""
        switch name {
        case "write", "notebookedit", "file_edit":
            let content = (args["content"] ?? args["new_source"]) as? String ?? ""
            guard !content.isEmpty else { return nil }
            return ToolDiff(file: file, sections: [diff(old: "", new: content)])
        case "apply_patch", "patch":
            let patch = (args["patch"] ?? args["diff"] ?? args["input"]) as? String ?? full
            return ToolDiff(file: file.isEmpty ? patchFile(patch) : file, sections: [patchLines(patch)])
        case "multiedit":
            guard let edits = args["edits"] as? [[String: Any]] else { return nil }
            let sections = edits.prefix(8).compactMap { e -> [DiffLine]? in
                guard e["old_string"] != nil || e["new_string"] != nil else { return nil }
                return diff(old: e["old_string"] as? String ?? "",
                            new: e["new_string"] as? String ?? "")
            }
            return sections.isEmpty ? nil : ToolDiff(file: file, sections: sections)
        default:
            guard args["old_string"] != nil || args["new_string"] != nil else { return nil }
            return ToolDiff(file: file, sections: [diff(old: args["old_string"] as? String ?? "",
                                                        new: args["new_string"] as? String ?? "")])
        }
    }

    /// Common-affix diff: shared leading and trailing lines collapse to a
    /// little context; the differing middle shows removed then added.
    static func diff(old: String, new: String) -> [DiffLine] {
        let a = old.isEmpty ? [] : old.components(separatedBy: "\n")
        let b = new.isEmpty ? [] : new.components(separatedBy: "\n")
        var pre = 0
        while pre < a.count, pre < b.count, a[pre] == b[pre] { pre += 1 }
        var suf = 0
        while suf < a.count - pre, suf < b.count - pre, a[a.count - 1 - suf] == b[b.count - 1 - suf] { suf += 1 }
        var out: [DiffLine] = []
        let top = Array(a[0..<pre])
        if top.count > 3 { out.append(DiffLine(kind: .meta, text: "\(top.count - 3) unchanged lines")) }
        out += top.suffix(3).map { DiffLine(kind: .context, text: $0) }
        out += a[pre..<(a.count - suf)].map { DiffLine(kind: .removed, text: $0) }
        out += b[pre..<(b.count - suf)].map { DiffLine(kind: .added, text: $0) }
        let bottom = Array(a[(a.count - suf)...])
        out += bottom.prefix(2).map { DiffLine(kind: .context, text: $0) }
        if bottom.count > 2 { out.append(DiffLine(kind: .meta, text: "\(bottom.count - 2) more lines")) }
        return out
    }

    /// Raw patch text (codex apply_patch): the sigil picks the row kind
    /// and stays off the text, the way the web view splits sign column
    /// from code.
    static func patchLines(_ patch: String) -> [DiffLine] {
        patch.components(separatedBy: "\n").prefix(400).map { ln in
            if ln.hasPrefix("+") && !ln.hasPrefix("+++") { return DiffLine(kind: .added, text: String(ln.dropFirst())) }
            if ln.hasPrefix("-") && !ln.hasPrefix("---") { return DiffLine(kind: .removed, text: String(ln.dropFirst())) }
            if ln.hasPrefix("@@") || ln.hasPrefix("***") { return DiffLine(kind: .meta, text: ln) }
            return DiffLine(kind: .context, text: ln.hasPrefix(" ") ? String(ln.dropFirst()) : ln)
        }
    }

    /// The file a patch names first (codex's "*** Update File: x"), if
    /// any.
    static func patchFile(_ patch: String) -> String {
        for line in patch.components(separatedBy: "\n") {
            for kind in ["Update", "Add", "Delete"] {
                let lead = "*** \(kind) File: "
                if line.hasPrefix(lead) {
                    return String(line.dropFirst(lead.count)).trimmingCharacters(in: .whitespaces)
                }
            }
        }
        return ""
    }
}

/// Tool arguments and output worded for reading: the shell command out
/// of its JSON wrapper, a file's contents under its own language, and
/// everything else pretty-printed JSON. A port of `readableArgs()` and
/// `toolOutLang()` in the web app; keep them in step.
public enum ToolDisplay {
    /// The call's args as readable text plus the language to colour it
    /// with, or nil when there is nothing to show.
    public static func readable(_ call: HubToolCall) -> (text: String, language: String?)? {
        guard let full = call.argsFull, !full.isEmpty else { return nil }
        guard let obj = try? JSONSerialization.jsonObject(with: Data(full.utf8), options: [.fragmentsAllowed]) else {
            return (full, nil)
        }
        guard let dict = obj as? [String: Any] else { return (full, "json") }
        for key in ["command", "cmd", "script"] {
            if let v = dict[key] as? String { return (v, "bash") }
            if let parts = dict[key] as? [String] {
                return (parts.map(shQuote).joined(separator: " "), "bash")
            }
        }
        let file = filePath(dict)
        for key in ["content", "new_source", "code", "source"] {
            if let v = dict[key] as? String, !v.isEmpty {
                return (v, file.isEmpty ? nil : SyntaxHighlight.language(path: file))
            }
        }
        let pretty = (try? JSONSerialization.data(
            withJSONObject: dict,
            options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        )).flatMap { String(data: $0, encoding: .utf8) } ?? full
        return (pretty, "json")
    }

    /// The language a result should be coloured with: the file's own for
    /// a read-ish call, `json` when the output parses as an object.
    public static func outputLanguage(_ call: HubToolCall, result: String) -> String? {
        var language: String? = nil
        if let full = call.argsFull,
           let obj = try? JSONSerialization.jsonObject(with: Data(full.utf8), options: [.fragmentsAllowed]) as? [String: Any] {
            let path = filePath(obj)
            if !path.isEmpty,
               call.name.range(of: "read|view|cat", options: [.regularExpression, .caseInsensitive]) != nil
                || Set(obj.keys).isSubset(of: pathArgKeys) {
                language = SyntaxHighlight.language(path: path)
            }
        }
        return language ?? (looksJson(result) ? "json" : nil)
    }

    /// `read|view|cat` claims a language — Edit's "file updated" note is
    /// prose, not the file. A call whose args are only a path and range
    /// numbers counts too.
    private static let pathArgKeys: Set<String> = [
        "file_path", "path", "filename", "notebook_path", "filePath",
        "offset", "limit", "start_line", "end_line", "view_range",
    ]

    private static func filePath(_ obj: [String: Any]) -> String {
        (obj["file_path"] ?? obj["path"] ?? obj["filename"]
            ?? obj["notebook_path"] ?? obj["filePath"]) as? String ?? ""
    }

    /// A shell word shown the way it could be pasted back: bare when
    /// safe, single-quoted when it holds whitespace or shell syntax, so
    /// a ["bash","-lc","echo hi"] array reads as `bash -lc 'echo hi'`.
    static func shQuote(_ s: String) -> String {
        let punct = Set("_@%+=:,./~-".utf8)
        let safe = !s.isEmpty && s.utf8.allSatisfy {
            (0x41...0x5A).contains($0) || (0x61...0x7A).contains($0)
                || (0x30...0x39).contains($0) || punct.contains($0)
        }
        return safe ? s : "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    private static func looksJson(_ text: String) -> Bool {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let first = t.first, first == "{" || first == "[" else { return false }
        guard let v = try? JSONSerialization.jsonObject(with: Data(t.utf8), options: [.fragmentsAllowed]) else { return false }
        return v is [String: Any] || v is [Any]
    }
}
