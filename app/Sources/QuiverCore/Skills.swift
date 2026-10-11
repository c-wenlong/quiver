import Foundation
import Observation

/// `swe skills --json`: every skill on the machine, and each harness's
/// skills folder with its link state.
public struct SkillInventory: Decodable, Sendable, Equatable {
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
public struct SkillRoot: Decodable, Sendable, Equatable, Identifiable {
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

public struct Skill: Decodable, Sendable, Equatable, Identifiable {
    public enum Source: String, Decodable, Sendable, CaseIterable {
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
public struct HubCatalog: Decodable, Sendable, Equatable {
    public let hub: String
    public let plugins: [HubPlugin]

    public init(hub: String, plugins: [HubPlugin]) {
        self.hub = hub
        self.plugins = plugins
    }
}

public struct HubPlugin: Decodable, Sendable, Equatable, Identifiable {
    public struct Skill: Decodable, Sendable, Equatable, Identifiable {
        public var id: String { path }
        public let name: String
        public let description: String
        public let path: String
    }

    public struct Install: Decodable, Sendable, Equatable {
        public enum State: String, Decodable, Sendable {
            case installed
            case unregistered
            case notInstalled = "not-installed"
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
                case .stale: "Out of date"
                case .disabledHere, .disabled: "Disabled"
                case .unknown: "Unknown"
                }
            }
        }

        public let state: State
        /// The shell command that closes the gap, nil when installed.
        public let fix: String?

        enum CodingKeys: String, CodingKey { case state, fix }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            state = (try? c.decode(State.self, forKey: .state)) ?? .unknown
            fix = try c.decodeIfPresent(String.self, forKey: .fix)
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
    public private(set) var inventory: Loadable<SkillInventory> = .idle
    public private(set) var hub: Loadable<HubCatalog> = .idle
    /// Roots with a link or unlink in flight, by path.
    public private(set) var busy: Set<String> = []
    /// The last link or unlink that failed, in swe's words.
    public var actionError: String?

    public var query = ""
    /// A harness name, or nil for every harness.
    public var harness: String?
    public var source: Skill.Source?

    private let swe: SweClient
    private let gate = SerialGate()
    private var generation = 0
    private var hubGeneration = 0

    public init(swe: SweClient) {
        self.swe = swe
    }

    public func load() async {
        generation += 1
        let mine = generation
        if case .loaded = inventory {} else { inventory = .loading }
        let next: Loadable<SkillInventory>
        do {
            next = .loaded(try await swe.skills())
        } catch is CancellationError {
            // Back to idle, so the next visit loads again instead of spinning.
            if mine == generation, inventory == .loading { inventory = .idle }
            return
        } catch {
            next = .failed(error.localizedDescription)
        }
        if mine == generation { inventory = next }
    }

    public func loadHub() async {
        hubGeneration += 1
        let mine = hubGeneration
        if case .loaded = hub {} else { hub = .loading }
        let next: Loadable<HubCatalog>
        do {
            next = .loaded(try await swe.skillsHub())
        } catch is CancellationError {
            // Leaving the tab cancels the load; idle lets the next visit retry.
            if mine == hubGeneration, hub == .loading { hub = .idle }
            return
        } catch {
            next = .failed(error.localizedDescription)
        }
        if mine == hubGeneration { hub = next }
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
        await load()
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
