import Foundation
import Testing
@testable import QuiverCore

/// `ActivityBlocks.make` mirrors the ledger's SQL block rule; these are
/// the same cases `CalendarActivityTests` puts through the query.
@Suite struct ActivityBlocksTests {
    private let range = DateInterval(start: utc("2026-10-14T00:00:00Z"),
                                     end: utc("2026-10-15T00:00:00Z"))

    private func make(_ times: [Date]) -> [ActivityBlock] {
        ActivityBlocks.make(harness: "pi", accountHome: "/h", sessionId: "s",
                            cwd: "/w", timestamps: times, range: range,
                            gap: CalendarModel.gap)
    }

    @Test func responsesInsideTheGapJoinIntoOneBlock() {
        let blocks = make([utc("2026-10-14T10:00:00Z"),
                           utc("2026-10-14T10:30:00Z")])
        #expect(blocks.count == 1)
        #expect(blocks[0].start == utc("2026-10-14T10:00:00Z"))
        #expect(blocks[0].end == utc("2026-10-14T10:30:00Z"))
        #expect(blocks[0].responses == 2)
        #expect(blocks[0].sessions[0].cwd == "/w")
    }

    @Test func aGapLongerThanTheLimitStartsANewBlock() {
        let blocks = make([utc("2026-10-14T10:00:00Z"),
                           utc("2026-10-14T10:30:01Z")])
        #expect(blocks.count == 2)
    }

    @Test func aBlockStraddlingTheRangeEdgeStaysWhole() {
        let blocks = make([utc("2026-10-13T23:50:00Z"),
                           utc("2026-10-14T00:04:00Z")])
        #expect(blocks.count == 1)
        #expect(blocks[0].start == utc("2026-10-13T23:50:00Z"))
    }

    @Test func blocksOutsideTheRangeAreDropped() {
        #expect(make([utc("2026-10-12T10:00:00Z")]).isEmpty)
        #expect(make([utc("2026-10-15T00:10:00Z")]).isEmpty)
    }

    @Test func aSingleResponseIsOneBlock() {
        let blocks = make([utc("2026-10-14T09:00:00Z")])
        #expect(blocks.count == 1)
        #expect(blocks[0].responses == 1)
    }
}

/// `groupByProject` re-keys session blocks into project blocks with the
/// same gap rule, and `ProjectResolver` maps folders to projects with an
/// injectable `.git` probe.
@Suite struct ProjectGroupingTests {
    private let gap = CalendarModel.gap

    private func block(_ start: String, minutes: Int = 30, harness: String = "claude",
                       home: String = "/h", session: String, cwd: String? = nil) -> ActivityBlock {
        let start = utc(start)
        return ActivityBlock(harness: harness, accountHome: home, sessionId: session,
                             start: start,
                             end: start.addingTimeInterval(TimeInterval(minutes * 60)),
                             responses: 1, cwd: cwd)
    }

    /// Folders: /repo is a repository; /repo/src is inside it;
    /// /repo/.worktrees/feat is a linked worktree of it; /plain has none.
    private func resolver() -> ProjectResolver {
        ProjectResolver { folder in
            switch folder {
            case "/repo": return .repository
            case "/repo/.worktrees/feat":
                return .worktree("gitdir: /repo/.git/worktrees/feat")
            default: return nil
            }
        }
    }

    @Test func twoSessionsInOneRepoMergeIntoAProjectBlock() {
        let resolve = resolver()
        let blocks = ActivityBlocks.groupByProject([
            block("2026-10-14T09:00:00Z", session: "a", cwd: "/repo"),
            // Ten minutes after the first session's end: inside the gap.
            block("2026-10-14T09:40:00Z", session: "b", cwd: "/repo"),
        ], gap: gap, project: resolve.identity(of:))
        #expect(blocks.count == 1)
        #expect(blocks[0].project == "repo")
        #expect(blocks[0].sessions.map(\.sessionId) == ["a", "b"])
        #expect(blocks[0].sessions[0].end == utc("2026-10-14T09:30:00Z"))
        #expect(blocks[0].sessions[1].start == utc("2026-10-14T09:40:00Z"))
        #expect(blocks[0].responses == 2)
        #expect(blocks[0].start == utc("2026-10-14T09:00:00Z"))
        #expect(blocks[0].end == utc("2026-10-14T10:10:00Z"))
    }

    @Test func parallelSessionsShareOneBlock() {
        let resolve = resolver()
        let blocks = ActivityBlocks.groupByProject([
            block("2026-10-14T09:00:00Z", minutes: 120, session: "a", cwd: "/repo"),
            block("2026-10-14T09:30:00Z", minutes: 60, session: "b", cwd: "/repo"),
        ], gap: gap, project: resolve.identity(of:))
        #expect(blocks.count == 1)
        // The union span: 09:00 to 11:00, not the sessions' sum.
        #expect(blocks[0].start == utc("2026-10-14T09:00:00Z"))
        #expect(blocks[0].end == utc("2026-10-14T11:00:00Z"))
        #expect(blocks[0].sessions.count == 2)
    }

    @Test func aSubfolderAndAWorktreeResolveToTheRepo() {
        let resolve = resolver()
        let blocks = ActivityBlocks.groupByProject([
            block("2026-10-14T09:00:00Z", session: "a", cwd: "/repo/src"),
            block("2026-10-14T09:40:00Z", session: "b", cwd: "/repo/.worktrees/feat"),
        ], gap: gap, project: resolve.identity(of:))
        #expect(blocks.count == 1)
        #expect(blocks[0].project == "repo")
    }

    @Test func harnessesAndAccountsStayApart() {
        let resolve = resolver()
        let blocks = ActivityBlocks.groupByProject([
            block("2026-10-14T09:00:00Z", session: "a", cwd: "/repo"),
            block("2026-10-14T09:10:00Z", harness: "codex", session: "b", cwd: "/repo"),
            block("2026-10-14T09:20:00Z", home: "/work", session: "c", cwd: "/repo"),
        ], gap: gap, project: resolve.identity(of:))
        #expect(blocks.count == 3)
        #expect(Set(blocks.map { "\($0.harness)|\($0.accountHome)" })
            == ["claude|/h", "codex|/h", "claude|/work"])
    }

    @Test func sameNamedFoldersInDifferentReposStayApart() {
        // /work/client-a/app and /work/client-b/app are both "app" to
        // show, but the grouping key is the repository root path.
        let resolve = ProjectResolver { folder in
            folder == "/work/client-a/app" || folder == "/work/client-b/app"
                ? .repository : nil
        }
        let blocks = ActivityBlocks.groupByProject([
            block("2026-10-14T09:00:00Z", session: "a", cwd: "/work/client-a/app"),
            block("2026-10-14T09:10:00Z", session: "b", cwd: "/work/client-b/app"),
        ], gap: gap, project: resolve.identity(of:))
        #expect(blocks.count == 2)
        #expect(Set(blocks.map(\.project)) == ["app"])
        #expect(Set(blocks.map(\.projectKey))
            == ["/work/client-a/app", "/work/client-b/app"])
    }

    @Test func aWorktreeJoinsItsMainRepoByPath() {
        let resolve = resolver()
        let blocks = ActivityBlocks.groupByProject([
            block("2026-10-14T09:00:00Z", session: "a", cwd: "/repo"),
            block("2026-10-14T09:10:00Z", session: "b", cwd: "/repo/.worktrees/feat"),
        ], gap: gap, project: resolve.identity(of:))
        #expect(blocks.count == 1)
        #expect(blocks[0].projectKey == "/repo")
        #expect(blocks[0].project == "repo")
        #expect(blocks[0].sessions.count == 2)
    }

    @Test func codexWorktreesUnderTheSameHomeStayApart() {
        // Deleted Codex worktrees live under ~/.codex/worktrees/<id>/<name>;
        // ~/.codex is not a repo root, so each worktree is its own
        // identity even though they share the home.
        let resolve = ProjectResolver { _ in nil }
        let blocks = ActivityBlocks.groupByProject([
            block("2026-10-14T09:00:00Z", session: "a",
                  cwd: "/Users/k/.codex/worktrees/aaa/project-a"),
            block("2026-10-14T09:10:00Z", session: "b",
                  cwd: "/Users/k/.codex/worktrees/bbb/project-b"),
        ], gap: gap, project: resolve.identity(of:))
        #expect(blocks.count == 2)
        #expect(Set(blocks.map(\.project)) == ["project-a", "project-b"])
        #expect(Set(blocks.map(\.projectKey))
            == ["/Users/k/.codex/worktrees/aaa/project-a",
                "/Users/k/.codex/worktrees/bbb/project-b"])
    }

    @Test func sessionsWithoutAFolderGroupUnderNoFolder() {
        let blocks = ActivityBlocks.groupByProject([
            block("2026-10-14T09:00:00Z", session: "a"),
            block("2026-10-14T09:20:00Z", session: "b"),
        ], gap: gap, project: resolver().identity(of:))
        #expect(blocks.count == 1)
        #expect(blocks[0].project == ProjectResolver.noFolder)
        #expect(blocks[0].projectKey == "")
    }

    @Test func theResolverMemoizesPerFolder() {
        let count = Box(0)
        let resolve = ProjectResolver { folder in
            count.value += 1
            return folder == "/repo" ? .repository : nil
        }
        #expect(resolve.project(of: "/repo/src") == "repo")
        #expect(resolve.project(of: "/repo") == "repo")
        let first = count.value
        _ = resolve.project(of: "/repo/src")
        _ = resolve.project(of: "/repo")
        #expect(count.value == first)
        #expect(resolve.project(of: nil) == ProjectResolver.noFolder)
    }
}

extension ActivityBlocksTests {
    @Test func rootAndEmptyFoldersAreNoFolder() {
        let resolve = ProjectResolver { _ in nil }
        #expect(resolve.project(of: "/") == ProjectResolver.noFolder)
        #expect(resolve.project(of: "") == ProjectResolver.noFolder)
        #expect(resolve.project(of: nil) == ProjectResolver.noFolder)
        #expect(resolve.identity(of: "/") == ("", ProjectResolver.noFolder))
    }
}
