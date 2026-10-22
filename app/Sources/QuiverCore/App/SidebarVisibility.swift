import Observation

/// Settings › Sidebar: which pages the sidebar lists, and their order
/// inside each group. Rows drag only within their own group — the group
/// headings never move. Core pages (Overview, Accounts, Settings) always
/// show. What is kept is the order and the pages switched off, so a page
/// the enum gains later turns on by itself.
@MainActor
@Observable
public final class SidebarVisibilityModel {
    public struct Row: Identifiable, Equatable, Sendable {
        public var id: String { section.rawValue }
        public var section: AppSection
        public var isEnabled: Bool
        public var isCore: Bool { section.isCore }

        public init(section: AppSection, isEnabled: Bool) {
            self.section = section
            self.isEnabled = isEnabled
        }
    }

    static let orderKey = "sidebarOrder"
    static let hiddenKey = "sidebarHidden"

    private let store: KeyValueStoring
    /// Every page, grouped the way `SidebarGroup` lays them out; only the
    /// order inside a group ever changes.
    private var order: [AppSection]
    private var hidden: Set<AppSection>

    public init(store: KeyValueStoring) {
        self.store = store
        order = Self.completeOrder((store.strings(forKey: Self.orderKey) ?? [])
            .compactMap(AppSection.init(rawValue:)))
        hidden = Set((store.strings(forKey: Self.hiddenKey) ?? [])
            .compactMap(AppSection.init(rawValue:)))
        hidden.subtract(AppSection.allCases.filter(\.isCore))
    }

    /// All of a group's rows in the user's order, shown or not — the
    /// settings card lists them all so a hidden page can come back.
    public func rows(in group: SidebarGroup) -> [Row] {
        order.filter { $0.group == group }
            .map { Row(section: $0, isEnabled: isVisible($0)) }
    }

    /// The pages a group's sidebar entry lists, in the user's order.
    public func sections(in group: SidebarGroup) -> [AppSection] {
        order.filter { $0.group == group && isVisible($0) }
    }

    /// Groups with at least one page showing; an empty group leaves no
    /// heading in the sidebar.
    public var visibleGroups: [SidebarGroup] {
        SidebarGroup.allCases.filter { !sections(in: $0).isEmpty }
    }

    public var enabledCount: Int { order.count { isVisible($0) } }
    public var totalCount: Int { order.count }

    public func isVisible(_ section: AppSection) -> Bool {
        section.isCore || !hidden.contains(section)
    }

    public func isHidden(_ section: AppSection) -> Bool { !isVisible(section) }

    public func setEnabled(_ section: AppSection, _ enabled: Bool) {
        guard !section.isCore else { return }
        if enabled { hidden.remove(section) } else { hidden.insert(section) }
        save()
    }

    public func enableAll() {
        hidden.removeAll()
        save()
    }

    /// Every optional page off; the core ones stay.
    public func disableAll() {
        hidden = Set(AppSection.allCases.filter { !$0.isCore })
        save()
    }

    /// Moves `id` to sit just before `target`. A nil target drops it last
    /// in its own group, which is as far as a row can go.
    public func move(_ id: String, before target: String?) {
        guard let moving = AppSection(rawValue: id),
              let from = order.firstIndex(of: moving) else { return }
        order.remove(at: from)
        if let target, let targetSection = AppSection(rawValue: target),
           let to = order.firstIndex(of: targetSection) {
            order.insert(moving, at: to)
        } else if let anchor = order.lastIndex(where: { $0.group == moving.group }) {
            order.insert(moving, at: anchor + 1)
        } else {
            order.append(moving)
        }
        save()
    }

    /// The page `offset` rows away for arrow-key navigation, walking only
    /// the shown pages. A hidden or unlisted page still has a place in
    /// the full order, so a step out of it lands on the neighbour the
    /// direction points to. The ends stay put.
    public func step(_ section: AppSection, by offset: Int) -> AppSection {
        let visible = order.filter(isVisible)
        guard !visible.isEmpty else { return section }
        guard let position = visible.firstIndex(of: section) else {
            // A hidden page holds the slot between the shown rows around
            // it: down lands on the next shown one, up on the previous.
            let at = order.firstIndex(of: section) ?? order.count
            let slot = visible.filter { order.firstIndex(of: $0)! < at }.count
            let target = offset > 0 ? slot + offset - 1 : slot + offset
            return visible[min(max(target, 0), visible.count - 1)]
        }
        return visible[min(max(position + offset, 0), visible.count - 1)]
    }

    private func save() {
        store.set(order.map(\.rawValue), forKey: Self.orderKey)
        store.set(hidden.map(\.rawValue).sorted(), forKey: Self.hiddenKey)
    }

    /// The saved order, minus pages the enum dropped, plus pages it
    /// gained — each new one slots in after its nearest ordered neighbour
    /// in the default group layout, so it lands inside its own group
    /// rather than at the end of the list.
    private static func completeOrder(_ saved: [AppSection]) -> [AppSection] {
        var seen = Set<AppSection>()
        var order = saved.filter { seen.insert($0).inserted }
        let natural = SidebarGroup.order
        for (index, section) in natural.enumerated() where !seen.contains(section) {
            seen.insert(section)
            let at = natural[..<index].last { order.contains($0) }
                .flatMap { order.firstIndex(of: $0) }.map { $0 + 1 }
                ?? natural[(index + 1)...].first { order.contains($0) }
                    .flatMap { order.firstIndex(of: $0) }
                ?? order.endIndex
            order.insert(section, at: at)
        }
        return order
    }
}
