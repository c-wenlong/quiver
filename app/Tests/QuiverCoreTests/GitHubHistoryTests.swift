import Foundation
import Testing
@testable import QuiverCore

/// The focus page's on-disk history: per-(repo, number) rows that
/// upsert, come back newest-first, trim to the cap and drop with
/// the repo.
@Suite struct GitHubHistoryTests {

    private func tempURL() -> URL {
        FileManager.default.temporaryDirectory
            .appending(path: "gh-history-\(UUID().uuidString).db")
    }

    private func pr(_ n: Int) -> GhPr {
        GhPr(number: n, title: "Fix \(n)", state: .open, draft: false,
             author: "me", branch: "f", base: "main", labels: [],
             mergeable: .mergeable, reviewDecision: .approved,
             checks: .passing, updatedAt: GhTime(Date()),
             url: "https://github.com/o/a/pull/\(n)")
    }

    @Test func rowsComeBackNewestFirst() {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        let store = GitHubHistory(url: url)
        store.remember(repo: "o/a", pulls: [pr(3), pr(9), pr(5)])
        #expect(store.pulls(repo: "o/a").map(\.number) == [9, 5, 3])
        #expect(store.pulls(repo: "o/b").isEmpty)
    }

    /// An upsert keeps the latest shape under the same key — a PR that
    /// merged since it was remembered comes back merged.
    @Test func upsertRefreshesARow() {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        let store = GitHubHistory(url: url)
        store.remember(repo: "o/a", pulls: [pr(7)])
        var merged = pr(7)
        merged = GhPr(number: 7, title: "Fix 7", state: .merged, draft: false,
                      author: "me", branch: "f", base: "main", labels: [],
                      mergeable: .mergeable, reviewDecision: .approved,
                      checks: .passing, updatedAt: GhTime(Date()),
                      url: "https://github.com/o/a/pull/7")
        store.remember(repo: "o/a", pulls: [merged])
        #expect(store.pulls(repo: "o/a").first?.state == .merged)
    }

    /// Only the newest `cap` rows persist per repo per surface.
    @Test func rememberTrimsToTheCap() {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        let store = GitHubHistory(url: url)
        store.remember(repo: "o/a", pulls: (1...510).map(pr))
        let kept = store.pulls(repo: "o/a")
        #expect(kept.count == GitHubHistory.cap)
        #expect(kept.first?.number == 510)
        #expect(kept.last?.number == 11)
    }

    @Test func removeDropsOnlyThatRepo() {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }
        let store = GitHubHistory(url: url)
        store.remember(repo: "o/a", pulls: [pr(1)])
        store.remember(repo: "o/b", pulls: [pr(2)])
        store.remove(repo: "o/a")
        #expect(store.pulls(repo: "o/a").isEmpty)
        #expect(store.pulls(repo: "o/b").count == 1)
    }

    /// A file that cannot open reports it once and reads empty —
    /// the page fetches as if nothing were remembered.
    @Test func unopenableFileAnswersEmpty() {
        let store = GitHubHistory(url: URL(fileURLWithPath: "/dev/null/x.db"))
        #expect(store.openError != nil)
        #expect(store.pulls(repo: "o/a").isEmpty)
    }
}
