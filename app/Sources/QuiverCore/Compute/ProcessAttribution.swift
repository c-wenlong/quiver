import Foundation

/// How to tell one harness's own processes from everyone else's: its app
/// bundle, the names its CLI goes by, and its config home under ~.
public struct HarnessMatcher: Sendable, Equatable {
    public var harness: String
    /// e.g. "/Applications/ChatGPT.app", from AppLocator.
    public var appPath: String?
    /// The registry command's basename plus its aliases.
    public var commands: Set<String>
    /// "<home>/.<harness>", e.g. ~/.codex.
    public var homeFolder: String

    public init(harness: String, appPath: String? = nil, commands: Set<String>, homeFolder: String) {
        self.harness = harness
        self.appPath = appPath
        self.commands = commands
        self.homeFolder = homeFolder
    }
}

/// Sorts a process table into the harnesses the matchers describe, and
/// into trees of orphaned helper processes.
public enum ProcessAttribution {
    /// Interpreted languages a harness CLI can be a script of:
    /// `node /opt/homebrew/lib/.../claude` runs claude under node.
    static let scriptRunners: Set<String> = ["node", "bun", "deno", "python", "python3"]

    /// Whether `p` itself is one of the harness's own processes: it runs out
    /// of the harness's app bundle, its executable is the harness's command
    /// or an alias, it runs out of the harness's home folder, or it is a
    /// script runner running the harness's script.
    static func isRoot(_ p: ProcessSample, of m: HarnessMatcher) -> Bool {
        if let appPath = m.appPath, !appPath.isEmpty, p.path.hasPrefix(appPath + "/") { return true }
        if m.commands.contains((p.path as NSString).lastPathComponent) { return true }
        if !m.homeFolder.isEmpty, p.path.hasPrefix(m.homeFolder + "/") { return true }
        if Self.scriptRunners.contains(p.runnerName), p.arguments.count > 1,
           m.commands.contains((p.arguments[1] as NSString).lastPathComponent) {
            return true
        }
        return false
    }

    /// Whether `p` is the harness's entry process itself: the app bundle's
    /// own executable straight under Contents/MacOS (not a helper inside a
    /// nested bundle), its CLI command or an alias, or a script runner
    /// running the harness's script. A helper that merely lives under the
    /// harness's home folder is not the main process.
    static func isMainProcess(_ p: ProcessSample, of m: HarnessMatcher) -> Bool {
        if let appPath = m.appPath, !appPath.isEmpty,
           (p.path as NSString).deletingLastPathComponent == appPath + "/Contents/MacOS" {
            return true
        }
        if m.commands.contains((p.path as NSString).lastPathComponent) { return true }
        if Self.scriptRunners.contains(p.runnerName), p.arguments.count > 1,
           m.commands.contains((p.arguments[1] as NSString).lastPathComponent) {
            return true
        }
        return false
    }

    /// Each process goes to the harness of its nearest self-or-ancestor
    /// root; what is left and hangs from pid 1 under a script runner is an
    /// orphan tree. Anything else is dropped, so nothing unrelated is ever
    /// listed (or quittable). `managedPIDs` are launchd services: they
    /// also hang under pid 1 but are not leftovers.
    public static func attribute(_ samples: [ProcessSample], matchers: [HarnessMatcher],
                                 managedPIDs: Set<Int32> = []) -> Attribution {
        let byPid = Dictionary(samples.map { ($0.pid, $0) }, uniquingKeysWith: { first, _ in first })
        var byHarness: [String: [ProcessSample]] = [:]
        var rest: [ProcessSample] = []
        for p in samples {
            if let harness = harness(of: p, byPid: byPid, matchers: matchers) {
                byHarness[harness, default: []].append(p)
            } else {
                rest.append(p)
            }
        }

        // An orphan tree's root answered to pid 1 itself. Anything launchd
        // manages looks the same but is a service, not a leftover: it sits
        // in "/" or has no working directory, or launchd lists its pid.
        var roots: [Int32: ProcessSample] = [:]
        for p in rest where p.ppid == 1 && OrphanTree.runners.contains(p.runnerName) {
            if let cwd = p.cwd, cwd != "/", !managedPIDs.contains(p.pid) {
                roots[p.pid] = p
            }
        }
        var trees: [Int32: [ProcessSample]] = [:]
        for p in rest where roots[p.pid] == nil {
            var node = p
            for _ in 0..<64 {
                if roots[node.pid] != nil {
                    trees[node.pid, default: []].append(p)
                    break
                }
                guard let parent = byPid[node.ppid], parent.pid != node.pid else { break }
                node = parent
            }
        }
        let orphans = roots.values
            .sorted { $0.pid < $1.pid }
            .map { OrphanTree(root: $0, descendants: trees[$0.pid] ?? []) }
        return Attribution(byHarness: byHarness, orphans: orphans)
    }

    /// The harness of `p`'s nearest self-or-ancestor root: itself first,
    /// then its parent, and so on to pid 1 (or a process the sample does
    /// not know, e.g. one owned by another user).
    private static func harness(of p: ProcessSample, byPid: [Int32: ProcessSample],
                                matchers: [HarnessMatcher]) -> String? {
        var node = p
        for _ in 0..<64 {
            if let match = matchers.first(where: { isRoot(node, of: $0) }) { return match.harness }
            guard node.pid > 1, let parent = byPid[node.ppid], parent.pid != node.pid else { return nil }
            node = parent
        }
        return nil
    }
}

public struct Attribution: Sendable, Equatable {
    /// The harness's processes, self or descendant of one of its roots.
    public var byHarness: [String: [ProcessSample]]
    public var orphans: [OrphanTree]

    public init(byHarness: [String: [ProcessSample]], orphans: [OrphanTree]) {
        self.byHarness = byHarness
        self.orphans = orphans
    }
}

/// A script-runner process whose parent is gone, and everything that hangs
/// under it: usually a dev server or an MCP server an agent left behind.
public struct OrphanTree: Sendable, Equatable, Identifiable {
    public var id: Int32 { root.pid }
    public var root: ProcessSample
    public var descendants: [ProcessSample]

    public static let runners: Set<String> = [
        "node", "npm", "npx", "pnpm", "yarn", "bun", "deno",
        "python", "python3", "uv", "uvx", "tsx", "vite",
    ]

    public var memoryBytes: UInt64 {
        descendants.reduce(root.memoryBytes) { $0 + $1.memoryBytes }
    }
}
