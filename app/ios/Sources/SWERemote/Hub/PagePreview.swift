import Foundation

/// Which tool calls get a Preview button, and what the hub's
/// `GET /v1/sessions/{key}/preview?path=` route is asked for.
///
/// A tool call names the file it writes the way the harness saw it,
/// usually as an absolute path. The route only takes a path relative to
/// the session's working folder (`cwd`), so the absolute path loses that
/// prefix here, and a file outside the folder gets no button at all: the
/// hub would refuse it anyway.
enum PagePreview {
    /// The file types the route serves, lowercased, without the dot.
    static let extensions: Set<String> = ["html", "htm", "svg"]

    /// Tools that write or edit a file, by lowercased name, across the
    /// harnesses the hub decodes (Claude Code, Codex, Gemini, opencode,
    /// Droid and the like).
    static let tools: Set<String> = [
        "write", "edit", "multiedit", "file_edit", "write_file", "edit_file",
        "create", "create_file", "replace", "str_replace_editor",
        "str_replace_based_edit_tool", "apply_patch", "patch",
    ]

    /// The keys a write tool names its file under.
    static let pathKeys = ["file_path", "filePath", "path", "filename", "target_file"]

    /// The route refuses longer paths.
    static let maxPathLength = 4096

    /// One page a Preview button opens: `path` is relative to the
    /// session's working folder, as the route takes it.
    struct Target: Identifiable, Hashable {
        let path: String
        var id: String { path }
        var name: String { (path as NSString).lastPathComponent }
    }

    /// The paths the route can serve for this call, relative to `cwd`,
    /// first-seen order, no repeats. Empty when the call writes no page,
    /// or writes one outside the folder.
    static func targets(of call: ToolCall, cwd: String?) -> [Target] {
        targets(files(of: call), cwd: cwd)
    }

    /// `files` (from `files(of:)`) as the route's targets.
    static func targets(_ files: [String], cwd: String?) -> [Target] {
        var seen = Set<String>()
        return files.compactMap { relative($0, to: cwd) }
            .filter { isPage($0) && seen.insert($0).inserted }
            .map(Target.init)
    }

    /// The page files a write tool call names, as it names them
    /// (absolute or relative).
    static func files(of call: ToolCall) -> [String] {
        guard tools.contains(call.name.lowercased()), let full = call.argsFull,
              // Every call that could match names a page file somewhere,
              // so most cards skip the JSON parse.
              mentionsPage(full),
              let args = try? JSONSerialization.jsonObject(with: Data(full.utf8), options: .fragmentsAllowed)
        else { return [] }
        if let patch = args as? String { return patchFiles(patch) }
        guard let dict = args as? [String: Any] else { return [] }
        // The text editor tool's `view` reads the file.
        if let command = dict["command"] as? String, command == "view" { return [] }
        var out: [String] = []
        for key in pathKeys {
            if let path = dict[key] as? String, !path.isEmpty { out.append(path); break }
        }
        for key in ["patch", "input", "diff"] {
            if let patch = dict[key] as? String { out += patchFiles(patch) }
        }
        return out.filter(isPage)
    }

    /// The page files a Codex-style patch adds or updates. An update
    /// that moves the file counts where it lands; a deleted file has
    /// nothing to show.
    static func patchFiles(_ patch: String) -> [String] {
        var out: [String] = []
        /// Where in `out` the update whose section this is went, so a
        /// `Move to` under it replaces the old name.
        var updated: Int?
        func path(_ line: String, after marker: String) -> String? {
            line.hasPrefix(marker) ? line.dropFirst(marker.count).trimmingCharacters(in: .whitespaces) : nil
        }
        for raw in patch.split(separator: "\n", omittingEmptySubsequences: true) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard line.hasPrefix("*** ") else { continue }
            if let p = path(line, after: "*** Add File: ") {
                updated = nil
                if isPage(p) { out.append(p) }
            } else if let p = path(line, after: "*** Update File: ") {
                updated = nil
                if isPage(p) { out.append(p); updated = out.count - 1 }
            } else if let p = path(line, after: "*** Move to: ") {
                if let i = updated { out.remove(at: i) }
                updated = nil
                if isPage(p) { out.append(p) }
            } else {
                updated = nil
            }
        }
        return out
    }

    /// The name ends in `.html`, `.htm` or `.svg`, any case.
    static func isPage(_ path: String) -> Bool {
        let name = (path as NSString).lastPathComponent
        guard let dot = name.lastIndex(of: "."), dot != name.startIndex else { return false }
        return extensions.contains(name[name.index(after: dot)...].lowercased())
    }

    private static func mentionsPage(_ s: String) -> Bool {
        s.range(of: ".htm", options: .caseInsensitive) != nil
            || s.range(of: ".svg", options: .caseInsensitive) != nil
    }

    /// `path` relative to the working folder `cwd`, or nil when the route
    /// couldn't serve it: outside the folder, a `..` part, a home-relative
    /// `~` path, a NUL, or an absolute path with no absolute `cwd` to
    /// strip. A relative path is taken as relative to `cwd` already.
    /// `/private/var` and `/private/tmp` match `/var` and `/tmp`, the same
    /// folders on macOS.
    static func relative(_ path: String, to cwd: String?) -> String? {
        let path = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !path.isEmpty, path.count <= maxPathLength, !path.contains("\0"), !path.hasPrefix("~"),
              let parts = components(path) else { return nil }
        guard path.hasPrefix("/") else {
            return parts.isEmpty ? nil : parts.joined(separator: "/")
        }
        guard let cwd = cwd?.trimmingCharacters(in: .whitespacesAndNewlines), cwd.hasPrefix("/"),
              let root = components(cwd) else { return nil }
        let file = unprivate(parts), base = unprivate(root)
        guard file.count > base.count, Array(file.prefix(base.count)) == base else { return nil }
        return file.dropFirst(base.count).joined(separator: "/")
    }

    /// A path's parts without empty and `.` ones; nil when one is `..`.
    private static func components(_ path: String) -> [String]? {
        let parts = path.split(separator: "/").map(String.init).filter { $0 != "." }
        return parts.contains("..") ? nil : parts
    }

    private static func unprivate(_ parts: [String]) -> [String] {
        guard parts.count >= 2, parts[0] == "private", ["var", "tmp", "etc"].contains(parts[1]) else { return parts }
        return Array(parts.dropFirst())
    }

    // MARK: Drawing

    /// A fetched page: the route's bytes and type.
    struct Page: Equatable {
        enum Kind { case html, svg }
        let path: String
        let body: Data
        let kind: Kind

        /// The hub types the answer from the file it read (a link keeps
        /// its target's type); without a type, the name decides.
        init(path: String, body: Data, contentType: String?) {
            self.path = path
            self.body = body
            let type = contentType?.lowercased() ?? ""
            if type.hasPrefix("image/svg") {
                kind = .svg
            } else if type.hasPrefix("text/html") {
                kind = .html
            } else {
                kind = path.lowercased().hasSuffix(".svg") ? .svg : .html
            }
        }

        /// The HTML the web view loads. A page is its own text. An SVG is
        /// drawn as an image (a `data:` URL in an `<img>`), where WebKit
        /// runs none of its scripts and loads none of its links whatever
        /// the web view allows, scaled down to the screen's width.
        var document: String {
            switch kind {
            case .html:
                return String(decoding: body, as: UTF8.self)
            case .svg:
                return """
                <!doctype html><html><head><meta charset="utf-8">\
                <meta name="viewport" content="width=device-width, initial-scale=1">\
                <style>html,body{margin:0;background:#fff}\
                img{display:block;max-width:100%;height:auto;margin:0 auto}</style></head>\
                <body><img alt="" src="data:image/svg+xml;base64,\(body.base64EncodedString())"></body></html>
                """
            }
        }
    }

    /// The web view's content rules: block every load, then let through
    /// the blank document the page is drawn into and inline `data:`
    /// images and fonts. A stylesheet, image or font from the network
    /// would tell a server the page was opened, and from where.
    static let contentRules = """
    [{"trigger":{"url-filter":".*"},"action":{"type":"block"}},\
    {"trigger":{"url-filter":"^about:"},"action":{"type":"ignore-previous-rules"}},\
    {"trigger":{"url-filter":"^data:"},"action":{"type":"ignore-previous-rules"}}]
    """

    /// Whether the web view may follow a navigation. Only the one load
    /// the sheet starts goes through: the main frame, to `about:blank`
    /// (what `loadHTMLString` with no base URL reports), once. Links,
    /// form posts, meta refreshes and frames are all refused.
    static func allowsNavigation(firstLoadPending: Bool, isMainFrame: Bool, url: URL?) -> Bool {
        firstLoadPending && isMainFrame && url?.absoluteString == "about:blank"
    }

    /// The plain message the sheet shows when the route says no.
    static func message(for error: Error) -> String {
        guard let e = error as? HubError else { return "Couldn't load the preview." }
        switch e.code {
        case "not_found":
            // A hub from before the route answers every unknown path so.
            return e.reason == "unknown route"
                ? "Update the hub to preview pages from the phone."
                : "That file isn't on disk any more."
        case "too_large": return "This file is too big to preview (the hub's limit is 2 MB)."
        case "unsupported_type": return "Only .html, .htm and .svg files preview."
        case "bad_path": return "That path can't be previewed."
        case "not_a_file": return "That path isn't a file."
        case "outside_folder": return "That file is outside the session's folder."
        case "not_readable": return "The hub can't read that file."
        case "preview_unavailable": return "Preview works only for sessions on the hub's own machine for now."
        case "cwd_missing": return "This session's folder is gone."
        case "bad_cwd": return "This session has no working folder."
        case "unknown_session": return "The hub doesn't know this session any more."
        case "network", "unauthorized": return e.message
        default:
            switch e.status {
            case 404: return "That file isn't on disk any more."
            case 413: return "This file is too big to preview."
            case 415: return "Only .html, .htm and .svg files preview."
            case 422: return "That path can't be previewed."
            default: return e.message
            }
        }
    }
}
