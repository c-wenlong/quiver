import Foundation
import Observation

/// Limits for harnesses that have no Quiver account (Devin, Cursor, Copilot
/// and the rest), read from `swe list --usage --json`. swe polls only the
/// harnesses starred in its registry and carries its own cooldown and stale
/// handling, so the app adds none of its own here.
@MainActor
@Observable
public final class OtherLimitsModel {
    /// Why a card cannot show a reading: the account needs signing in,
    /// its credentials are there but the fetch failed, or the installed swe
    /// cannot answer at all.
    public enum UsageNote: String, Codable, Sendable {
        case signIn = "sign-in"
        case fetchFailed = "fetch-failed"
        case updateSwe = "update-swe"

        /// The note for swe's `no_reading` on a harness's own row.
        init?(_ reason: Harness.NoReading?) {
            switch reason {
            case .signedOut?: self = .signIn
            case .error?: self = .fetchFailed
            case nil: return nil
            }
        }
    }

    public struct Row: Identifiable, Codable, Equatable, Sendable {
        /// The profile id for an extra account, the harness name otherwise:
        /// two extra Cursor accounts must order and hide independently.
        public var id: String { profile?.id ?? name }
        public var name: String
        /// Set when this row is an extra account of the harness, not the
        /// harness's own reading.
        public var profile: Profile? = nil
        public var limit: RateLimit
        /// Its display name from harness.json, if set.
        public var displayName: String? = nil
        /// Its account name from the app's own names file, if set. Wins over
        /// `displayName` everywhere the harness is presented as an account.
        public var accountName: String? = nil
        /// What swe runs for it, from harness.json.
        public var command: String = ""
        /// Whether that command is on PATH; nil from an older swe.
        public var cliInstalled: Bool? = nil

        /// The account name, else the display name, else the brand name.
        public var title: String {
            if let profile { return OtherLimitsModel.accountTitle(name: name, displayName: profile.name) }
            let set = (accountName ?? displayName)?.trimmingCharacters(in: .whitespaces) ?? ""
            return set.isEmpty ? UsageFormat.harness(name) : set
        }

        /// Titled like an account ("Claude: Work"): the brand, then the name
        /// given to it, unless that name already says the brand.
        public var accountTitle: String {
            OtherLimitsModel.accountTitle(name: name,
                                          displayName: profile?.name ?? accountName ?? displayName)
        }
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
        /// The profile id for an extra account, the harness name otherwise.
        public var id: String { profile?.id ?? name }
        public var name: String
        /// Set when this card is an extra account of the harness, not the
        /// harness's own card.
        public var profile: Profile? = nil
        /// Why the account shows no usage, when it should (`nil` when the
        /// generic "no reading" answers).
        public var usageNote: UsageNote? = nil
        /// swe's typed verdict and remedy, when it named one: the card's
        /// "Action needed" state and Fix sheet come from this.
        public var issue: HarnessIssue? = nil
        public var displayName: String?
        /// Its account name from the app's own names file, if set.
        public var accountName: String? = nil
        public var command: String
        public var cliInstalled: Bool?
        public var limit: RateLimit?

        /// The account name, else the display name, else the brand name.
        public var title: String {
            if let profile { return OtherLimitsModel.accountTitle(name: name, displayName: profile.name) }
            let set = (accountName ?? displayName)?.trimmingCharacters(in: .whitespaces) ?? ""
            return set.isEmpty ? UsageFormat.harness(name) : set
        }

        /// The harness's own name (display name, else brand), ignoring the
        /// account name: what "go back to the harness name" means.
        public var harnessTitle: String {
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
    /// Why there is nothing to show, when that is not simply "nothing
    /// starred", or why the cards shown are from an earlier load.
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
    private(set) var sweKnowsUsageFlags = true
    /// The freshness window swe actually honours: `cacheTTL` once it took
    /// --cache-ttl, else 0 (its own five minutes). Staleness checks use
    /// this, so an swe too old for the flag never has its outage
    /// fallback read as current.
    public var acceptedCacheTTL: TimeInterval { sweKnowsUsageFlags ? cacheTTL : 0 }
    /// Same for --exclude: an swe that old polls Claude and Codex too.
    private var sweKnowsExclude = true
    /// Set when swe lacks `harness usage`, so extra accounts are not asked
    /// again this session; their cards say to update swe instead.
    private(set) var sweKnowsAccountUsage = true
    /// Where the last successful list persists between launches.
    private let snapshotFile: URL
    /// The app's own account names, separate from harness.json.
    private let names: HarnessAccountNameStore
    /// The extra Devin/Cursor accounts from the profile store, each with
    /// its account directory, live so a removal stops being asked about.
    private let extraAccounts: @MainActor () -> [(Profile, ProfilePaths)]
    /// Each extra account's last reading by profile id, good or failed.
    private var extraReadings: [String: ExtraReading] = [:]
    /// Where poll outcomes become persisted issues: nil keeps the model
    /// usable standalone (tests, previews).
    private let issues: IssuesModel?

    /// One extra account's reading: the limit swe returned (nil = signed
    /// out), when, and whether the last attempt failed so a plain load
    /// refetches it. `issue` carries swe's typed verdict and its remedy
    /// when it answered with one.
    struct ExtraReading: Codable, Equatable, Sendable {
        var limit: RateLimit?
        var fetchedAt: Date
        var failed: Bool = false
        var issue: HarnessIssue? = nil
    }

    public init(swe: SweClient,
                snapshotFile: URL? = nil,
                names: HarnessAccountNameStore =
                    HarnessAccountNameStore(file: HarnessAccountNameStore.defaultFile),
                extraAccounts: @escaping @MainActor () -> [(Profile, ProfilePaths)] = { [] },
                issues: IssuesModel? = nil) {
        self.swe = swe
        self.issues = issues
        self.snapshotFile = snapshotFile ?? FileManager.default
            .homeDirectoryForCurrentUser
            .appending(path: ".quiver/cache/app-other-limits.json")
        self.names = names
        self.extraAccounts = extraAccounts
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
        /// Per extra profile id; absent in snapshots written before
        /// extra accounts existed.
        var extras: [String: ExtraReading] = [:]

        private enum CodingKeys: String, CodingKey {
            case savedAt, rows, cards, starred, cliInstalled, extras
        }

        init(savedAt: Date, rows: [Row], cards: [Card], starred: [String],
             cliInstalled: [String: Bool], extras: [String: ExtraReading]) {
            self.savedAt = savedAt
            self.rows = rows
            self.cards = cards
            self.starred = starred
            self.cliInstalled = cliInstalled
            self.extras = extras
        }

        init(from decoder: any Decoder) throws {
            let box = try decoder.container(keyedBy: CodingKeys.self)
            savedAt = try box.decode(Date.self, forKey: .savedAt)
            rows = try box.decode([Row].self, forKey: .rows)
            cards = try box.decode([Card].self, forKey: .cards)
            starred = try box.decode([String].self, forKey: .starred)
            cliInstalled = try box.decode([String: Bool].self, forKey: .cliInstalled)
            extras = try box.decodeIfPresent([String: ExtraReading].self, forKey: .extras) ?? [:]
        }
    }

    private func hydrate() {
        guard let data = try? Data(contentsOf: snapshotFile),
              let saved = try? JSONDecoder().decode(Snapshot.self, from: data)
        else { return }
        let names = names.load()
        rows = saved.rows.map { row in
            var row = row
            row.accountName = names[row.name]
            return row
        }
        cards = saved.cards.map { card in
            var card = card
            card.accountName = names[card.name]
            return card
        }
        starred = Set(saved.starred)
        cliInstalled = saved.cliInstalled
        extraReadings = saved.extras
    }

    /// Rename a harness's account card. The name lives in the app's own
    /// file — a rename in Accounts must never write harness.json, where it
    /// would double as the harness's display name on the Harnesses page.
    /// An empty name clears it. Updates rows, cards and the snapshot.
    public func rename(_ harness: String, to name: String) throws {
        try names.setName(name, for: harness)
        let stored = names.load()[harness]
        for index in rows.indices where rows[index].name == harness {
            rows[index].accountName = stored
        }
        for index in cards.indices where cards[index].name == harness {
            cards[index].accountName = stored
        }
        persist()
    }

    private func persist() {
        let snapshot = Snapshot(savedAt: .now, rows: rows, cards: cards,
                                starred: starred.map { Array($0) } ?? [],
                                cliInstalled: cliInstalled, extras: extraReadings)
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
    /// `force` refetches every extra account regardless of freshness.
    public func load(force: Bool = false, refreshFailed: Bool = false) async {
        request += 1
        let mine = request
        // Extra accounts are polled with `swe harness usage` against the
        // account's own files, never the shared cache. The fetch waits for
        // the list call so the starred set is current: a vendor just
        // starred gets its extras read, one just unstarred is skipped.
        let extras = extraAccounts().filter { !$0.0.isDefault && !$0.0.vendor.hasDefaultProfile }
        // A removed profile's reading is never asked for again — and
        // its open issues close now, whatever swe answers below, since a
        // deleted account can't fail again.
        let live = Set(extras.map { $0.0.id })
        extraReadings = extraReadings.filter { live.contains($0.key) }
        var liveKeys: [String: Set<String>] = [:]
        for (profile, _) in extras {
            liveKeys[profile.vendor.rawValue, default: []].insert(Self.accountKey(for: profile))
        }
        await issues?.record([], accounts: liveKeys)
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
            let accountNames = names.load()
            // Base rows stay local until the extras are merged below:
            // publishing harness-only lists mid-load would flash every
            // extra account off the page for the length of its fetch.
            let baseRows = harnesses.compactMap { harness -> Row? in
                // Starred only: swe polls just those, but can still return a
                // cached reading for one since unstarred.
                guard !Self.ownAccounts.contains(harness.name), harness.state == .starred,
                      let limit = harness.rateLimit else { return nil }
                return Row(name: harness.name, limit: limit, displayName: harness.displayName,
                           accountName: accountNames[harness.name], command: harness.command,
                           cliInstalled: harness.installed)
            }
            starred = Set(harnesses.lazy.filter { $0.state == .starred }.map(\.name))
            // Every outcome becomes a persisted issue (or resolves one):
            // swe names the failure itself now; an older swe's coarse
            // reason or sentinel reading maps onto the same statuses.
            var polls = harnesses
                .filter { !Self.ownAccounts.contains($0.name) && $0.state == .starred }
                .compactMap(Self.poll)
            var cardList = harnesses
                .filter { !Self.ownAccounts.contains($0.name) && $0.state == .starred }
                .map { harness in
                    Card(name: harness.name, usageNote: UsageNote(harness.noReading),
                         issue: harness.issue,
                         displayName: harness.displayName,
                         accountName: accountNames[harness.name], command: harness.command,
                         cliInstalled: harness.installed, limit: harness.rateLimit)
                }
            var rowList = baseRows
            let installed = harnesses.reduce(into: [String: Bool]()) { map, harness in
                if let value = harness.installed { map[harness.name] = value }
            }
            // An extra account's card sits right after its harness's own;
            // one for an unstarred vendor is hidden with the harness.
            let readings = await fetchExtras(
                extras, force: force, refreshFailed: refreshFailed)
            guard mine == request else { return }
            extraReadings.merge(readings) { _, new in new }
            for (profile, _) in extras {
                let vendor = profile.vendor.rawValue
                guard starred?.contains(vendor) == true else { continue }
                let reading = extraReadings[profile.id]
                let note: UsageNote? = !sweKnowsAccountUsage ? .updateSwe
                    : reading == nil || reading?.limit != nil ? nil
                    : reading?.failed == true ? .fetchFailed : .signIn
                let card = Card(name: vendor, profile: profile, usageNote: note,
                                issue: reading?.issue,
                                displayName: nil, command: profile.vendor.cliCommand,
                                cliInstalled: installed[vendor], limit: reading?.limit)
                if let poll = Self.poll(profile, reading) { polls.append(poll) }
                if let own = cardList.firstIndex(where: { $0.profile == nil && $0.name == vendor }) {
                    cardList.insert(card, at: own + 1)
                } else {
                    cardList.append(card)
                }
                if let limit = reading?.limit {
                    let row = Row(name: vendor, profile: profile, limit: limit,
                                  command: profile.vendor.cliCommand,
                                  cliInstalled: installed[vendor])
                    if let own = rowList.firstIndex(where: { $0.profile == nil && $0.name == vendor }) {
                        rowList.insert(row, at: own + 1)
                    } else {
                        rowList.append(row)
                    }
                }
            }
            cards = cardList
            rows = rowList
            cliInstalled = installed
            note = nil
            persist()
            // One store pass for the whole poll, off the main actor. The
            // starred set, known only once swe answered, closes the issues
            // of a harness that is no longer polled.
            await issues?.record(polls, starred: starred ?? [], accounts: liveKeys)
        } catch is CancellationError {
            return
        } catch {
            guard mine == request else { return }
            // One failed poll (a swe timeout, a busy disk) keeps the last
            // cards up, as the starred set already is; the note says the
            // refresh failed. With nothing shown yet it is the only text.
            // An account removed since is gone either way: it has no
            // reading to keep.
            cards.removeAll { $0.profile.map { !live.contains($0.id) } ?? false }
            rows.removeAll { $0.profile.map { !live.contains($0.id) } ?? false }
            note = cards.isEmpty ? error.localizedDescription
                : "Couldn't refresh: \(error.localizedDescription)"
        }
    }

    /// The directory `swe harness usage` reads for this account: Devin's
    /// credentials under XDG_DATA_HOME, Cursor's sign-in db under the
    /// isolated `--user-data-dir`.
    static func accountDir(for profile: Profile, paths: ProfilePaths) -> String? {
        switch profile.vendor {
        case .devin: paths.home.path
        case .cursor: paths.desktopData?.path
        case .claude, .codex: nil
        }
    }

    /// The profile's stable issue slot: `cursor-work` stays its key when
    /// the account is renamed, so a rename never re-toasts the failure.
    static func accountKey(for profile: Profile) -> String {
        let prefix = "\(profile.vendor.rawValue)-"
        return profile.id.hasPrefix(prefix) ? String(profile.id.dropFirst(prefix.count)) : profile.id
    }

    /// A harness's poll verdict into the issue store: swe's `issue` when
    /// it sent one, else the same verdict derived from what it sent —
    /// sentinel plan statuses are statuses, not plans, and no_reading's
    /// coarse pair maps onto the same vocabulary.
    static func poll(_ harness: Harness) -> IssuesModel.Poll? {
        if let issue = harness.issue {
            return .init(harness: harness.name, status: issue.status, remedy: issue.remedy)
        }
        let status: IssueStatus
        switch harness.rateLimit?.status {
        case .authRequired?: status = .authRequired
        case .timeout?: status = .timeout
        case .ok?, .noSubscription?: status = .ok
        case nil:
            switch harness.noReading {
            case .signedOut?: status = .signedOut
            case .error?: status = .unavailable
            case nil: return nil  // Can't tell; the stored issue stands.
            }
        }
        return .init(harness: harness.name, status: status)
    }

    /// An extra account's poll verdict for the issue store, under its
    /// own slot so a failure on one login never reads as another's.
    static func poll(_ profile: Profile, _ reading: ExtraReading?) -> IssuesModel.Poll? {
        guard let reading else { return nil }
        let vendor = profile.vendor.rawValue
        let key = accountKey(for: profile)
        let status: IssueStatus
        var remedy: UsageRemedy?
        if let issue = reading.issue {
            status = issue.status
            remedy = issue.remedy
        } else if let limit = reading.limit {
            let read: IssueStatus = switch limit.status {
            case .authRequired: .authRequired
            case .timeout: .timeout
            case .ok, .noSubscription: .ok
            }
            status = reading.failed && read == .ok ? .unavailable : read
        } else {
            status = reading.failed ? .unavailable : .signedOut
        }
        return .init(harness: vendor, status: status, remedy: remedy,
                     account: profile.name, accountKey: key)
    }

    /// Whether a fetch is owed: nothing read yet, the reading is older
    /// than swe's freshness window, the last attempt failed, or this load
    /// is forced. A Refresh-press load refetches only the failures.
    private func needsFetch(_ reading: ExtraReading?, force: Bool, refreshFailed: Bool) -> Bool {
        if force { return true }
        guard let reading else { return true }
        // Inside the refresh window a Refresh-press re-checks only the
        // failures; anything older than the window is re-read anyway.
        return reading.failed || Date().timeIntervalSince(reading.fetchedAt) > acceptedCacheTTL
    }

    /// `swe harness usage` for each extra account that needs it, all at
    /// once. An swe too old for the subcommand is asked once, remembered,
    /// and every extra's card then carries the update hint.
    private func fetchExtras(_ extras: [(Profile, ProfilePaths)],
                             force: Bool, refreshFailed: Bool) async -> [String: ExtraReading] {
        guard sweKnowsAccountUsage else { return [:] }
        var results: [String: ExtraReading] = [:]
        await withTaskGroup(of: (String, ExtraReading?).self) { group in
            for (profile, paths) in extras {
                // Only extras of a starred vendor are polled, on the set
                // this load just learned — a just-starred vendor's extras
                // are fetched, a just-unstarred one's are not.
                guard let starredNow = starred,
                      starredNow.contains(profile.vendor.rawValue) else { continue }
                let reading = extraReadings[profile.id]
                guard let dir = Self.accountDir(for: profile, paths: paths),
                      needsFetch(reading, force: force, refreshFailed: refreshFailed) else { continue }
                let vendor = profile.vendor.rawValue
                group.addTask { [swe] in
                    do {
                        let outcome = try await swe.accountOutcome(name: vendor, accountDir: dir)
                        var limit = outcome.limit
                        // An older swe may not stamp the read; the row's
                        // age is the fetch time, or it reads as stale.
                        if limit?.fetchedAt == nil { limit?.fetchedAt = .now }
                        // A reply that reports a failure — a timed-out or
                        // re-login reading, or a window the provider left
                        // unread — counts as failed so Refresh retries it.
                        // A named status that isn't ok says the same.
                        let failed = limit.map {
                            $0.status == .timeout || $0.status == .authRequired
                                || $0.allWindows.contains { $0.unavailable }
                        } ?? (outcome.status != nil
                              && outcome.status != .ok && outcome.status != .signedOut)
                        // The issue is nil from a swe that predates the
                        // status field: its nil-limit reply already reads
                        // as signed out downstream.
                        let issue = outcome.status.map {
                            HarnessIssue(status: $0, remedy: outcome.remedy)
                        }
                        return (profile.id, ExtraReading(limit: limit, fetchedAt: .now,
                                                         failed: failed, issue: issue))
                    } catch SweError.commandFailed(_, let message)
                        // "Unknown harness subcommand"/"Unknown argument"
                        // specifically: an AccountUsageError's own message
                        // must not retire the feature for the session.
                        where message.localizedCaseInsensitiveContains("unknown harness subcommand")
                            || message.localizedCaseInsensitiveContains("unknown argument") {
                        return (profile.id, nil)
                    } catch {
                        // Keep the last good reading if there was one, marked
                        // failed and published as unavailable so LimitReading
                        // and the cards treat it like swe's outage fallback.
                        return (profile.id, ExtraReading(limit: reading?.limit?.markedUnavailable(),
                                                         fetchedAt: reading?.fetchedAt ?? .now,
                                                         failed: true))
                    }
                }
            }
            for await (id, reading) in group {
                if let reading {
                    results[id] = reading
                } else {
                    // "unknown subcommand"/"unknown argument": this swe
                    // cannot answer for extra accounts this session.
                    sweKnowsAccountUsage = false
                }
            }
        }
        return results
    }
}

extension SweClient {
    /// The verdict `swe harness usage --account-dir --json` reports: a
    /// status in the remedies vocabulary, the fix it names and the limit
    /// when there is one. Failures exit 1 with the same document, so the
    /// call reads it on any exit status.
    public struct AccountOutcome: Sendable, Equatable {
        /// The status swe named; nil from a swe that predates the field
        /// (derived from the limit by the caller).
        public let status: IssueStatus?
        public let remedy: UsageRemedy?
        public let limit: RateLimit?
    }

    /// `swe harness usage <name> --account-dir=<dir> --json`: the account's
    /// own rate limit (nil when it is not signed in) as a typed outcome.
    /// `allowFailure` because a failed read still carries the document it
    /// describes.
    public func accountOutcome(name: String, accountDir: String) async throws -> AccountOutcome {
        struct Reply: Decodable {
            var name: String
            var accountDir: String
            var status: IssueStatus?
            var remedy: UsageRemedy?
            var limit: RateLimit?
        }
        let reply: Reply = try await call(
            ["harness", "usage", name, "--account-dir=\(accountDir)", "--json"],
            allowFailure: true)
        return AccountOutcome(status: reply.status, remedy: reply.remedy,
                              limit: reply.limit)
    }
}
