import Foundation

/// Compares the commit a dev build was stamped with against what its
/// source checkout looks like now. Read-only: the heaviest thing it can
/// do is `git fetch`, and only when asked to.
public struct LocalBuildCheck: Sendable {
    public enum State: Sendable, Equatable {
        /// HEAD is the stamped commit (or its descendant and ancestor at
        /// once): nothing newer to build.
        case upToDate
        /// HEAD is n commits ahead of the stamped commit.
        case behind(Int)
        /// The stamped commit and HEAD have both moved: the build came
        /// from another line of history.
        case diverged(buildOnly: Int, headOnly: Int)
        case notARepo
        /// The build carries no SWECommit stamp.
        case unknownBuild
        /// The stamped commit is not an object in this checkout.
        case builtCommitMissing
    }

    public struct Result: Sendable, Equatable {
        public var state: State
        /// The checkout has uncommitted changes the build does not contain.
        public var checkoutDirty: Bool
        /// The build itself was stamped dirty: SWEDirty was true.
        public var buildDirty: Bool
        /// Commits the configured upstream (or origin/HEAD) has that HEAD
        /// does not, nil when neither ref resolves.
        public var upstreamBehind: Int?

        public init(state: State, checkoutDirty: Bool = false, buildDirty: Bool = false,
                    upstreamBehind: Int? = nil) {
            self.state = state
            self.checkoutDirty = checkoutDirty
            self.buildDirty = buildDirty
            self.upstreamBehind = upstreamBehind
        }
    }

    private let runner: any CommandRunning

    public init(runner: any CommandRunning = ProcessRunner(executable: "git")) {
        self.runner = runner
    }

    /// The checkout's relationship to `builtCommit`. Every git failure is
    /// a state, never a throw.
    public func check(path: String, builtCommit: String?,
                      builtDirty: Bool = false) async -> Result {
        guard let head = await git(path, "rev-parse", "HEAD"),
              head.status == 0
        else { return Result(state: .notARepo) }
        guard let built = builtCommit, !built.isEmpty
        else { return Result(state: .unknownBuild) }
        let exists = await git(path, "cat-file", "-e", "\(built)^{commit}")
        guard exists?.status == 0 else { return Result(state: .builtCommitMissing) }

        var state: State = .upToDate
        if let counts = await git(path, "rev-list", "--left-right", "--count",
                                "\(built)...HEAD"), counts.status == 0 {
            let parts = String(decoding: counts.stdout, as: UTF8.self)
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .split(whereSeparator: { $0 == " " || $0 == "\t" })
                .compactMap { Int($0) }
            if parts.count == 2 {
                let (buildOnly, headOnly) = (parts[0], parts[1])
                if buildOnly > 0 {
                    state = .diverged(buildOnly: buildOnly, headOnly: headOnly)
                } else if headOnly > 0 {
                    state = .behind(headOnly)
                }
            }
        }

        let statusOut = await git(path, "status", "--porcelain")
        let dirty = statusOut.map {
            !$0.stdout.isEmpty &&
            !String(decoding: $0.stdout, as: UTF8.self)
                .trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        } ?? false

        var upstreamBehind: Int? = nil
        for range in ["HEAD..@{upstream}", "HEAD..origin/HEAD"] {
            if let r = await git(path, "rev-list", "--count", range), r.status == 0,
               let n = Int(String(decoding: r.stdout, as: UTF8.self)
                    .trimmingCharacters(in: .whitespacesAndNewlines)) {
                upstreamBehind = n
                break
            }
        }
        return Result(state: state, checkoutDirty: dirty, buildDirty: builtDirty,
                      upstreamBehind: upstreamBehind)
    }

    /// `git fetch`, for the explicit "Check now" path only. The working
    /// tree is never touched.
    @discardableResult
    public func fetch(path: String) async -> CommandResult? {
        await git(path, "fetch", "--quiet")
    }

    private func git(_ path: String, _ args: String...) async -> CommandResult? {
        try? await runner.run(["-C", path] + args)
    }
}
