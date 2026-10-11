import Foundation
import Observation

/// What `swe harness star|archive --json` reports back.
public struct HarnessStateChange: Decodable, Sendable, Equatable {
    public let name: String
    public let state: Harness.State
}

extension SweClient {
    /// Every registered harness, archived ones included.
    public func allHarnesses() async throws -> [Harness] {
        let data: HarnessList = try await call(["list", "--scope=all", "--json"])
        return data.harnesses
    }

    /// swe's star is a toggle; the reply says where it landed.
    @discardableResult
    public func toggleStar(_ name: String) async throws -> HarnessStateChange {
        try await call(["harness", "star", name, "--json"])
    }

    /// Archives with a reason. swe records the date and guesses the usage.
    @discardableResult
    public func archive(_ name: String, reason: String) async throws -> HarnessStateChange {
        let reason = reason.trimmingCharacters(in: .whitespacesAndNewlines)
        // Without a reason, `swe harness archive <name>` restores instead.
        guard !reason.isEmpty else { throw SweError.commandFailed(status: 1, message: "Say why you're archiving it.") }
        return try await call(["harness", "archive", name, reason, "--json"])
    }

    /// Brings an archived harness back into `swe list`.
    @discardableResult
    public func restore(_ name: String) async throws -> HarnessStateChange {
        try await call(["harness", "archive", name, "--json"])
    }
}

/// What `swe edit <name> --json` reports back.
public struct HarnessEditResult: Decodable, Sendable, Equatable {
    public let name: String
    public let changes: [String]
    public let harness: Harness
}

extension SweClient {
    /// Applies registry edits, given as `--field=value` flags.
    @discardableResult
    public func editHarness(_ name: String, flags: [String]) async throws -> HarnessEditResult {
        try await call(["edit", name] + flags + ["--json"])
    }
}

/// The edit sheet's draft of a harness's registry fields. Lists are kept as
/// the comma-separated text the user types.
public struct HarnessEdit: Equatable, Sendable {
    public var command: String
    public var description: String
    public var version: String
    public var aliases: String
    public var tags: String
    public var notes: String

    public init(_ harness: Harness) {
        command = harness.command
        description = harness.description
        version = harness.version ?? ""
        aliases = harness.aliases.joined(separator: ", ")
        tags = harness.tags.joined(separator: ", ")
        notes = harness.notes ?? ""
    }

    public var commandProblem: String? {
        command.trimmingCharacters(in: .whitespaces).isEmpty ? "Enter the command that runs it." : nil
    }

    /// `--field=value` for each field that differs from `original`, in a
    /// fixed order. An empty value clears the field, as `swe edit` does.
    public func flags(from original: HarnessEdit) -> [String] {
        let fields: [(String, KeyPath<HarnessEdit, String>, Bool)] = [
            ("command", \.command, false), ("description", \.description, false),
            ("version", \.version, false), ("aliases", \.aliases, true),
            ("tags", \.tags, true), ("notes", \.notes, false),
        ]
        return fields.compactMap { name, path, isList in
            let new = Self.normalised(self[keyPath: path], isList: isList)
            let old = Self.normalised(original[keyPath: path], isList: isList)
            return new == old ? nil : "--\(name)=\(new)"
        }
    }

    private static func normalised(_ text: String, isList: Bool) -> String {
        guard isList else { return text.trimmingCharacters(in: .whitespacesAndNewlines) }
        var seen = Set<String>()
        return text.split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && seen.insert($0).inserted }
            .joined(separator: ",")
    }
}

/// The Harnesses page: every AI coding CLI swe knows about.
@MainActor
@Observable
public final class HarnessesModel {
    public enum Scope: Hashable, Sendable { case inUse, archived }

    public private(set) var state: Loadable<[Harness]> = .idle
    /// Harnesses with a change in flight, by name.
    public private(set) var busy: Set<String> = []
    public var actionError: String?
    public var scope: Scope = .inUse
    public var query = ""
    /// Unstarred harnesses in the In use list stay folded until expanded.
    public var showsUnstarred = false

    private let swe: SweClient
    private let gate = SerialGate()
    private var generation = 0

    public init(swe: SweClient) {
        self.swe = swe
    }

    public func load() async {
        generation += 1
        let mine = generation
        if case .loaded = state {} else { state = .loading }
        let next: Loadable<[Harness]>
        do {
            next = .loaded(try await swe.allHarnesses())
        } catch is CancellationError {
            if mine == generation, state == .loading { state = .idle }
            return
        } catch {
            next = .failed(error.localizedDescription)
        }
        if mine == generation { state = next }
    }

    public var all: [Harness] {
        guard case .loaded(let harnesses) = state else { return [] }
        return harnesses
    }

    public func count(_ scope: Scope) -> Int {
        all.filter { ($0.state == .archived) == (scope == .archived) }.count
    }

    /// The current tab: starred first, then the busiest, then by name.
    public var visible: [Harness] {
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        return all
            .filter { ($0.state == .archived) == (scope == .archived) }
            .filter { harness in
                needle.isEmpty
                    || harness.name.lowercased().contains(needle)
                    || harness.description.lowercased().contains(needle)
                    || harness.tags.contains { $0.lowercased().contains(needle) }
            }
            .sorted { a, b in
                if (a.state == .starred) != (b.state == .starred) { return a.state == .starred }
                if (a.sessions100d ?? 0) != (b.sessions100d ?? 0) { return (a.sessions100d ?? 0) > (b.sessions100d ?? 0) }
                return a.name < b.name
            }
    }

    /// Whether the In use list has unstarred rows to fold or unfold. A
    /// search shows every match, and the archived list is never folded.
    public var canCollapse: Bool {
        scope == .inUse
            && query.trimmingCharacters(in: .whitespaces).isEmpty
            && visible.contains { $0.state != .starred }
    }

    /// The rows on screen: `visible`, minus the unstarred ones while folded.
    public var shown: [Harness] {
        guard canCollapse, !showsUnstarred else { return visible }
        return visible.filter { $0.state == .starred }
    }

    /// How many rows the fold is hiding.
    public var collapsedCount: Int { visible.count - shown.count }

    public func toggleStar(_ harness: Harness) async {
        await change(harness.name) { try await $0.toggleStar(harness.name) }
    }

    public func archive(_ harness: Harness, reason: String) async {
        await change(harness.name) { try await $0.archive(harness.name, reason: reason) }
    }

    public func restore(_ harness: Harness) async {
        guard harness.state == .archived else { return }
        await change(harness.name) { try await $0.restore(harness.name) }
    }

    /// Saves an edit through `swe edit`, then reloads. Returns what went
    /// wrong, for the sheet to show, or nil when it saved or had nothing to do.
    public func save(_ harness: Harness, _ edit: HarnessEdit) async -> String? {
        let flags = edit.flags(from: HarnessEdit(harness))
        guard !flags.isEmpty else { return nil }
        if let problem = edit.commandProblem { return problem }
        guard !busy.contains(harness.name) else { return "\(harness.name) is still saving." }
        busy.insert(harness.name)
        defer { busy.remove(harness.name) }
        await gate.acquire()
        defer { gate.release() }
        do {
            try await swe.editHarness(harness.name, flags: flags)
        } catch is CancellationError {
            return nil
        } catch {
            return error.localizedDescription
        }
        await load()
        return nil
    }

    private func change(_ name: String, _ work: (SweClient) async throws -> HarnessStateChange) async {
        guard !busy.contains(name) else { return }
        busy.insert(name)
        defer { busy.remove(name) }
        // Each swe run reads harness.json, changes one row and writes it
        // back, so two at once can lose one change. Run them in turn.
        await gate.acquire()
        defer { gate.release() }
        actionError = nil
        do {
            _ = try await work(swe)
        } catch is CancellationError {
            return
        } catch {
            actionError = error.localizedDescription
            return
        }
        await load()
    }
}

/// Runs async work one piece at a time, in the order it arrived.
@MainActor
final class SerialGate {
    private var running = false
    private var waiting: [CheckedContinuation<Void, Never>] = []

    func acquire() async {
        guard running else {
            running = true
            return
        }
        await withCheckedContinuation { waiting.append($0) }
    }

    func release() {
        if waiting.isEmpty {
            running = false
        } else {
            waiting.removeFirst().resume()
        }
    }
}
