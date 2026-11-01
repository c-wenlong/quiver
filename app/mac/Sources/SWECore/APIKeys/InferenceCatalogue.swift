import Foundation

/// What the "Provider catalogue" sheet lists: every coding plan and every
/// pay-as-you-go provider SWE knows, each one tap from its add-key
/// flow. `hasKey` marks the ones the keys file already covers, so the
/// sheet can say so instead of offering a duplicate.
public enum InferenceCatalogue {
    public struct Entry: Identifiable, Equatable, Sendable {
        /// Which add-key flow the row starts.
        public enum Target: Equatable, Sendable {
            case plan(InferencePlan)
            case provider(APIKeyProvider)
        }
        public var target: Target
        /// The keys file already has a non-empty value for it.
        public var hasKey: Bool

        public init(target: Target, hasKey: Bool) {
            self.target = target
            self.hasKey = hasKey
        }

        public var id: String {
            switch target {
            case .plan(let plan): "plan:" + plan.id
            case .provider(let provider): "provider:" + provider.id
            }
        }

        /// The page's own kind split; the catalogue's chips filter on it.
        public var kind: InferenceItem.Kind {
            switch target {
            case .plan: .plan
            case .provider: .apiKey
            }
        }

        public var name: String {
            switch target {
            case .plan(let plan): plan.name
            case .provider(let provider): provider.name
            }
        }

        public var logo: String? {
            switch target {
            case .plan(let plan): plan.logo
            case .provider(let provider): provider.logo
            }
        }

        /// The row's second line: the vendor for a plan, the variables a
        /// provider saves under.
        public var detail: String {
            switch target {
            case .plan(let plan): plan.vendor
            case .provider(let provider): provider.envVars.joined(separator: ", ")
            }
        }

        /// The variable the entry's add-key flow writes first.
        public var keyName: String {
            switch target {
            case .plan(let plan): plan.keyName
            case .provider(let provider): provider.envVars[0]
            }
        }
    }

    /// Every plan, then every provider, in their own catalog orders.
    public static func entries(keys: [APIKeysModel.Row],
                               plans: [InferencePlan] = InferenceCatalog.all,
                               providers: [APIKeyProvider] = APIKeyProviders.all) -> [Entry] {
        let filled = Set(keys.filter { !$0.value.isEmpty }.map(\.name))
        return plans.map { Entry(target: .plan($0), hasKey: filled.contains($0.keyName)) }
            + providers.map {
                Entry(target: .provider($0), hasKey: $0.envVars.contains { filled.contains($0) })
            }
    }

    /// The entries passing the chip pick and the search; the query matches
    /// the name, the detail line and the variable the entry writes.
    public static func filter(_ entries: [Entry], kind: InferenceItem.Kind?,
                              query: String) -> [Entry] {
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        return entries.filter { entry in
            if let kind, entry.kind != kind { return false }
            guard !needle.isEmpty else { return true }
            return [entry.name, entry.detail, entry.keyName]
                .contains { $0.lowercased().contains(needle) }
        }
    }
}
