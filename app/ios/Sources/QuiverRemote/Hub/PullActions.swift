import Foundation

/// The merge strategies the hub's merge route takes, in the Mac and
/// web menus' order and words.
enum MergeMethod: String, CaseIterable, Identifiable {
    case squash, rebase, merge

    var id: String { rawValue }

    var title: String {
        switch self {
        case .squash: "Squash and merge"
        case .rebase: "Rebase and merge"
        case .merge: "Create a merge commit"
        }
    }
}

/// Which write the confirm dialog is asking about.
enum MergeAsk: String, Identifiable {
    case merge, auto
    var id: String { rawValue }
}

/// The PR detail page's write actions, pure so tests can feed them hub
/// rows: when Merge and Auto-merge are on, what the confirm says, and
/// which check rows can re-run their workflow run.
enum PullActions {
    /// Merge and Auto-merge show on an open PR only.
    static func offersMerge(_ pr: PullDetail) -> Bool { pr.state == "open" }

    /// Why Merge is off, in the Mac and web words, or nil when it's on.
    static func mergeBlock(_ pr: PullDetail) -> String? {
        if pr.draft == true { return "Draft PRs can't merge" }
        if pr.mergeable == "conflicting" { return "Resolve the conflicts first" }
        return nil
    }

    /// Why Auto-merge is off, or nil when it's on.
    static func autoBlock(_ pr: PullDetail) -> String? {
        if pr.draft == true { return "Draft PRs can't merge" }
        if pr.autoMerge != nil { return "Auto-merge is already armed" }
        return nil
    }

    static func block(_ ask: MergeAsk, _ pr: PullDetail) -> String? {
        ask == .auto ? autoBlock(pr) : mergeBlock(pr)
    }

    /// The line under the buttons: Merge's reason first, then
    /// Auto-merge's when it differs, so a draft says it once.
    static func blockNote(_ pr: PullDetail) -> String? {
        let reasons = [mergeBlock(pr), autoBlock(pr)].compactMap { $0 }
        let unique = reasons.reduce(into: [String]()) { if !$0.contains($1) { $0.append($1) } }
        return unique.isEmpty ? nil : unique.joined(separator: ". ")
    }

    /// The confirm's body, naming exactly what happens, as the web's.
    static func confirmMessage(_ ask: MergeAsk, _ pr: PullDetail, repo: String,
                               method: MergeMethod) -> String {
        let what = "#\(pr.number) \(pr.title ?? "")".trimmingCharacters(in: .whitespaces)
        let base = pr.base?.isEmpty == false ? pr.base! : "the base branch"
        switch ask {
        case .auto:
            return "Arm auto-merge for \(what) in \(repo) with \(method.title)? GitHub merges it into \(base) once its checks pass."
        case .merge:
            return "Merge \(what) into \(base) of \(repo) with \(method.title)? This happens on GitHub now and can't be undone from here."
        }
    }

    static func confirmButton(_ ask: MergeAsk, _ pr: PullDetail) -> String {
        ask == .auto ? "Arm auto-merge" : "Merge #\(pr.number)"
    }

    /// The GitHub Actions run behind a check row: its `detailsUrl` is
    /// `https://github.com/<o>/<r>/actions/runs/<id>/job/<job>`. Only a
    /// run in `repo` itself counts, since the hub re-runs there; status
    /// contexts and other apps' checks have no run.
    static func runId(_ url: String?, repo: String) -> Int? {
        guard let url, let parsed = URL(string: url),
              parsed.host?.lowercased() == "github.com" else { return nil }
        let parts = parsed.path.split(separator: "/").map(String.init)
        guard parts.count >= 5, parts[2] == "actions", parts[3] == "runs",
              "\(parts[0])/\(parts[1])".lowercased() == repo.lowercased(),
              parts[4].allSatisfy(\.isASCII), let id = Int(parts[4]), id > 0
        else { return nil }
        return id
    }

    /// Whether any check of `run` failed: Re-run failed jobs shows only
    /// then, as on the web's Actions rows.
    static func runFailed(_ run: Int, in checks: [CheckRun], repo: String) -> Bool {
        checks.contains { runId($0.url, repo: repo) == run && $0.state == "failing" }
    }
}
