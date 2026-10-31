import Foundation
import Observation

/// What `swe init --accounts` links into Quiver's extra Claude and Codex
/// account homes: one switch per piece of shared content, kept in swe's
/// config.json as `accounts.sync.<piece>` so the CLI and the app agree.
public enum AccountSyncPiece: String, CaseIterable, Sendable, Identifiable {
    case instructions, skills, hooks, mcp

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .instructions: "Instructions"
        case .skills: "Skills"
        case .hooks: "Hooks"
        case .mcp: "MCP servers"
        }
    }

    /// What turning it on puts in each account's folder.
    public var caption: String {
        switch self {
        case .instructions: "The shared AGENTS.md, as CLAUDE.md or AGENTS.md"
        case .skills: "The shared skills folder"
        case .hooks: "Each hook script; the account's settings still declare it"
        case .mcp: "The hub's MCP servers, merged into the account's .claude.json or config.toml"
        }
    }
}

/// `swe init --accounts [--check] --json`.
public struct AccountSyncReport: Decodable, Sendable, Equatable {
    /// Nil when swe could not read config.json.
    public let accountSync: [String: Bool]?
    public let accounts: [Row]
    /// Why the pass did not run (an unreadable file); empty when it ran.
    public let notes: [String]

    public struct Row: Decodable, Sendable, Equatable {
        public let account: String
        public let primitive: String
        public let path: String
        public let state: String
        public let detail: String
        /// The MCP server a row is about; nil for a link.
        public let name: String?

        /// How the card names it: the server, else the file.
        public var target: String { name ?? (path as NSString).lastPathComponent }
    }

    public func isOn(_ piece: AccountSyncPiece) -> Bool {
        accountSync?[piece.rawValue] == true
    }

    /// Targets the account already fills with something of its own, which
    /// swe reports and leaves alone.
    public var conflicts: [Row] { accounts.filter { $0.state == "conflict" } }

    /// Account files swe could not read or edit safely (invalid JSON or
    /// TOML), so it changed nothing in them.
    public var blocked: [Row] { accounts.filter { $0.state == "blocked" } }
}

extension SweClient {
    /// The account pass: links what each switch asks for and unlinks what
    /// a switch turned off. `check` only reports.
    public func syncAccounts(check: Bool = false) async throws -> AccountSyncReport {
        // Exit 1 still carries the document: its notes say why it didn't run.
        try await call(check ? ["init", "--accounts", "--check", "--json"]
                             : ["init", "--accounts", "--json"],
                       allowFailure: true)
    }

    public func setAccountSync(_ piece: AccountSyncPiece, on: Bool) async throws {
        _ = try await runPlain(["config", "set", "accounts.sync.\(piece.rawValue)", on ? "true" : "false"])
    }
}

/// The Accounts page's "Shared content" switches.
@MainActor
@Observable
public final class AccountSyncModel {
    public private(set) var report: AccountSyncReport?
    public private(set) var busy = false
    public private(set) var error: String?
    /// A new account asked for a pass while another was running.
    private var pendingSync = false

    private let swe: SweClient

    public init(swe: SweClient) {
        self.swe = swe
    }

    public func isOn(_ piece: AccountSyncPiece) -> Bool { report?.isOn(piece) ?? false }

    public var anyOn: Bool { AccountSyncPiece.allCases.contains { isOn($0) } }

    public func load() async {
        do {
            report = try await swe.syncAccounts(check: true)
            error = report?.notes.first
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Saves the switch, then runs the pass so the account folders match.
    public func set(_ piece: AccountSyncPiece, on: Bool) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            try await swe.setAccountSync(piece, on: on)
            try await apply()
        } catch {
            // Re-read the switches, but keep saying why this one failed.
            let message = error.localizedDescription
            await load()
            self.error = message
        }
        if pendingSync { await runPasses() }
    }

    /// After an account is added, so it starts out linked rather than
    /// waiting for the next switch or `swe init`. While another pass runs,
    /// the request waits and runs after it: that pass may have read the
    /// profiles before the new account was saved.
    public func syncIfOn() async {
        if report == nil { await load() }
        guard anyOn else { return }
        if busy {
            pendingSync = true
            return
        }
        busy = true
        defer { busy = false }
        await runPasses()
    }

    /// One pass, then another for each request that came in meanwhile.
    private func runPasses() async {
        repeat {
            pendingSync = false
            do {
                try await apply()
            } catch {
                self.error = error.localizedDescription
            }
        } while pendingSync
    }

    private func apply() async throws {
        let ran = try await swe.syncAccounts()
        report = ran
        error = ran.notes.first
    }
}
