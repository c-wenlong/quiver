import Foundation
import Observation

/// What `swe setup --json` found: harnesses to register, MCP servers to
/// import, and what linking the shared AGENTS.md would do.
public struct SetupPreview: Decodable, Sendable, Equatable {
    public struct Harness: Decodable, Sendable, Equatable {
        public let name: String
    }

    public struct Server: Decodable, Sendable, Equatable {
        public let name: String
    }

    public struct LinkRow: Decodable, Sendable, Equatable {
        /// "instructions", "skills" or "hooks".
        public let kind: String
        public let harness: String
        public let path: String
        /// A result word: linked, blocked, protected, skipped… In a preview
        /// a change reads "would-create", "would-absorb" and so on.
        public let result: String
        public let detail: String

        public init(kind: String, harness: String, path: String, result: String, detail: String = "") {
            self.kind = kind
            self.harness = harness
            self.path = path
            self.result = result
            self.detail = detail
        }

        /// A real file or folder setup leaves where it is.
        public var isLeftAlone: Bool {
            ["blocked", "protected", "conflict", "keep", "would-conflict", "would-keep"].contains(result)
        }
    }

    /// A real instruction file that could start the shared AGENTS.md.
    public struct Candidate: Decodable, Sendable, Equatable, Hashable {
        public let harness: String
        public let path: String

        public init(harness: String, path: String) {
            self.harness = harness
            self.path = path
        }
    }

    public struct Instructions: Decodable, Sendable, Equatable {
        public let agentsFile: String
        public let agentsFileExists: Bool
        /// AGENTS.md holds the user's own text, so nothing can be adopted.
        public let agentsFileCustomised: Bool
        public let rows: [LinkRow]
        public let adoptCandidates: [Candidate]

        public init(agentsFile: String, agentsFileExists: Bool, agentsFileCustomised: Bool,
                    rows: [LinkRow], adoptCandidates: [Candidate]) {
            self.agentsFile = agentsFile
            self.agentsFileExists = agentsFileExists
            self.agentsFileCustomised = agentsFileCustomised
            self.rows = rows
            self.adoptCandidates = adoptCandidates
        }
    }

    /// `swe setup --json`'s `hub`: whether `~/.swe` can still take a
    /// clone of the user's hub repo, and what `--hub` just cloned.
    public struct Hub: Decodable, Sendable, Equatable {
        public struct Marketplace: Decodable, Sendable, Hashable {
            public let harness: String
            public let marketplace: String
            /// Why it couldn't be registered; nil when it was.
            public let error: String?
        }

        public struct Cloned: Decodable, Sendable, Equatable {
            /// Without credentials: swe strips them before printing.
            public let url: String
            public let path: String
            public let marketplaces: [Marketplace]
        }

        /// No hub in `~/.swe` yet (no AGENTS.md, no .git), so the sheet
        /// offers its hub URL field.
        public let canClone: Bool
        /// The public demo hub to fork; nil until it exists.
        public let demoUrl: String?
        public let cloned: Cloned?
    }

    public let harness: [Harness]
    public let mcp: [Server]
    /// Missing from an swe older than the setup sheet.
    public let instructions: Instructions?
    /// Missing from an swe older than `swe setup --hub`.
    public let hub: Hub?
}

/// What `swe setup --apply --json` did.
public struct SetupOutcome: Decodable, Sendable, Equatable {
    public struct Starred: Decodable, Sendable, Equatable {
        public let starred: [String]
        public let registered: [String]
        public let unknown: [String]
    }

    public struct Links: Decodable, Sendable, Equatable {
        public let adopted: String?
        public let error: String?
        public let rows: [SetupPreview.LinkRow]?
    }

    public let harness: [String]
    public let mcp: [String]
    public let starred: Starred?
    public let links: Links?
    /// Apply steps that failed part way ("harness: …", "mcp: …"). The run
    /// exits 1 but still emits the envelope, so without this field a
    /// half-applied setup would read as success here.
    public let errors: [String]?

    /// Picks swe has no registry or catalog entry for, so it couldn't
    /// star them (Settings › Providers knows a few more than swe does).
    public var unstarred: [String] { starred?.unknown ?? [] }

    /// Paths setup left as they were, so the user can deal with them.
    public var leftAlone: [SetupPreview.LinkRow] {
        (links?.rows ?? []).filter(\.isLeftAlone)
    }

    /// Something the user should read before moving on.
    public var needsReading: Bool { !leftAlone.isEmpty || !unstarred.isEmpty }
}

private struct SetupApplied: Decodable { let applied: SetupOutcome }

extension SweClient {
    /// `swe setup --json`: a preview, nothing written.
    public func setupPreview() async throws -> SetupPreview {
        try await call(["setup", "--json", "--non-interactive"])
    }

    /// What `swe setup --hub` may take: `git ls-remote` (30 s), the clone
    /// (600 s), then registering the hub's marketplaces.
    static let hubCloneTimeout: Duration = .seconds(720)

    /// `swe setup --hub=<url> --json --non-interactive`: clone the user's
    /// hub repo as `~/.swe`, then the preview of what setup would do
    /// with it. A refusal (a hub already there, a URL git can't reach, a
    /// timeout) exits 1 with the reason on stderr and no document, which the
    /// error carries. A clone that worked but whose preview then failed also
    /// exits 1, with the document (its `hub.cloned` set) still on stdout, so
    /// that one is read rather than reported as a failed clone.
    public func cloneHub(url: String) async throws -> SetupPreview {
        try await call(["setup", "--hub=" + url, "--json", "--non-interactive"],
                       allowFailure: true, timeout: Self.hubCloneTimeout)
    }

    /// `swe setup --apply --json`: register what was found, import MCP
    /// servers, link AGENTS.md and skills, and star exactly `starred`.
    /// `adopt` starts the shared AGENTS.md from that file.
    /// `starred` nil leaves the stars alone; an array makes them exactly
    /// that list, so an empty one unstars everything.
    public func applySetup(starred: [String]?, adopt: String?) async throws -> SetupOutcome {
        var arguments = ["setup", "--apply", "--json"]
        if let starred { arguments.append("--starred=" + starred.joined(separator: ",")) }
        if let adopt { arguments.append("--adopt=" + adopt) }
        // A linking pass that stopped exits 1 but still says what was
        // applied and why it stopped (`links.error`), so read that.
        let reply: SetupApplied = try await call(arguments, allowFailure: true)
        return reply.applied
    }
}

/// Whether to open the setup window at launch: never once it has been
/// finished or skipped, and only on a Mac with no shared AGENTS.md yet,
/// so an existing install isn't asked to set up again after an update.
public enum FirstRun {
    public static let defaultsKey = "firstRunSetupDone"

    public static func shouldOffer(done: Bool, agentsFileExists: Bool) -> Bool {
        !done && !agentsFileExists
    }

    public static func agentsFile(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> URL {
        home.appending(path: ".swe/AGENTS.md")
    }
}

/// The first-run setup window's state: five steps, the agents you use,
/// which instruction file starts the shared AGENTS.md, and the one swe
/// call that applies it all. The last, phone access, is optional: it only
/// offers the remote-control checklist for after the window closes.
@MainActor
@Observable
public final class FirstRunSetupModel {
    public enum Step: Int, CaseIterable, Sendable {
        case agents, links, look, notifications, phone

        public var number: Int { rawValue + 1 }
    }

    /// Where the shared AGENTS.md starts from.
    public enum Start: Hashable, Sendable {
        /// Quiver's starter file, or the AGENTS.md already there.
        case seed
        case file(String)
    }

    public private(set) var step: Step = .agents
    public private(set) var preview: Loadable<SetupPreview> = .idle
    /// Ticked on the agents step; harness ids as swe and Settings name them.
    public var selected: Set<String> = []
    public var start: Start = .seed
    public private(set) var applying = false
    /// The links step's hub field: what's typed, a clone running, why the
    /// last one failed, and what the last one cloned.
    public var hubURL = ""
    public private(set) var cloning = false
    public private(set) var hubError: String?
    public private(set) var cloned: SetupPreview.Hub.Cloned?
    public private(set) var outcome: SetupOutcome?
    public private(set) var applyError: String?
    /// The picks the last setup run starred. Changing them after going
    /// Back runs setup again, so what's saved matches what's ticked.
    private var appliedSelection: Set<String>?
    /// Some run adopted the start file. Kept apart from `outcome`, which
    /// each later run (sent without --adopt) replaces with adopted: null.
    private var adopted = false

    /// Asked on the main actor at each call, so the app's current swe
    /// (whose path can change while the sheet is open) answers.
    private let swe: @MainActor () -> SweClient
    private var generation = 0

    public init(swe: @escaping @MainActor () -> SweClient, starred: Set<String> = []) {
        self.swe = swe
        selected = starred
    }

    public func toggle(_ id: String) {
        if selected.contains(id) { selected.remove(id) } else { selected.insert(id) }
    }

    public func load() async {
        generation += 1
        let run = generation
        preview = .loading
        do {
            let found = try await swe().setupPreview()
            guard run == generation else { return }
            preview = .loaded(found)
            start = Self.defaultStart(found.instructions)
        } catch is CancellationError {
            if run == generation { preview = .idle }
        } catch {
            if run == generation { preview = .failed(error.localizedDescription) }
        }
    }

    /// What swe setup says beside its hub prompt (`FORK_NOTE` in
    /// src/swe/init/hub.py); the web Setup page says the same.
    public static let hubForkNote =
        "Fork a hub repo first (or use it as a template for a private copy), then paste your fork's URL."

    /// Offer the hub field: the preview says `~/.swe` has no hub yet,
    /// and setup hasn't run (after it, the shared AGENTS.md is there).
    public var offersHub: Bool {
        guard case .loaded(let found) = preview, outcome == nil else { return false }
        return found.hub?.canClone == true
    }

    /// Clone the typed hub repo with `swe setup --hub`, then take the
    /// preview it answers with: from then on the step shows the cloned
    /// hub (its AGENTS.md, nothing to adopt). A failure keeps the URL to
    /// fix, says why, and reads the machine again, so the sheet shows
    /// what is on disk whichever way the run ended.
    public func cloneHub() async {
        let url = hubURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !url.isEmpty, !cloning, !applying else { return }
        cloning = true
        hubError = nil
        defer { cloning = false }
        // A preview load still in flight must not replace the clone's.
        generation += 1
        let run = generation
        do {
            let found = try await swe().cloneHub(url: url)
            guard run == generation else { return }
            preview = .loaded(found)
            start = Self.defaultStart(found.instructions)
            cloned = found.hub?.cloned
            hubURL = ""
        } catch is CancellationError {
            return
        } catch {
            let message = error.localizedDescription
            let chosen = start
            await load()
            // The reload resets the start to its default; keep the one the
            // user picked while the new preview still offers it.
            if case .loaded(let found) = preview, Self.offers(chosen, found.instructions) {
                start = chosen
            }
            hubError = message
        }
    }

    /// Whether `start` is a choice this preview still offers: the starter
    /// file always, a file only while it is an adopt candidate.
    static func offers(_ start: Start, _ instructions: SetupPreview.Instructions?) -> Bool {
        guard case .file(let path) = start else { return true }
        guard let instructions, !instructions.agentsFileCustomised else { return false }
        return instructions.adoptCandidates.contains { $0.path == path }
    }

    /// Your own instructions beat the starter file: the first real file
    /// found (Claude's comes first in swe's order) starts the shared one,
    /// unless AGENTS.md already has your text.
    static func defaultStart(_ instructions: SetupPreview.Instructions?) -> Start {
        guard let instructions, !instructions.agentsFileCustomised,
              let first = instructions.adoptCandidates.first else { return .seed }
        return .file(first.path)
    }

    /// Candidates the chosen start leaves as they are.
    public var untouchedCandidates: [SetupPreview.Candidate] {
        guard case .loaded(let found) = preview else { return [] }
        return (found.instructions?.adoptCandidates ?? []).filter { .file($0.path) != start }
    }

    public func back() {
        guard let previous = Step(rawValue: step.rawValue - 1) else { return }
        step = previous
    }

    /// Continue. Leaving the links step runs setup; a failure, or files
    /// setup left alone, keep you there to read about them, and a second
    /// Continue moves on.
    public func next(order: [String]) async {
        guard !cloning else { return }
        if step == .links && needsApply {
            await apply(order: order)
            if applyError != nil || outcome?.needsReading == true { return }
        }
        if let following = Step(rawValue: step.rawValue + 1) { step = following }
    }

    /// The ticked harnesses in `order` (the grid's), then any others.
    func starredList(order: [String]) -> [String] {
        order.filter(selected.contains) + selected.subtracting(order).sorted()
    }

    /// Setup hasn't run, failed, or ran with picks since changed.
    public var needsApply: Bool {
        outcome == nil || applyError != nil || appliedSelection != selected
    }

    func apply(order: [String]) async {
        applying = true
        applyError = nil
        defer { applying = false }
        // Only the first run adopts: after it, AGENTS.md holds that text,
        // and swe refuses a second --adopt (rightly) as "your own text".
        let adopt: String? = if case .file(let path) = start, !adopted { path } else { nil }
        let picks = selected
        // setup rewrites harness.json, so it waits behind the gate every
        // other registry write takes.
        await HarnessesModel.registryWrites.acquire()
        defer { HarnessesModel.registryWrites.release() }
        do {
            outcome = try await swe().applySetup(starred: starredList(order: order), adopt: adopt)
            appliedSelection = picks
            if outcome?.links?.adopted != nil { adopted = true }
            let failures = (outcome?.errors ?? []) + (outcome?.links?.error.map { [$0] } ?? [])
            if !failures.isEmpty { applyError = failures.joined(separator: "\n") }
        } catch {
            applyError = error.localizedDescription
        }
    }
}
