import Foundation

/// One tab of the menu bar popup. A new tab is a new case here, plus its
/// content and settings views in the app's `PopupTab+Views.swift`; the
/// tab bar, the Tabs settings list and the remembered selection pick it
/// up from `allCases`, after the tabs the user has already ordered.
public enum PopupTab: String, CaseIterable, Identifiable, Sendable {
    case usage, compute

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .usage: "Usage"
        case .compute: "Compute"
        }
    }

    /// SF Symbol name, for the Tabs settings list.
    public var symbol: String {
        switch self {
        case .usage: "gauge.with.dots.needle.67percent"
        case .compute: "cpu"
        }
    }
}

/// Which popup tabs show, in what order, and which one is open. At least
/// one tab always shows: the switch of the last shown tab does nothing.
public struct PopupTabs: Equatable, Sendable {
    public private(set) var list = ToggleList()
    /// The tab the popup opened on last, kept across launches.
    public var selected = PopupTab.usage

    public enum Key {
        public static let order = "popupTabOrder"
        public static let hidden = "popupHiddenTabs"
        public static let selected = "popupSelectedTab"
    }

    public init(defaults: UserDefaults) {
        list = ToggleList(defaults: defaults, orderKey: Key.order, hiddenKey: Key.hidden)
        selected = defaults.string(forKey: Key.selected).flatMap(PopupTab.init(rawValue:)) ?? .usage
        // A store that hides every tab (edited by hand, or a tab that
        // later took the last place) still opens on something.
        if visible.isEmpty, let first = all.first { list.setShown(true, first.rawValue) }
    }

    public func save(to defaults: UserDefaults) {
        list.save(to: defaults, orderKey: Key.order, hiddenKey: Key.hidden)
        defaults.set(selected.rawValue, forKey: Key.selected)
    }

    private static let ids = PopupTab.allCases.map(\.rawValue)

    /// Every tab in the dragged order, hidden ones included.
    public var all: [PopupTab] {
        list.ordered(Self.ids).compactMap(PopupTab.init(rawValue:))
    }

    /// The tabs the popup's tab bar shows.
    public var visible: [PopupTab] {
        list.visible(Self.ids).compactMap(PopupTab.init(rawValue:))
    }

    /// The tab the popup draws: the selected one while it shows, else
    /// the first shown tab.
    public var current: PopupTab {
        visible.contains(selected) ? selected : visible.first ?? .usage
    }

    public func shows(_ tab: PopupTab) -> Bool { list.shows(tab.rawValue) }

    /// Whether the tab's switch can turn it off: not when it is the last
    /// one showing.
    public func canHide(_ tab: PopupTab) -> Bool {
        !shows(tab) || visible.count > 1
    }

    public mutating func setShown(_ shown: Bool, _ tab: PopupTab) {
        guard shown || canHide(tab) else { return }
        list.setShown(shown, tab.rawValue)
    }

    public mutating func move(_ tab: String, before target: String) {
        list.move(tab, before: target, in: Self.ids)
    }

    public mutating func moveToEnd(_ tab: String) {
        list.moveToEnd(tab, in: Self.ids)
    }
}
