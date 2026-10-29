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
        default:
            return [source, kind].compactMap { $0 }.joined(separator: " ").capitalized
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

    public var id: String { name }

    public init(name: String, displayName: String? = nil, tracked: Bool? = nil) {
        self.name = name
        self.displayName = displayName
        self.tracked = tracked
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
        case .prOpen: task.state == "pr_open"
            || (task.kind == "pull" && task.pr?.state == "open")
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
    public static func sections(of tasks: [HubTaskRow], filter: TaskFilter) -> [TaskSection] {
        var order: [String] = []
        var buckets: [String: [HubTaskRow]] = [:]
        var repos: [String: String] = [:]
        for task in tasks where filter.includes(task) {
            let scope = task.scope.isEmpty ? "Other" : task.scope
            if buckets[scope] == nil {
                order.append(scope)
                repos[scope] = task.source == "github" ? task.repo : nil
            }
            buckets[scope, default: []].append(task)
        }
        return order.map { TaskSection(name: $0, rows: buckets[$0] ?? [], error: nil, repo: repos[$0]) }
    }

    /// `filter`'s matching count across every task — the filter chips.
    public static func count(_ tasks: [HubTaskRow], filter: TaskFilter) -> Int {
        tasks.filter(filter.includes).count
    }
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
            let status = body.linear?.status
            linearBlocked = switch status {
            case nil, "ok", "disabled": nil
            default: body.linear?.error ?? status ?? "Linear is unreachable"
            }
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
        default: false
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
