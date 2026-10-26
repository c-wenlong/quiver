import Foundation

/// Whether a path stored in the app's own preferences can be trusted to
/// name what the user picked. The Settings path overrides are readable
/// and writable by any process running as the user, but that alone
/// cannot hurt: a same-user writer can only point them at files the user
/// already controls. What it must not be able to do is aim them at a
/// file another user planted — under /tmp, a shared folder, anywhere the
/// "other" bits allow a write — which would turn a preference write into
/// code execution or a secret hand-off across users.
///
/// So the rule is about the path, not the preference: absolute, and no
/// component is other-writable, checked two ways. Same-user and
/// admin-group writes are left alone — group-writable is how Homebrew
/// installs /opt/homebrew, and an admin peer is trusted for this anyway.
public enum PathSafety {
    /// Why a path fails the check, or nil when it is safe to trust.
    /// `path` may still carry `~`; it is expanded before the check.
    /// A missing component (a file the app will create) is fine — what
    /// matters is that nothing in or above it can be replaced.
    public static func rejection(of path: String,
                                 fileManager: FileManager = .default) -> String? {
        let expanded = (path as NSString).expandingTildeInPath
        guard expanded.hasPrefix("/") else { return "not an absolute path" }

        func mode(_ path: String) -> Int? {
            try? fileManager.attributesOfItem(atPath: path)[.posixPermissions] as? Int
        }
        func writableByOthers(_ path: String) -> String? {
            guard let mode = mode(path), mode & 0o002 != 0 else { return nil }
            return "\(path) is writable by other users"
        }

        // The path as written: any other-writable folder in it could
        // swap the next entry for another user's file. A symlink's own
        // mode says nothing about its target — that is judged below.
        var component = expanded
        while true {
            if let why = writableByOthers(component) { return why }
            let parent = (component as NSString).deletingLastPathComponent
            if parent == component || parent.isEmpty { break }
            component = parent
        }

        // The path as resolved: attributesOfItem reports a link's own
        // mode, so /tmp reads 0755 while its target /private/tmp is
        // world-writable. realpath(3) only resolves what exists, so
        // climb to the deepest real component, then walk the canonical
        // chain it lands in.
        var deepest = expanded
        while !deepest.isEmpty, deepest != "/" {
            guard let buffer = realpath(deepest, nil) else {
                deepest = (deepest as NSString).deletingLastPathComponent
                continue
            }
            component = String(cString: buffer)
            free(buffer)
            while true {
                if let why = writableByOthers(component) { return why }
                let parent = (component as NSString).deletingLastPathComponent
                if parent == component || parent.isEmpty { break }
                component = parent
            }
            return nil
        }
        return nil
    }
}
