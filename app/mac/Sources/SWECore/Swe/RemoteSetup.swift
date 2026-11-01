import Foundation
import Observation

/// `swe remote setup --json`: every step from nothing to a paired phone,
/// in order, each with its state and the one thing to do about it. The
/// shape is documented in docs/json-contracts.md; src/swe/remote/setup.py
/// builds it.
public struct RemoteSetupReport: Decodable, Sendable, Equatable {
    public let ready: Bool
    public let platform: String
    /// The https address phones reach; "" before MagicDNS gives a name.
    public let url: String
    public let steps: [Step]

    public init(ready: Bool, platform: String, url: String, steps: [Step]) {
        self.ready = ready
        self.platform = platform
        self.url = url
        self.steps = steps
    }

    public enum State: String, Sendable, Equatable {
        case ok, todo, needsYou = "needs_you", failed, blocked
    }

    public struct Step: Decodable, Sendable, Equatable, Identifiable {
        public let id: String
        public let title: String
        public let state: State
        public let detail: String
        public let action: Action?

        public init(id: String, title: String, state: State, detail: String = "", action: Action? = nil) {
            self.id = id
            self.title = title
            self.state = state
            self.detail = detail
            self.action = action
        }

        enum CodingKeys: String, CodingKey { case id, title, state, detail, action }

        public init(from decoder: any Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            id = try c.decode(String.self, forKey: .id)
            title = try c.decode(String.self, forKey: .title)
            // A state a newer swe adds reads as waiting, never as done.
            state = State(rawValue: try c.decode(String.self, forKey: .state)) ?? .blocked
            detail = try c.decodeIfPresent(String.self, forKey: .detail) ?? ""
            action = try c.decodeIfPresent(Action.self, forKey: .action)
        }
    }

    public struct Action: Decodable, Sendable, Equatable {
        public enum Kind: String, Sendable, Equatable {
            /// Open `url` in the browser.
            case open
            /// Open the macOS app named `app`.
            case launch
            /// Copy `command` for a terminal.
            case command
            /// `swe remote setup --apply` does it.
            case apply
            /// Mint a pair code.
            case pair
            /// A kind this build doesn't know; no button.
            case unknown
        }

        public let kind: Kind
        /// The button text swe chose ("Move hub", "Sign in"); shown as is.
        public let label: String
        public let url: String?
        public let app: String?
        public let command: String?

        public init(kind: Kind, label: String, url: String? = nil, app: String? = nil, command: String? = nil) {
            self.kind = kind
            self.label = label
            self.url = url
            self.app = app
            self.command = command
        }

        enum CodingKeys: String, CodingKey { case kind, label, url, app, command }

        public init(from decoder: any Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            kind = Kind(rawValue: try c.decode(String.self, forKey: .kind)) ?? .unknown
            // An action with no label still gets a button that says what it does.
            let given = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
            label = given.isEmpty ? Self.defaultLabel(kind) : given
            url = try c.decodeIfPresent(String.self, forKey: .url)
            app = try c.decodeIfPresent(String.self, forKey: .app)
            command = try c.decodeIfPresent(String.self, forKey: .command)
        }

        static func defaultLabel(_ kind: Kind) -> String {
            switch kind {
            case .open: "Open"
            case .launch: "Open app"
            case .command: "Copy command"
            case .apply: "Set up"
            case .pair: "Pair a phone"
            case .unknown: ""
            }
        }
    }

    /// The first step that isn't done: the one the sheet points at.
    public var current: Step? { steps.first { $0.state != .ok } }

    /// SWE has steps it can do itself (`--apply`): a `todo`, or a
    /// failed step that still offers an apply ("Restart hub" for a hub
    /// that keeps stopping, which `--apply` retries once).
    public var canApply: Bool { steps.contains(where: Self.applies) }

    /// What `--apply` would do, in order, as the steps' own labels.
    public var applyLabels: [String] {
        steps.filter(Self.applies).compactMap { step in
            guard let action = step.action, action.kind == .apply, !action.label.isEmpty else { return nil }
            return action.label
        }
    }

    static func applies(_ step: Step) -> Bool {
        step.state == .todo || (step.state == .failed && step.action?.kind == .apply)
    }
}

extension SweClient {
    /// `--apply` does every step SWE can and returns the report after.
    /// It can wait out a certificate fetch (90 s), the hub's first answer
    /// (20 s) and `tailscale serve` (60 s), so it gets more than the
    /// runner's usual two minutes.
    public static let remoteSetupApplyTimeout: Duration = .seconds(300)

    public func remoteSetup(apply: Bool = false) async throws -> RemoteSetupReport {
        var args = ["remote", "setup"]
        if apply { args.append("--apply") }
        args.append("--json")
        return try await call(args, allowFailure: true,
                              timeout: apply ? Self.remoteSetupApplyTimeout : nil)
    }
}

/// The remote-control setup checklist behind Settings › Integrations ›
/// Remote. The sheet polls while it is open, so a step finished in a
/// browser (signing in, turning on HTTPS) ticks on its own; `apply` runs
/// `--apply`, one at a time, and the poll waits while it runs.
///
/// Every load and apply takes a new generation, and an answer lands only
/// if its generation is still the newest, so a slow older answer never
/// replaces a newer one. The report already shown stays while a newer
/// one loads, so polling never flickers.
@MainActor
@Observable
public final class RemoteSetupModel {
    public private(set) var report: RemoteSetupReport?
    /// Why the newest load failed; cleared by the next one that works.
    public private(set) var loadError: String?
    public private(set) var loading = false
    public private(set) var applying = false
    public private(set) var applyError: String?

    /// How long the poll waits between looks.
    public let interval: Duration
    private let swe: @MainActor () -> SweClient
    private let sleep: @Sendable (Duration) async throws -> Void
    private var generation = 0

    /// `swe` is asked at each call, so the app's current swe (whose path
    /// can change while the sheet is open) answers.
    public init(swe: @escaping @MainActor () -> SweClient,
                interval: Duration = .seconds(3),
                sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }) {
        self.swe = swe
        self.interval = interval
        self.sleep = sleep
    }

    public var isReady: Bool { report?.ready == true }

    /// One look. A no-op while `--apply` runs: its report is newer than
    /// anything a plain look started now could say.
    public func load() async {
        guard !applying else { return }
        generation += 1
        let mine = generation
        loading = true
        defer { if mine == generation { loading = false } }
        do {
            let next = try await swe().remoteSetup()
            guard mine == generation else { return }
            report = next
            loadError = nil
        } catch is CancellationError {
        } catch {
            guard mine == generation else { return }
            loadError = error.localizedDescription
        }
    }

    /// Look now, then every `interval` until the report is ready or the
    /// calling task is cancelled (the sheet's `.task` ends when it closes).
    /// A tick that falls while `--apply` runs is skipped, and one after
    /// an apply that finished the job doesn't look again.
    public func watch() async {
        await load()
        while !Task.isCancelled && !isReady {
            do { try await sleep(interval) } catch { return }
            if Task.isCancelled || isReady { return }
            await load()
        }
    }

    /// `swe remote setup --apply`: every step SWE can do, then the
    /// report after. A second press while one runs does nothing.
    public func apply() async {
        guard !applying else { return }
        applying = true
        applyError = nil
        generation += 1
        let mine = generation
        // A look in flight belongs to an older generation now.
        loading = false
        defer { applying = false }
        do {
            let next = try await swe().remoteSetup(apply: true)
            guard mine == generation else { return }
            report = next
            loadError = nil
        } catch is CancellationError {
        } catch {
            guard mine == generation else { return }
            applyError = error.localizedDescription
        }
    }
}
