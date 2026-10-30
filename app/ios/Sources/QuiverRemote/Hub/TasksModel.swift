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
    /// The scope sections as the page draws them under `filter`: the
    /// repo for GitHub rows, the project elsewhere, else the source's
    /// name (`sectionName`), ordered by the most
    /// recently active task each holds — the reply is already sorted by
    /// `last_active_at`, so first sight of a scope is its rank.
    /// The GitHub banner's reason: `gh` signed out (`envBlocked`) or
    /// failing (`githubFailed`). With no rows at all the page's empty
    /// card already says GitHub can't be reached, in the same words, so
    /// the banner — which says the rows under it are last-known — goes.
    static func githubBanner(envBlocked: String?, githubFailed: String?, hasRows: Bool) -> String? {
        hasRows ? envBlocked ?? githubFailed : nil
    }

    static func sections(of tasks: [TaskRow], filter: TaskFilter) -> [TaskSection] {
        var order: [String] = []
        var buckets: [String: [TaskRow]] = [:]
        for task in tasks where filter.includes(task) {
            let scope = sectionName(task)
            if buckets[scope] == nil { order.append(scope) }
            buckets[scope, default: []].append(task)
        }
        return order.map { TaskSection(name: $0, rows: buckets[$0] ?? [], error: nil) }
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

    /// `filter`'s matching count across every task — the filter chips.
    static func count(_ tasks: [TaskRow], filter: TaskFilter) -> Int {
        tasks.filter(filter.includes).count
    }
}

/// One section of the tasks page: a repo or project's matching rows, or
/// the fetch error a failed repo reports.
struct TaskSection: Identifiable {
    let name: String
    let rows: [TaskRow]?
    let error: String?
    var id: String { name }
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
