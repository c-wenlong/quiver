import Foundation
import Observation

/// What the new-session sheet offers: machines that can launch, their
/// recent folders, and the harnesses each accepts. Pure, so tests can
/// feed it hub rows.
enum NewSessionOptions {
    /// What the machine says it can start, in Provider visibility order.
    /// A machine row older than `launchable` falls back to every harness
    /// it detected; the hub still refuses any it can't start.
    static func harnesses(on machine: Machine) -> [String] {
        machine.launchable ?? machine.harnesses ?? []
    }

    /// What "No machine can start a session" says, from what the hub
    /// reported. A machine row with `launchable` answered: nothing is
    /// installed there or every harness is switched off, so updating swe
    /// would change nothing. Only rows without the field (a hub older
    /// than the new-session route), or none at all, mean swe is too old.
    static func noMachineMessage(_ machines: [Machine]) -> String {
        guard machines.contains(where: { $0.launchable != nil }) else {
            return "Update swe on the hub. A machine offers the harnesses swe lists there, minus the ones switched off in Quiver's Provider visibility."
        }
        return noHarnessMessage(machineCount: machines.count)
    }

    /// A machine that reports `launchable` but lists none: the Mac and
    /// web launch pickers' wording, with "Quiver's" because a phone has
    /// a Settings of its own.
    static func noHarnessMessage(machineCount: Int) -> String {
        machineCount > 1
            ? "No harness can start on any machine: install one, or turn it on in Quiver's Settings › Providers."
            : "No harness can start on the hub: install one there, or turn it on in Quiver's Settings › Providers."
    }

    /// Online machines first, then by name; a machine that can launch
    /// nothing is left out.
    static func machines(_ machines: [Machine]) -> [Machine] {
        machines
            .filter { !harnesses(on: $0).isEmpty }
            .sorted { a, b in
                if (a.online ?? false) != (b.online ?? false) { return a.online ?? false }
                return a.displayName.localizedCaseInsensitiveCompare(b.displayName) == .orderedAscending
            }
    }

    /// Every folder a session ran in on `machineId`, most recent first.
    static func folders(on machineId: String, sessions: [SessionRow]) -> [String] {
        var seen = Set<String>()
        return newestFirst(sessions).compactMap { row in
            guard row.machineId == machineId, let cwd = row.cwd, cwd.hasPrefix("/"),
                  seen.insert(cwd).inserted else { return nil }
            return cwd
        }
    }

    /// Folders whose path holds every word of `query`, any case.
    static func matching(_ folders: [String], query: String) -> [String] {
        let words = query.lowercased().split(whereSeparator: \.isWhitespace)
        guard !words.isEmpty else { return folders }
        return folders.filter { path in
            let p = path.lowercased()
            return words.allSatisfy { p.contains($0) }
        }
    }

    /// The harness last used in `folder`, when it is on offer.
    static func defaultHarness(folder: String?, machineId: String, offered: [String], sessions: [SessionRow]) -> String? {
        let last = newestFirst(sessions).first { $0.machineId == machineId && $0.cwd == folder }?.harness
        if let last, offered.contains(last) { return last }
        return offered.first
    }

    /// Whether a sheet's Model row shows at all: only a machine row
    /// carrying `model_at_launch` can answer for its harnesses. An older
    /// satellite's row lacks it and would refuse any pick, so the row
    /// stays hidden rather than offer what can't work.
    static func showsModelField(on machine: Machine) -> Bool {
        machine.modelAtLaunch != nil
    }

    /// Whether `harness` honours a `model` pick at launch on `machine`.
    static func acceptsModel(_ harness: String?, on machine: Machine) -> Bool {
        guard let harness else { return false }
        return machine.modelAtLaunch?.contains(harness) == true
    }

    /// Whether a sheet's Autonomy row shows at all: only a machine row
    /// carrying `yolo_at_launch` can answer for its harnesses. An older
    /// satellite's row lacks it and would silently run the usual checks,
    /// so the row stays hidden rather than offer what can't apply.
    static func showsYoloField(on machine: Machine) -> Bool {
        machine.yoloAtLaunch != nil
    }

    /// Whether `harness` honours `yolo` at launch on `machine` — the
    /// checkbox's enabled state.
    static func acceptsYolo(_ harness: String?, on machine: Machine) -> Bool {
        guard let harness else { return false }
        return machine.yoloAtLaunch?.contains(harness) == true
    }

    /// The provider slugs `harness` can launch with on `machine`, in the
    /// hub's order. Empty on an older satellite's row (no
    /// `providers_at_launch`) or a harness with no provider key set.
    static func providers(_ harness: String?, on machine: Machine) -> [String] {
        guard let harness else { return [] }
        return machine.providersAtLaunch?[harness] ?? []
    }

    /// Whether a sheet's Provider row shows: only when the machine row
    /// carries `providers_at_launch` and lists something for `harness`.
    /// Unlike Autonomy there is nothing to show disabled, since the only
    /// choice left would be the harness default.
    static func showsProviderField(_ harness: String?, on machine: Machine) -> Bool {
        !providers(harness, on: machine).isEmpty
    }

    /// A provider slug's label from `provider_names`, else the slug.
    static func providerName(_ slug: String, on machine: Machine) -> String {
        if let name = machine.providerNames?[slug], !name.isEmpty { return name }
        return slug
    }

    /// What the launch posts as `provider`: the pick when `harness` on
    /// `machine` still offers it, else nil (the harness default).
    static func providerPick(_ pick: String?, for harness: String?, on machine: Machine) -> String? {
        guard let pick, !pick.isEmpty, providers(harness, on: machine).contains(pick) else { return nil }
        return pick
    }

    /// The model `machine` last ran `harness` with.
    static func lastModel(_ harness: String?, on machine: Machine) -> String? {
        guard let harness, let model = machine.lastModels?[harness], !model.isEmpty else { return nil }
        return model
    }

    /// The Model menu's recent entries for `harness`, newest first: the
    /// row's `recent_models`, or on a hub older than that field a
    /// one-item list from `last_models`. Blanks and repeats are dropped.
    static func recentModels(_ harness: String?, on machine: Machine) -> [String] {
        guard let harness else { return [] }
        let raw = machine.recentModels.map { $0[harness] ?? [] }
            ?? lastModel(harness, on: machine).map { [$0] } ?? []
        var seen = Set<String>()
        return raw.filter { !$0.isEmpty && seen.insert($0).inserted }
    }

    /// The Model menu's entries in order: Harness default, the recent
    /// models newest first, then Other.
    static func modelChoices(_ harness: String?, on machine: Machine) -> [ModelChoice] {
        [.harnessDefault] + recentModels(harness, on: machine).map(ModelChoice.recent) + [.other]
    }

    /// An untouched menu's selection: the newest recent model, else the
    /// harness's own default.
    static func defaultModelChoice(_ harness: String?, on machine: Machine) -> ModelChoice {
        recentModels(harness, on: machine).first.map(ModelChoice.recent) ?? .harnessDefault
    }

    /// What the launch posts as `model`: the trimmed pick, or nil when
    /// the field is empty or the harness can't honour one — on the wire
    /// an absent key already means "no pick".
    static func modelPick(_ text: String, for harness: String?, on machine: Machine) -> String? {
        guard acceptsModel(harness, on: machine) else { return nil }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// The machine's name for it, else what its sessions call it, with a
    /// capital first letter.
    static func name(_ harness: String, on machine: Machine?, sessions: [SessionRow]) -> String {
        let raw = machine?.harnessNames?[harness] ?? sessions.first { $0.harness == harness }?.agent ?? harness
        return raw.prefix(1).uppercased() + raw.dropFirst()
    }

    private static func newestFirst(_ rows: [SessionRow]) -> [SessionRow] {
        rows.sorted { ($0.lastActiveAt ?? 0) > ($1.lastActiveAt ?? 0) }
    }
}

/// One entry in a launch sheet's Model menu.
enum ModelChoice: Hashable {
    /// Sends no `model`: the harness starts on its own default.
    case harnessDefault
    /// One of the machine's recent models for the harness.
    case recent(String)
    /// A typed model id, from the field the menu shows under it.
    case other

    var label: String {
        switch self {
        case .harnessDefault: "Harness default"
        case .recent(let model): model
        case .other: "Other…"
        }
    }
}

/// A launch sheet's Model menu: the choice, the Other field's text, and
/// whether the user picked anything. Pure, so tests can drive it.
struct ModelSelection: Equatable {
    private(set) var choice: ModelChoice = .harnessDefault
    var otherText = ""
    /// Set by a pick from the menu; an untouched menu follows the
    /// harness's newest model.
    private(set) var touched = false

    mutating func choose(_ choice: ModelChoice) {
        self.choice = choice
        touched = true
    }

    /// Run on every harness or machine change.
    mutating func follow(_ harness: String?, on machine: Machine) {
        // A pick survives a switch (Harness default and Other always, a recent model only while the new harness lists it); anything else re-defaults.
        if touched {
            switch choice {
            case .harnessDefault, .other: return
            case .recent(let model):
                if NewSessionOptions.recentModels(harness, on: machine).contains(model) { return }
            }
        }
        choice = NewSessionOptions.defaultModelChoice(harness, on: machine)
        touched = false
    }

    /// The menu's entries for the sheet. A machine-row refresh can push
    /// the selected recent model out of the five; it stays listed, just
    /// before Other, so the menu still shows it checked.
    func choices(for harness: String?, on machine: Machine) -> [ModelChoice] {
        var all = NewSessionOptions.modelChoices(harness, on: machine)
        if case .recent = choice, !all.contains(choice) { all.insert(choice, at: all.count - 1) }
        return all
    }

    /// What the launch posts as `model`: nil for Harness default, an
    /// empty Other field, or a harness that can't take a pick.
    func model(for harness: String?, on machine: Machine) -> String? {
        switch choice {
        case .harnessDefault: nil
        case .recent(let model): NewSessionOptions.modelPick(model, for: harness, on: machine)
        case .other: NewSessionOptions.modelPick(otherText, for: harness, on: machine)
        }
    }
}

/// One launch from the sheet: post it, then poll the hub until it says
/// which session the launch became.
@MainActor @Observable
final class LaunchFlow {
    enum Phase: Equatable {
        case idle, starting, waiting
        case failed(String)
        /// Gave up waiting, but nothing says it failed: a slow start, or
        /// a satellite that is asleep.
        case unconfirmed(String)
        case started(String)
    }

    private(set) var phase: Phase = .idle

    /// The launch id the hub accepted, set before the polling starts:
    /// a caller that needs the launch to outlive the sheet (the task
    /// linker's delayed adoption) reads it even when `run` never
    /// answers a session key.
    private(set) var launchId: String?

    /// Starting, waiting, or started and about to open the chat: Start
    /// stays off the whole time, so one tap can't launch twice.
    var busy: Bool {
        switch phase {
        case .starting, .waiting, .started: true
        default: false
        }
    }

    /// Returns the new session's key, or nil with `phase` saying why.
    /// `attempts` polls, `interval` apart: a cold Claude start takes a
    /// few seconds before its transcript exists.
    func run(post: () async throws -> LaunchStatus,
             fetch: (String) async throws -> LaunchStatus,
             attempts: Int = 90,
             interval: Duration = .seconds(1)) async -> String? {
        guard !busy else { return nil }
        phase = .starting
        launchId = nil
        let accepted: LaunchStatus
        do {
            accepted = try await post()
        } catch {
            phase = .failed(error.localizedDescription)
            return nil
        }
        launchId = accepted.launchId
        if Task.isCancelled { phase = .idle; return nil }
        phase = .waiting
        var status = accepted
        var polls = 0
        while true {
            if let key = status.key, status.state == "started" {
                phase = .started(key)
                return key
            }
            if status.state == "untracked" {
                phase = .unconfirmed("Started. Quiver can't read this harness's sessions yet, so there's no chat to open. It keeps running under tmux on the machine.")
                return nil
            }
            if status.state == "failed" {
                phase = .failed(status.note.map { "The session didn't start: \($0)" } ?? "The session didn't start.")
                return nil
            }
            if polls == attempts { break }
            polls += 1
            try? await Task.sleep(for: interval)
            if Task.isCancelled { phase = .idle; return nil }
            do {
                status = try await fetch(accepted.launchId)
            } catch let error as HubError where error.status == 404 {
                phase = .failed(error.message)
                return nil
            } catch {
                // A dropped poll is retried until the attempts run out.
            }
        }
        phase = .unconfirmed(accepted.queued == true
            ? "Queued. It starts when that machine is online, then shows up in Sessions."
            : "It started but hasn't shown up yet. It'll appear in Sessions when it does.")
        return nil
    }

    /// Started: the sheet is refreshing the list before it opens the chat.
    var opening: Bool { if case .started = phase { true } else { false } }

    func reset() { phase = .idle }
}
