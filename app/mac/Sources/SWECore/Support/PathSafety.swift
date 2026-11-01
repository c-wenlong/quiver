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
/// component the path can land on is other-writable — following each
/// symlink hop, since a link's target is only as safe as the folder the
/// link sits in. Same-user and admin-group writes are left alone —
/// group-writable is how Homebrew installs /opt/homebrew, and an admin
/// peer is trusted for this anyway.
public enum PathSafety {
    /// Why a path fails the check, or nil when it is safe to trust.
    /// `path` may still carry `~`; it is expanded before the check.
    /// A missing component (a file the app will create) is fine — what
    /// matters is that nothing in or above it can be replaced.
    public static func rejection(of path: String,
                                 fileManager: FileManager = .default) -> String? {
        let expanded = (path as NSString).expandingTildeInPath
        guard expanded.hasPrefix("/") else { return "not an absolute path" }
        var visited = Set<String>()
        return rejectionInResolved(path: expanded, visited: &visited,
                                   fileManager: fileManager)
    }

    /// Walks `path` and its ancestors; where a component is a symlink its
    /// target is walked too. Checking only the written chain misses the
    /// folders a middle hop passes through: a 0755 link under ~ could
    /// land in another user's writable folder, and whoever owns that
    /// folder swaps where the path ends up between checks. Checking only
    /// the fully resolved destination misses them the same way, since
    /// realpath(3) jumps straight to the end of the hop chain.
    private static func rejectionInResolved(path: String, visited: inout Set<String>,
                                            fileManager: FileManager) -> String? {
        // A path already walked cannot fail again — this also ends link
        // cycles.
        guard visited.insert(path).inserted else { return nil }
        var component = path
        while true {
            let attributes = try? fileManager.attributesOfItem(atPath: component)
            if (attributes?[.type] as? FileAttributeType) == .typeSymbolicLink {
                // A link's own mode is ignored by the kernel — its target,
                // and every folder that target lives under, is what can
                // swap the outcome. readlink(2), not FileManager: firmlinks
                // like /tmp make destinationOfSymbolicLink answer the link
                // itself, which would walk in place.
                if let destination = readDestination(component) {
                    let resolved = destination.hasPrefix("/") ? destination
                        : ((component as NSString).deletingLastPathComponent as NSString)
                            .appendingPathComponent(destination)
                    if let why = rejectionInResolved(path: normalize(resolved),
                                                     visited: &visited,
                                                     fileManager: fileManager) {
                        return why
                    }
                }
            } else if let mode = attributes?[.posixPermissions] as? Int, mode & 0o002 != 0 {
                return "\(component) is writable by other users"
            }
            let parent = (component as NSString).deletingLastPathComponent
            if parent == component || parent.isEmpty { break }
            component = parent
        }
        return nil
    }

    /// `.`/`..` removed, links left spelled out: standardizingPath runs
    /// the platform's own resolution, which names firmlinks by their
    /// link (/private/tmp comes back /tmp) and would undo the hop.
    private static func normalize(_ path: String) -> String {
        var parts: [String] = []
        for part in path.split(separator: "/", omittingEmptySubsequences: true) {
            if part == ".." {
                if !parts.isEmpty { parts.removeLast() }
            } else if part != "." {
                parts.append(String(part))
            }
        }
        return "/" + parts.joined(separator: "/")
    }

    /// One hop of a symlink, or nil when it can't be read.
    private static func readDestination(_ path: String) -> String? {
        var buffer = [CChar](repeating: 0, count: Int(PATH_MAX))
        let length = readlink(path, &buffer, buffer.count)
        guard length > 0 else { return nil }
        return String(decoding: buffer[..<length].map { UInt8(bitPattern: $0) }, as: UTF8.self)
    }
}
