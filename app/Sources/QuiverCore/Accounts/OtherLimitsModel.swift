import Foundation
import Observation

/// Limits for harnesses that have no Quiver account (Devin, Cursor, Copilot
/// and the rest), read from `swe list --usage --json`. swe polls only the
/// harnesses starred in its registry and carries its own cooldown and stale
/// handling, so the app adds none of its own here.
@MainActor
@Observable
public final class OtherLimitsModel {
    public struct Row: Identifiable, Equatable, Sendable {
        public var id: String { name }
        public var name: String
        public var limit: RateLimit
        /// Its display name from harness.json, if set.
        public var displayName: String? = nil
        /// What swe runs for it, from harness.json.
        public var command: String = ""
        /// Whether that command is on PATH; nil from an older swe.
        public var cliInstalled: Bool? = nil

        /// The display name, else the brand name, else the id capitalised.
        public var title: String {
            let set = displayName?.trimmingCharacters(in: .whitespaces) ?? ""
            return set.isEmpty ? UsageFormat.harness(name) : set
        }
    }

    /// Covered by Accounts, one card per signed-in account.
    static let ownAccounts = ["claude", "codex"]

    public private(set) var rows: [Row] = []
    /// Whether each registered harness's command is on PATH, by name,
    /// Claude and Codex included, for the account cards' Terminal button.
    public private(set) var cliInstalled: [String: Bool] = [:]
    /// Why there is nothing to show, when that is not simply "nothing starred".
    public private(set) var note: String?
    private let swe: SweClient

    public init(swe: SweClient) {
        self.swe = swe
    }

    public func load() async {
        do {
            let harnesses: [Harness]
            do {
                harnesses = try await swe.harnesses(withUsage: true, excludingUsage: Self.ownAccounts)
            } catch SweError.commandFailed(_, let message) where message.contains("--exclude") {
                // An swe from before --exclude: ask again and accept that it
                // polls Claude and Codex too.
                harnesses = try await swe.harnesses(withUsage: true)
            }
            rows = harnesses.compactMap { harness in
                guard !Self.ownAccounts.contains(harness.name), let limit = harness.rateLimit else { return nil }
                return Row(name: harness.name, limit: limit, displayName: harness.displayName, command: harness.command,
                           cliInstalled: harness.installed)
            }
            cliInstalled = harnesses.reduce(into: [:]) { map, harness in
                if let installed = harness.installed { map[harness.name] = installed }
            }
            note = nil
        } catch is CancellationError {
            return
        } catch {
            rows = []
            note = error.localizedDescription
        }
    }
}
