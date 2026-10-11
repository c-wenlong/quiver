import Foundation
import Observation

/// `swe skills --json`: every skill on the machine, and each harness's
/// skills folder with its link state.
public struct SkillInventory: SnapshotValue {
    public static let snapshotVersion = 1

    public let sharedDir: String
    public let harnesses: [SkillRoot]
    public let skills: [Skill]

    public init(sharedDir: String, harnesses: [SkillRoot], skills: [Skill]) {
        self.sharedDir = sharedDir
        self.harnesses = harnesses
        self.skills = skills
    }
}

/// The one folder a harness reads skills from. quiver links whole folders,
/// so this is where linking and unlinking happen.
public struct SkillRoot: Codable, Sendable, Equatable, Identifiable {
    /// What the page can do about a folder.
    public enum Link: Sendable, Equatable {
        /// Points at `~/.quiver/skills`.
        case linked
        /// Missing, empty, holding only copies of shared skills, or pointing
        /// somewhere else: linking it loses nothing.
        case notLinked
        /// Holds skills that exist nowhere else, or is ignored on purpose
        /// (`.linkignore`, not a coding harness). Quiver will not link it.
        case blocked
    }

    public var id: String { path }
    /// Registry name, so it joins `swe list --json` on `name`.
    public let name: String
    public let path: String
    /// `swe init`'s vocabulary: linked, relink, create, absorb, keep,
    /// ignored, skipped.
    public let state: String
    public let detail: String
    /// Archived in harness.json. `swe init` leaves such a folder alone; swe
    /// still reports what it holds, so a click here can link it.
    public let archived: Bool

    public init(name: String, path: String, state: String, detail: String = "", archived: Bool = false) {
        self.name = name
        self.path = path
        self.state = state
        self.detail = detail
        self.archived = archived
    }

    enum CodingKeys: String, CodingKey { case name, path, state, detail, archived }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        path = try c.decode(String.self, forKey: .path)
        state = try c.decode(String.self, forKey: .state)
        detail = try c.decodeIfPresent(String.self, forKey: .detail) ?? ""
        archived = try c.decodeIfPresent(Bool.self, forKey: .archived) ?? false
    }

    public var link: Link {
        switch state {
        case "linked": .linked
        case "create", "relink", "absorb": .notLinked
        default: .blocked
        }
    }

    /// A folder holding copies of shared skills is replaced only with
    /// `--force`, which backs it up first. An empty one needs nothing.
    public var linkNeedsForce: Bool { state == "absorb" && detail != "empty" }

    /// One sentence on why the folder is in its state.
    public var explanation: String {
        switch state {
        case "linked" where archived: "Reads the shared skills. Archived in swe, so swe init won't relink it if you unlink it, but you can link it here."
        case "linked": "Reads the shared skills in ~/.quiver/skills."
        case "create" where archived: "No skills folder yet. Archived in swe, so swe init leaves it alone; linking here creates it."
        case "create": "No skills folder yet. Linking creates it."
        case "relink": detail.isEmpty ? "Points somewhere else." : "Points somewhere else: \(detail.replacingOccurrences(of: "points at ", with: ""))."
        case "absorb": detail == "empty"
            ? "An empty folder. Linking replaces it."
            : "Holds only copies of shared skills (\(detail)). Linking backs it up and replaces it."
        case "keep": "Holds skills that exist nowhere else (\(detail)). Move them into ~/.quiver/skills before linking."
        case "ignored": detail.isEmpty ? "Ignored by swe init." : "Ignored: \(detail)."
        case "skipped": "The harness isn't installed."
        default: detail.isEmpty ? state : "\(state): \(detail)"
        }
    }
}

public struct Skill: Codable, Sendable, Equatable, Identifiable {
    public enum Source: String, Codable, Sendable, CaseIterable {
        case shared, plugin, harness, builtin, catalog, project

        public var title: String {
            switch self {
            case .shared: "Shared"
            case .plugin: "Plugin"
            case .harness: "Harness"
            case .builtin: "Built in"
            case .catalog: "Catalog"
            case .project: "Project"
            }
        }
    }

    public var id: String { path }
    public let name: String
    public let description: String
    /// The skill's folder.
    public let path: String
    /// The root label it was found under, like `shared` or `claude-plugin`.
    public let root: String
    public let source: Source
    /// Harnesses that load it.
    public let harnesses: [String]

    public init(name: String, description: String = "", path: String, root: String,
                source: Source, harnesses: [String]) {
        self.name = name
        self.description = description
        self.path = path
        self.root = root
        self.source = source
        self.harnesses = harnesses
    }

    enum CodingKeys: String, CodingKey { case name, description, path, root, source, harnesses }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        description = try c.decodeIfPresent(String.self, forKey: .description) ?? ""
        path = try c.decode(String.self, forKey: .path)
        root = try c.decode(String.self, forKey: .root)
        // A source a newer swe adds reads as the plainest one, not a failure.
        source = (try? c.decode(Source.self, forKey: .source)) ?? .harness
        harnesses = try c.decodeIfPresent([String].self, forKey: .harnesses) ?? []
    }
}

/// `swe skills hub --json`: the plugins in the `~/.quiver/plugins`
/// marketplaces, and whether each harness has them.
public struct HubCatalog: SnapshotValue {
    public static let snapshotVersion = 1

    public let hub: String
    public let plugins: [HubPlugin]

    public init(hub: String, plugins: [HubPlugin]) {
        self.hub = hub
        self.plugins = plugins
    }
}

public struct HubPlugin: Codable, Sendable, Equatable, Identifiable {
    public struct Skill: Codable, Sendable, Equatable, Identifiable {
        public var id: String { path }
        public let name: String
        public let description: String
        public let path: String
    }

    public struct Install: Codable, Sendable, Equatable {
        public enum State: String, Codable, Sendable {
            case installed
            case unregistered
            case notInstalled = "not-installed"
            /// The source's `.codex-plugin` is a symlink, which Codex skips.
            case linkedManifest = "linked-manifest"
            case stale
            case disabledHere = "disabled-here"
            /// Disabled in every harness that has it.
            case disabled
            /// A state from a newer swe.
            case unknown

            public var title: String {
                switch self {
                case .installed: "Installed"
                case .unregistered: "Marketplace not added"
                case .notInstalled: "Not installed"
                case .linkedManifest: "Linked manifest"
                case .stale: "Out of date"
                case .disabledHere, .disabled: "Disabled"
                case .unknown: "Unknown"
                }
            }
        }

        public let state: State
        /// The shell command that closes the gap, nil when installed.
        public let fix: String?
        /// What `swe find plugins --fix=` takes, when swe can run the fix
        /// itself. Nil for a fix that is an edit by hand.
        public let id: String?

        enum CodingKeys: String, CodingKey { case state, fix, id }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            state = (try? c.decode(State.self, forKey: .state)) ?? .unknown
            fix = try c.decodeIfPresent(String.self, forKey: .fix)
            id = try c.decodeIfPresent(String.self, forKey: .id)
        }
    }

    public var id: String { "\(name)@\(marketplace)" }
    public let name: String
    public let marketplace: String
    public let description: String
    public let version: String?
    public let path: String
    public let components: [String: Int]
    public let skills: [Skill]
    /// Keyed by harness (`claude`, `codex`), only those on this machine.
    public let install: [String: Install]

    /// Harness names in a fixed order, so the footer never shuffles.
    public var harnesses: [String] { install.keys.sorted() }

    /// Every fix this plugin needs, one per line.
    public var fixes: [String] { harnesses.compactMap { install[$0]?.fix } }

    /// The gaps swe can close itself, in harness order.
    public var runnableFixes: [Install] {
        harnesses.compactMap { install[$0] }.filter { $0.id != nil && $0.fix != nil }
    }

    /// The gaps left for you to close by hand.
    public var manualFixes: [String] {
        harnesses.compactMap { install[$0] }.filter { $0.id == nil }.compactMap(\.fix)
    }
}

extension SweClient {
    public func skills() async throws -> SkillInventory {
        try await call(["skills", "--json"])
    }

    public func skillsHub() async throws -> HubCatalog {
        try await call(["skills", "hub", "--json"])
    }

    /// Points the folder at `~/.quiver/skills`. By path: some harnesses
    /// have no label `swe skills link` knows.
    public func link(_ root: SkillRoot) async throws {
        var args = ["skills", "link", root.path]
        if root.linkNeedsForce { args.append("--force") }
        args.append("--json")
        let _: LinkReply = try await call(args)
    }

    public func unlink(_ root: SkillRoot) async throws {
        let _: LinkReply = try await call(["skills", "unlink", root.path, "--json"])
    }
}

struct LinkReply: Decodable { let action: String }

/// The Skills page: installed skills, the per-harness links, and the hub.
@MainActor
@Observable
public final class SkillsModel {
    public var inventory: Loadable<SkillInventory> { skills.state }
    public var hub: Loadable<HubCatalog> { hubLoader.state }
    /// Roots with a link or unlink in flight, by path.
    public private(set) var busy: Set<String> = []
    /// The last link or unlink that failed, in swe's words.
    public var actionError: String?
    /// The hub plugin whose fixes are running, by id.
    public private(set) var fixingPlugin: String?
    /// The last hub fix that failed, in swe's words.
    public var hubError: String?

    public var query = ""
    /// A harness name, or nil for every harness.
    public var harness: String?
    public var source: Skill.Source?

    /// The swe chosen in Settings and its path; a new one is used from the next load.
    public private(set) var swe: SweClient
    private var sweSource: String?
    private let gate = SerialGate()
    private let skills: SnapshotLoader<SkillInventory>
    private let hubLoader: SnapshotLoader<HubCatalog>

    /// - Parameters:
    ///   - snapshots, hubSnapshots: where the last skills and hub are kept
    ///     between launches.
    ///   - maxAge: how long a list counts as fresh, so a page opened again
    ///     soon after doesn't ask swe again.
    ///   - timeout: how long swe may take before a list counts as failed.
    public init(swe: SweClient, snapshots: (any SnapshotStoring<Snapshot<SkillInventory>>)? = nil,
                hubSnapshots: (any SnapshotStoring<Snapshot<HubCatalog>>)? = nil, maxAge: TimeInterval = 60,
                timeout: Duration = .seconds(60), now: @escaping @Sendable () -> Date = Date.init) {
        self.swe = swe
        skills = SnapshotLoader(snapshots: snapshots, maxAge: maxAge, timeout: timeout, now: now)
        hubLoader = SnapshotLoader(snapshots: hubSnapshots, maxAge: maxAge, timeout: timeout, now: now)
    }

    /// Switches to the swe picked in Settings.
    public func use(_ swe: SweClient, for source: String) {
        self.swe = swe
        sweSource = source
    }

    /// Shows the last saved skills and hub until swe answers.
    public func restore() async {
        await skills.restore()
        await hubLoader.restore()
    }

    /// Lists the skills unless this swe's list is fresh; joins a list in flight.
    /// - Parameter source: the swe asked, as set in Settings.
    public func loadIfStale(for source: String) async {
        let swe = swe
        await skills.loadIfStale(from: source) { try await swe.skills() }
    }

    public func loadHubIfStale(for source: String) async {
        let swe = swe
        await hubLoader.loadIfStale(from: source) { try await swe.skillsHub() }
    }

    /// Always lists. `source` nil keeps the last swe.
    public func load(for source: String? = nil) async {
        let swe = swe
        await skills.load(from: source) { try await swe.skills() }
    }

    public func loadHub(for source: String? = nil) async {
        let swe = swe
        await hubLoader.load(from: source) { try await swe.skillsHub() }
    }

    public func link(_ root: SkillRoot) async {
        guard root.link == .notLinked else { return }
        await change(root) { try await $0.link(root) }
    }

    public func unlink(_ root: SkillRoot) async {
        guard root.link == .linked else { return }
        await change(root) { try await $0.unlink(root) }
    }

    private func change(_ root: SkillRoot, _ work: (SweClient) async throws -> Void) async {
        guard !busy.contains(root.path) else { return }
        let (swe, source) = (swe, sweSource)
        busy.insert(root.path)
        defer { busy.remove(root.path) }
        // swe rewrites skill_links.json on every link; one at a time.
        await gate.acquire()
        defer { gate.release() }
        actionError = nil
        do {
            try await work(swe)
        } catch is CancellationError {
            return
        } catch {
            actionError = error.localizedDescription
        }
        // Reload either way: a failed change may still have moved something.
        await reload(through: swe, source)
    }

    /// Lists again after a change made through `swe`, unless another swe
    /// has been picked since: its list is the one to keep.
    private func reload(through swe: SweClient, _ source: String?, hub: Bool = false) async {
        guard let source else {
            await load()
            if hub { await loadHub() }
            return
        }
        await skills.reload(from: source) { try await swe.skills() }
        if hub { await hubLoader.reload(from: source) { try await swe.skillsHub() } }
    }

    /// Runs every fix swe can run for `plugin`, one at a time, then reloads
    /// the skills and the hub. swe checks each gap still exists and builds the commands; the
    /// app sends only the finding id. Call after the user has confirmed.
    public func fixHub(_ plugin: HubPlugin) async {
        let fixes = plugin.runnableFixes
        guard fixingPlugin == nil, !fixes.isEmpty else { return }
        fixingPlugin = plugin.id
        defer { fixingPlugin = nil }
        let (swe, source) = (swe, sweSource)
        hubError = nil
        await gate.acquire()
        do {
            for fix in fixes {
                guard let id = fix.id else { continue }
                try await swe.fixPluginDrift(id: id)
            }
        } catch is CancellationError {
            gate.release()
            return
        } catch {
            hubError = error.localizedDescription
        }
        gate.release()
        // A fix can add, update or enable a plugin's skills, so the Installed
        // tab is stale too. Reload both, failure or not: a failed run may
        // still have changed files.
        await reload(through: swe, source, hub: true)
    }

    public var roots: [SkillRoot] {
        guard case .loaded(let inventory) = inventory else { return [] }
        return inventory.harnesses
    }

    public var allSkills: [Skill] {
        guard case .loaded(let inventory) = inventory else { return [] }
        return inventory.skills
    }

    /// The skills matching the harness, source and search text: your own
    /// library first, then what harnesses, catalogs and plugins bring.
    public var visibleSkills: [Skill] {
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        let rank: [Skill.Source: Int] = [.shared: 0, .harness: 1, .catalog: 2, .project: 3, .plugin: 4, .builtin: 5]
        return allSkills.filter { skill in
            (harness == nil || skill.harnesses.contains(harness!))
                && (source == nil || skill.source == source)
                && (needle.isEmpty
                    || skill.name.lowercased().contains(needle)
                    || skill.description.lowercased().contains(needle)
                    || skill.root.lowercased().contains(needle))
        }
        .enumerated()
        .sorted { a, b in
            let (ra, rb) = (rank[a.element.source] ?? 9, rank[b.element.source] ?? 9)
            return ra != rb ? ra < rb : a.offset < b.offset
        }
        .map(\.element)
    }

    /// For one skill, each harness folder and whether it loads the skill.
    /// Only a shared skill can be reached by linking; anything else belongs
    /// to the harnesses that already see it.
    public func chips(for skill: Skill) -> [(root: SkillRoot, link: SkillRoot.Link)] {
        let seeing = Set(skill.harnesses)
        if skill.source != .shared {
            return roots.filter { seeing.contains($0.name) }.map { ($0, .linked) }
        }
        return roots.map { root in
            if seeing.contains(root.name) { return (root, .linked) }
            return (root, root.link == .blocked ? .blocked : .notLinked)
        }
    }

    public var hubPlugins: [HubPlugin] {
        guard case .loaded(let hub) = hub else { return [] }
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !needle.isEmpty else { return hub.plugins }
        return hub.plugins.filter { plugin in
            plugin.name.lowercased().contains(needle)
                || plugin.marketplace.lowercased().contains(needle)
                || plugin.description.lowercased().contains(needle)
                || plugin.skills.contains { $0.name.lowercased().contains(needle) }
        }
    }
}
