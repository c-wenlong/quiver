import Foundation

/// Edit-ish tool calls rendered as a diff, ported from `toolDiff()` in
/// `src/quiver/webapp/static/pages/chat.js` so both clients show the same lines.
struct DiffLine: Hashable {
    enum Kind { case context, added, removed, meta }
    let kind: Kind
    let text: String
}

struct ToolDiff: Hashable {
    let file: String
    let lines: [DiffLine]

    var added: Int { lines.filter { $0.kind == .added }.count }
    var removed: Int { lines.filter { $0.kind == .removed }.count }

    static let tools: Set<String> = ["edit", "multiedit", "write", "notebookedit", "apply_patch", "patch", "file_edit"]

    static func from(_ tool: ToolCall) -> ToolDiff? {
        let name = tool.name.lowercased()
        guard tools.contains(name), let full = tool.argsFull else { return nil }
        let parsed = try? JSONSerialization.jsonObject(with: Data(full.utf8), options: [.fragmentsAllowed])
        // Codex's apply_patch is a custom tool whose input is the patch
        // text itself, so the hub's args_full is a JSON string, not an
        // object (as the Mac app's ToolDiff reads it).
        if name == "apply_patch" || name == "patch", let patch = parsed as? String {
            return patch.isEmpty ? nil : ToolDiff(file: patchFile(patch), lines: patchLines(patch))
        }
        guard let args = parsed as? [String: Any] else { return nil }
        let file = (args["file_path"] ?? args["path"] ?? args["filename"]) as? String ?? ""
        switch name {
        case "write", "notebookedit", "file_edit":
            let content = (args["content"] ?? args["new_source"]) as? String ?? ""
            guard !content.isEmpty else { return nil }
            return ToolDiff(file: file, lines: diff(old: "", new: content))
        case "apply_patch", "patch":
            let patch = (args["patch"] ?? args["diff"] ?? args["input"]) as? String ?? full
            return ToolDiff(file: file.isEmpty ? patchFile(patch) : file, lines: patchLines(patch))
        case "multiedit":
            guard let edits = args["edits"] as? [[String: Any]] else { return nil }
            var lines: [DiffLine] = []
            for e in edits.prefix(8) {
                let old = e["old_string"] as? String, new = e["new_string"] as? String
                guard old != nil || new != nil else { continue }
                // Between the edits shown, not before the first one shown.
                if !lines.isEmpty { lines.append(DiffLine(kind: .meta, text: "···")) }
                lines += diff(old: old ?? "", new: new ?? "")
            }
            return lines.isEmpty ? nil : ToolDiff(file: file, lines: lines)
        default:
            let old = args["old_string"] as? String, new = args["new_string"] as? String
            guard old != nil || new != nil else { return nil }
            return ToolDiff(file: file, lines: diff(old: old ?? "", new: new ?? ""))
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

/// Tool arguments worded for reading, not as escaped JSON.
enum ToolArgs {
    /// The hub's `args_summary` is "Name · hint"; the card's icon already
    /// says the name, so only the hint stays. An MCP tool's icon is a
    /// generic wrench, so its name stays in front of the hint.
    static func summary(_ call: ToolCall) -> String {
        guard let s = call.argsSummary, !s.isEmpty, s != call.name else { return displayName(call.name) }
        let prefix = "\(call.name) · "
        guard s.hasPrefix(prefix) else { return s }
        let hint = String(s.dropFirst(prefix.count))
        return call.name.hasPrefix("mcp__") ? "\(displayName(call.name)) · \(hint)" : hint
    }

    /// `mcp__computer-use__app_batch` reads as `computer-use · app_batch`,
    /// the way Claude Code shows an MCP tool; other names pass through.
    static func displayName(_ name: String) -> String {
        guard name.hasPrefix("mcp__") else { return name }
        let parts = name.dropFirst(5).components(separatedBy: "__").filter { !$0.isEmpty }
        guard parts.count >= 2 else { return parts.first ?? name }
        return parts[0] + " · " + parts[1...].joined(separator: "__")
    }

    /// A shell command as its own text; anything else as indented JSON
    /// with real newlines instead of `\n`.
    static func readable(_ call: ToolCall) -> String? {
        guard let full = call.argsFull, !full.isEmpty else { return nil }
        guard let obj = try? JSONSerialization.jsonObject(with: Data(full.utf8)) else { return full }
        if let dict = obj as? [String: Any] {
            for key in ["command", "cmd", "script"] {
                if let c = dict[key] as? String { return c }
                if let parts = dict[key] as? [String] { return parts.joined(separator: " ") }
            }
            if dict.count == 1, let only = dict.values.first as? String { return only }
            return dict.keys.sorted().map { key in
                let v = dict[key]!
                if let str = v as? String { return "\(key): \(str)" }
                let data = (try? JSONSerialization.data(withJSONObject: v, options: [.prettyPrinted, .withoutEscapingSlashes, .fragmentsAllowed])) ?? Data()
                return "\(key): \(String(decoding: data, as: UTF8.self))"
            }.joined(separator: "\n")
        }
        return full
    }
}
