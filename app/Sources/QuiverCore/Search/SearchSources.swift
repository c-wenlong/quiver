import Foundation

/// Where one agent's transcript files live and which of them count as
/// sessions — Quiver's port of Orca's `AI_VAULT_AGENT_SOURCES`
/// (`session-scanner-agent-sources.ts`). One table drives discovery so the
/// shape of "discoverable" cannot drift from what a decoder expects.
///
/// `harness == nil` means Orca knows the layout but Quiver has no decoder
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
    /// per file (or a metadata-plus-sidecar pair, for cline).
    public let isJSONL: Bool
    /// A sibling file whose mtime also counts as the session's freshness —
    /// Orca's `contentDependencyPath`. Ordering and read decisions blend it
    /// in, so a sidecar-only update still lands inside a capped window.
    public let sidecarPath: (@Sendable (URL) -> URL?)?

    public init(
        harness: SearchDecoder.Harness?, agent: String, roots: [URL],
        extensions: Set<String>, filePredicate: (@Sendable (URL) -> Bool)? = nil,
        directoryPredicate: (@Sendable (String, Int) -> Bool)? = nil, isJSONL: Bool,
        sidecarPath: (@Sendable (URL) -> URL?)? = nil
    ) {
        self.harness = harness
        self.agent = agent
        self.roots = roots
        self.extensions = extensions
        self.filePredicate = filePredicate
        self.directoryPredicate = directoryPredicate
        self.isJSONL = isJSONL
        self.sidecarPath = sidecarPath
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

    /// Every agent source Orca's table knows, in its order. Orca discovers
    /// four more agents by shape-specific scanners that never appear here —
    /// opencode/opencode2/zcode (SQLite) and antigravity (brain dirs);
    /// Quiver indexes OpenCode sessions through `indexOpenCodeSessions`.
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
                harness: nil, agent: "qoder",
                roots: [dir(".qoder/projects")],
                extensions: [".jsonl"],
                directoryPredicate: { name, _ in name != subagentDir },
                isJSONL: true),
            SearchTranscriptSource(
                harness: nil, agent: "codebuddy",
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
                // which reads which is its decoder's call when it lands.
                harness: nil, agent: "gemini",
                roots: [dir(".gemini/tmp")],
                extensions: [".json", ".jsonl"],
                isJSONL: true),
            SearchTranscriptSource(
                harness: nil, agent: "copilot",
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
                harness: nil, agent: "grok",
                roots: [dir(".grok/sessions")],
                extensions: [".json"],
                filePredicate: { $0.lastPathComponent == "summary.json" },
                isJSONL: false),
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
                harness: nil, agent: "hermes",
                roots: [dir(".hermes/sessions")],
                extensions: [".json"],
                filePredicate: { $0.lastPathComponent.hasPrefix("session_") },
                isJSONL: false),
            SearchTranscriptSource(
                harness: nil, agent: "jcode",
                roots: [dir(".jcode/sessions")],
                extensions: [".json"],
                // Skips the live .journal.jsonl appends and consolidated
                // backups.
                filePredicate: { $0.lastPathComponent.hasPrefix("session_") },
                isJSONL: false),
            SearchTranscriptSource(
                harness: nil, agent: "kiro",
                roots: [dir(".kiro/sessions/cli")],
                extensions: [".json"],
                filePredicate: { matches(kiroSessionFile, $0.lastPathComponent) },
                // Per-session subdirectories hold background task state, not
                // sessions — only files directly under the root count.
                directoryPredicate: { _, _ in false },
                isJSONL: false),
            SearchTranscriptSource(
                harness: nil, agent: "rovo",
                roots: [dir(".rovodev/sessions")],
                extensions: [".json"],
                filePredicate: { $0.lastPathComponent == "metadata.json" },
                isJSONL: false),
            SearchTranscriptSource(
                harness: .pi, agent: "pi",
                roots: [dir(".pi/agent/sessions")],
                extensions: [".jsonl"],
                isJSONL: true),
            SearchTranscriptSource(
                harness: nil, agent: "omp",
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
                harness: nil, agent: "prime-agent",
                roots: [dir(".prime/agent/sessions")],
                extensions: [".jsonl"],
                isJSONL: true),
            SearchTranscriptSource(
                // OpenClaw keeps transcripts under
                // `<state>/agents/<agent>/sessions/**`; siblings hold other
                // tools' state, so only `sessions` below each agent dir is
                // walked. `.clawdbot` is the legacy state dir.
                harness: nil, agent: "openclaw",
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
                harness: nil, agent: "kimi",
                roots: [dir(".kimi-code/sessions")],
                extensions: [".json"],
                // Each session is `<sessions>/wd_*/session_*/state.json`;
                // sibling agents/*/wire.jsonl transcripts are not it.
                filePredicate: {
                    $0.lastPathComponent == "state.json"
                        && $0.deletingLastPathComponent().lastPathComponent.hasPrefix("session_")
                },
                isJSONL: false),
            SearchTranscriptSource(
                harness: nil, agent: "muse",
                roots: [dir(".local/share/muse/sessions")],
                extensions: [".jsonl"],
                // `<root>/YYYY/MM/DD/<uuid>/session.jsonl` — not the .log and
                // .sqlite3 sidecars beside it.
                filePredicate: { $0.lastPathComponent == "session.jsonl" },
                isJSONL: true),
        ]
    }
}
