import Foundation
import Observation

/// `swe doctor --json`.
public struct DoctorReport: Decodable, Sendable, Equatable {
    public let ok: Bool
    public let findings: [Finding]

    public init(ok: Bool, findings: [Finding]) {
        self.ok = ok
        self.findings = findings
    }

    public struct Finding: Decodable, Sendable, Equatable, Identifiable {
        public enum Severity: String, Decodable, Sendable { case error, warning, info }

        /// Check and message joined by a NUL, which neither contains, so
        /// "ab"+"c" and "a"+"bc" never share run state (as the web does).
        public var id: String { check + "\u{0}" + message }
        public let check: String
        public let severity: Severity
        public let message: String
        public let fix: String?
        /// The harness the finding is about (an off-PATH install), when it
        /// is about one; older swe builds leave it out.
        public let harness: String?
        /// The fix swe can run for us, when it has one.
        public let action: Action?
        /// For `install`: the method swe reinstalls the harness with.
        public let via: String?
        /// The file or place behind the finding, e.g. "declared in
        /// ~/.claude/settings.json"; older swe builds leave it out.
        public let source: String?
        /// The ways out, best first: Run for a fix swe can do, Fix with
        /// agent for one needing judgment, a copyable command or manual
        /// steps. Older swe builds leave it out; `fix` is the fallback.
        public let remedies: [Remedy]?

        /// Fixes swe runs for us (`action` in the row, or its first
        /// `action` remedy); an action a newer swe adds decodes as nil so
        /// this build just doesn't offer Run. Every value here has a runner
        /// in `harness/doctor_fix.py` (a Python test compares the lists).
        public enum Action: String, Decodable, Sendable, CaseIterable {
            case setup, `init`, discover, install
            case undeclare
            case marketplaceDrop = "marketplace-drop"
            case pluginFix = "plugin-fix"
            case pluginReinstall = "plugin-reinstall"
            case danglingLink = "dangling-link"
            case mcpSync = "mcp-sync"

            /// Fix all runs these: local repairs that only add or relink.
            /// The rest (installs, and fixes that delete or overwrite)
            /// keep their row's Run. Mirrors `doctor_fix.BATCH`.
            public var isBatch: Bool {
                switch self {
                case .setup, .`init`, .discover, .pluginFix: true
                default: false
                }
            }

            /// The four swe has always run through their own commands;
            /// the rest go through `swe doctor --fix=<check> --message=`.
            var isLegacy: Bool {
                switch self {
                case .setup, .`init`, .discover, .install: true
                default: false
                }
            }
        }

        /// One way out of a finding (the web page's remedy rows). `kind`
        /// is lenient like `action`: a kind a newer swe adds decodes as
        /// `other`, which still shows its title and detail. `action`
        /// stays a String: a remedy names its fix for `swe doctor --fix`
        /// to resolve, so an action this build doesn't know simply shows
        /// no Run, the same as `action` on the finding.
        public struct Remedy: Decodable, Sendable, Equatable {
            public enum Kind: String, Sendable { case action, agent, command, steps, other }
            public let kind: Kind
            public let title: String
            public let detail: String?
            /// `agent`: the prompt the launch carries.
            public let prompt: String?
            /// `command`: the line to copy; an `action` can carry one too,
            /// for the terminal path its confirm offers.
            public let command: String?
            /// `steps`: the manual path, one line per step.
            public let steps: [String]?
            /// `action`: the fix `swe doctor --fix` runs, by name.
            public let action: String?
            /// `install`'s method, as the finding's `via` carries it.
            public let via: String?
            /// `undeclare`'s dead script and `dangling-link`'s broken
            /// link, for the confirm line.
            public let script: String?
            public let path: String?

            private enum CodingKeys: String, CodingKey {
                case kind, title, detail, prompt, command, steps, action, via, script, path
            }

            public init(kind: Kind, title: String, detail: String? = nil,
                        prompt: String? = nil, command: String? = nil,
                        steps: [String]? = nil, action: String? = nil,
                        via: String? = nil, script: String? = nil, path: String? = nil) {
                self.kind = kind
                self.title = title
                self.detail = detail
                self.prompt = prompt
                self.command = command
                self.steps = steps
                self.action = action
                self.via = via
                self.script = script
                self.path = path
            }

            public init(from decoder: any Decoder) throws {
                let box = try decoder.container(keyedBy: CodingKeys.self)
                kind = (try box.decodeIfPresent(String.self, forKey: .kind))
                    .flatMap(Kind.init) ?? .other
                action = try box.decodeIfPresent(String.self, forKey: .action)
                // Every remedy swe emits has a title; one that drops it
                // still shows its action or kind rather than failing the
                // whole list.
                title = try box.decodeIfPresent(String.self, forKey: .title)
                    ?? action ?? kind.rawValue
                detail = try box.decodeIfPresent(String.self, forKey: .detail)
                prompt = try box.decodeIfPresent(String.self, forKey: .prompt)
                command = try box.decodeIfPresent(String.self, forKey: .command)
                steps = try box.decodeIfPresent([String].self, forKey: .steps)
                via = try box.decodeIfPresent(String.self, forKey: .via)
                script = try box.decodeIfPresent(String.self, forKey: .script)
                path = try box.decodeIfPresent(String.self, forKey: .path)
            }

            /// The remedy's action as a known case, or nil when a newer
            /// swe named one this build doesn't have: no Run then, as
            /// `action` on the finding does.
            public var knownAction: Action? { action.flatMap(Action.init) }
        }

        private enum CodingKeys: String, CodingKey {
            case check, severity, message, fix, harness, action, via, source, remedies
        }

        public init(check: String, severity: Severity, message: String,
                    fix: String? = nil, harness: String? = nil, action: Action? = nil,
                    via: String? = nil, source: String? = nil,
                    remedies: [Remedy]? = nil) {
            self.check = check
            self.severity = severity
            self.message = message
            self.fix = fix
            self.harness = harness
            self.action = action
            self.via = via
            self.source = source
            self.remedies = remedies
        }

        public init(from decoder: any Decoder) throws {
            let box = try decoder.container(keyedBy: CodingKeys.self)
            check = try box.decode(String.self, forKey: .check)
            severity = try box.decode(Severity.self, forKey: .severity)
            message = try box.decode(String.self, forKey: .message)
            fix = try box.decodeIfPresent(String.self, forKey: .fix)
            harness = try box.decodeIfPresent(String.self, forKey: .harness)
            let named = try box.decodeIfPresent(String.self, forKey: .harness)
            let method = try box.decodeIfPresent(String.self, forKey: .via)
            via = method
            // A drop or a broken link names its action only on a remedy.
            remedies = (try? box.decodeIfPresent([Remedy].self, forKey: .remedies)) ?? nil
            let own = try box.decodeIfPresent(String.self, forKey: .action)
            let actionName = own ?? remedies?.first { $0.kind == .action && $0.knownAction != nil }?.action
            // An install with no harness or method to run it with can't be offered.
            action = actionName
                .flatMap(Action.init)
                .flatMap { $0 == .install && (named == nil || method == nil) ? nil : $0 }
            source = try box.decodeIfPresent(String.self, forKey: .source)
        }
    }
}

extension SweClient {
    /// Doctor exits 1 when it finds problems, so a non-zero status with a
    /// JSON body is a report, not a failure.
    public func doctor() async throws -> DoctorReport {
        try await call(["doctor", "--json"], allowFailure: true)
    }

    /// `swe harness discover --apply`: registers the high-confidence
    /// matches. The JSON is informational; only the exit matters.
    @discardableResult
    public func applyDiscovery() async throws -> String {
        try await runPlain(["harness", "discover", "--apply", "--json"])
    }

    /// `swe install <name> --via=<via>`, as the web Doctor runs it: no
    /// terminal, stdin closed, the output kept. An installer can take a
    /// while (a cask download), so it gets 15 minutes rather than the
    /// default bound. A non-zero exit is a result, not a throw: the row
    /// shows what the installer printed.
    public func installHarness(name: String, via: String) async throws -> InstallRun {
        let result = try await runner.run(["install", name, "--via=\(via)"], input: nil,
                                          timeout: .seconds(900))
        try Task.checkCancellation()
        let out = Self.plain(String(decoding: result.stdout, as: UTF8.self))
        let err = Self.plain(result.stderr)
        if result.status == 127 && err.contains("No such file or directory") {
            throw SweError.notInstalled("swe")
        }
        return InstallRun(status: result.status,
                          output: [out, err].filter { !$0.isEmpty }.joined(separator: "\n"))
    }
}

/// `swe doctor --fix --json`: what ran, what it fixed, what is left.
public struct FixRun: Decodable, Sendable, Equatable {
    public struct Step: Decodable, Sendable, Equatable {
        public let action: String
        public let fix: String?
        public let error: String?
    }

    public struct Ref: Decodable, Sendable, Equatable {
        public let check: String
        public let message: String
    }

    public let ran: [Step]
    public let fixed: [Ref]
    public let notCleared: [Ref]
    public let skipped: [Ref]
    /// Every finding in scope still there, manual ones included.
    public let stillFailing: [Ref]

    // SweClient decodes with .convertFromSnakeCase: not_cleared arrives
    // as notCleared, so the keys carry no raw values.
    private enum CodingKeys: String, CodingKey {
        case ran, fixed, skipped, notCleared, stillFailing
    }

    public init(ran: [Step], fixed: [Ref], notCleared: [Ref], skipped: [Ref],
                stillFailing: [Ref] = []) {
        self.ran = ran
        self.fixed = fixed
        self.notCleared = notCleared
        self.skipped = skipped
        self.stillFailing = stillFailing
    }

    public init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        ran = try box.decode([Step].self, forKey: .ran)
        fixed = try box.decode([Ref].self, forKey: .fixed)
        notCleared = try box.decodeIfPresent([Ref].self, forKey: .notCleared) ?? []
        skipped = try box.decodeIfPresent([Ref].self, forKey: .skipped) ?? []
        stillFailing = try box.decodeIfPresent([Ref].self, forKey: .stillFailing) ?? []
    }

    /// The first fix that failed, for the row or the summary.
    public var firstError: String? { ran.compactMap(\.error).first }
}

extension SweClient {
    /// `swe doctor --fix --json`: Fix all. Exits 1 when a fix failed or
    /// one is still owed, with the JSON either way.
    public func fixAll() async throws -> FixRun {
        try await call(["doctor", "--fix", "--json"], allowFailure: true,
                       timeout: Self.fixTimeout)
    }

    /// A plugin command alone may take five minutes, and a batch runs
    /// several: stopping early could leave a reinstall half done.
    static let fixTimeout: Duration = .seconds(1800)

    /// One finding's fix by name, as the web's Run sends it: swe finds
    /// the finding in a fresh report and refuses one that is gone.
    public func fix(check: String, message: String, action: String) async throws -> FixRun {
        try await call(["doctor", "--fix=\(check)", "--message=\(message)",
                        "--action=\(action)", "--json"], allowFailure: true,
                       timeout: Self.fixTimeout)
    }
}

/// The confirm line an action remedy's panel carries, porting the web
/// page's `DOCTOR_RUNS` (static/pages/doctor.js): same wording, "this
/// Mac" where the web names the hub machine.
public enum DoctorConfirm {
    public static func text(action: DoctorReport.Finding.Action,
                            finding: DoctorReport.Finding,
                            remedy: DoctorReport.Finding.Remedy?) -> String {
        let fix = remedy?.command ?? finding.fix ?? remedy?.title ?? "this fix"
        switch action {
        case .setup:
            return "This runs swe setup --apply on this Mac: it registers the harnesses it finds, imports their MCP servers into ~/.swe/mcp.json, and links the shared AGENTS.md and skills into each harness. Real files in the way are left alone, and your stars stay as they are."
        case .`init`:
            return "This runs swe init on this Mac: it links the shared AGENTS.md and skills into each harness that doesn't read them yet. Real files in the way are left alone, and new skills folders wait for a run in a terminal."
        case .discover:
            return "This runs swe harness discover --apply on this Mac: it adds the high-confidence matches it finds to harness.json."
        case .install:
            let name = finding.harness.map { HarnessMark($0).name } ?? "the harness"
            let via = remedy?.via ?? finding.via ?? "the installer"
            return "This runs \(fix) on this Mac: it installs \(name) again with \(via), which puts it on PATH, and registers it. A cask or install script that asks for a password can't finish here; copy the command into a terminal instead."
        case .undeclare:
            return "On this Mac: deletes the hook entries naming \(remedy?.script ?? "the missing script") from the settings files in the source line, after backing each one up to ~/.swe/backups. Nothing else in the files changes."
        case .pluginFix:
            return "On this Mac: runs \(remedy?.detail ?? fix), which registers the marketplace folder or installs the plugin from it. Nothing is removed."
        case .pluginReinstall:
            return "On this Mac: runs \(remedy?.detail ?? fix): Codex's copy of the plugin is removed, then installed again from your folder in ~/.swe/plugins."
        case .danglingLink:
            return "On this Mac: copies the broken link \(remedy?.path ?? "") to ~/.swe/backups, then removes it. A link synced from another machine may still work there."
        case .mcpSync:
            return "On this Mac: \(fix) replaces this one server entry with the hub's copy from ~/.swe/mcp.json, after backing the config file up to ~/.swe/backups."
        case .marketplaceDrop:
            return "On this Mac: deletes the missing marketplace entries from the settings files, after backing each one up to ~/.swe/backups. The folders stay gone; restore them by copying them over."
        }
    }
}

/// What a `swe install` run printed, and how it exited.
public struct InstallRun: Sendable, Equatable {
    public let status: Int32
    public let output: String

    public init(status: Int32, output: String) {
        self.status = status
        self.output = output
    }

    public var succeeded: Bool { status == 0 }
}

/// Loading state shared by every page that reads from swe.
public enum Loadable<Value: Equatable & Sendable>: Equatable, Sendable {
    case idle
    case loading
    case loaded(Value)
    case failed(String)
}

@MainActor
@Observable
public final class DoctorModel {
    public private(set) var state: Loadable<DoctorReport> = .idle
    /// Findings whose Run is in flight, and what the last failure said.
    /// `internal(set)` so a test can stage one in flight.
    public internal(set) var running: Set<DoctorReport.Finding.ID> = []
    public internal(set) var runErrors: [DoctorReport.Finding.ID: String] = [:]
    /// When the run in flight started, for the row's elapsed time.
    public private(set) var runStarted: Date?
    /// What a failed install printed, kept on its row with Run in
    /// Terminal beside it (an installer may need a password).
    public internal(set) var installOutput: [DoctorReport.Finding.ID: String] = [:]
    /// The last install that worked: its finding is gone from the report,
    /// so the section shows what it printed until dismissed.
    public internal(set) var installed: (harness: String, output: String)?
    /// Fix all's last result, shown until dismissed, and its failure.
    public internal(set) var fixAllResult: FixRun?
    public internal(set) var fixAllError: String?
    /// The `running` id Fix all holds while it runs.
    public static let fixAllID = "fix-all"
    private let swe: SweClient
    /// Bumped by each load; a reply from an older load is dropped, so a slow
    /// earlier refresh cannot overwrite a newer one.
    private var generation = 0

    /// The Plugins page's one-change-at-a-time turn: a plugin repair here
    /// must not overlap an install or uninstall there (a reinstall could
    /// undo an uninstall made meanwhile). The app hands over its own.
    public var work: PluginWork

    public init(swe: SweClient, work: PluginWork = PluginWork()) {
        self.swe = swe
        self.work = work
    }

    /// The plugin actions, which take `work` for their run.
    static func touchesPlugins(_ action: DoctorReport.Finding.Action) -> Bool {
        action == .pluginFix || action == .pluginReinstall
    }

    /// Checks or a fix are in flight, so the report is about to change:
    /// copying it now would copy a stale or missing one.
    public var isBusy: Bool {
        if case .loading = state { return true }
        return !running.isEmpty
    }

    public func load() async {
        generation += 1
        let mine = generation
        // A cancelled load (the page closed) puts this back rather than
        // leaving the spinner, and `isBusy`, on for good.
        let before = state == .loading ? .idle : state
        state = .loading
        let next: Loadable<DoctorReport>
        do {
            next = .loaded(try await swe.doctor())
        } catch is CancellationError {
            if mine == generation { state = before }
            return
        } catch {
            next = .failed(error.localizedDescription)
        }
        if mine == generation { state = next }
    }

    /// One repair at a time: a run in flight keeps the others waiting.
    /// `action` overrides the finding's own: an action remedy's Run runs
    /// its fix, and `swe doctor --fix` resolves the remedy by name on a
    /// fresh report, so a finding gone stale is refused by swe.
    public func run(_ finding: DoctorReport.Finding,
                    action override: DoctorReport.Finding.Action? = nil) async {
        guard let action = override ?? finding.action, running.isEmpty else { return }
        running.insert(finding.id)
        runErrors[finding.id] = nil
        installOutput[finding.id] = nil
        runStarted = Date()
        defer { runStarted = nil }
        do {
            switch action {
            case .setup:
                // setup and discover rewrite harness.json: they wait behind
                // the Harnesses page's gate so no edit there is lost.
                await HarnessesModel.registryWrites.acquire()
                defer { HarnessesModel.registryWrites.release() }
                let outcome = try await swe.applySetup(starred: nil, adopt: nil)
                // A linking pass that stops early still counts as a
                // repair for the report, but its reason shows on the row.
                if let stopped = outcome.links?.error {
                    runErrors[finding.id] = stopped
                }
            case .`init`:
                try await swe.runInit()
            case .discover:
                await HarnessesModel.registryWrites.acquire()
                defer { HarnessesModel.registryWrites.release() }
                _ = try await swe.applyDiscovery()
            case .undeclare, .marketplaceDrop, .pluginFix, .pluginReinstall,
                 .danglingLink, .mcpSync:
                let plugin = Self.touchesPlugins(action)
                if plugin && !work.begin() {
                    runErrors[finding.id] = "A plugin change is still running. Wait for it, then try again."
                    break
                }
                defer { if plugin { work.end() } }
                let run = try await swe.fix(check: finding.check, message: finding.message,
                                            action: action.rawValue)
                if let error = run.firstError { runErrors[finding.id] = error }
            case .install:
                guard let harness = finding.harness, let via = finding.via else { break }
                let run = try await swe.installHarness(name: harness, via: via)
                // Only the end is shown, so only the end is kept.
                let tail = Self.outputTail(run.output)
                if run.succeeded {
                    installed = (harness, tail)
                } else {
                    runErrors[finding.id] = "swe install \(harness) exited \(run.status)."
                    installOutput[finding.id] = tail
                }
            }
        } catch {
            runErrors[finding.id] = error.localizedDescription
        }
        // Even a failed run may have fixed something (a partial `swe
        // init`), so re-read the report either way.
        await load()
        running.remove(finding.id)
    }

    /// Fix all: `swe doctor --fix`, one at a time with the row Runs. It
    /// can run setup and discover, so it waits behind the registry gate.
    public func fixAll() async {
        guard running.isEmpty else { return }
        running.insert(Self.fixAllID)
        fixAllResult = nil
        fixAllError = nil
        runStarted = Date()
        defer { runStarted = nil }
        do {
            // Fix all can install plugins: it takes the Plugins turn too.
            guard work.begin() else {
                fixAllError = "A plugin change is still running. Wait for it, then try again."
                running.remove(Self.fixAllID)
                return
            }
            defer { work.end() }
            await HarnessesModel.registryWrites.acquire()
            defer { HarnessesModel.registryWrites.release() }
            fixAllResult = try await swe.fixAll()
        } catch {
            fixAllError = error.localizedDescription
        }
        await load()
        running.remove(Self.fixAllID)
    }

    public func dismissFixAll() {
        fixAllResult = nil
        fixAllError = nil
    }

    /// How many findings Fix all would run a fix for.
    public static func batchCount(_ report: DoctorReport) -> Int {
        report.findings.filter { $0.action?.isBatch == true }.count
    }

    /// A run the page did itself (Run in Terminal) still reports under
    /// the row.
    public func noteError(_ message: String, for id: DoctorReport.Finding.ID) {
        runErrors[id] = message
    }

    public func dismissInstalled() {
        installed = nil
    }

    /// The last lines of an install's output, for the row: the end is
    /// where an installer says what went wrong.
    public static func outputTail(_ output: String, lines: Int = 40) -> String {
        output.split(separator: "\n", omittingEmptySubsequences: false).suffix(lines).joined(separator: "\n")
    }

    /// The line Run in Terminal types: a leading bare `swe` becomes the
    /// quoted executable the page would use itself, so the bundled one works.
    public static func terminalLine(fix: String, swe: String) -> String {
        guard fix.hasPrefix("swe") else { return fix }
        return LaunchPlanner.shellQuote(swe) + fix.dropFirst(3)
    }
}
