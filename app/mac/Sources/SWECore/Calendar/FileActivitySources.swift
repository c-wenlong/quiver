import Foundation

/// An `ActivityProviding` over a directory of session log files, through
/// `ActivityFileIndex`: each query stats the files, re-parses only the
/// new or changed ones, and turns every session's timestamps into blocks
/// with the shared `ActivityBlocks.make` rule.
public struct FileActivitySource: ActivityProviding {
    public let harness: String
    private let index: ActivityFileIndex
    private let files: @Sendable () -> [String: ActivityFileIndex.FileStamp]
    private let parse: @Sendable (String) throws -> [ActivityFileIndex.IndexedSession]
    /// Whole-session drop applied before grouping, e.g. the ledger's
    /// trivial-session rule; a file source's sessions never pass through
    /// the block query that would judge them otherwise.
    private let filter: (@Sendable ([ActivityFileIndex.IndexedSession]) async -> [ActivityFileIndex.IndexedSession])?

    public init(harness: String, index: ActivityFileIndex,
                files: @escaping @Sendable () -> [String: ActivityFileIndex.FileStamp],
                parse: @escaping @Sendable (String) throws -> [ActivityFileIndex.IndexedSession],
                filter: (@Sendable ([ActivityFileIndex.IndexedSession]) async -> [ActivityFileIndex.IndexedSession])? = nil) {
        self.harness = harness
        self.index = index
        self.files = files
        self.parse = parse
        self.filter = filter
    }

    public func activityBlocks(in range: DateInterval, gap: TimeInterval,
                               force: Bool) async throws -> [ActivityBlock] {
        let sessions = try await index.sessions(files: files, force: force, parse: parse)
        let visible = if let filter { await filter(sessions) } else { sessions }
        return visible.flatMap {
            ActivityBlocks.make(harness: harness, accountHome: $0.accountHome,
                                sessionId: $0.sessionId, cwd: $0.cwd,
                                timestamps: $0.timestamps.map(Date.init(timeIntervalSince1970:))
                                    .sorted(),
                                range: range, gap: gap)
        }
    }
}

/// The session-log readers shared by the file sources: JSONL streams and
/// the little JSON dig they all need.
public enum ActivityParsers {
    /// Every line of a JSONL file, through JSONLReader's line-size cap.
    /// Lines that are not objects or whose JSON is broken are skipped:
    /// one bad write must not fail the whole file.
    public static func jsonl(_ path: String,
                             line body: ([String: Any]) -> Void) throws {
        _ = try JSONLReader().read(URL(fileURLWithPath: path), from: 0) { data in
            guard let object = try? JSONSerialization.jsonObject(with: data),
                  let dict = object as? [String: Any] else { return }
            body(dict)
        }
    }

    /// The timestamp field a message line carries, as ISO 8601.
    public static func timestamp(_ line: [String: Any]) -> Date? {
        (line["timestamp"] as? String).flatMap(Timestamp.parse)
    }

    /// `~`-aware recursive `*.jsonl` listing under a sessions root, with
    /// each file's stamp; files that fail to stat are skipped.
    public static func jsonlFiles(root: URL) -> [String: ActivityFileIndex.FileStamp] {
        var files: [String: ActivityFileIndex.FileStamp] = [:]
        let enumerator = FileManager.default.enumerator(
            at: root, includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles])
        while let url = enumerator?.nextObject() as? URL {
            guard url.pathExtension == "jsonl",
                  let stamp = ActivityFileIndex.stamp(url) else { continue }
            files[url.path] = stamp
        }
        return files
    }
}

/// Droid: `~/.factory/sessions/<project>/<uuid>.jsonl`; the file stem is
/// swe's session id and `session_start` carries the cwd. `type ==
/// "message"` lines carry the response times.
public struct DroidActivity: ActivityProviding {
    private let source: FileActivitySource

    public init(sessionsRoot: URL, accountHome: String, index: ActivityFileIndex,
                filter: (@Sendable ([ActivityFileIndex.IndexedSession]) async -> [ActivityFileIndex.IndexedSession])? = nil) {
        source = FileActivitySource(
            harness: "droid",
            index: index,
            files: { ActivityParsers.jsonlFiles(root: sessionsRoot) },
            parse: { try Self.parse(file: $0, accountHome: accountHome) },
            filter: filter)
    }

    public func activityBlocks(in range: DateInterval, gap: TimeInterval,
                               force: Bool) async throws -> [ActivityBlock] {
        try await source.activityBlocks(in: range, gap: gap, force: force)
    }

    static func parse(file path: String,
                      accountHome: String) throws -> [ActivityFileIndex.IndexedSession] {
        var cwd: String?
        var timestamps: [Double] = []
        try ActivityParsers.jsonl(path) { line in
            switch line["type"] as? String {
            case "session_start":
                cwd = cwd ?? line["cwd"] as? String
            case "message":
                if let ts = ActivityParsers.timestamp(line) {
                    timestamps.append(ts.timeIntervalSince1970)
                }
            default: break
            }
        }
        guard !timestamps.isEmpty else { return [] }
        let url = URL(fileURLWithPath: path)
        return [.init(sessionId: url.deletingPathExtension().lastPathComponent,
                      accountHome: accountHome, cwd: cwd,
                      timestamps: timestamps.sorted())]
    }
}

/// Pi: `~/.pi/agent/sessions/<project>/<ts>_<uuid>.jsonl`; swe's session
/// id is the FULL file path. A `session` line carries the cwd, `message`
/// lines the times.
public struct PiActivity: ActivityProviding {
    private let source: FileActivitySource

    public init(sessionsRoot: URL, accountHome: String, index: ActivityFileIndex,
                filter: (@Sendable ([ActivityFileIndex.IndexedSession]) async -> [ActivityFileIndex.IndexedSession])? = nil) {
        source = FileActivitySource(
            harness: "pi",
            index: index,
            files: { ActivityParsers.jsonlFiles(root: sessionsRoot) },
            parse: { try Self.parse(file: $0, accountHome: accountHome) },
            filter: filter)
    }

    public func activityBlocks(in range: DateInterval, gap: TimeInterval,
                               force: Bool) async throws -> [ActivityBlock] {
        try await source.activityBlocks(in: range, gap: gap, force: force)
    }

    static func parse(file path: String,
                      accountHome: String) throws -> [ActivityFileIndex.IndexedSession] {
        var cwd: String?
        var timestamps: [Double] = []
        try ActivityParsers.jsonl(path) { line in
            switch line["type"] as? String {
            case "session":
                cwd = cwd ?? line["cwd"] as? String
            case "message":
                if let ts = ActivityParsers.timestamp(line) {
                    timestamps.append(ts.timeIntervalSince1970)
                }
            default: break
            }
        }
        guard !timestamps.isEmpty else { return [] }
        return [.init(sessionId: path, accountHome: accountHome, cwd: cwd,
                      timestamps: timestamps.sorted())]
    }
}

/// Freebuff (Manicode): `<projects>/<project>/chats/<chat>/run-state.json`;
/// the chat directory name is swe's session id and `mainAgentState`'s
/// message history carries `sentAt` epoch milliseconds.
public struct FreebuffActivity: ActivityProviding {
    private let source: FileActivitySource

    public init(projectsRoot: URL, accountHome: String, index: ActivityFileIndex) {
        source = FileActivitySource(
            harness: "freebuff",
            index: index,
            files: { Self.files(root: projectsRoot) },
            parse: { try Self.parse(file: $0, accountHome: accountHome) })
    }

    public func activityBlocks(in range: DateInterval, gap: TimeInterval,
                               force: Bool) async throws -> [ActivityBlock] {
        try await source.activityBlocks(in: range, gap: gap, force: force)
    }

    /// Every `<project>/chats/<chat>/run-state.json` under the root.
    static func files(root: URL) -> [String: ActivityFileIndex.FileStamp] {
        var files: [String: ActivityFileIndex.FileStamp] = [:]
        let enumerator = FileManager.default.enumerator(
            at: root, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles])
        while let url = enumerator?.nextObject() as? URL {
            guard url.lastPathComponent == "run-state.json",
                  url.deletingLastPathComponent()
                      .deletingLastPathComponent().lastPathComponent == "chats",
                  let stamp = ActivityFileIndex.stamp(url)
            else { continue }
            files[url.path] = stamp
        }
        return files
    }

    static func parse(file path: String,
                      accountHome: String) throws -> [ActivityFileIndex.IndexedSession] {
        guard let data = FileManager.default.contents(atPath: path),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let state = root["sessionState"] as? [String: Any],
              let agent = state["mainAgentState"] as? [String: Any],
              let history = agent["messageHistory"] as? [[String: Any]]
        else { return [] }
        let timestamps = history.compactMap { message -> Double? in
            guard let sent = message["sentAt"] as? NSNumber else { return nil }
            return sent.doubleValue / 1000
        }.sorted()
        guard !timestamps.isEmpty else { return [] }
        let url = URL(fileURLWithPath: path)
        let cwd = (agent["cwd"] as? String) ?? (agent["initialCwd"] as? String)
            ?? (state["initialCwd"] as? String) ?? (root["initialCwd"] as? String)
        return [.init(sessionId: url.deletingLastPathComponent().lastPathComponent,
                      accountHome: accountHome, cwd: cwd, timestamps: timestamps)]
    }
}
