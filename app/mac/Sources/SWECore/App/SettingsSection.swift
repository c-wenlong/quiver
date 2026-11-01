/// The Settings page's sub-nav, after TokenTracker's: groups of sections,
/// each with its own title, description and cards.
public enum SettingsSection: String, CaseIterable, Identifiable, Hashable, Sendable {
    case general, sidebar, notifications, providers, integrations, files, experimental

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .general: "General"
        case .sidebar: "Sidebar"
        case .notifications: "Notifications"
        case .integrations: "Integrations"
        case .providers: "Providers"
        case .files: "Files"
        case .experimental: "Experimental"
        }
    }

    /// One line under the section title.
    public var detail: String {
        switch self {
        case .general: "When SWE starts, how it looks, and which version you're running."
        case .sidebar: "Which pages the sidebar lists, and their order inside each group."
        case .notifications: "Alerts when a usage limit runs low."
        case .integrations: "The GitHub CLI behind the GitHub pages, the keys for the services SWE calls, and the phones paired to watch your sessions."
        case .providers: "Which harnesses show up across the app, and the inference plans and API keys behind them."
        case .files: "Where SWE finds swe, your API keys file and your MCP server folders, and the editor that opens them."
        case .experimental: "Features being tried out. They may change or go away."
        }
    }

    /// SF Symbol name.
    public var symbol: String {
        switch self {
        case .general: "gearshape"
        case .sidebar: "sidebar.left"
        case .notifications: "bell"
        case .integrations: "puzzlepiece.extension"
        case .providers: "square.stack.3d.up"
        case .files: "folder"
        case .experimental: "flask"
        }
    }

    /// The section saved from last time, or General.
    public init(saved: String?) {
        switch saved {
        // GitHub, Remote and Services were their own sections before Integrations.
        case "github", "remote", "services": self = .integrations
        // Doctor moved to a card group on General.
        case "doctor": self = .general
        // Paths was renamed Files.
        case "paths": self = .files
        default: self = saved.flatMap(Self.init(rawValue:)) ?? .general
        }
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
        case .app: [.general, .sidebar, .notifications]
        case .usage: [.providers]
        case .advanced: [.integrations, .files, .experimental]
        }
    }

    /// Every section, top to bottom.
    public static var order: [SettingsSection] { allCases.flatMap(\.sections) }
}
