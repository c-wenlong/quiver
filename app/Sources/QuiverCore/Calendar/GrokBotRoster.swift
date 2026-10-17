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
public final class GrokBotRoster: Sendable {
    /// The shared instance the calendar uses. Reads are cached and the
    /// roster file is only re-parsed when its modification date changes.
    public static let shared = GrokBotRoster()

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
        (checkedAt: Date, mtime: Date?, names: [String: String])?>(initialState: nil)

    /// `directory` is injectable so tests point at fixtures rather than
    /// the real Application Support folder.
    public init(directory: URL? = nil, recheckInterval: TimeInterval = 30) {
        self.directory = directory ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Grok Bot/sand-client-persistence",
                                    isDirectory: true)
        self.recheckInterval = recheckInterval
    }

    /// A `grok-bot` session row's title: the Bot's name when the roster
    /// has it, "Subagent" for a `sand-subagent-` id, else the usual
    /// session-id fallback.
    public func title(for sessionId: String) -> String {
        if let name = names[sessionId], !name.isEmpty { return name }
        if sessionId.hasPrefix(Self.subagentPrefix) { return "Subagent" }
        return CalendarFormat.sessionTitle(nil, sessionId: sessionId)
    }

    /// The roster's id → name map. Within `recheckInterval` of the last
    /// disk check it is served from memory with no file-system access;
    /// after that the folder is listed again and the blob re-parsed only
    /// when its modification date moved. A missing file or any read or
    /// decode failure returns (and caches) an empty map.
    private var names: [String: String] {
        let now = Date()
        if let cached = cache.withLock({ $0 }),
           now.timeIntervalSince(cached.checkedAt) < recheckInterval {
            return cached.names
        }
        let file = rosterFile()
        let mtime = file.flatMap { try? $0.resourceValues(forKeys: [.contentModificationDateKey])
            .contentModificationDate }
        return cache.withLock { cached in
            if let previous = cached, previous.mtime == mtime {
                cached = (now, mtime, previous.names)
                return previous.names
            }
            let names = file.flatMap(Self.parse) ?? [:]
            cached = (now, mtime, names)
            return names
        }
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
