import Foundation
import Testing
@testable import QuiverCore

/// `SearchTranscriptSource.table()` is Quiver's port of Orca's
/// `AI_VAULT_AGENT_SOURCES`: every agent Orca's table knows appears here,
/// and entries without a decoder carry `harness == nil` so the scan never
/// enumerates them into index-pure `files` rows.
@Suite struct SearchSourcesTests {
    /// Orca's full table, in its order — the regression list for coverage.
    private static let orcaAgents = [
        "claude", "qoder", "codebuddy", "codex", "gemini", "copilot", "cursor",
        "grok", "devin", "hermes", "jcode", "kiro", "rovo", "pi", "omp",
        "prime-agent", "openclaw", "droid", "cline", "kimi", "muse",
    ]

    @Test func theTableCoversEveryOrcaAgent() {
        let agents = SearchTranscriptSource.table().map(\.agent)
        #expect(agents == Self.orcaAgents)
        // One row per agent — a duplicate would enumerate the same files twice.
        #expect(Set(agents).count == agents.count)
    }

    @Test func onlyDecodedHarnessesEnumerate() {
        let decoded = SearchTranscriptSource.table().compactMap(\.harness)
        // The seven decoders the index actually has; everything else in the
        // table is documentation until its decoder lands.
        #expect(Set(decoded) == [.claude, .codex, .pi, .devin, .cursor, .droid, .cline])
        let waiting = SearchTranscriptSource.table()
            .filter { $0.harness == nil }.map(\.agent)
        #expect(Set(waiting) == [
            "qoder", "codebuddy", "gemini", "copilot", "grok", "hermes", "jcode",
            "kiro", "rovo", "omp", "prime-agent", "openclaw", "kimi", "muse",
        ])
    }

    @Test func cursorOnlyAcceptsAgentTranscripts() {
        let cursor = SearchTranscriptSource.table().first { $0.agent == "cursor" }!
        let transcript = URL(fileURLWithPath:
            "/h/.cursor/projects/-repo/agent-transcripts/abc/abc.jsonl")
        let stray = URL(fileURLWithPath: "/h/.cursor/projects/-repo/mcps/x.jsonl")
        #expect(cursor.filePredicate?(transcript) == true)
        #expect(cursor.filePredicate?(stray) == false)
    }

    @Test func clineOnlyAcceptsSessionManifests() {
        let cline = SearchTranscriptSource.table().first { $0.agent == "cline" }!
        let root = URL(fileURLWithPath: "/h/.cline/data/sessions")
        let manifest = root.appendingPathComponent("s1/s1.json")
        // The sibling is not a manifest — its stem carries `.messages`.
        let messages = root.appendingPathComponent("s1/s1.messages.json")
        // A deeper nesting fails the manifest stem-equals-directory rule.
        let nested = root.appendingPathComponent("s1/deep/x.json")
        #expect(cline.filePredicate?(manifest) == true)
        #expect(cline.filePredicate?(messages) == false)
        #expect(cline.filePredicate?(nested) == false)
        // Only the session dirs themselves are walked (depth 0).
        #expect(cline.directoryPredicate?("s1", 0) == true)
        #expect(cline.directoryPredicate?("deeper", 1) == false)
    }

    @Test func sourcePredicatesPruneAndSelectFiles() throws {
        let temp = try TempDir()
        let home = temp.url
        // A kiro-shaped tree: uuid.json files at the root count, nested
        // session state dirs do not.
        let kiro = home.appendingPathComponent(".kiro/sessions/cli")
        let nested = kiro.appendingPathComponent("session-state/tasks")
        try FileManager.default.createDirectory(at: nested, withIntermediateDirectories: true)
        let uuid = "123e4567-e89b-42d3-a456-426614174000"
        try Data("{}".utf8).write(to: kiro.appendingPathComponent("\(uuid).json"))
        try Data("{}".utf8).write(to: kiro.appendingPathComponent("not-a-uuid.json"))
        try Data("{}".utf8).write(to: nested.appendingPathComponent("\(uuid).json"))

        let kiroSource = SearchTranscriptSource.table(home: home).first { $0.agent == "kiro" }!
        let found = Ledger.transcriptFiles(under: kiro, source: kiroSource)
        #expect(found.map(\.lastPathComponent) == ["\(uuid).json"])

        // An omp-shaped tree: the same-named artifact directory next to a
        // session file is pruned (Orca #9330); a plain workspace dir is not.
        let omp = home.appendingPathComponent(".omp/agent/sessions")
        let workspace = omp.appendingPathComponent("ws")
        let artifact = workspace.appendingPathComponent(
            "2026-01-02T03-04-05-678Z_123e4567-e89b-42d3-a456-426614174000")
        try FileManager.default.createDirectory(at: artifact, withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: workspace.appendingPathComponent("a.jsonl"))
        try Data("{}".utf8).write(to: artifact.appendingPathComponent("inner.jsonl"))

        let ompSource = SearchTranscriptSource.table(home: home).first { $0.agent == "omp" }!
        let foundOmp = Ledger.transcriptFiles(under: omp, source: ompSource)
        #expect(foundOmp.map(\.lastPathComponent) == ["a.jsonl"])
    }
}
