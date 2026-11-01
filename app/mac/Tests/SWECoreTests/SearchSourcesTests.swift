import Foundation
import Testing
@testable import SWECore

/// `SearchTranscriptSource.table()` is SWE's port of Orca's
/// `AI_VAULT_AGENT_SOURCES`: every agent Orca's table knows appears here,
/// plus antigravity — which Orca discovers with a shape-specific scanner
/// rather than a table row — so the same enumeration serves it. Entries
/// without a decoder would carry `harness == nil`; today every row decodes.
@Suite struct SearchSourcesTests {
    /// Orca's full table in its order, plus antigravity seated beside
    /// gemini — the regression list for coverage.
    private static let orcaAgents = [
        "claude", "qoder", "codebuddy", "codex", "gemini", "antigravity",
        "copilot", "cursor", "grok", "devin", "hermes", "jcode", "kiro",
        "rovo", "pi", "omp", "prime-agent", "openclaw", "droid", "cline",
        "kimi", "muse",
    ]

    @Test func theTableCoversEveryOrcaAgent() {
        let agents = SearchTranscriptSource.table().map(\.agent)
        #expect(agents == Self.orcaAgents)
        // One row per agent — a duplicate would enumerate the same files twice.
        #expect(Set(agents).count == agents.count)
    }

    @Test func everyRowHasADecoder() {
        // Index-pure scan semantics: an enumerated file with no decoder
        // would mark its `files` row current with no session behind it.
        for source in SearchTranscriptSource.table() {
            let harness = try? #require(source.harness)
            #expect(harness?.rawValue == source.agent)
            #expect(!source.roots.isEmpty)
            #expect(!source.extensions.isEmpty)
        }
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

    @Test func antigravityFindsTheHiddenTranscriptChain() throws {
        let temp = try TempDir()
        let brain = temp.url.appendingPathComponent(".gemini/antigravity-cli/brain")
        let logs = brain.appendingPathComponent("conv-1/.system_generated/logs")
        try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: logs.appendingPathComponent("transcript_full.jsonl"))
        // The compact transcript beside the full one is the head duplicate —
        // Orca dedupes it away.
        try Data("{}".utf8).write(to: logs.appendingPathComponent("transcript.jsonl"))
        // Artifacts outside the fixed chain — another dir at depth 1 and a
        // plain file at depth 2 — are not transcripts.
        try Data("{}".utf8).write(to: logs.appendingPathComponent("notes.txt"))
        let other = brain.appendingPathComponent("conv-2/cache")
        try FileManager.default.createDirectory(at: other, withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: other.appendingPathComponent("transcript.jsonl"))

        let source = SearchTranscriptSource.table(home: temp.url)
            .first { $0.agent == "antigravity" }!
        let found = Ledger.transcriptFiles(under: brain, source: source)
        #expect(found.map(\.lastPathComponent) == ["transcript_full.jsonl"])
        // The session id is the conversation directory.
        #expect(source.sessionIDFromPath?(found[0]) == "conv-1")
        // `.system_generated` is hidden — the row must keep hidden entries.
        #expect(source.includesHiddenFiles)

        // A lone transcript.jsonl with no _full sibling still indexes.
        let lone = brain.appendingPathComponent("conv-3/.system_generated/logs")
        try FileManager.default.createDirectory(at: lone, withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: lone.appendingPathComponent("transcript.jsonl"))
        let found2 = Ledger.transcriptFiles(under: brain, source: source)
        #expect(Set(found2.map(\.lastPathComponent)) == ["transcript_full.jsonl", "transcript.jsonl"])
    }

    @Test func openclawWalksOnlySessionsBelowAgentDirs() throws {
        let temp = try TempDir()
        let agents = temp.url.appendingPathComponent(".openclaw/agents")
        let sessions = agents.appendingPathComponent("bot/sessions/2026/01")
        try FileManager.default.createDirectory(at: sessions, withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: sessions.appendingPathComponent("s.jsonl"))
        // Sibling agent state — not a sessions dir — is pruned at depth 1.
        let state = agents.appendingPathComponent("bot/state")
        try FileManager.default.createDirectory(at: state, withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: state.appendingPathComponent("stray.jsonl"))

        let source = SearchTranscriptSource.table(home: temp.url)
            .first { $0.agent == "openclaw" }!
        let found = Ledger.transcriptFiles(under: agents, source: source)
        #expect(found.map(\.lastPathComponent) == ["s.jsonl"])
    }

    @Test func kimiAdmitsStateJsonOnlyUnderSessionDirs() throws {
        let temp = try TempDir()
        let home = temp.url
        let session = home.appendingPathComponent(
            ".kimi-code/sessions/wd_ws_ab12/session_abc-123")
        let wire = session.appendingPathComponent("agents/main")
        try FileManager.default.createDirectory(at: wire, withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: session.appendingPathComponent("state.json"))
        try Data("{}".utf8).write(to: wire.appendingPathComponent("wire.jsonl"))
        // A state.json outside a session_* dir does not count.
        let stray = home.appendingPathComponent(".kimi-code/sessions/wd_ws_ab12")
        try Data("{}".utf8).write(to: stray.appendingPathComponent("state.json"))

        let source = SearchTranscriptSource.table(home: home).first { $0.agent == "kimi" }!
        let found = Ledger.transcriptFiles(
            under: home.appendingPathComponent(".kimi-code/sessions"), source: source)
        #expect(found.map(\.lastPathComponent) == ["state.json"])
        #expect(source.sessionIDFromPath?(found[0]) == "session_abc-123")
        // The wire transcript is the freshness sidecar.
        #expect(source.sidecarPath?(found[0])?.lastPathComponent == "wire.jsonl")
    }

    @Test func pairedRowsDeriveSidecarsAndPathSessionIds() {
        let table = SearchTranscriptSource.table()
        let rovo = table.first { $0.agent == "rovo" }!
        let metadata = URL(fileURLWithPath: "/h/.rovodev/sessions/sess-9/metadata.json")
        #expect(rovo.filePredicate?(metadata) == true)
        #expect(rovo.sessionIDFromPath?(metadata) == "sess-9")
        #expect(rovo.sidecarPath?(metadata)?.lastPathComponent == "session_context.json")

        let grok = table.first { $0.agent == "grok" }!
        let summary = URL(fileURLWithPath: "/h/.grok/sessions/-repo/sess-1/summary.json")
        #expect(grok.filePredicate?(summary) == true)
        #expect(grok.sessionIDFromPath?(summary) == "sess-1")
        #expect(grok.sidecarPath?(summary)?.lastPathComponent == "chat_history.jsonl")

        let kiro = table.first { $0.agent == "kiro" }!
        let manifest = URL(fileURLWithPath:
            "/h/.kiro/sessions/cli/123e4567-e89b-42d3-a456-426614174000.json")
        #expect(kiro.filePredicate?(manifest) == true)
        #expect(kiro.sidecarPath?(manifest)?.path == manifest.path + "l")

        let muse = table.first { $0.agent == "muse" }!
        let log = URL(fileURLWithPath:
            "/h/.local/share/muse/sessions/2026/11/02/uuid-7/session.jsonl")
        #expect(muse.filePredicate?(log) == true)
        #expect(muse.sessionIDFromPath?(log) == "uuid-7")
    }
}
