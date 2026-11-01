import Foundation
import Observation

/// Sessions search. The hub's `/v1/search` matches transcripts through
/// the Quiver app's FTS index when that index exists, and always matches
/// titles, harness and folder as case-insensitive text (as the Mac app
/// does). A hub too old to have the route gets the same metadata match
/// here, over the list already loaded.
@MainActor @Observable
final class SessionSearch {
    var query = ""
    private(set) var results: [SearchResult] = []
    /// The hub searched transcripts too, not just metadata.
    private(set) var indexed = false
    private(set) var searching = false
    private(set) var error: String?
    /// The query the current `results` answer, so a slow reply to an old
    /// query never replaces a newer one.
    private(set) var answered = ""

    var isActive: Bool { !query.trimmingCharacters(in: .whitespaces).isEmpty }

    func run(app: AppModel) async {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else {
            results = []
            answered = ""
            error = nil
            return
        }
        // Wait for a pause in typing; a newer keystroke cancels this task.
        try? await Task.sleep(for: .milliseconds(250))
        guard !Task.isCancelled, let client = app.client else { return }
        searching = true
        defer { searching = false }
        do {
            let body = try await client.search(q)
            guard !Task.isCancelled else { return }
            results = body.results
            indexed = body.indexed ?? false
            error = nil
        } catch let e as HubError where e.status == 404 {
            results = Self.localMatches(app.displaySessions, query: q)
            indexed = false
            error = nil
        } catch {
            guard !Task.isCancelled else { return }
            results = Self.localMatches(app.displaySessions, query: q)
            indexed = false
            self.error = error.localizedDescription
        }
        answered = q
    }

    /// Metadata match over loaded rows, ranked the way the hub ranks them
    /// (`src/swe/remote/search.py`): titles first, then the rest of the
    /// details, each holding the whole query or every word of it; within
    /// a tier, the whole query beats the words apart, scaled by
    /// `recencyWeight`. Plain text, never a regex, so no typed pattern can
    /// stall it.
    static func localMatches(_ rows: [SessionRow], query: String, limit: Int = 50,
                             now: Double = Date().timeIntervalSince1970) -> [SearchResult] {
        let needle = query.lowercased()
        let words = needle.split(whereSeparator: \.isWhitespace).map(String.init)
        func match(_ text: String) -> Double {
            if text.contains(needle) { return 1 }
            if words.count > 1, words.allSatisfy(text.contains) { return 0.7 }
            return 0
        }
        let ranked: [(row: SessionRow, tier: Int, score: Double)] = rows.compactMap { row in
            let weight = recencyWeight(row.lastActiveAt, now: now)
            let title = match((row.title ?? "").lowercased())
            if title > 0 { return (row, 0, title * weight) }
            let details = match([row.title, row.agent, row.harness, row.cwd, row.key].compactMap { $0 }
                .joined(separator: " ").lowercased())
            if details > 0 { return (row, 1, details * weight) }
            return nil
        }
        return ranked
            .sorted { $0.tier != $1.tier ? $0.tier < $1.tier : $0.score > $1.score }
            .prefix(limit)
            .map { SearchResult(key: $0.row.key, source: "metadata", snippet: nil, markedSnippet: nil, role: nil, session: $0.row) }
    }

    /// 1 for a session active now, falling by half every 14 days toward a
    /// floor of 0.3, the hub's `recency_weight`.
    static func recencyWeight(_ lastActiveAt: Double?, now: Double) -> Double {
        let age = max(0, now - (lastActiveAt ?? 0))
        return 0.3 + 0.7 * pow(0.5, age / 86400 / 14)
    }

    /// The index marks matched words with U+E000 ... U+E001; split them
    /// out so the view can bold the matches.
    static func snippetRuns(_ marked: String) -> [(text: String, hit: Bool)] {
        var runs: [(String, Bool)] = []
        var current = ""
        var inHit = false
        for ch in marked {
            if ch == "\u{E000}" || ch == "\u{E001}" {
                if !current.isEmpty { runs.append((current, inHit)) }
                current = ""
                inHit = ch == "\u{E000}"
            } else {
                current.append(ch)
            }
        }
        if !current.isEmpty { runs.append((current, inHit)) }
        return runs
    }
}
