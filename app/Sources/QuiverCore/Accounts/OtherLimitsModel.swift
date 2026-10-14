import Foundation
import Observation

/// Limits for harnesses that have no Quiver account (Devin, Cursor, Copilot
/// and the rest), read from `swe list --usage --json`. swe polls only the
/// harnesses starred in its registry and carries its own cooldown and stale
/// handling, so the app adds none of its own here.
@MainActor
@Observable
public final class OtherLimitsModel {
    public struct Row: Identifiable, Codable, Equatable, Sendable {
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

        /// Titled like an account ("Claude: Work"): the brand, then the name
        /// given to it, unless that name already says the brand.
        public var accountTitle: String { OtherLimitsModel.accountTitle(name: name, displayName: displayName) }
    }

    nonisolated static func accountTitle(name: String, displayName: String?) -> String {
        let brand = UsageFormat.harness(name)
        let given = displayName?.trimmingCharacters(in: .whitespaces) ?? ""
        func plain(_ text: String) -> String {
            text.lowercased().replacingOccurrences(of: "-", with: " ").replacingOccurrences(of: "_", with: " ")
        }
        if given.isEmpty { return brand }
        // The name says the brand when it is the brand or starts with it as
        // a whole word: "Qwen Code", "Devin Work", not "Devinshire".
        let name = plain(given), word = plain(brand)
        if name == word || name.hasPrefix(word + " ") { return given }
        return "\(brand): \(given)"
    }

    /// Covered by Accounts, one card per signed-in account.
    static let ownAccounts = ["claude", "codex"]

    /// One per harness starred in swe's registry that has no Quiver
    /// account, in swe's order, with its limit when swe read one. The
    /// Accounts page's harness cards.
    public struct Card: Identifiable, Codable, Equatable, Sendable {
        public var id: String { name }
        public var name: String
        public var displayName: String?
        public var command: String
        public var cliInstalled: Bool?
        public var limit: RateLimit?

        public var title: String {
            let set = displayName?.trimmingCharacters(in: .whitespaces) ?? ""
            return set.isEmpty ? UsageFormat.harness(name) : set
        }
    }

    public private(set) var rows: [Row] = []
    public private(set) var cards: [Card] = []
    /// Every harness swe stars, Claude and Codex included, from the last
    /// successful load; nil until then. A failed load keeps the previous
    /// set, so a swe hiccup hides nothing.
    public private(set) var starred: Set<String>?
    /// Whether each registered harness's command is on PATH, by name,
    /// Claude and Codex included, for the account cards' Terminal button.
    public private(set) var cliInstalled: [String: Bool] = [:]
    /// Why there is nothing to show, when that is not simply "nothing starred".
    public private(set) var note: String?
    private let swe: SweClient
    /// The latest load's ticket: a refresh overtaken while it ran (a star
    /// change reloading ahead of the five-minute background load) drops
    /// its answer rather than write back older state.
    private var request = 0
    /// How fresh swe's cached reading must be to serve it, in seconds: the
    /// Settings "Refresh only what failed for" window, passed to swe as
    /// `--cache-ttl`. An swe too old for the flag is asked without it.
    public var cacheTTL: TimeInterval = 300
    /// Set when swe rejected the usage flags, so they're never sent again
    /// this session.
    private var sweKnowsUsageFlags = true
    /// Same for --exclude: an swe that old polls Claude and Codex too.
    private var sweKnowsExclude = true
    /// Where the last successful list persists between launches.
    private let snapshotFile: URL

    public init(swe: SweClient,
                snapshotFile: URL? = nil) {
        self.swe = swe
        self.snapshotFile = snapshotFile ?? FileManager.default
            .homeDirectoryForCurrentUser
            .appending(path: ".quiver/cache/app-other-limits.json")
        hydrate()
    }

    /// One saved copy of what the last load showed, so the cards render
    /// before swe answers. Missing or corrupt files mean a cold start.
    private struct Snapshot: Codable {
        var savedAt: Date
        var rows: [Row]
        var cards: [Card]
        var starred: [String]
        var cliInstalled: [String: Bool]
    }

    private func hydrate() {
        guard let data = try? Data(contentsOf: snapshotFile),
              let saved = try? JSONDecoder().decode(Snapshot.self, from: data)
        else { return }
        rows = saved.rows
        cards = saved.cards
        starred = Set(saved.starred)
        cliInstalled = saved.cliInstalled
    }

    private func persist() {
        let snapshot = Snapshot(savedAt: .now, rows: rows, cards: cards,
                                starred: starred.map { Array($0) } ?? [],
                                cliInstalled: cliInstalled)
        guard let data = try? JSONEncoder().encode(snapshot) else { return }
        try? FileManager.default.createDirectory(
            at: snapshotFile.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: snapshotFile, options: .atomic)
    }

    /// Whether swe's registry stars a harness. Before the first successful
    /// load everything counts as starred, so a slow start hides nothing.
    public func isStarred(_ harness: String) -> Bool {
        starred?.contains(harness) ?? true
    }

    /// `refreshFailed` is a Refresh-press load: swe re-checks only the
    /// readings that failed, inside the `cacheTTL` freshness window.
    public func load(refreshFailed: Bool = false) async {
        request += 1
        let mine = request
        do {
            // An swe too old for a flag fails the whole call, so each
            // attempt drops what the last error named: the new usage
            // flags first, then --exclude, then the plain call. Both
            // "doesn't know" facts are remembered for the session.
            var harnesses: [Harness]?
            let attempts: [(flags: Bool, exclude: Bool)] = [
                (true, true), (false, true), (false, false),
            ]
            for (sendFlags, sendExclude) in attempts {
                if sendFlags && !sweKnowsUsageFlags { continue }
                if sendExclude && !sweKnowsExclude { continue }
                do {
                    if sendFlags {
                        harnesses = try await swe.harnesses(
                            withUsage: true, excludingUsage: Self.ownAccounts,
                            cacheTTL: cacheTTL, refreshFailed: refreshFailed)
                    } else if sendExclude {
                        harnesses = try await swe.harnesses(
                            withUsage: true, excludingUsage: Self.ownAccounts)
                    } else {
                        harnesses = try await swe.harnesses(withUsage: true)
                    }
                    break
                } catch SweError.commandFailed(let status, let message) {
                    let newFlags = message.contains("--cache-ttl")
                        || message.contains("--refresh-failed")
                    if sendFlags && newFlags {
                        sweKnowsUsageFlags = false
                        continue
                    }
                    if sendExclude && message.contains("--exclude") {
                        sweKnowsExclude = false
                        continue
                    }
                    throw SweError.commandFailed(status: status, message: message)
                }
            }
            guard let harnesses else {
                throw SweError.commandFailed(status: 2, message: "swe could not answer")
            }
            // A newer load already answered; this one's state is older.
            guard mine == request else { return }
            rows = harnesses.compactMap { harness in
                // Starred only: swe polls just those, but can still return a
                // cached reading for one since unstarred.
                guard !Self.ownAccounts.contains(harness.name), harness.state == .starred,
                      let limit = harness.rateLimit else { return nil }
                return Row(name: harness.name, limit: limit, displayName: harness.displayName, command: harness.command,
                           cliInstalled: harness.installed)
            }
            starred = Set(harnesses.lazy.filter { $0.state == .starred }.map(\.name))
            cards = harnesses
                .filter { !Self.ownAccounts.contains($0.name) && $0.state == .starred }
                .map { harness in
                    Card(name: harness.name, displayName: harness.displayName, command: harness.command,
                         cliInstalled: harness.installed, limit: harness.rateLimit)
                }
            cliInstalled = harnesses.reduce(into: [:]) { map, harness in
                if let installed = harness.installed { map[harness.name] = installed }
            }
            note = nil
            persist()
        } catch is CancellationError {
            return
        } catch {
            guard mine == request else { return }
            rows = []
            cards = []
            note = error.localizedDescription
        }
    }
}
