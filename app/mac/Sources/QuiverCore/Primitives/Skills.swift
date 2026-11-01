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
        /// Points at `~/.swe/skills`.
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
        case "linked": "Reads the shared skills in ~/.swe/skills."
        case "create" where archived: "No skills folder yet. Archived in swe, so swe init leaves it alone; linking here creates it."
        case "create": "No skills folder yet. Linking creates it."
        case "relink": detail.isEmpty ? "Points somewhere else." : "Points somewhere else: \(detail.replacingOccurrences(of: "points at ", with: ""))."
        case "absorb": detail == "empty"
            ? "An empty folder. Linking replaces it."
            : "Holds only copies of shared skills (\(detail)). Linking backs it up and replaces it."
        case "keep": "Holds skills that exist nowhere else (\(detail)). Move them into ~/.swe/skills before linking."
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

extension SweClient {
    public func skills() async throws -> SkillInventory {
        try await call(["skills", "--json"])
    }

    /// Points the folder at `~/.swe/skills`. By path: some harnesses
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

    /// Writes ~/.swe/skills/<name>/SKILL.md. No force flag: an existing
    /// name refuses.
    public func newSkill(name: String, description: String) async throws -> NewSkillReply {
        var args = ["skills", "new", name]
        if !description.isEmpty { args.append("--description=\(description)") }
        args.append("--json")
        return try await call(args)
    }
}

struct LinkReply: Decodable { let action: String }

/// `swe skills new <name>`: the SKILL.md path it wrote.
public struct NewSkillReply: Decodable {
    public let name: String
    public let path: String
}

/// The Skills page: the skills that stand alone, and the per-harness links.
/// Skills that ship inside a plugin are listed with it on the Plugins page.
@MainActor
@Observable
public final class SkillsModel {
    public var inventory: Loadable<SkillInventory> { skills.state }
    /// Roots with a link or unlink in flight, by path.
    public private(set) var busy: Set<String> = []
    /// The last link or unlink that failed, in swe's words.
    public var actionError: String?
    /// The plugin catalog, which decides which skills move to the Plugins
    /// page. Until it is known the page waits, rather than flash plugin
    /// skills that are about to move.
    public var plugins: CatalogLookup = .pending {
        didSet { place() }
    }
    /// Which skills this page lists and which ship inside plugins; nil
    /// until both the inventory and the catalog are known.
    public private(set) var placement: SkillPlacement?

    public var query = ""
    /// A harness name, or nil for every harness.
    public var harness: String?
    public var source: Skill.Source?

    /// The swe chosen in Settings and its path; a new one is used from the next load.
    public private(set) var swe: SweClient
    private var sweSource: String?
    private let gate = SerialGate()
    private let skills: SnapshotLoader<SkillInventory>
    /// What `placement` was worked out from, or is being worked out from,
    /// so it is redone only on a change.
    private var placedFrom: (inventory: SkillInventory, plugins: CatalogLookup)?
    /// Places skills against a catalog off the main actor: that resolves
    /// every skill folder on disk.
    private let placing = LatestBuild<SkillPlacement>()

    /// - Parameters:
    ///   - snapshots: where the last skills are kept between launches.
    ///   - maxAge: how long a list counts as fresh, so a page opened again
    ///     soon after doesn't ask swe again.
    ///   - timeout: how long swe may take before a list counts as failed.
    public init(swe: SweClient, snapshots: (any SnapshotStoring<Snapshot<SkillInventory>>)? = nil,
                maxAge: TimeInterval = 60, timeout: Duration = .seconds(60),
                now: @escaping @Sendable () -> Date = Date.init) {
        self.swe = swe
        skills = SnapshotLoader(snapshots: snapshots, maxAge: maxAge, timeout: timeout, now: now)
    }

    /// Switches to the swe picked in Settings.
    public func use(_ swe: SweClient, for source: String) {
        self.swe = swe
        sweSource = source
    }

    /// Shows the last saved skills until swe answers.
    public func restore() async {
        await skills.restore()
        place()
        await placed()
    }

    /// Lists the skills unless this swe's list is fresh; joins a list in flight.
    /// - Parameter source: the swe asked, as set in Settings.
    public func loadIfStale(for source: String) async {
        let swe = swe
        await skills.loadIfStale(from: source) { try await swe.skills() }
        place()
        await placed()
    }

    /// Always lists. `source` nil keeps the last swe.
    public func load(for source: String? = nil) async {
        let swe = swe
        await skills.load(from: source) { try await swe.skills() }
        place()
        await placed()
    }

    /// Returns once `placement` matches the latest inventory and catalog.
    /// The loads wait for it themselves; this is for a caller that set
    /// `plugins`.
    public func placed() async {
        await placing.settled()
    }

    /// Works out which skills this page lists, once the inventory and the
    /// catalog are both known. Against a catalog that resolves every skill
    /// folder on disk, so it runs off the main actor and the last
    /// placement stays on show meanwhile.
    private func place() {
        guard case .loaded(let inventory) = inventory else {
            placing.cancel()
            placement = nil
            placedFrom = nil
            return
        }
        if let placedFrom, placedFrom.inventory == inventory, placedFrom.plugins == plugins {
            dropMissingFilters()
            return
        }
        switch plugins {
        case .pending:
            placing.cancel()
            placement = nil
            placedFrom = nil
        // swe couldn't read the catalog: every skill stays here, so none
        // is lost. Nothing to resolve, so no need to leave the main actor.
        case .unavailable:
            placing.cancel()
            placement = SkillPlacement(skills: inventory.skills, catalog: nil)
            placedFrom = (inventory, plugins)
            dropMissingFilters()
        case .ready(let catalog):
            placedFrom = (inventory, plugins)
            let skills = inventory.skills
            placing.start({ SkillPlacement(skills: skills, catalog: catalog) }) { [weak self] placement in
                self?.placement = placement
                self?.dropMissingFilters()
            }
        }
    }

    /// Clears a harness or source filter the list on show no longer has,
    /// which the menus can't offer and which would hide every row.
    private func dropMissingFilters() {
        guard placement != nil else { return }
        if let harness, !listedSkills.contains(where: { $0.harnesses.contains(harness) }) { self.harness = nil }
        if let source, !listedSkills.contains(where: { $0.source == source }) { self.source = nil }
    }

    public func link(_ root: SkillRoot) async {
        guard root.link == .notLinked else { return }
        _ = await change(root) { try await $0.link(root) }
    }

    public func unlink(_ root: SkillRoot) async {
        guard root.link == .linked else { return }
        _ = await change(root) { try await $0.unlink(root) }
    }

    /// What "Link all" can safely take on: loose folders that cost nothing
    /// to point at the shared root. `skipped` holds the ones a person
    /// should look at (a folder with unique skills, an explicit keep, a
    /// conflict) or that swe would only touch deliberately (archived).
    public static func linkAllTargets(_ roots: [SkillRoot]) -> (link: [SkillRoot], skipped: [SkillRoot]) {
        var link: [SkillRoot] = []
        var skipped: [SkillRoot] = []
        for root in roots {
            switch (root.link, root.archived) {
            case (.notLinked, false) where !root.linkNeedsForce:
                link.append(root)
            case (.notLinked, false):
                skipped.append(root)
            case (.blocked, _):
                // keep/conflict mean the folder holds work only a person
                // can judge; name it so the summary can ask for a look.
                if root.state == "keep" || root.state == "conflict" {
                    skipped.append(root)
                }
            default:
                continue
            }
        }
        return (link, skipped)
    }

    /// What the last Link all left behind, shown as a caption.
    public private(set) var linkAllSummary: String?

    /// Creates a skill in the shared root through the same one-at-a-time
    /// path as a link, then reloads so it shows in the list.
    public func create(name: String, description: String) async throws -> NewSkillReply {
        let (swe, source) = (swe, sweSource)
        let reply = try await swe.newSkill(name: name, description: description)
        await reload(through: swe, source)
        return reply
    }

    /// Links every root `linkAllTargets` clears, one at a time through
    /// `link`; failures land in the summary without stopping the rest.
    public func linkAll() async {
        let (targets, skipped) = SkillsModel.linkAllTargets(roots)
        var linked = 0
        var failures: [String] = []
        for root in targets {
            let message = await change(root) { try await $0.link(root) }
            if let message {
                failures.append("\(root.name): \(message)")
            } else {
                linked += 1
            }
        }
        var parts: [String] = []
        if linked > 0 || failures.isEmpty { parts.append("Linked \(linked) \(linked == 1 ? "harness" : "harnesses").") }
        if !failures.isEmpty { parts.append(failures.joined(separator: "; ")) }
        if !skipped.isEmpty {
            let names = skipped.map { "\($0.name) (\($0.explanation))" }.joined(separator: ", ")
            parts.append("\(skipped.count) \(skipped.count == 1 ? "needs" : "need") a look: \(names)")
        }
        linkAllSummary = parts.joined(separator: " ")
    }

    /// The failure's message on error, nil on success. Cancels quietly.
    private func change(_ root: SkillRoot,
                        _ work: (SweClient) async throws -> Void) async -> String? {
        guard !busy.contains(root.path) else { return nil }
        let (swe, source) = (swe, sweSource)
        busy.insert(root.path)
        defer { busy.remove(root.path) }
        // swe rewrites skill_links.json on every link; one at a time.
        await gate.acquire()
        defer { gate.release() }
        actionError = nil
        var failure: String?
        do {
            try await work(swe)
        } catch is CancellationError {
            return nil
        } catch {
            failure = error.localizedDescription
            actionError = failure
        }
        // Reload either way: a failed change may still have moved something.
        await reload(through: swe, source)
        return failure
    }

    /// Lists again after a change made through `swe`, unless another swe
    /// has been picked since, page open or not: its list is the one to keep.
    private func reload(through swe: SweClient, _ source: String?) async {
        guard let source else {
            await load()
            return
        }
        guard source == sweSource else { return }
        await skills.reload(from: source) { try await swe.skills() }
        place()
        await placed()
    }

    public var roots: [SkillRoot] {
        guard case .loaded(let inventory) = inventory else { return [] }
        return inventory.harnesses
    }

    /// Every skill on the Mac, plugin skills included.
    public var allSkills: [Skill] {
        guard case .loaded(let inventory) = inventory else { return [] }
        return inventory.skills
    }

    /// The skills this page lists: every one not shipped inside a plugin.
    public var listedSkills: [Skill] { placement?.standalone ?? [] }

    /// How many skills ship inside plugins, listed on the Plugins page.
    public var pluginSkillCount: Int { placement?.inPlugins ?? 0 }

    /// The listed skills matching the harness, source and search text: your
    /// own library first, then what harnesses and catalogs bring.
    public var visibleSkills: [Skill] {
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        let rank: [Skill.Source: Int] = [.shared: 0, .harness: 1, .catalog: 2, .project: 3, .plugin: 4, .builtin: 5]
        return listedSkills.filter { skill in
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
}
