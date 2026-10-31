import Foundation

/// `swe instructions check --json`: whether the shared AGENTS.md is built
/// from chapters under `~/.quiver/AGENTS_MD`, the chapter list in index
/// order, and whether the output still matches the sources.
public struct InstructionsCheck: Decodable, Sendable, Equatable {
    /// `~/.quiver/AGENTS_MD`.
    public let directory: String
    /// `index.txt`, the ordered chapter list.
    public let index: String
    /// The generated `~/.quiver/AGENTS.md`.
    public let output: String
    /// Chapters are set up at all; without them the flat file is edited.
    public let indexExists: Bool
    public let outputExists: Bool
    /// The output carries the compiler's generated marker.
    public let outputGenerated: Bool
    public let chapters: [Chapter]
    /// A source problem the build would refuse (missing file, bad entry);
    /// nil means the sources compile.
    public let error: String?
    /// nil when `indexExists` is false: flat-file mode has nothing to go stale.
    public let stale: Bool?

    /// One row per chapter name, as the rail shows them. A duplicated
    /// index entry is a source problem `error` already reports; a
    /// second row would share the first's identity while dragged.
    public var uniqueChapters: [Chapter] {
        var seen = Set<String>()
        return chapters.filter { seen.insert($0.name).inserted }
    }

    public init(directory: String, index: String, output: String, indexExists: Bool,
                outputExists: Bool, outputGenerated: Bool, chapters: [Chapter],
                error: String?, stale: Bool?) {
        self.directory = directory
        self.index = index
        self.output = output
        self.indexExists = indexExists
        self.outputExists = outputExists
        self.outputGenerated = outputGenerated
        self.chapters = chapters
        self.error = error
        self.stale = stale
    }

    public struct Chapter: Decodable, Sendable, Equatable, Identifiable {
        public var id: String { name }
        /// The index.txt line: a relative .md path, possibly in a folder.
        public let name: String
        /// The chapter's absolute path.
        public let path: String
        /// Listed but absent files still get a row, so the entry can be fixed.
        public let exists: Bool

        public init(name: String, path: String, exists: Bool) {
            self.name = name
            self.path = path
            self.exists = exists
        }
    }
}

/// `swe instructions build --json`.
public struct InstructionsBuildReply: Decodable, Sendable, Equatable {
    public let changed: Bool
    public init(changed: Bool) { self.changed = changed }
}

/// `swe instructions init --json`.
public struct InstructionsInitReply: Decodable, Sendable, Equatable {
    public let created: Bool
    public let chapter: String
    /// What the chapter was folded from: the old AGENTS.md path or a starter.
    public let source: String
    public let built: Bool
    public init(created: Bool, chapter: String, source: String, built: Bool) {
        self.created = created
        self.chapter = chapter
        self.source = source
        self.built = built
    }
}

extension SweClient {
    /// The chapter state. `check` exits 1 when the output is stale or the
    /// sources are broken, and still prints the document — so the run is
    /// allowed to fail and the state inside is what matters.
    public func instructionsCheck() async throws -> InstructionsCheck {
        try await call(["instructions", "check", "--json"], allowFailure: true)
    }

    /// Compiles the chapters into `~/.quiver/AGENTS.md`. A refusal (bad
    /// index entry, escaping source) throws with the compiler's message.
    public func instructionsBuild() async throws -> Bool {
        let reply: InstructionsBuildReply = try await call(["instructions", "build", "--json"])
        return reply.changed
    }

    /// Folds the current flat AGENTS.md into a first chapter. Refuses when
    /// chapters exist already.
    public func instructionsInit() async throws -> InstructionsInitReply {
        try await call(["instructions", "init", "--json"])
    }
}
