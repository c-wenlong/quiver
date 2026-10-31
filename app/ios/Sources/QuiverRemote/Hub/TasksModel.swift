import Foundation
import Observation

/// The tasks page's data: one fetch per open, pull to refresh. Kept out
/// of `AppModel`'s 15 s poll, as issues and pulls are — the hub already
/// serves the list from its one-minute `gh` cache.
@MainActor @Observable
final class TasksModel {
    private(set) var tasks: [TaskRow] = []
    /// Pinned repos whose fetch failed (`repos` in the reply): their
    /// stored rows still list; the page also shows the error itself.
    private(set) var failedRepos: [TaskRepoFailure] = []
    private(set) var machine: String?
    private(set) var machineId: String?
    /// `gh` missing or signed out on the hub (`env_blocked`): GitHub rows
    /// are the last-known ones and read as stale.
    private(set) var envBlocked: String?
    /// `gh` ran on the hub but failed (`github.status` of
    /// `upstream-error`: a timeout, a crash). Not a setup problem, so no
    /// `env_blocked`, but the GitHub rows are only what the store kept.
    private(set) var githubFailed: String?
    /// Linear's blocking state with its reason (`auth-required`,
    /// `bad-config`, `upstream-error`), nil when ok, disabled or absent.
    private(set) var linearBlocked: String?
    /// Notion's, the same way.
    private(set) var notionBlocked: String?
    private(set) var fetchedAt: Double?
    private(set) var error: String?
    private(set) var loaded = false
    /// Bumped per load: an on-open fetch and a pull-to-refresh can
    /// overlap, and only the newer one's reply may land.
    @ObservationIgnored private var generation = 0

    func load(client: HubClient?, refresh: Bool = false) async {
        guard let client else { return }
        generation += 1
        let gen = generation
        do {
            let body = try await client.tasks(refresh: refresh)
            guard gen == generation else { return }
            apply(body)
        } catch is CancellationError {
            // The page went away mid-fetch: nothing failed.
            return
        } catch let e as HubError {
            guard gen == generation else { return }
            error = e.message
        } catch {
            guard gen == generation else { return }
            self.error = error.localizedDescription
        }
        loaded = true
    }

    /// Why GitHub's rows are last-known, for the amber banner over the
    /// list; nil when there's no banner to draw.
    var githubBanner: String? {
        TaskGrouping.githubBanner(envBlocked: envBlocked, githubFailed: githubFailed,
                                  hasRows: !tasks.isEmpty || !failedRepos.isEmpty)
    }

    /// Takes one reply: its rows and each source's env block, so a
    /// source that recovered stops marking its rows last-known.
    func apply(_ body: TasksResponse) {
        tasks = body.tasks
        failedRepos = body.repos ?? []
        machine = body.machine
        machineId = body.machineId
        envBlocked = body.envBlocked
        githubFailed = body.github?.status == "upstream-error"
            ? (body.github?.error ?? "gh failed on the hub") : nil
        fetchedAt = body.fetchedAt
        error = nil
        linearBlocked = Self.blocked(body.linear, source: "Linear")
        notionBlocked = Self.blocked(body.notion, source: "Notion")
    }

    /// The detail page's row, live as the list refreshes under it.
    func task(_ id: String) -> TaskRow? { tasks.first { $0.id == id } }

    /// True when the row's source or repo failed this fetch: what it
    /// shows is last-known state, and the row says so.
    func stale(_ task: TaskRow) -> Bool {
        switch task.source {
        case "github":
            envBlocked != nil || githubFailed != nil
                || (task.repo.map { repo in failedRepos.contains { $0.name == repo } } ?? false)
        case "linear": linearBlocked != nil
        case "notion": notionBlocked != nil
        default: false
        }
    }

    /// A keyed source's blocking reason from its env block: nil when the
    /// block is absent (an older hub), `ok` or `disabled` (off, not
    /// broken); otherwise the hub's `error`, else the status word.
    nonisolated static func blocked(_ env: GhEnv?, source: String) -> String? {
        let status = env?.status
        return switch status {
        case nil, "ok", "disabled": nil
        default: env?.error ?? status ?? "\(source) is unreachable"
        }
    }
}

/// Grouping and counting for the tasks page, pure so tests can feed it
/// hub rows without an actor.
enum TaskGrouping {
    /// The GitHub banner's reason: `gh` signed out (`envBlocked`) or
    /// failing (`githubFailed`). With no rows at all the page's empty
    /// card already says GitHub can't be reached, in the same words, so
    /// the banner — which says the rows under it are last-known — goes.
    static func githubBanner(envBlocked: String?, githubFailed: String?, hasRows: Bool) -> String? {
        hasRows ? envBlocked ?? githubFailed : nil
    }

    /// The scope sections as the page draws them under `filter`: the
    /// repo for GitHub rows, the project elsewhere, else the source's
    /// name (`sectionName`), ordered by the most
    /// recently active task each holds — the reply is already sorted by
    /// `last_active_at`, so first sight of a scope is its rank.
    /// `scope` narrows to the one section the scope menu picked.
    static func sections(of tasks: [TaskRow], filter: TaskFilter,
                         scope only: String? = nil) -> [TaskSection] {
        var order: [String] = []
        var buckets: [String: [TaskRow]] = [:]
        for task in tasks where filter.includes(task) {
            let scope = sectionName(task)
            if let only, scope != only { continue }
            if buckets[scope] == nil { order.append(scope) }
            buckets[scope, default: []].append(task)
        }
        return order.map { TaskSection(name: $0, rows: buckets[$0] ?? [], error: nil) }
    }

    /// The sections with each failed repo's reason on its own section;
    /// a failed repo with no rows under the filter keeps a section for
    /// the error. One header per repo, never a second for the failure.
    static func sections(of tasks: [TaskRow], failed: [TaskRepoFailure],
                         filter: TaskFilter, scope only: String? = nil) -> [TaskSection] {
        var sections = sections(of: tasks, filter: filter, scope: only)
        for failure in failed where only == nil || failure.name == only {
            let reason = failure.error ?? failure.status ?? "unknown error"
            if let at = sections.firstIndex(where: { $0.name == failure.name }) {
                sections[at].error = reason
            } else {
                sections.append(TaskSection(name: failure.name, rows: [], error: reason))
            }
        }
        return sections
    }

    /// The section a task lists under: its repo or project, else its
    /// source's name ("Linear", "Notion"), as the web and the Mac group
    /// them; "Other" only when the row names no source either.
    static func sectionName(_ task: TaskRow) -> String {
        if !task.scope.isEmpty { return task.scope }
        guard let source = task.source, !source.isEmpty else { return "Other" }
        return sourceName(source)
    }

    /// A source as the pages spell it: GitHub, Linear, Notion; anything
    /// else capitalized.
    static func sourceName(_ source: String) -> String {
        ["github": "GitHub", "linear": "Linear", "notion": "Notion"][source]
            ?? source.prefix(1).uppercased() + source.dropFirst()
    }

    /// `filter`'s matching count across every task — the filter chips;
    /// under a scope pick, that scope's tasks only.
    static func count(_ tasks: [TaskRow], filter: TaskFilter, scope: String? = nil) -> Int {
        tasks.filter { filter.includes($0) && (scope == nil || sectionName($0) == scope) }.count
    }

    /// Rows a group shows before its "Show N more" row, as on the Mac
    /// and the web.
    static let preview = 5

    /// The rows a group draws and how many past the first five its
    /// "Show N more" row stands for (0: no such row). A group the scope
    /// menu narrowed to shows every row: the pick is the ask to see it all.
    static func fold(_ rows: [TaskRow], expanded: Bool, narrowed: Bool) -> (visible: [TaskRow], hidden: Int) {
        let hidden = narrowed ? 0 : max(rows.count - preview, 0)
        return (expanded || hidden == 0 ? rows : Array(rows.prefix(preview)), hidden)
    }

    /// The key a group's unfolded state is kept under: per group and
    /// filter, as the Mac and the web keep it.
    static func foldKey(_ section: TaskSection, filter: TaskFilter) -> String {
        "\(section.name):\(filter.rawValue)"
    }

    /// The scope pick while it still names a scope or a failed repo; a
    /// refresh that drops it falls back to every task.
    static func shownScope(_ scope: String?, tasks: [TaskRow], failed: [TaskRepoFailure]) -> String? {
        guard let scope,
              tasks.contains(where: { sectionName($0) == scope })
                || failed.contains(where: { $0.name == scope })
        else { return nil }
        return scope
    }

    /// What a filter with nothing in it says, naming the picked scope,
    /// in the web's words.
    static func emptyText(_ filter: TaskFilter, scope: String?) -> String {
        guard let scope else {
            return switch filter {
            case .all: "No task matches this filter right now."
            case .running: "Nothing is running right now."
            case .prOpen: "No task has a pull request open."
            case .done: "Nothing is merged or closed yet."
            }
        }
        return switch filter {
        case .all: "No tasks in \(scope)."
        case .running: "Nothing is running in \(scope)."
        case .prOpen: "No task in \(scope) has a pull request open."
        case .done: "Nothing in \(scope) is merged or closed yet."
        }
    }

    /// The scope menu (the Mac and web's rail): every scope, `filter` or
    /// not, so one with nothing under the chip still has a row to fold
    /// as quiet, plus each failed repo so its error can be picked. GitHub
    /// repos group by owner as the Repositories rail does; Linear
    /// projects under "Linear", any other source under its own name.
    /// Owners come first, then the other sources, and groups and rows
    /// sort by name: the list reorders by activity, the menu holds still.
    static func rail(of tasks: [TaskRow], failed: [TaskRepoFailure] = [],
                     filter: TaskFilter) -> [TaskRailGroup] {
        struct Key: Hashable { var github: Bool; var title: String }
        var groups: [Key: [String: TaskRailScope]] = [:]
        func add(_ key: Key, _ name: String, label: String, hit: Int, running: Bool) {
            var scope = groups[key, default: [:]][name]
                ?? TaskRailScope(name: name, label: label, count: 0, running: false)
            scope.count += hit
            scope.running = scope.running || running
            groups[key, default: [:]][name] = scope
        }
        for task in tasks {
            let name = sectionName(task)
            let github = task.source == "github" && task.repo?.contains("/") == true
            add(Key(github: github, title: railGroupTitle(task)), name,
                label: github ? shortName(name) : name,
                hit: filter.includes(task) ? 1 : 0, running: task.agentRunning == true)
        }
        for failure in failed where failure.name.contains("/") {
            let owner = String(failure.name.split(separator: "/", maxSplits: 1)[0])
            add(Key(github: true, title: owner), failure.name,
                label: shortName(failure.name), hit: 0, running: false)
        }
        let byName = { (a: String, b: String) in a.localizedStandardCompare(b) == .orderedAscending }
        return groups.keys
            .sorted { $0.github != $1.github ? $0.github : byName($0.title, $1.title) }
            .map { key in
                TaskRailGroup(title: key.title,
                              scopes: (groups[key] ?? [:]).values.sorted { byName($0.label, $1.label) })
            }
    }

    /// A menu group's scopes split as the rail draws them: those with
    /// something under the chip, and the quiet ones folded under "N
    /// quiet". The picked scope never folds away.
    static func split(_ group: TaskRailGroup, shown: String?) -> (active: [TaskRailScope], quiet: [TaskRailScope]) {
        let quiet = { (s: TaskRailScope) in s.count == 0 && s.name != shown }
        return (group.scopes.filter { !quiet($0) }, group.scopes.filter(quiet))
    }

    private static func shortName(_ repo: String) -> String {
        String(repo.split(separator: "/").last ?? Substring(repo))
    }

    /// A repo's owner for GitHub; the source's own name elsewhere.
    private static func railGroupTitle(_ task: TaskRow) -> String {
        if task.source == "github", let repo = task.repo, repo.contains("/") {
            return String(repo.split(separator: "/", maxSplits: 1)[0])
        }
        guard let source = task.source, !source.isEmpty else { return "Other" }
        return sourceName(source)
    }
}

/// One section of the tasks page: a repo or project's matching rows,
/// and the fetch error when its repo failed.
struct TaskSection: Identifiable {
    let name: String
    var rows: [TaskRow]?
    var error: String?
    var id: String { name }
}

/// One scope-menu row: a scope the list groups under, the short name the
/// menu shows, and how many of its tasks the chip filter leaves.
struct TaskRailScope: Identifiable, Equatable {
    /// The `TaskSection` name a tap narrows the list to.
    let name: String
    let label: String
    var count: Int
    /// An agent is on one of its tasks.
    var running: Bool
    var id: String { name }
}

/// A scope-menu heading (a GitHub owner, "Linear") and its scopes in order.
struct TaskRailGroup: Identifiable, Equatable {
    let title: String
    let scopes: [TaskRailScope]
    var id: String { title }
}

/// The tasks page's row filters: everything, what an agent is on, the
/// issues whose PR is open plus the pull rows themselves (a pull task
/// is an open PR by definition until it lands), and the terminal states.
enum TaskFilter: String, CaseIterable, Identifiable {
    case all, running, prOpen, done

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: "All"
        case .running: "Running"
        case .prOpen: "PR open"
        case .done: "Done"
        }
    }

    func includes(_ task: TaskRow) -> Bool {
        switch self {
        case .all: true
        case .running: task.agentRunning == true
        case .prOpen: task.state == "pr_open" || (task.kind == "pull" && !task.isDone)
        case .done: task.isDone
        }
    }
}

extension TaskRow {
    /// The status the row and detail show: the hub's `status`, in the
    /// source's words. An older hub sends none; then the pipeline's own
    /// words, never "in progress" for a session that has ended (#875).
    var stateLabel: String {
        if let status, !status.isEmpty { return status }
        switch state {
        case "pr_open": return "PR open"
        case "merged": return "Merged"
        case "closed": return "Closed"
        case "in_progress" where source != "github":
            // Another source says itself that work has started.
            return "Started"
        default: return agentRunning == true ? "Agent running" : "Open"
        }
    }

    /// "GitHub issue · o/r", "Linear · QUI" — the source line under a
    /// detail's title.
    var sourceLine: String {
        switch source {
        case "github":
            let what = kind == "pull" ? "pull request" : "issue"
            return "GitHub \(what)\(repo.map { " · \($0)" } ?? "")"
        case "linear":
            return "Linear\(project.map { " · \($0)" } ?? "")"
        case "notion":
            return "Notion\(project.map { " · \($0)" } ?? "")"
        default:
            return [source, kind].compactMap { $0 }.joined(separator: " ").capitalized
        }
    }

    /// The link-out button's words: "Open on GitHub", "Open in Linear",
    /// "Open in Notion".
    var openLabel: String {
        switch source {
        case "linear": "Open in Linear"
        case "notion": "Open in Notion"
        default: "Open on GitHub"
        }
    }

    /// What Copy link writes: the app's `quiver://issue` deep link for
    /// a GitHub issue (so it opens back inside the app), the task's own
    /// URL for anything else.
    var copyLink: URL? {
        if source == "github", kind == "issue",
           let repo, let number {
            return IssueLink(repo: repo, number: number).url
        }
        return url.flatMap(URL.init)
    }
}

/// The state stepper the detail page draws: rungs with which are
/// reached and which is current. Pure so the ladder rules — a pull's
/// lifecycle skips "PR open" (the task is the PR), `closed` replaces
/// the terminal rung — are tested without a view.
enum TaskStepper {
    struct Step: Equatable, Identifiable {
        let title: String
        /// The task passed this rung on its way to `current`.
        let reached: Bool
        /// The task sits here now.
        let current: Bool
        var id: String { title }
    }

    /// `open → agent → PR open → merged` for GitHub issues, the only
    /// tasks whose own PR can stand between "an agent is on it" and
    /// done; `open → agent → merged` for pulls, and `open → started →
    /// done` for every other source, whose own words these are. `closed` keeps the ladder's shape with "Closed"
    /// as the terminal rung — how far a hand-closed task got isn't
    /// inferable, so only its ends are lit.
    static func steps(for task: TaskRow) -> [Step] {
        let githubIssue = task.source == "github" && task.kind == "issue"
        let github = task.source == "github"
        var rungs = githubIssue ? ["Open", "Agent", "PR open", "Merged"]
                    : github ? ["Open", "Agent", "Merged"]
                    : ["Open", "Started", "Done"]
        let order = githubIssue ? ["open", "in_progress", "pr_open", "merged"]
                                : ["open", "in_progress", "merged"]
        let last = rungs.count - 1
        if task.state == "closed" {
            rungs[last] = "Closed"
            return rungs.enumerated().map {
                Step(title: $1, reached: $0 == 0 || $0 == last, current: $0 == last)
            }
        }
        let index = order.firstIndex(of: task.state ?? "open") ?? 0
        return rungs.enumerated().map {
            Step(title: $1, reached: $0 <= index, current: $0 == index)
        }
    }
}
