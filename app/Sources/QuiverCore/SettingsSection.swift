/// The Settings page's sub-nav, after TokenTracker's: groups of sections,
/// each with its own title, description and cards.
public enum SettingsSection: String, CaseIterable, Identifiable, Hashable, Sendable {
    case general, notifications, providers, paths

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .general: "General"
        case .notifications: "Notifications"
        case .providers: "Providers"
        case .paths: "Paths"
        }
    }

    /// One line under the section title.
    public var detail: String {
        switch self {
        case .general: "When Quiver starts, which terminal it opens, and its Dock icon."
        case .notifications: "Alerts when a usage limit runs low."
        case .providers: "Which harnesses show up across the app, and in what order."
        case .paths: "Where Quiver finds swe and your API keys file."
        }
    }

    /// SF Symbol name.
    public var symbol: String {
        switch self {
        case .general: "gearshape"
        case .notifications: "bell"
        case .providers: "square.stack.3d.up"
        case .paths: "folder"
        }
    }

    /// The section saved from last time, or General.
    public init(saved: String?) {
        self = saved.flatMap(Self.init(rawValue:)) ?? .general
    }

    /// The section `offset` rows away in the sub-nav, stopping at either end.
    public func step(_ offset: Int) -> SettingsSection {
        let order = SettingsGroup.order
        guard let index = order.firstIndex(of: self) else { return self }
        return order[min(max(index + offset, 0), order.count - 1)]
    }
}

public enum SettingsGroup: CaseIterable, Identifiable, Sendable {
    case app, usage, advanced

    public var id: Self { self }

    public var title: String {
        switch self {
        case .app: "App"
        case .usage: "Usage"
        case .advanced: "Advanced"
        }
    }

    public var sections: [SettingsSection] {
        switch self {
        case .app: [.general, .notifications]
        case .usage: [.providers]
        case .advanced: [.paths]
        }
    }

    /// Every section, top to bottom.
    public static var order: [SettingsSection] { allCases.flatMap(\.sections) }
}
