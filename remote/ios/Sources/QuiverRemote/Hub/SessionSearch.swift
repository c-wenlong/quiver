import Foundation
import Observation

/// Sessions search. The hub's `/v1/search` matches transcripts through
/// the Quiver app's FTS index when that index exists, and always matches
/// titles, harness and folder as a case-insensitive regex. A hub too old
/// to have the route gets the same metadata match here, over the list
/// already loaded.
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
            self.error = (error as? HubError)?.message ?? error.localizedDescription
        }
        answered = q
    }

    /// Metadata match over loaded rows: a case-insensitive regex, or the
    /// literal text when the query isn't a valid pattern. Newest first.
    static func localMatches(_ rows: [SessionRow], query: String, limit: Int = 50) -> [SearchResult] {
        let regex = (try? NSRegularExpression(pattern: query, options: [.caseInsensitive]))
            ?? (try? NSRegularExpression(pattern: NSRegularExpression.escapedPattern(for: query), options: [.caseInsensitive]))
        guard let regex else { return [] }
        return rows
            .filter { row in
                let text = [row.title, row.agent, row.harness, row.cwd, row.key].compactMap { $0 }.joined(separator: " ")
                return regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
            }
            .sorted { ($0.lastActiveAt ?? 0) > ($1.lastActiveAt ?? 0) }
            .prefix(limit)
            .map { SearchResult(key: $0.key, source: "metadata", snippet: nil, markedSnippet: nil, role: nil, session: $0) }
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
