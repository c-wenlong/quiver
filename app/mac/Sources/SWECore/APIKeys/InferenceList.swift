import Foundation
import Observation

/// One row of Settings › Providers › Inference: a key in the keys file, a
/// plan whose key is there, or a plan with no key yet. The id is the
/// variable name, so a plan keeps its place when its key is added.
public struct InferenceItem: Identifiable, Equatable, Sendable {
    public enum Kind: String, CaseIterable, Sendable {
        case plan, apiKey
    }

    public var id: String
    public var plan: InferencePlan?
    /// The key as the keys file has it; nil when a plan has none yet.
    public var key: APIKeysModel.Row?

    public var kind: Kind { plan == nil ? .apiKey : .plan }
    /// A plan's own name; a key's heading in the file.
    public var title: String { plan?.name ?? key?.providerLabel ?? id }
    public var hasKey: Bool { !(key?.value.isEmpty ?? true) }

    public init(id: String, plan: InferencePlan?, key: APIKeysModel.Row?) {
        self.id = id
        self.plan = plan
        self.key = key
    }
}

/// What a row's key is known to be, for the status filter.
public enum InferenceKeyState: String, CaseIterable, Sendable {
    case valid, invalid, unchecked, noKey

    /// A provider that accepted the key, even with no credit left, has a
    /// valid key; one that refused it has an invalid one; anything else
    /// hasn't said.
    public init(hasKey: Bool, check: APIKeyCheckResult?) {
        guard hasKey else { self = .noKey; return }
        switch check {
        case .works?, .noCredit?: self = .valid
        case .rejected?: self = .invalid
        case .couldNotCheck?, .noCheck?, nil: self = .unchecked
        }
    }
}

public enum InferenceList {
    /// Every key in file order, a plan's key taking its plan, then the
    /// plans with no key in catalog order.
    public static func items(keys: [APIKeysModel.Row],
                             plans: [InferencePlan] = InferenceCatalog.all) -> [InferenceItem] {
        let byKey = Dictionary(plans.map { ($0.keyName, $0) }, uniquingKeysWith: { first, _ in first })
        let present = keys.map { InferenceItem(id: $0.name, plan: byKey[$0.name], key: $0) }
        let named = Set(keys.map(\.name))
        let missing = plans.filter { !named.contains($0.keyName) }
            .map { InferenceItem(id: $0.keyName, plan: $0, key: nil) }
        return present + missing
    }

    /// Whether a row passes the filters. The query matches the title, the
    /// variable name, a plan's vendor and the heading the key is filed under.
    public static func matches(_ item: InferenceItem, state: InferenceKeyState?,
                               kind: InferenceItem.Kind?, query: String,
                               stateOf: (InferenceItem) -> InferenceKeyState) -> Bool {
        if let kind, item.kind != kind { return false }
        if let state, stateOf(item) != state { return false }
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !needle.isEmpty else { return true }
        return [item.title, item.id, item.plan?.vendor ?? "", item.key?.providerLabel ?? ""]
            .contains { $0.lowercased().contains(needle) }
    }
}

/// The order you dragged the Inference rows into. What is kept is the list
/// of ids; a row it doesn't name yet (a new key) goes at the end, in the
/// list's own order, and an id that no longer has a row is skipped.
@MainActor
@Observable
public final class InferenceOrder {
    static let key = "inferenceOrder"

    private let store: KeyValueStoring
    private var saved: [String]

    public init(store: KeyValueStoring) {
        self.store = store
        saved = store.strings(forKey: Self.key) ?? []
    }

    public func sorted(_ items: [InferenceItem]) -> [InferenceItem] {
        let rank = Dictionary(saved.enumerated().map { ($1, $0) }, uniquingKeysWith: { first, _ in first })
        let known = items.filter { rank[$0.id] != nil }.sorted { rank[$0.id]! < rank[$1.id]! }
        return known + items.filter { rank[$0.id] == nil }
    }

    /// Moves `id` to sit just before `target`, or to the end for nil,
    /// among every row (filtered out or not).
    public func move(_ id: String, before target: String?, in items: [InferenceItem]) {
        var order = sorted(items).map(\.id)
        guard id != target, let from = order.firstIndex(of: id) else { return }
        order.remove(at: from)
        if let target, let to = order.firstIndex(of: target) {
            order.insert(id, at: to)
        } else {
            order.append(id)
        }
        // Keep ids of rows that are gone for now (a key removed and added
        // back finds its place), after the live ones.
        saved = order + saved.filter { !order.contains($0) }
        store.set(saved, forKey: Self.key)
    }
}
