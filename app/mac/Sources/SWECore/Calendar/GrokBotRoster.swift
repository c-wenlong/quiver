import Foundation
import os

/// Grok Bot's local Bot list. The feed carries only a conversation id,
/// which is the Bot's id, so a `grok-bot` session row looks up the Bot's
/// name here. Blocks do not split by Bot: Bots hand one task between
/// them, so every Bot shares one Calendar lane (`ProjectResolver`).
///
/// Source: `~/Library/Application Support/Grok Bot/sand-client-persistence/`
/// holds `*.blob` files whose stems are lowercase unpadded RFC 4648
/// base32 encodings of keys; the roster's key ends with
/// `.roster.last-roster` and its JSON is
/// `{"schemaVersion": n, "value": {"rows": [{"id", "name", …}]}}`. Only
/// `id` and `name` are read — never `lastEntry` or any other field, and
/// no other blob (transcripts, credentials) is ever opened. A missing
/// directory, unreadable or malformed blob yields an empty map: a row
/// then falls back to the short session id.
///
/// Deleting a Bot drops it from the roster, and Grok Bot keeps no name
/// for it. So every id and name read is also remembered in `memory`
/// (`~/.swe/data/grok-bot-names.json`, SWE's own copy), never
/// pruned, and a Bot deleted later still titles by its last name. An id
/// in neither, while the roster names Bots, is a Bot deleted before SWE
/// saw it: "Deleted Bot (<short id>)".
public final class GrokBotRoster: Sendable {
    /// The shared instance the calendar uses. Reads are cached and the
    /// roster file is only re-parsed when its modification date changes.
    public static let shared = GrokBotRoster(memory: FileManager.default.homeDirectoryForCurrentUser
        .appending(path: ".swe/data/grok-bot-names.json"))

    /// `grok-bot` sessions under a Bot that is not a top-level Bot of its
    /// own: subagent runs, titled "Subagent".
    private static let subagentPrefix = "sand-subagent-"
    /// The filename key suffix marking the roster blob among the other
    /// persistence blobs (transcripts, secrets — none of which we open).
    private static let rosterSuffix = ".roster.last-roster"

    private let directory: URL
    /// How long a lookup trusts the last disk check. Titles are read while
    /// drawing rows, so the folder listing and stat
    /// happen at most this often, never once per row.
    private let recheckInterval: TimeInterval
    /// Decoded id → name, with when the disk was last checked and the
    /// modification date it was read at, so a rewritten blob re-parses
    /// once and an unchanged one is never re-listed within the interval.
    private let cache = OSAllocatedUnfairLock<
        (checkedAt: Date, mtime: Date?, known: Known)?>(initialState: nil)

    /// What one roster read knows: every name, live or remembered; the
    /// ids the roster lists now, named or not; and whether it named at
    /// least one Bot, the only case where an unlisted id means deleted.
    private struct Known: Sendable {
        var names: [String: String] = [:]
        var live: Set<String> = []
        var read = false
    }
    /// Where every Bot name ever read is kept; nil remembers nothing
    /// (tests, unless they pass a temporary file).
    private let memory: URL?

    /// `directory` and `memory` are injectable so tests point at fixtures
    /// rather than the real Application Support folder and `~/.swe`.
    public init(directory: URL? = nil, memory: URL? = nil, recheckInterval: TimeInterval = 30) {
        self.memory = memory
        self.directory = directory ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Grok Bot/sand-client-persistence",
                                    isDirectory: true)
        self.recheckInterval = recheckInterval
    }

    /// A `grok-bot` session row's title: the Bot's name, live or
    /// remembered; "Subagent" for a `sand-subagent-` id; "Deleted Bot
    /// (<short id>)" for a conversation id the roster, naming Bots, does
    /// not list; else the usual session-id fallback (an unnamed live Bot,
    /// a daily `cursor-YYYY-MM-DD` bucket, or an unread roster).
    public func title(for sessionId: String) -> String {
        let known = lookup
        if let name = known.names[sessionId], !name.isEmpty { return name }
        if sessionId.hasPrefix(Self.subagentPrefix) { return "Subagent" }
        let short = CalendarFormat.sessionTitle(nil, sessionId: sessionId)
        guard known.read, !known.live.contains(sessionId),
              !CursorUsageFeed.isDayBucket(sessionId) else { return short }
        return "Deleted Bot (\(short))"
    }

    /// The roster merged over every remembered name. Within
    /// `recheckInterval` of the last disk check it is served from memory
    /// with no file-system access; after that the folder is listed again
    /// and the blob re-parsed only when its modification date moved. A
    /// missing file or any read or decode failure leaves only the
    /// remembered names, with `read` false.
    private var lookup: Known {
        let now = Date()
        if let cached = cache.withLock({ $0 }),
           now.timeIntervalSince(cached.checkedAt) < recheckInterval {
            return cached.known
        }
        let file = rosterFile()
        let mtime = file.flatMap { try? $0.resourceValues(forKeys: [.contentModificationDateKey])
            .contentModificationDate }
        return cache.withLock { cached in
            if let previous = cached, previous.mtime == mtime {
                cached = (now, mtime, previous.known)
                return previous.known
            }
            let live = file.flatMap(Self.parse) ?? [:]
            let named = live.filter { !$0.value.isEmpty }
            // Names this process already knows stay even if the file
            // write below failed, so a Bot deleted next is not lost.
            let known = Known(names: remember(named, alsoKnown: cached?.known.names ?? [:]),
                              live: Set(live.keys), read: !named.isEmpty)
            cached = (now, mtime, known)
            return known
        }
    }

    /// The saved names, then `alsoKnown`, then `live` merged over them (a
    /// renamed Bot takes its new name), written back only when the file
    /// is missing something. Only ids and names are stored, the same two
    /// fields `parse` reads.
    private func remember(_ live: [String: String],
                          alsoKnown: [String: String]) -> [String: String] {
        guard let memory else { return alsoKnown.merging(live) { _, new in new } }
        let saved = (try? Data(contentsOf: memory))
            .flatMap { try? JSONDecoder().decode([String: String].self, from: $0) } ?? [:]
        let merged = saved.merging(alsoKnown) { _, new in new }.merging(live) { _, new in new }
        if merged != saved {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            do {
                try FileManager.default.createDirectory(at: memory.deletingLastPathComponent(),
                                                        withIntermediateDirectories: true)
                try encoder.encode(merged).write(to: memory, options: .atomic)
            } catch {
                Logger.calendar.error("grok bot names not saved: \(error.localizedDescription, privacy: .public)")
            }
        }
        return merged
    }

    /// The one blob whose decoded filename key ends with the roster
    /// suffix. Every stem is base32-decoded; undecodable or non-roster
    /// names are skipped without ever opening the file.
    private func rosterFile() -> URL? {
        guard let entries = try? FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]) else { return nil }
        var best: (url: URL, mtime: Date?)?
        for url in entries where url.pathExtension == "blob" {
            guard let key = Self.decodeBase32(url.deletingPathExtension().lastPathComponent),
                  key.hasSuffix(Self.rosterSuffix) else { continue }
            let mtime = try? url.resourceValues(forKeys: [.contentModificationDateKey])
                .contentModificationDate
            if let found = best,
               (mtime ?? .distantPast) <= (found.mtime ?? .distantPast) { continue }
            best = (url: url, mtime: mtime)
        }
        return best?.url
    }

    /// id → name from the roster blob. Only `id` and `name` leave the
    /// decoded object — other fields (transcripts, lastEntry, secrets)
    /// are never touched. Any malformed shape returns nil → empty map.
    private static func parse(_ url: URL) -> [String: String]? {
        guard let data = try? Data(contentsOf: url),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let value = root["value"] as? [String: Any],
              let rows = value["rows"] as? [[String: Any]] else { return nil }
        var names: [String: String] = [:]
        for row in rows {
            if let id = row["id"] as? String, let name = row["name"] as? String {
                names[id] = name
            }
        }
        return names
    }

    /// RFC 4648 base32, lowercase, unpadded — the persistence layer's
    /// filename encoding. Returns nil for any character outside the
    /// alphabet rather than guessing.
    static func decodeBase32(_ stem: String) -> String? {
        var value: UInt32 = 0
        var bits = 0
        var bytes: [UInt8] = []
        for scalar in stem.lowercased().unicodeScalars {
            guard scalar >= "a", scalar <= "z" else {
                guard let digit = scalar.value >= 50 && scalar.value <= 55
                    ? scalar.value - 50 + 26 : nil else { return nil }
                value = value << 5 | digit
                bits += 5
                if bits >= 8 {
                    bytes.append(UInt8((value >> UInt32(bits - 8)) & 0xff))
                    bits -= 8
                }
                continue
            }
            value = value << 5 | (scalar.value - 97)
            bits += 5
            if bits >= 8 {
                bytes.append(UInt8((value >> UInt32(bits - 8)) & 0xff))
                bits -= 8
            }
        }
        return String(bytes: bytes, encoding: .utf8)
    }

    /// RFC 4648 base32 of raw bytes, lowercase without padding — the
    /// mirror of `decodeBase32`, used by tests to name fixture blobs.
    static func encodeBase32(_ key: String) -> String {
        let alphabet = Array("abcdefghijklmnopqrstuvwxyz234567".utf8)
        var value: UInt32 = 0
        var bits = 0
        var out: [UInt8] = []
        for byte in key.utf8 {
            value = value << 8 | UInt32(byte)
            bits += 8
            while bits >= 5 {
                out.append(alphabet[Int((value >> UInt32(bits - 5)) & 0x1f)])
                bits -= 5
            }
        }
        if bits > 0 {
            out.append(alphabet[Int((value << UInt32(5 - bits)) & 0x1f)])
        }
        return String(decoding: out, as: UTF8.self)
    }
}
