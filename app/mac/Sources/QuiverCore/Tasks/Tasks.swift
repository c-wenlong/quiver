import Foundation
import Observation

/// Wire types and the model for the Tasks page: the work items the
/// hub's agents are on (`GET /v1/app/tasks`, the page payload the web
/// app's `tasks.js` reads — the task store over the pinned repos' open
/// issues and PRs, plus each row's `session_rows`/`launch_rows` joins).
/// The iPhone app's `TasksModel` (app/ios) reads the same store through
/// `GET /v1/tasks` and holds the same row shape.
///
/// Every field the hub may omit is optional, so a newer hub never
/// breaks decoding.

/// A source's env block — GitHub's `gh` probe, Linear's configured
/// state. `status` stays a string: `disabled`, `auth-required`,
/// `bad-config` and friends all pass through instead of failing a row.
public struct HubSourceEnv: Decodable, Sendable, Equatable {
    public var status: String?
    public var login: String?
    public var error: String?

    public init(status: String? = nil, login: String? = nil, error: String? = nil) {
        self.status = status
        self.login = login
        self.error = error
    }
}

/// The PR a task row carries: for an issue the PR its `quiver/issue-<n>`
/// branch produced (open or merged), for a pull the PR itself.
public struct HubTaskPr: Codable, Sendable, Equatable, Hashable {
    public var number: Int?
    public var title: String?
    public var state: String?
    public var draft: Bool?
    public var url: String?

    public init(number: Int? = nil, title: String? = nil, state: String? = nil,
                draft: Bool? = nil, url: String? = nil) {
        self.number = number
        self.title = title
        self.state = state
        self.draft = draft
        self.url = url
    }

    /// The caption under the PR row, in the state ladder's words.
    public var stateLabel: String {
        switch state {
        case "merged": "Merged"
        case "closed": "Closed"
        default: "Open"
        }
    }
}

/// One linked session as the page payload joins it: the catalog row
/// trimmed to what a row draws, or just `key` with `gone` when the
/// catalog no longer lists it.
public struct HubTaskSessionRow: Codable, Sendable, Equatable, Hashable {
    public var key: String
    public var gone: Bool?
    public var harness: String?
    public var agent: String?
    public var title: String?
    public var status: String?
    public var cwd: String?
    /// The satellite's name when the row is remote.
    public var machine: String?
    public var machineId: String?
    public var remote: Bool?
    public var ownership: String?
    public var attachable: Bool?
    public var lastActiveAt: Double?

    public init(key: String, gone: Bool? = nil, harness: String? = nil, agent: String? = nil,
                title: String? = nil, status: String? = nil, cwd: String? = nil,
                machine: String? = nil, machineId: String? = nil, remote: Bool? = nil,
                ownership: String? = nil, attachable: Bool? = nil, lastActiveAt: Double? = nil) {
        self.key = key
        self.gone = gone
        self.harness = harness
        self.agent = agent
        self.title = title
        self.status = status
        self.cwd = cwd
        self.machine = machine
        self.machineId = machineId
        self.remote = remote
        self.ownership = ownership
        self.attachable = attachable
        self.lastActiveAt = lastActiveAt
    }
}

/// One linked launch's live view: `pending` while the hub prepares it
/// (`note` names the step), `started` once adopted (its session row then
/// carries it), `failed` with the reason, or `untracked` for a harness
/// whose sessions the hub can't read. A launch the hub forgot is old
/// history and simply isn't joined.
public struct HubTaskLaunchRow: Codable, Sendable, Equatable, Hashable, Identifiable {
    public var id: String
    public var state: String?
    public var harness: String?
    public var note: String?
    public var key: String?
    public var cwd: String?
    public var machine: String?

    public init(id: String, state: String? = nil, harness: String? = nil, note: String? = nil,
                key: String? = nil, cwd: String? = nil, machine: String? = nil) {
        self.id = id
        self.state = state
        self.harness = harness
        self.note = note
        self.key = key
        self.cwd = cwd
        self.machine = machine
    }
}

/// One work item: the TaskRef fields plus the derived `state` ladder
/// (`open` → `in_progress` → `pr_open` → `merged`|`closed`), the
/// store's `sessions`/`launches` links, `agent_running` while a linked
/// session is active or a launch still pending, and the page payload's
/// `sessionRows`/`launchRows` joins.
public struct HubTaskRow: Codable, Sendable, Equatable, Hashable, Identifiable {
    public var id: String
    /// github | linear | notion
    public var source: String?
    /// issue | pull | card | page — GitHub carries issue and pull only.
    public var kind: String?
    /// The source's own id: "5" for GitHub, "QUI-123" for Linear.
    public var ref: String?
    public var title: String?
    public var url: String?
    /// `owner/repo` for GitHub.
    public var repo: String?
    /// The team/project key for non-GitHub sources.
    public var project: String?
    public var state: String?
    /// Session keys linked to this task.
    public var sessions: [String]?
    /// Launch ids still preparing or already adopted.
    public var launches: [String]?
    /// True while a linked session is `active` or a launch is pending.
    public var agentRunning: Bool?
    /// The store has links on this task.
    public var linked: Bool?
    public var lastActiveAt: Double?
    public var createdAt: Double?
    /// The source's own stamp — ISO-8601 from GitHub and Linear both.
    public var updatedAt: String?
    public var pr: HubTaskPr?
    /// What the row says, in the source's own words ("Agent running",
    /// "PR open", a Linear workflow state's name). `state` is the
    /// ladder the list sorts and filters by, never shown as text (#875).
    public var status: String?
    /// `session_rows` in the reply: each linked key's catalog row.
    public var sessionRows: [HubTaskSessionRow]?
    /// `launch_rows`: the live view of each linked launch still tracked.
    public var launchRows: [HubTaskLaunchRow]?

    public init(id: String, source: String? = nil, kind: String? = nil, ref: String? = nil,
                title: String? = nil, url: String? = nil, repo: String? = nil,
                project: String? = nil, state: String? = nil, sessions: [String]? = nil,
                launches: [String]? = nil, agentRunning: Bool? = nil, linked: Bool? = nil,
                lastActiveAt: Double? = nil, createdAt: Double? = nil, updatedAt: String? = nil,
                pr: HubTaskPr? = nil, status: String? = nil,
                sessionRows: [HubTaskSessionRow]? = nil,
                launchRows: [HubTaskLaunchRow]? = nil) {
        self.id = id
        self.source = source
        self.kind = kind
        self.ref = ref
        self.title = title
        self.url = url
        self.repo = repo
        self.project = project
        self.state = state
        self.sessions = sessions
        self.launches = launches
        self.agentRunning = agentRunning
        self.linked = linked
        self.lastActiveAt = lastActiveAt
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.pr = pr
        self.status = status
        self.sessionRows = sessionRows
        self.launchRows = launchRows
    }

    /// The list's group key: the repo for GitHub, the project elsewhere.
    public var scope: String { repo ?? project ?? "" }
    /// "#42" for a GitHub issue/pull; the ref as it is anywhere else.
    public var refLabel: String {
        guard let ref, !ref.isEmpty else { return "" }
        return source == "github" ? "#\(ref)" : ref
    }
    /// The GitHub issue/PR number, when the task is one.
    public var number: Int? { Int(ref ?? "") }
    /// Terminal states: merged, or closed without one.
    public var isDone: Bool { state == "merged" || state == "closed" }
    public var updatedDate: Date? { updatedAt.flatMap(Timestamp.parse) }
    public var lastActiveDate: Date? { lastActiveAt.map { Date(timeIntervalSince1970: $0) } }

    /// Where a new agent session can start through the hub: only a
    /// GitHub issue (resolve) or pull (open) has a launch route; both
    /// stamp the launch's origin so the session links itself here.
    public var canLaunch: Bool {
        source == "github" && (kind == "issue" || kind == "pull") && repo?.isEmpty == false
    }

    /// The status the row and detail show: the hub's `status`, in the
    /// source's words. An older hub sends none; then the pipeline's own
    /// words for GitHub (never "in progress" for a session that has
    /// ended) and "Started" where another source says work has started.
    public var stateLabel: String {
        if let status, !status.isEmpty { return status }
        switch state {
        case "pr_open": return "PR open"
        case "merged": return "Merged"
        case "closed": return "Closed"
        case "in_progress" where source != "github": return "Started"
        default: return agentRunning == true ? "Agent running" : "Open"
        }
    }

    /// "GitHub issue · o/r", "Linear · QUI" — the line under a detail's title.
    public var sourceLine: String {
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

    /// The link-out's words: "Open on GitHub", "Open in Linear",
    /// "Open in Notion".
    public var openLabel: String {
        switch source {
        case "linear": "Open in Linear"
        case "notion": "Open in Notion"
        default: "Open on GitHub"
        }
    }

    /// The `task_ref` `POST`/`DELETE /v1/sessions/<key>/task` accept.
    public var refBody: [String: any Sendable] {
        var body: [String: any Sendable] = ["source": source ?? "", "kind": kind ?? "",
                                            "ref": ref ?? "", "title": title ?? "",
                                            "url": url ?? ""]
        if let repo { body["repo"] = repo }
        if let project { body["project"] = project }
        return body
    }
}

/// One pinned repo's failed fetch inside the tasks reply: its stored
/// rows still list under their group, dimmed, with this reason.
public struct HubTaskRepoFailure: Decodable, Sendable, Equatable, Identifiable, Hashable {
    public var name: String
    public var status: String?
    public var error: String?

    public var id: String { name }

    public init(name: String, status: String? = nil, error: String? = nil) {
        self.name = name
        self.status = status
        self.error = error
    }

    private enum CodingKeys: String, CodingKey { case name, repo, status, error }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // `/v1/app/tasks` writes `name`; accept `repo` for parity with
        // the web's `r.repo || r.name` fallback.
        name = try c.decodeIfPresent(String.self, forKey: .name)
            ?? c.decodeIfPresent(String.self, forKey: .repo) ?? ""
        status = try c.decodeIfPresent(String.self, forKey: .status)
        error = try c.decodeIfPresent(String.self, forKey: .error)
    }
}

/// `GET /v1/app/tasks`: the work-item store plus the page's joins.
public struct HubTasksResponse: Decodable, Sendable {
    public var tasks: [HubTaskRow]
    /// Per-repo fetch failures; absent when every repo listed fine.
    public var repos: [HubTaskRepoFailure]?
    /// The hub machine's display name.
    public var machine: String?
    /// The hub's machine id.
    public var machineId: String?
    public var github: HubSourceEnv?
    /// Linear's env block, shaped like `github`'s: `disabled` when no
    /// `linear` section is configured, a blocking word with `error`
    /// when the source can't be reached.
    public var linear: HubSourceEnv?
    /// Notion's env block, the same shape: `disabled` when no `notion`
    /// section is configured.
    public var notion: HubSourceEnv?
    /// Set when `gh` is missing or signed out on the hub. Stored GitHub
    /// tasks still list, marked stale.
    public var envBlocked: String?
    public var fetchedAt: Double?
}

/// One harness `GET /v1/app/tasks?harnesses=1` says the hub can start,
/// in Provider visibility order.
public struct HubTaskHarness: Decodable, Sendable, Equatable, Identifiable {
    public var name: String
    public var displayName: String?
    /// Quiver can read this harness's sessions; an untracked one starts
    /// but never opens a chat.
    public var tracked: Bool?
    /// Whether this harness's launches honour a `model` pick. Absent on
    /// a hub older than the launch-option fields — it would take the
    /// pick and drop it, so the panel gives that hub no Model row.
    public var launchModel: Bool?
    /// `launchModel`'s effort twin; decoded for completeness, unused
    /// until the effort picker ships.
    public var launchEffort: Bool?
    /// Whether this harness's launches honour `yolo`. Absent on a hub
    /// older than the field; decoded for completeness, unused until a
    /// task sheet offers the checkbox.
    public var launchYolo: Bool?
    /// The hub ledger's most recent model for this harness — the Model
    /// menu's default ("" when none ran). The head of `recentModels`.
    public var lastModel: String?
    /// This harness's recently used models, newest first (at most 5):
    /// the Model menu's entries. nil from a hub older than the menu.
    public var recentModels: [String]?

    public var id: String { name }

    public init(name: String, displayName: String? = nil, tracked: Bool? = nil,
                launchModel: Bool? = nil, launchEffort: Bool? = nil,
                launchYolo: Bool? = nil,
                lastModel: String? = nil, recentModels: [String]? = nil) {
        self.name = name
        self.displayName = displayName
        self.tracked = tracked
        self.launchModel = launchModel
        self.launchEffort = launchEffort
        self.launchYolo = launchYolo
        self.lastModel = lastModel
        self.recentModels = recentModels
    }
}

struct HubTaskHarnesses: Decodable {
    let harnesses: [HubTaskHarness]
    let machine: String?
}

/// `GET /v1/folders`: one folder's subfolders on the hub machine — the
/// new-session sheet's folder browser. A satellite's disk can't be
/// read from the hub, so browsing is the hub machine's only.
public struct HubFolderListing: Decodable, Sendable, Equatable {
    public struct Entry: Decodable, Sendable, Equatable, Identifiable, Hashable {
        public var name: String
        public var path: String
        public var id: String { path }
    }
    public var path: String
    public var parent: String?
    public var folders: [Entry]
    public var truncated: Bool?
}

// MARK: - list logic

/// The tasks page's row filters: everything, what an agent is on, the
/// issues whose PR is open plus the pull rows themselves (a pull task
/// is an open PR by definition until it lands), and the terminal
/// states. The same tests the web's `tasks.js` and the phone run.
public enum TaskFilter: String, CaseIterable, Identifiable, Sendable {
    case all, running, prOpen, done

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .all: "All"
        case .running: "Running"
        case .prOpen: "PR open"
        case .done: "Done"
        }
    }

    /// What the page says when the filter leaves nothing.
    public var emptyText: String {
        switch self {
        case .all: "No tasks yet."
        case .running: "Nothing is running right now."
        case .prOpen: "No task has a pull request open."
        case .done: "Nothing is merged or closed yet."
        }
    }

    public func includes(_ task: HubTaskRow) -> Bool {
        switch self {
        case .all: true
        case .running: task.agentRunning == true
        // A pull task counts until it lands, as the web and the phone
        // count it: its `pr` block can be missing from a row.
        case .prOpen: task.state == "pr_open" || (task.kind == "pull" && !task.isDone)
        case .done: task.isDone
        }
    }
}

/// One section of the tasks page: a repo or project's matching rows,
/// or the fetch error a failed repo reports.
public struct TaskSection: Identifiable, Sendable {
    public var name: String
    /// nil while `error` is set.
    public var rows: [HubTaskRow]?
    public var error: String?
    /// A GitHub repo section can link out.
    public var repo: String?
    public var id: String { name }
}

/// Grouping and counting for the tasks page, pure so tests can feed it
/// hub rows without an actor.
public enum TaskGrouping {
    /// The scope sections as the page draws them under `filter`: the
    /// repo for GitHub rows, the project elsewhere, ordered by the most
    /// recently active task each holds — the reply is already sorted by
    /// `last_active_at`, so first sight of a scope is its rank.
    /// `scope` narrows to the one section the rail picked.
    public static func sections(of tasks: [HubTaskRow], filter: TaskFilter,
                                scope only: String? = nil) -> [TaskSection] {
        var order: [String] = []
        var buckets: [String: [HubTaskRow]] = [:]
        var repos: [String: String] = [:]
        for task in tasks where filter.includes(task) {
            let scope = sectionName(task)
            if let only, scope != only { continue }
            if buckets[scope] == nil {
                order.append(scope)
                repos[scope] = task.source == "github" ? task.repo : nil
            }
            buckets[scope, default: []].append(task)
        }
        return order.map { TaskSection(name: $0, rows: buckets[$0] ?? [], error: nil, repo: repos[$0]) }
    }

    /// The sections with each failed repo's reason on its own section;
    /// a failed repo with no rows under the filter keeps a section for
    /// the error. One header per repo, never a second for the failure.
    public static func sections(of tasks: [HubTaskRow], failed: [HubTaskRepoFailure],
                                filter: TaskFilter, scope only: String? = nil) -> [TaskSection] {
        var sections = sections(of: tasks, filter: filter, scope: only)
        for failure in failed where only == nil || failure.name == only {
            let reason = failure.error ?? failure.status ?? "Can't list this repo's work."
            if let at = sections.firstIndex(where: { $0.name == failure.name }) {
                sections[at].error = reason
            } else {
                sections.append(TaskSection(name: failure.name, rows: [], error: reason,
                                            repo: failure.name))
            }
        }
        return sections
    }

    /// `filter`'s matching count across every task — the filter chips;
    /// under a rail pick, that scope's tasks only.
    public static func count(_ tasks: [HubTaskRow], filter: TaskFilter,
                             scope: String? = nil) -> Int {
        tasks.filter { filter.includes($0) && (scope == nil || sectionName($0) == scope) }.count
    }

    /// The section a task lists under: its repo or project, else its
    /// source's name ("Linear", "Notion"), as the web groups them; "Other"
    /// only when the row names no source either.
    public static func sectionName(_ task: HubTaskRow) -> String {
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

    /// The page's rail: every scope, `filter` or not, so one with nothing
    /// under the chip still has a row to fold as quiet, plus each failed
    /// repo so its error can be picked. GitHub repos group by owner as the
    /// Repositories rail does; Linear projects under "Linear", any other
    /// source under its own name. Owners come first, then the other
    /// sources, and groups and rows sort by name: the list reorders by
    /// activity, the rail holds still. The web's `tasks.js` sorts the same.
    public static func rail(of tasks: [HubTaskRow], failed: [HubTaskRepoFailure] = [],
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

    private static func shortName(_ repo: String) -> String {
        String(repo.split(separator: "/").last ?? Substring(repo))
    }

    /// A repo's owner for GitHub; the source's own name elsewhere.
    private static func railGroupTitle(_ task: HubTaskRow) -> String {
        if task.source == "github", let repo = task.repo, repo.contains("/") {
            return String(repo.split(separator: "/", maxSplits: 1)[0])
        }
        guard let source = task.source, !source.isEmpty else { return "Other" }
        return sourceName(source)
    }
}

/// One rail row: a scope the list groups under, the short name the
/// rail shows, and how many of its tasks the chip filter leaves.
public struct TaskRailScope: Identifiable, Sendable, Equatable {
    /// The `TaskSection` name a tap narrows the list to.
    public var name: String
    public var label: String
    public var count: Int
    /// An agent is on one of its tasks: the rail's green dot.
    public var running: Bool
    public var id: String { name }
}

/// A rail heading (a GitHub owner, "Linear") and its scopes in order.
public struct TaskRailGroup: Identifiable, Sendable, Equatable {
    public var title: String
    public var scopes: [TaskRailScope]
    public var id: String { title }
}

/// The state stepper the detail page draws: rungs with which are
/// reached and which is current. Pure so the ladder rules — a pull's
/// lifecycle skips "PR open" (the task is the PR), `closed` replaces
/// the terminal rung — are tested without a view.
public enum TaskStepper {
    public struct Step: Equatable, Identifiable, Sendable {
        public var title: String
        /// The task passed this rung on its way to `current`.
        public var reached: Bool
        /// The task sits here now.
        public var current: Bool
        public var id: String { title }
    }

    /// `open → agent → (PR open) → merged`: only a GitHub issue's own
    /// PR stands between "an agent is on it" and done — a pull IS its
    /// PR. Linear and Notion read `open → started → done`, their words. `closed` keeps
    /// the ladder's shape with "Closed" as the terminal rung — how far a
    /// hand-closed task got isn't inferable, so only its ends are lit.
    public static func steps(for task: HubTaskRow) -> [Step] {
        let viaPR = task.source == "github" && task.kind == "issue"
        let github = task.source == "github"
        var rungs = viaPR ? ["Open", "Agent", "PR open", "Merged"]
                    : github ? ["Open", "Agent", "Merged"]
                    : ["Open", "Started", "Done"]
        let order = viaPR ? ["open", "in_progress", "pr_open", "merged"]
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

// MARK: - model

/// The Tasks page's data: one fetch per open, Refresh asks for a new
/// one. The hub already serves the list from its one-minute `gh`
/// cache, so a plain read is cheap.
@MainActor
@Observable
public final class TasksModel {
    public private(set) var tasks: [HubTaskRow] = []
    /// Pinned repos whose fetch failed (`repos` in the reply): their
    /// stored rows still list; the page also shows the error itself.
    public private(set) var failedRepos: [HubTaskRepoFailure] = []
    /// The hub machine's name and id — the launch sheet offers its
    /// harnesses, worktrees live there.
    public private(set) var machine: String?
    public private(set) var machineId: String?
    /// GitHub's env block as the hub probed it.
    public private(set) var github: HubSourceEnv?
    /// `gh` missing or signed out on the hub (`env_blocked`): GitHub
    /// rows are the last-known ones and read as stale.
    public private(set) var envBlocked: String?
    /// `gh`'s probe itself failed (`github.status == "upstream-error"` —
    /// e.g. an `auth status` timeout): no setup card fixes it, but the
    /// GitHub list didn't refresh either, so its rows are last-known.
    public private(set) var githubFailed: String?
    /// Linear's blocking state with its reason (`auth-required`,
    /// `bad-config`, `upstream-error`), nil when ok, disabled or absent.
    public private(set) var linearBlocked: String?
    /// Notion's, the same way.
    public private(set) var notionBlocked: String?
    public private(set) var error: String?
    /// The last load found this Mac's hub not answering: stopped, or
    /// still starting after a restart (see `hubNotAnswering`).
    public private(set) var hubDown = false

    /// The failures a restart produces, worth trying again: swe's status
    /// says the hub is down (`hub_down`, when the app has no client
    /// yet), or a client the app already holds can't connect (`network`).
    /// A hub bound off this Mac, a refused credential or a bad reply is
    /// not fixed by waiting.
    public static func hubNotAnswering(_ error: HubError) -> Bool {
        error.code == "hub_down" || error.code == "network"
    }
    public private(set) var loaded = false
    /// The rows' filter, shared by the list's chips.
    public var filter: TaskFilter = .all
    /// The rail's pick: one section name the list narrows to, nil for
    /// every task. Kept across visits like `filter`.
    public var shownScope: String?
    /// A link, unlink or launch in flight — keyed so the same action on
    /// the same object can't fire twice.
    public private(set) var busy: Set<String> = []
    /// The last action's failure, shown on the task's detail.
    public private(set) var actionError: String?
    /// Bumped per load: an on-open fetch and a Refresh can overlap, and
    /// only the newer one's reply may land.
    @ObservationIgnored private var generation = 0

    public init() {}

    public func load(hub: LocalHub, refresh: Bool = false) async {
        generation += 1
        let gen = generation
        do {
            let body = try await hub.withClient { try await $0.appTasks(refresh: refresh) }
            guard gen == generation else { return }
            tasks = body.tasks
            failedRepos = body.repos ?? []
            machine = body.machine
            machineId = body.machineId
            github = body.github
            envBlocked = body.envBlocked
            githubFailed = github?.status == "upstream-error"
                ? (github?.error ?? "GitHub did not answer this refresh.")
                : nil
            error = nil
            hubDown = false
            linearBlocked = Self.blocked(body.linear, source: "Linear")
            notionBlocked = Self.blocked(body.notion, source: "Notion")
        } catch let e as HubError {
            guard gen == generation else { return }
            error = e.message
            hubDown = Self.hubNotAnswering(e)
        } catch {
            guard gen == generation else { return }
            self.error = error.localizedDescription
            hubDown = false
        }
        loaded = true
    }

    /// Loads, then keeps trying while this Mac's hub is down, so the page
    /// fills in once the hub is up rather than keeping its error until
    /// Refresh. The hub restarts on every app install and at login, and
    /// can take a while to answer. Waits 3 s, doubling to 30 s; ends when
    /// the calling task is cancelled (the page went away).
    public func loadUntilHubAnswers(
        hub: LocalHub,
        pause: @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }
    ) async {
        await load(hub: hub)
        var wait = 3
        while hubDown {
            do { try await pause(.seconds(wait)) } catch { return }
            await load(hub: hub)
            wait = min(wait * 2, 30)
        }
    }

    /// The detail page's row, live as the list refreshes under it.
    public func task(_ id: String) -> HubTaskRow? { tasks.first { $0.id == id } }

    /// The blocking env state the page's setup card covers — gh missing
    /// or signed out on the hub. nil while probing, ok, or upstream
    /// trouble — a failed probe earns `githubFailed`, not a setup ask.
    public var githubSetup: GhEnv.Status? {
        switch github?.status {
        case "unavailable": .unavailable
        case "auth-required": .authRequired
        default: nil
        }
    }

    /// True when the row's source or repo failed this fetch: what it
    /// shows is last-known state, and the row says so.
    public func stale(_ task: HubTaskRow) -> Bool {
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
    public nonisolated static func blocked(_ env: HubSourceEnv?, source: String) -> String? {
        let status = env?.status
        return switch status {
        case nil, "ok", "disabled": nil
        default: env?.error ?? status ?? "\(source) is unreachable"
        }
    }

    /// `POST /v1/sessions/<key>/task`: link a session that is already
    /// running to this task — it counts as work on it until unlinked.
    /// Returns the failure, or nil; a linked task reloads so the join
    /// rows catch up.
    @discardableResult
    public func link(task: HubTaskRow, key: String, hub: LocalHub) async -> String? {
        guard busy.insert("link:\(key)").inserted else { return nil }
        defer { busy.remove("link:\(key)") }
        do {
            try await hub.withClient { try await $0.linkTask(key, ref: task.refBody) }
            actionError = nil
            await load(hub: hub, refresh: true)
            return nil
        } catch let e as HubError {
            actionError = e.message
            return e.message
        } catch {
            actionError = error.localizedDescription
            return error.localizedDescription
        }
    }

    /// `DELETE /v1/sessions/<key>/task`: drop the session's link from
    /// this task.
    @discardableResult
    public func unlink(task: HubTaskRow, key: String, hub: LocalHub) async -> String? {
        guard busy.insert("unlink:\(key)").inserted else { return nil }
        defer { busy.remove("unlink:\(key)") }
        do {
            try await hub.withClient { try await $0.unlinkTask(key, ref: task.refBody) }
            actionError = nil
            await load(hub: hub, refresh: true)
            return nil
        } catch let e as HubError {
            actionError = e.message
            return e.message
        } catch {
            actionError = error.localizedDescription
            return error.localizedDescription
        }
    }
}

// MARK: - new session options

/// What the new-session sheet offers: machines that can launch, their
/// recent folders, and the harnesses each accepts. Ported from the
/// iPhone app's `NewSessionOptions` (app/ios); pure, so tests can feed
/// it hub rows.
public enum NewSessionOptions {
    /// What the machine says it can start, in Provider visibility
    /// order. A machine row older than `launchable` falls back to every
    /// harness it detected; the hub still refuses any it can't start.
    public static func harnesses(on machine: HubMachine) -> [String] {
        machine.launchable ?? machine.harnesses ?? []
    }

    /// What the sheet says when no machine can start a session, from the
    /// rows the hub listed. A row with `launchable` answered: nothing is
    /// installed there or every harness is switched off, so updating swe
    /// would change nothing. Only rows without the field (a hub older
    /// than the new-session route), or none at all, mean swe is too old.
    /// The iPhone's twin says "Quiver's Settings"; here Settings is the
    /// app's own.
    public static func noMachineMessage(_ listed: [HubMachine]) -> String {
        guard listed.contains(where: { $0.launchable != nil }) else {
            return "No machine can start a session. Update swe on the hub."
        }
        return listed.count > 1
            ? "No harness can start on any machine: install one, or turn it on in Settings › Providers."
            : "No harness can start on the hub: install one there, or turn it on in Settings › Providers."
    }

    /// Online machines first, then by name; a machine that can launch
    /// nothing is left out.
    public static func machines(_ machines: [HubMachine]) -> [HubMachine] {
        machines
            .filter { !harnesses(on: $0).isEmpty }
            .sorted { a, b in
                if (a.online ?? false) != (b.online ?? false) { return a.online ?? false }
                return a.displayName.localizedCaseInsensitiveCompare(b.displayName) == .orderedAscending
            }
    }

    /// Every folder a session ran in on `machineId`, most recent first.
    public static func folders(on machineId: String, sessions: [HubSessionRow]) -> [String] {
        var seen = Set<String>()
        return newestFirst(sessions).compactMap { row in
            guard (row.machineId ?? "") == machineId, let cwd = row.cwd, cwd.hasPrefix("/"),
                  seen.insert(cwd).inserted else { return nil }
            return cwd
        }
    }

    /// Folders whose path holds every word of `query`, any case.
    public static func matching(_ folders: [String], query: String) -> [String] {
        let words = query.lowercased().split(whereSeparator: \.isWhitespace)
        guard !words.isEmpty else { return folders }
        return folders.filter { path in
            let p = path.lowercased()
            return words.allSatisfy { p.contains($0) }
        }
    }

    /// The harness last used in `folder`, when it is on offer.
    public static func defaultHarness(folder: String?, machineId: String, offered: [String],
                                      sessions: [HubSessionRow]) -> String? {
        let last = newestFirst(sessions).first {
            ($0.machineId ?? "") == machineId && $0.cwd == folder
        }?.harness
        if let last, offered.contains(last) { return last }
        return offered.first
    }

    /// The machine's name for it, else what its sessions call it, with
    /// a capital first letter.
    public static func name(_ harness: String, on machine: HubMachine?,
                            sessions: [HubSessionRow]) -> String {
        let raw = machine?.harnessNames?[harness]
            ?? sessions.first { $0.harness == harness }?.agent ?? harness
        return raw.prefix(1).uppercased() + raw.dropFirst()
    }

    private static func newestFirst(_ rows: [HubSessionRow]) -> [HubSessionRow] {
        rows.sorted { ($0.lastActiveAt ?? 0) > ($1.lastActiveAt ?? 0) }
    }
}

/// The launch sheets' Model row: whether a machine or harness row can
/// take a `model` pick at launch, which recent models its menu lists,
/// and what the POST body carries. Pure, so tests feed it rows.
///
/// The row hides rather than disables for a build that doesn't report
/// the launch fields (`model_at_launch` on a machine row,
/// `launch_model` on a task-harness row): it predates them, and would
/// take a pick it then silently drops — the hub's own gate refuses it
/// the same way (`unsupported_launch_option`).
public enum LaunchOptions {
    /// Whether the sheet shows the Model row at all: only a machine
    /// row that reports `model_at_launch` knows the launch fields.
    public static func showsModel(on machine: HubMachine?) -> Bool {
        machine?.modelAtLaunch != nil
    }

    /// Whether a launch on `machine` honours a `model` for `harness` —
    /// the field's enabled state.
    public static func takesModel(_ harness: String?, on machine: HubMachine?) -> Bool {
        guard let harness else { return false }
        return machine?.modelAtLaunch?.contains(harness) == true
    }

    /// Whether the sheet shows the Autonomy row at all: only a machine
    /// row that reports `yolo_at_launch` knows the launch switch.
    public static func showsYolo(on machine: HubMachine?) -> Bool {
        machine?.yoloAtLaunch != nil
    }

    /// Whether a launch on `machine` honours `yolo` for `harness` —
    /// the checkbox's enabled state. A harness outside the list keeps
    /// its usual approval checks whatever the checkbox says.
    public static func takesYolo(_ harness: String?, on machine: HubMachine?) -> Bool {
        guard let harness else { return false }
        return machine?.yoloAtLaunch?.contains(harness) == true
    }

    /// `machine`'s recently used models for `harness`, newest first: the
    /// Model menu's entries. A hub older than `recent_models` sends only
    /// `last_models`, which makes a one-item list.
    public static func recentModels(_ harness: String?, on machine: HubMachine?) -> [String] {
        guard let harness, let machine else { return [] }
        // A row with `recent_models` answers alone, even by leaving the
        // harness out; only a row without the key falls back.
        return recentModels(machine.recentModels.map { $0[harness] ?? [] },
                            last: machine.lastModels?[harness])
    }

    /// A task-harness row's recent models, read the same way.
    public static func recentModels(of row: HubTaskHarness?) -> [String] {
        guard let row else { return [] }
        return recentModels(row.recentModels, last: row.lastModel)
    }

    /// `recent` when the hub sent it, else `last` alone; blanks and
    /// repeats dropped so the menu never lists an empty or doubled row.
    static func recentModels(_ recent: [String]?, last: String?) -> [String] {
        var seen = Set<String>()
        return (recent ?? [last ?? ""])
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && seen.insert($0).inserted }
    }

    /// What the POST body's `model` is: `text` trimmed, or nil when
    /// it's blank or this launch can't take a pick.
    public static func model(_ text: String, supported: Bool) -> String? {
        let pick = text.trimmingCharacters(in: .whitespaces)
        return supported && !pick.isEmpty ? pick : nil
    }
}

/// One entry of the launch sheets' Model menu (#914).
public enum ModelChoice: Hashable, Sendable {
    /// Send no `model`: the harness starts on its own default.
    case harnessDefault
    /// One of the harness's recently used models.
    case recent(String)
    /// A typed model id, in the field the menu shows under it.
    case other

    public var title: String {
        switch self {
        case .harnessDefault: "Harness default"
        case .recent(let model): model
        case .other: "Other…"
        }
    }
}

/// The Model menu's state on a launch sheet: the choice, the "Other…"
/// field's text, and whether the user picked (so a harness or machine
/// switch knows what to keep). Pure, so tests drive it.
public struct ModelPick: Equatable, Sendable {
    public private(set) var choice: ModelChoice
    /// The "Other…" field's text; sent only while `choice` is `.other`.
    public var otherText: String
    /// Set by `pick(_:)`, the menu's own binding; cleared by a re-default.
    public private(set) var picked: Bool
    /// The recent model the user last picked from the menu. A switch to
    /// a harness that doesn't list it hides it but keeps it, so the next
    /// harness that lists it gets it back; picking anything else drops it.
    public private(set) var remembered: String?

    public init(recent: [String] = []) {
        choice = Self.defaultChoice(recent)
        otherText = ""
        picked = false
        remembered = nil
    }

    /// The menu's entries in order: "Harness default", each recent model
    /// newest first, then "Other…".
    public static func options(_ recent: [String]) -> [ModelChoice] {
        [.harnessDefault] + recent.map(ModelChoice.recent) + [.other]
    }

    /// The untouched menu's choice: the newest recent model, else
    /// "Harness default" — what the old free-text field prefilled.
    public static func defaultChoice(_ recent: [String]) -> ModelChoice {
        recent.first.map(ModelChoice.recent) ?? .harnessDefault
    }

    /// The user chose `choice` from the menu.
    public mutating func pick(_ choice: ModelChoice) {
        self.choice = choice
        picked = true
        if case .recent(let model) = choice { remembered = model } else { remembered = nil }
    }

    /// Re-reads the menu after a harness or machine change, `recent`
    /// being the new pair's list. A picked "Harness default" or Other
    /// stays. A remembered recent model shows wherever the list has it;
    /// elsewhere the menu shows the list's default, as if untouched, and
    /// keeps the model for a later list that has it. The choice is always
    /// one the list offers, so another harness's model never travels.
    public mutating func refit(_ recent: [String]) {
        switch choice {
        case .harnessDefault where picked, .other where picked: return
        default: break
        }
        if let remembered, recent.contains(remembered) {
            choice = .recent(remembered)
            picked = true
        } else {
            choice = Self.defaultChoice(recent)
            picked = false
        }
    }

    /// What the POST body's `model` is: the chosen recent model or the
    /// trimmed typed one, nil for the default, a blank Other, or a
    /// launch that can't take a pick.
    public func model(supported: Bool) -> String? {
        switch choice {
        case .harnessDefault: nil
        case .recent(let model): LaunchOptions.model(model, supported: supported)
        case .other: LaunchOptions.model(otherText, supported: supported)
        }
    }
}
