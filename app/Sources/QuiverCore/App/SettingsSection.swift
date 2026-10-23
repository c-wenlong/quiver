/// The Settings page's sub-nav, after TokenTracker's: groups of sections,
/// each with its own title, description and cards.
public enum SettingsSection: String, CaseIterable, Identifiable, Hashable, Sendable {
    case general, sidebar, notifications, github, remote, providers, services, paths, doctor, experimental

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .general: "General"
        case .sidebar: "Sidebar"
        case .notifications: "Notifications"
        case .github: "GitHub"
        case .remote: "Remote"
        case .providers: "Providers"
        case .services: "Services"
        case .paths: "Paths"
        case .doctor: "Doctor"
        case .experimental: "Experimental"
        }
    }

    /// One line under the section title.
    public var detail: String {
        switch self {
        case .general: "When Quiver starts, how it looks, and which version you're running."
        case .sidebar: "Which pages the sidebar lists, and their order inside each group."
        case .notifications: "Alerts when a usage limit runs low."
        case .github: "The gh install behind the GitHub pages, and the synced repo catalog the Add-repo search reads."
        case .remote: "Pair a phone to watch and steer sessions, and see which devices are paired."
        case .providers: "Which harnesses show up across the app, and the inference plans and API keys behind them."
        case .services: "Keys for the services Quiver itself calls, kept in the Keychain."
        case .paths: "Where Quiver finds swe, your API keys file and your MCP server folders."
        case .doctor: "Checks on PATH, Node and drift between quiver and your harnesses."
        case .experimental: "Features being tried out. They may change or go away."
        }
    }

    /// SF Symbol name.
    public var symbol: String {
        switch self {
        case .general: "gearshape"
        case .sidebar: "sidebar.left"
        case .notifications: "bell"
        case .github: "arrow.down.circle.dotted"
        case .remote: "iphone"
        case .providers: "square.stack.3d.up"
        case .services: "key"
        case .paths: "folder"
        case .doctor: "stethoscope"
        case .experimental: "flask"
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
    case app, usage, integrations, advanced

    public var id: Self { self }

    public var title: String {
        switch self {
        case .app: "App"
        case .usage: "Usage"
        case .integrations: "Integrations"
        case .advanced: "Advanced"
        }
    }

    public var sections: [SettingsSection] {
        switch self {
        case .app: [.general, .sidebar, .notifications]
        case .usage: [.providers, .services]
        case .integrations: [.github, .remote]
        case .advanced: [.paths, .doctor, .experimental]
        }
    }

    /// Every section, top to bottom.
    public static var order: [SettingsSection] { allCases.flatMap(\.sections) }
}
