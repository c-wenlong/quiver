import Foundation
import Testing
@testable import SWECore

/// The GitHub pages' on-disk row store: per-(repo, surface, key) rows
/// that upsert in board order, accumulate or replace by surface, trim
/// to the cap, and drop with the repo — plus the v1 table and
/// JSON-snapshot migrations that move old state in once.
@Suite struct GitHubHistoryTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }


    private func tempURL() -> URL {
        scratch.url
            .appending(path: "gh-history-\(UUID().uuidString).db")
    }

    private func pr(_ n: Int) -> GhPr {
        GhPr(number: n, title: "Fix \(n)", state: .open, draft: false,
             author: "me", branch: "f", base: "main", labels: [],
             mergeable: .mergeable, reviewDecision: .approved,
             checks: .passing, updatedAt: GhTime(Date()),
             url: "https://github.com/o/a/pull/\(n)")
    }

    private func tag(_ name: String) -> GhTag {
        GhTag(name: name, sha: "abc\(name)",
              url: "https://github.com/o/a/tree/\(name)")
    }

    /// Rows come back in the order the write put them down — the
    /// caller's merged sort is what persists.
    @Test func rowsReplayWriteOrder() {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        let store = GitHubHistory(url: url)
        store.remember(repo: "o/a", surface: .pulls, rows: [pr(9), pr(5), pr(3)])
        let pulls: [GhPr] = store.rows(repo: "o/a", surface: .pulls)
        #expect(pulls.map(\.number) == [9, 5, 3])
        let other: [GhPr] = store.rows(repo: "o/b", surface: .pulls)
        #expect(other.isEmpty)
    }

    /// An upsert keeps the latest shape under the same key — a PR that
    /// merged since it was remembered comes back merged.
    @Test func upsertRefreshesARow() {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        let store = GitHubHistory(url: url)
        store.remember(repo: "o/a", surface: .pulls, rows: [pr(7)])
        let merged = GhPr(number: 7, title: "Fix 7", state: .merged, draft: false,
                          author: "me", branch: "f", base: "main", labels: [],
                          mergeable: .mergeable, reviewDecision: .approved,
                          checks: .passing, updatedAt: GhTime(Date()),
                          url: "https://github.com/o/a/pull/7")
        store.remember(repo: "o/a", surface: .pulls, rows: [merged])
        let pulls: [GhPr] = store.rows(repo: "o/a", surface: .pulls)
        #expect(pulls.first?.state == .merged)
    }

    /// Accumulating surfaces keep rows a later write never mentioned —
    /// the fetch window slides but history holds.
    @Test func rememberAccumulatesPastTheWindow() {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        let store = GitHubHistory(url: url)
        store.remember(repo: "o/a", surface: .pulls, rows: [pr(9), pr(8), pr(7)])
        // The next window covers 10..7 — 9 and 8 stay.
        store.remember(repo: "o/a", surface: .pulls, rows: [pr(10), pr(7)])
        let pulls: [GhPr] = store.rows(repo: "o/a", surface: .pulls)
        #expect(pulls.count == 4)
        #expect(pulls.contains { $0.number == 9 })
        #expect(pulls.contains { $0.number == 8 })
        #expect(pulls.contains { $0.number == 10 })
    }

    /// Only the newest `cap` rows persist per repo per surface,
    /// whatever order the writes arrived in.
    @Test func rememberTrimsToTheCap() {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        let store = GitHubHistory(url: url)
        store.remember(repo: "o/a", surface: .pulls, rows: (1...510).reversed().map(pr))
        let kept: [GhPr] = store.rows(repo: "o/a", surface: .pulls)
        #expect(kept.count == GitHubHistory.cap)
        #expect(kept.first?.number == 510)
        #expect(kept.last?.number == 11)
    }

    /// A state-list surface mirrors the reply exactly — a tag the next
    /// fetch no longer lists is gone, not remembered.
    @Test func replaceDropsUnlistedRows() {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        let store = GitHubHistory(url: url)
        store.replace(repo: "o/a", surface: .tags, rows: [tag("v1"), tag("v2")])
        store.replace(repo: "o/a", surface: .tags, rows: [tag("v2")])
        let tags: [GhTag] = store.rows(repo: "o/a", surface: .tags)
        #expect(tags.map(\.name) == ["v2"])
    }

    /// The fetched marker separates "asked, got none" from "never
    /// asked" — an empty written surface boards as `.loaded([])`, an
    /// unwritten one stays `.idle`.
    @Test func boardSeparatesEmptyFromUnfetched() {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        let store = GitHubHistory(url: url)
        let empty: [GhRelease] = []
        store.replace(repo: "o/a", surface: .releases, rows: empty)
        #expect(store.board(repo: "o/a").releases == .loaded([]))
        #expect(store.board(repo: "o/a").tags == .idle)
    }

    /// Pages collapses its single stored row into the board's optional —
    /// fetched-and-empty means the repo has no site.
    @Test func boardCollapsesThePagesRow() {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        let store = GitHubHistory(url: url)
        // No write yet — never asked.
        #expect(store.board(repo: "o/a").pages == .idle)
        // Asked: repo has no site.
        let none: [GhPages] = []
        store.replace(repo: "o/a", surface: .pages, rows: none) { _ in "site" }
        #expect(store.board(repo: "o/a").pages == .loaded(nil))
    }

    @Test func removeDropsRowsAndMarkers() {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        let store = GitHubHistory(url: url)
        store.remember(repo: "o/a", surface: .pulls, rows: [pr(1)])
        store.replace(repo: "o/a", surface: .tags, rows: [tag("v1")])
        store.remember(repo: "o/b", surface: .pulls, rows: [pr(2)])
        store.remove(repo: "o/a")
        let pullsA: [GhPr] = store.rows(repo: "o/a", surface: .pulls)
        #expect(pullsA.isEmpty)
        // The marker went too — a re-pin starts from a fetch, not a
        // resurrected "asked, got none" card.
        #expect(store.board(repo: "o/a").tags == .idle)
        let pullsB: [GhPr] = store.rows(repo: "o/b", surface: .pulls)
        #expect(pullsB.count == 1)
    }

    /// A file that cannot open reports it once and reads empty —
    /// the page fetches as if nothing were remembered.
    @Test func unopenableFileAnswersEmpty() {
        let store = GitHubHistory(url: URL(fileURLWithPath: "/dev/null/x.db"))
        #expect(store.openError != nil)
        let pulls: [GhPr] = store.rows(repo: "o/a", surface: .pulls)
        #expect(pulls.isEmpty)
    }

    /// The first schema's `pulls`/`issues` tables fold into `history`
    /// at open, newest-first, with the fetched marker set.
    @Test func v1TablesMigrateIntoHistory() throws {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        // Hand-build a v1 database on the raw wrapper.
        let db = try SQLiteDatabase(url: url)
        try db.execute("""
            CREATE TABLE pulls (repo TEXT, number INTEGER, payload TEXT,
                                PRIMARY KEY (repo, number));
            CREATE TABLE issues (repo TEXT, number INTEGER, payload TEXT,
                                 PRIMARY KEY (repo, number));
            """)
        let payload4 = String(data: try JSONEncoder().encode(pr(4)), encoding: .utf8)!
        let payload9 = String(data: try JSONEncoder().encode(pr(9)), encoding: .utf8)!
        try db.prepare("INSERT INTO pulls (repo, number, payload) VALUES (?, ?, ?)")
            .bind([.text("o/a"), .int(4), .text(payload4)]).run()
        try db.prepare("INSERT INTO pulls (repo, number, payload) VALUES (?, ?, ?)")
            .bind([.text("o/a"), .int(9), .text(payload9)]).run()

        let store = GitHubHistory(url: url)
        let pulls: [GhPr] = store.rows(repo: "o/a", surface: .pulls)
        #expect(pulls.map(\.number) == [9, 4])
        #expect(store.board(repo: "o/a").pulls == .loaded(pulls))
    }

    /// `importAll` lands snapshot rows and boards once — a repeated
    /// call, or a store that already imported, changes nothing.
    @Test func legacyImportIsIdempotent() {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        let store = GitHubHistory(url: url)
        var board = RepoBoard()
        board.pulls = .loaded([pr(9), pr(8)])
        board.tags = .loaded([tag("v1")])
        #expect(store.importAll(["o/a": RepoHistory(pulls: [pr(7)])],
                                boards: ["o/a": board]))
        #expect(store.importedLegacy)

        // The live store now knows newer rows — a repeat import must
        // not resurrect the snapshot's copy.
        store.remember(repo: "o/a", surface: .pulls, rows: [pr(20), pr(19)])
        #expect(store.importAll(["o/a": RepoHistory(pulls: [pr(7)])],
                                boards: ["o/a": board]))
        let pulls: [GhPr] = store.rows(repo: "o/a", surface: .pulls)
        // IGNOREd, not replaced: 20 and 19 stayed, 7/9/8 still fill in.
        #expect(pulls.count == 5)
        #expect(pulls.contains { $0.number == 20 })
        #expect(pulls.contains { $0.number == 7 })
        let tags: [GhTag] = store.rows(repo: "o/a", surface: .tags)
        #expect(tags.map(\.name) == ["v1"])
    }
}
