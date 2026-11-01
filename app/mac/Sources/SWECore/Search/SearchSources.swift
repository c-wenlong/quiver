import Foundation

/// Where one agent's transcript files live and which of them count as
/// sessions — SWE's port of Orca's `AI_VAULT_AGENT_SOURCES`
/// (`session-scanner-agent-sources.ts`). One table drives discovery so the
/// shape of "discoverable" cannot drift from what a decoder expects.
///
/// `harness == nil` means Orca knows the layout but SWE has no decoder
/// for it yet: the entry documents the shape so adding the harness case is
/// the whole plug-in, and the scan must skip the entry entirely. An
/// index-pure file enumerated without a decoder would record a `files` row
/// marked `current` with no session behind it — and would never be re-read
/// when the decoder lands, because nothing about its stat changed.
///
/// Roots listed here are the defaults under one user home; a scan reads its
/// configured roots from `LedgerRoots` instead, so tests and account
/// profiles redirect the tree without editing the table.
public struct SearchTranscriptSource: Sendable {
    /// The decoder that reads these files, or nil when only the layout is
    /// known. Nil-harness entries are never enumerated by the scan.
    public let harness: SearchDecoder.Harness?
    /// Orca's agent id — kept even where `harness` is nil, since it is the
    /// name the transcripts' own records carry.
    public let agent: String
    /// Default roots under one user home. A scan substitutes its configured
    /// roots; these document the layout and seed `LedgerRoots.standard`.
    public let roots: [URL]
    /// Lowercased extensions with the dot, Orca's spelling (`".jsonl"`).
    public let extensions: Set<String>
    /// Extra acceptance on the file path itself (Orca's `filePredicate`).
    public let filePredicate: (@Sendable (URL) -> Bool)?
    /// Return false to prune a directory; depth 0 is a direct child of the
    /// root, Orca's indexing (`directoryPredicate`).
    public let directoryPredicate: (@Sendable (String, Int) -> Bool)?
    /// True when each file streams as JSONL; false means one JSON document
    /// per file (or a metadata-plus-sidecar pair, for cline/rovo/kiro/kimi).
    public let isJSONL: Bool
    /// A sibling file whose mtime also counts as the session's freshness —
    /// Orca's `contentDependencyPath`. Ordering and read decisions blend it
    /// in, so a sidecar-only update still lands inside a capped window.
    public let sidecarPath: (@Sendable (URL) -> URL?)?
    /// True when hidden files and directories under the root count —
    /// antigravity's transcript chain is `.system_generated/logs/…`. The
    /// default keeps `.skipsHiddenFiles` on.
    public let includesHiddenFiles: Bool
    /// The session id a file's records never carry, derived from its path —
    /// antigravity's conversation directory, rovo/muse's session directory.
    /// The decoder's own hint still wins when a record carries one (Orca's
    /// `*FromTranscriptPath` helpers).
    public let sessionIDFromPath: (@Sendable (URL) -> String?)?

    public init(
        harness: SearchDecoder.Harness?, agent: String, roots: [URL],
        extensions: Set<String>, filePredicate: (@Sendable (URL) -> Bool)? = nil,
        directoryPredicate: (@Sendable (String, Int) -> Bool)? = nil, isJSONL: Bool,
        sidecarPath: (@Sendable (URL) -> URL?)? = nil, includesHiddenFiles: Bool = false,
        sessionIDFromPath: (@Sendable (URL) -> String?)? = nil
    ) {
        self.harness = harness
        self.agent = agent
        self.roots = roots
        self.extensions = extensions
        self.filePredicate = filePredicate
        self.directoryPredicate = directoryPredicate
        self.isJSONL = isJSONL
        self.sidecarPath = sidecarPath
        self.includesHiddenFiles = includesHiddenFiles
        self.sessionIDFromPath = sessionIDFromPath
    }

    /// Claude's `subagents/` directory name — task transcripts share the
    /// parent session and are read on demand under it, not listed
    /// (Orca's `SUBAGENT_DIR_NAME` prune).
    private static let subagentDir = "subagents"

    /// OMP session artifact dirs are named like the session file's stem:
    /// `<ISO-stamp>_<uuidv7>` (Orca's `OMP_SESSION_ARTIFACT_DIR_PATTERN`).
    private static let ompArtifactDir = try! NSRegularExpression(
        pattern: #"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"#,
        options: [.caseInsensitive])

    /// Kiro session files are named `<uuid>.json` (Orca's
    /// `KIRO_SESSION_FILE_PATTERN`).
    private static let kiroSessionFile = try! NSRegularExpression(
        pattern: #"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.json$"#,
        options: [.caseInsensitive])

    private static func matches(_ regex: NSRegularExpression, _ string: String) -> Bool {
        regex.firstMatch(in: string, range: NSRange(string.startIndex..., in: string)) != nil
    }

    /// Cline's manifest rule, Orca's `isClineSessionMetadataPath`: the file
    /// is named for its session directory, `<id>/<id>.json`. That is also
    /// what keeps the `.messages.json` sibling out — its stem carries the
    /// extra `.messages` suffix.
    static func isClineManifest(_ url: URL) -> Bool {
        url.deletingPathExtension().lastPathComponent
            == url.deletingLastPathComponent().lastPathComponent
    }

    /// Orca's `antigravityConversationIdFromTranscriptPath`: the conversation
    /// directory three levels above `.system_generated/logs/<file>`.
    static func antigravityConversationID(_ url: URL) -> String? {
        let components = url.pathComponents
        guard components.count >= 4,
              ["transcript.jsonl", "transcript_full.jsonl"].contains(components.last),
              components[components.count - 2] == "logs",
              components[components.count - 3] == ".system_generated"
        else { return nil }
        return components[components.count - 4]
    }

    /// Orca's `kimiPrimaryAgentWirePath`: `<sessionDir>/agents/<primary>/
    /// wire.jsonl` — primary is the `agents` entry typed `main` with no
    /// parent, else `main`.
    static func kimiWireURL(forStateFile url: URL) -> URL {
        var primary = "main"
        if let manifest = try? JSONSerialization.jsonObject(with: Data(contentsOf: url))
            as? [String: Any],
           let agents = manifest["agents"] as? [String: Any] {
            for (id, value) in agents {
                let record = value as? [String: Any]
                // `record.parentAgentId == null` in Orca — the key absent or
                // JSON null both count.
                let parent = record.flatMap { $0["parentAgentId"] }
                if record?["type"] as? String == "main", parent == nil || parent is NSNull {
                    primary = id
                    break
                }
            }
        }
        return url.deletingLastPathComponent()
            .appendingPathComponent("agents/\(primary)/wire.jsonl")
    }

    /// Every agent source Orca's table knows, in its order. Orca discovers
    /// four more agents by shape-specific scanners that never appear in its
    /// table — opencode/opencode2/zcode (SQLite) and antigravity (brain
    /// dirs); SWE indexes OpenCode sessions through
    /// `indexOpenCodeSessions`, and antigravity sits in this table with its
    /// shape scan expressed as predicates so the same enumeration serves it.
    public static func table(
        home: URL = FileManager.default.homeDirectoryForCurrentUser
    ) -> [SearchTranscriptSource] {
        func dir(_ path: String) -> URL { home.appendingPathComponent(path) }
        return [
            SearchTranscriptSource(
                harness: .claude, agent: "claude",
                roots: [dir(".claude/projects")],
                extensions: [".jsonl"],
                directoryPredicate: { name, _ in name != subagentDir },
                isJSONL: true),
            SearchTranscriptSource(
                harness: .qoder, agent: "qoder",
                roots: [dir(".qoder/projects")],
                extensions: [".jsonl"],
                directoryPredicate: { name, _ in name != subagentDir },
                isJSONL: true),
            SearchTranscriptSource(
                harness: .codebuddy, agent: "codebuddy",
                roots: [dir(".codebuddy/projects")],
                extensions: [".jsonl"],
                // Nested subagent transcripts are not independent top-level
                // conversations.
                directoryPredicate: { name, _ in name != subagentDir },
                isJSONL: true),
            SearchTranscriptSource(
                harness: .codex, agent: "codex",
                roots: [dir(".codex/sessions"), dir(".codex/archived_sessions")],
                extensions: [".jsonl"],
                isJSONL: true),
            SearchTranscriptSource(
                // Gemini keeps .json and .jsonl session files side by side;
                // the scan dispatches on the extension (Orca's
                // `parseGeminiSessionFile` does the same).
                harness: .gemini, agent: "gemini",
                roots: [dir(".gemini/tmp")],
                extensions: [".json", ".jsonl"],
                isJSONL: true),
            SearchTranscriptSource(
                // Orca discovers Antigravity by scanning the brain directory
                // for the fixed `<conv>/.system_generated/logs/transcript{,_full}.jsonl`
                // chain rather than an ordinary source row — it sits beside
                // Gemini here because the brain root is a Gemini home too,
                // and its predicates are that chain.
                harness: .antigravity, agent: "antigravity",
                roots: [dir(".gemini/antigravity-cli/brain")],
                extensions: [".jsonl"],
                filePredicate: { url in
                    guard antigravityConversationID(url) != nil else { return false }
                    // transcript_full.jsonl is the complete record; the
                    // compact transcript.jsonl beside it is the head
                    // duplicate Orca dedupes away.
                    guard url.lastPathComponent == "transcript.jsonl" else { return true }
                    return !FileManager.default.fileExists(atPath: url.deletingLastPathComponent()
                        .appendingPathComponent("transcript_full.jsonl").path)
                },
                // Brain dirs hold large artifact trees; only the fixed
                // transcript chain is the conversation contract.
                directoryPredicate: { name, depth in
                    depth == 0 || (depth == 1 && name == ".system_generated")
                        || (depth == 2 && name == "logs")
                },
                isJSONL: true,
                // The chain lives under `.system_generated` — hidden on
                // purpose — so the enumerator must keep hidden entries.
                includesHiddenFiles: true,
                sessionIDFromPath: { antigravityConversationID($0) }),
            SearchTranscriptSource(
                harness: .copilot, agent: "copilot",
                roots: [dir(".copilot/session-state")],
                extensions: [".jsonl"],
                isJSONL: true),
            SearchTranscriptSource(
                harness: .cursor, agent: "cursor",
                roots: [dir(".cursor/projects")],
                extensions: [".jsonl"],
                // Only `agent-transcripts/` paths are chat transcripts;
                // projects dirs also carry mcps/, canvases/ and friends.
                filePredicate: { $0.pathComponents.contains("agent-transcripts") },
                isJSONL: true),
            SearchTranscriptSource(
                harness: .grok, agent: "grok",
                roots: [dir(".grok/sessions")],
                extensions: [".json"],
                filePredicate: { $0.lastPathComponent == "summary.json" },
                isJSONL: false,
                // The turns live in the `chat_history.jsonl` sibling.
                sidecarPath: {
                    $0.deletingLastPathComponent().appendingPathComponent("chat_history.jsonl")
                },
                // `info.id` may be absent — the session directory names it.
                sessionIDFromPath: {
                    $0.deletingLastPathComponent().lastPathComponent
                }),
            SearchTranscriptSource(
                harness: .devin, agent: "devin",
                roots: [
                    dir(".local/share/devin/cli/transcripts"),
                    // Devin 3000.10.31 exports ATIF to agent_logs by default.
                    dir(".local/share/devin/cli/agent_logs"),
                ],
                extensions: [".json"],
                isJSONL: false),
            SearchTranscriptSource(
                harness: .hermes, agent: "hermes",
                roots: [dir(".hermes/sessions")],
                extensions: [".json"],
                filePredicate: { $0.lastPathComponent.hasPrefix("session_") },
                isJSONL: false),
            SearchTranscriptSource(
                harness: .jcode, agent: "jcode",
                roots: [dir(".jcode/sessions")],
                extensions: [".json"],
                // Skips the live .journal.jsonl appends and consolidated
                // backups.
                filePredicate: { $0.lastPathComponent.hasPrefix("session_") },
                isJSONL: false),
            SearchTranscriptSource(
                harness: .kiro, agent: "kiro",
                roots: [dir(".kiro/sessions/cli")],
                extensions: [".json"],
                filePredicate: { matches(kiroSessionFile, $0.lastPathComponent) },
                // Per-session subdirectories hold background task state, not
                // sessions — only files directly under the root count.
                directoryPredicate: { _, _ in false },
                isJSONL: false,
                // The turns stream from the `<uuid>.jsonl` sibling.
                sidecarPath: { URL(fileURLWithPath: $0.path + "l") },
                // The UUID filename is the session id; the records carry
                // none (Orca's `sessionId: basename(file, '.json')`). A
                // metadata-only session still commits.
                sessionIDFromPath: { $0.deletingPathExtension().lastPathComponent }),
            SearchTranscriptSource(
                harness: .rovo, agent: "rovo",
                roots: [dir(".rovodev/sessions")],
                extensions: [".json"],
                filePredicate: { $0.lastPathComponent == "metadata.json" },
                isJSONL: false,
                // The turns live in the `session_context.json` sibling.
                sidecarPath: {
                    $0.deletingLastPathComponent().appendingPathComponent("session_context.json")
                },
                // The session id is the session directory; the documents
                // never carry it.
                sessionIDFromPath: {
                    $0.deletingLastPathComponent().lastPathComponent
                }),
            SearchTranscriptSource(
                harness: .pi, agent: "pi",
                roots: [dir(".pi/agent/sessions")],
                extensions: [".jsonl"],
                isJSONL: true),
            SearchTranscriptSource(
                harness: .omp, agent: "omp",
                roots: [dir(".omp/agent/sessions")],
                extensions: [".jsonl"],
                // Task-subagent transcripts live inside the session's
                // same-named artifact directory; depth 0 is the workspace
                // dir, which never matches (Orca #9330).
                directoryPredicate: { name, depth in
                    depth == 0 || !matches(ompArtifactDir, name)
                },
                isJSONL: true),
            SearchTranscriptSource(
                harness: .primeAgent, agent: "prime-agent",
                roots: [dir(".prime/agent/sessions")],
                extensions: [".jsonl"],
                isJSONL: true),
            SearchTranscriptSource(
                // OpenClaw keeps transcripts under
                // `<state>/agents/<agent>/sessions/**`; siblings hold other
                // tools' state, so only `sessions` below each agent dir is
                // walked. `.clawdbot` is the legacy state dir.
                harness: .openclaw, agent: "openclaw",
                roots: [dir(".openclaw/agents"), dir(".clawdbot/agents")],
                extensions: [".jsonl"],
                filePredicate: { $0.pathComponents.contains("sessions") },
                directoryPredicate: { name, depth in depth != 1 || name == "sessions" },
                isJSONL: true),
            SearchTranscriptSource(
                harness: .droid, agent: "droid",
                roots: [dir(".factory/sessions"), dir(".factory/projects")],
                extensions: [".jsonl"],
                isJSONL: true),
            SearchTranscriptSource(
                harness: .cline, agent: "cline",
                roots: [dir(".cline/data/sessions")],
                extensions: [".json"],
                filePredicate: { isClineManifest($0) },
                // Cline stores one manifest directly beneath each session
                // directory.
                directoryPredicate: { _, depth in depth == 0 },
                isJSONL: false,
                // The turns live in the `.messages.json` sibling — its
                // writes are the session's real activity.
                sidecarPath: {
                    $0.deletingPathExtension().appendingPathExtension("messages.json")
                }),
            SearchTranscriptSource(
                harness: .kimi, agent: "kimi",
                roots: [dir(".kimi-code/sessions")],
                extensions: [".json"],
                // Each session is `<sessions>/wd_*/session_*/state.json`;
                // sibling agents/*/wire.jsonl transcripts are not it.
                filePredicate: {
                    $0.lastPathComponent == "state.json"
                        && $0.deletingLastPathComponent().lastPathComponent.hasPrefix("session_")
                },
                isJSONL: false,
                // The turns stream from `agents/<primary>/wire.jsonl`.
                sidecarPath: { kimiWireURL(forStateFile: $0) },
                // The session id is the `session_*` directory name.
                sessionIDFromPath: {
                    $0.deletingLastPathComponent().lastPathComponent
                }),
            SearchTranscriptSource(
                harness: .muse, agent: "muse",
                roots: [dir(".local/share/muse/sessions")],
                extensions: [".jsonl"],
                // `<root>/YYYY/MM/DD/<uuid>/session.jsonl` — not the .log and
                // .sqlite3 sidecars beside it.
                filePredicate: { $0.lastPathComponent == "session.jsonl" },
                isJSONL: true,
                // Every session file is `session.jsonl` — the `<uuid>` dir
                // names it.
                sessionIDFromPath: {
                    $0.deletingLastPathComponent().lastPathComponent
                }),
        ]
    }
}
