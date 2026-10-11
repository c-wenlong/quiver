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
    }

    /// Covered by Accounts, one card per signed-in account.
    static let ownAccounts: Set<String> = ["claude", "codex"]

    public private(set) var rows: [Row] = []
    /// Why there is nothing to show, when that is not simply "nothing starred".
    public private(set) var note: String?
    private let swe: SweClient

    public init(swe: SweClient) {
        self.swe = swe
    }

    public func load() async {
        do {
            rows = try await swe.harnesses(withUsage: true).compactMap { harness in
                guard !Self.ownAccounts.contains(harness.name), let limit = harness.rateLimit else { return nil }
                return Row(name: harness.name, limit: limit)
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
