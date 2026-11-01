import Foundation

/// `swe init --check --json`: where the shared AGENTS.md and skills tree are
/// linked into each harness, and what a plain `swe init` would change.
public struct InitCheck: Decodable, Sendable, Equatable {
    public let agentsFile: String
    public let agentsFileExists: Bool
    public let harnesses: [Row]

    public init(agentsFile: String, agentsFileExists: Bool, harnesses: [Row]) {
        self.agentsFile = agentsFile
        self.agentsFileExists = agentsFileExists
        self.harnesses = harnesses
    }

    public struct Row: Decodable, Sendable, Equatable, Identifiable {
        public var id: String { name }
        public let name: String
        public let instructions: Link?
        public let skills: Link?

        public init(name: String, instructions: Link?, skills: Link?) {
            self.name = name
            self.instructions = instructions
            self.skills = skills
        }
    }

    public struct Link: Decodable, Sendable, Equatable {
        public let path: String
        public let state: LinkState

        public init(path: String, state: LinkState) {
            self.path = path
            self.state = state
        }
    }

    /// Harnesses that take the shared instruction file.
    public var instructionRows: [Row] { harnesses.filter { $0.instructions != nil } }

    /// True when a plain `swe init` would link at least one instruction file.
    public var instructionsNeedInit: Bool {
        instructionRows.contains { $0.instructions?.state.initFixes == true }
    }
}

/// The link-state vocabulary ARCHITECTURE.md defines, kept as a string so a
/// state added to swe later still decodes.
public struct LinkState: RawRepresentable, Codable, Sendable, Hashable {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }
    public init(from decoder: Decoder) throws {
        rawValue = try decoder.singleValueContainer().decode(String.self)
    }
    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }

    public static let linked = LinkState(rawValue: "linked")
    public static let relink = LinkState(rawValue: "relink")
    public static let create = LinkState(rawValue: "create")
    public static let absorb = LinkState(rawValue: "absorb")
    public static let keep = LinkState(rawValue: "keep")
    public static let conflict = LinkState(rawValue: "conflict")
    public static let skipped = LinkState(rawValue: "skipped")
    public static let ignored = LinkState(rawValue: "ignored")

    /// States a plain `swe init` resolves by itself.
    public var initFixes: Bool { [.create, .relink, .absorb].contains(self) }

    /// A real file or folder is in the way; only `swe init --force` would
    /// replace it, which the app runs only after asking.
    public var isBlocked: Bool { [.conflict, .keep].contains(self) }

    /// Sentence-case words for the state, as the page shows it.
    public var title: String {
        switch self {
        case .linked: "Linked"
        case .relink: "Points elsewhere"
        case .create: "Not linked"
        case .absorb: "Copy to absorb"
        case .keep: "Only copy, kept"
        case .conflict: "File in the way"
        case .skipped: "Not installed"
        case .ignored: "Archived"
        default: rawValue.prefix(1).uppercased() + rawValue.dropFirst()
        }
    }
}

extension SweClient {
    public func initCheck() async throws -> InitCheck {
        try await call(["init", "--check", "--json"])
    }

    /// A plain `swe init`: links what the check said it would. Without a
    /// terminal it leaves newly found harnesses alone rather than asking.
    /// The app never writes a symlink itself; this is the only way it links.
    /// `force` is `swe init --force`: each real file or folder in the way is
    /// copied to ~/.swe/backups, then replaced with a link.
    public func runInit(force: Bool = false) async throws {
        _ = try await runPlain(force ? ["init", "--force"] : ["init"])
    }

    /// Runs a swe command whose output is text, for commands that write and
    /// have no `--json`. Returns stdout; a non-zero exit throws with the
    /// clearest message the command left.
    func runPlain(_ arguments: [String]) async throws -> String {
        let result = try await runner.run(arguments)
        let out = Self.plain(String(decoding: result.stdout, as: UTF8.self))
        guard result.status == 0 else {
            let err = Self.plain(result.stderr)
            throw SweError.commandFailed(status: result.status, message: err.isEmpty ? out : err)
        }
        return out
    }
}
