/// The main window's sidebar, as app/ROADMAP.md lays it out.
public enum AppSection: String, CaseIterable, Identifiable, Hashable, Sendable {
    case overview, sessions, calendar, pricing
    case accounts, apiKeys, harnesses, compute, skills, agentsMd, mcpServers, plugins
    case menuBar, widgets, serviceStatus, doctor, settings

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .overview: "Overview"
        case .sessions: "Sessions"
        case .calendar: "Calendar"
        case .pricing: "Pricing"
        case .accounts: "Accounts"
        case .apiKeys: "API keys"
        case .harnesses: "Harnesses"
        case .compute: "Compute"
        case .skills: "Skills"
        case .agentsMd: "AGENTS.md"
        case .mcpServers: "MCP servers"
        case .plugins: "Plugins"
        case .menuBar: "Menu bar"
        case .widgets: "Widgets"
        case .serviceStatus: "Service status"
        case .doctor: "Doctor"
        case .settings: "Settings"
        }
    }

    /// SF Symbol name.
    public var symbol: String {
        switch self {
        case .overview: "chart.bar.xaxis"
        case .sessions: "clock.arrow.circlepath"
        case .calendar: "calendar"
        case .pricing: "dollarsign.circle"
        case .accounts: "person.2"
        case .apiKeys: "key"
        case .harnesses: "terminal"
        case .compute: "cpu"
        case .skills: "sparkles"
        case .agentsMd: "doc.text"
        case .mcpServers: "powerplug"
        case .plugins: "puzzlepiece.extension"
        case .menuBar: "menubar.rectangle"
        case .widgets: "square.grid.2x2"
        case .serviceStatus: "waveform.path.ecg"
        case .doctor: "stethoscope"
        case .settings: "gearshape"
        }
    }
}

public enum SidebarGroup: CaseIterable, Identifiable, Sendable {
    case usage, manage, primitives, system

    public var id: Self { self }

    public var title: String {
        switch self {
        case .usage: "Usage"
        case .manage: "Manage"
        case .primitives: "Primitives"
        case .system: "System"
        }
    }

    public var sections: [AppSection] {
        switch self {
        case .usage: [.overview, .sessions, .calendar, .pricing]
        case .manage: [.harnesses, .accounts, .apiKeys, .compute]
        case .primitives: [.plugins, .skills, .agentsMd, .mcpServers]
        case .system: [.menuBar, .widgets, .serviceStatus, .doctor, .settings]
        }
    }
}

extension SidebarGroup {
    /// Every page, top to bottom.
    public static var order: [AppSection] { allCases.flatMap(\.sections) }
}

extension AppSection {
    /// The page `offset` rows away in the sidebar, stopping at either end.
    public func step(_ offset: Int) -> AppSection {
        let order = SidebarGroup.order
        guard let index = order.firstIndex(of: self) else { return self }
        return order[min(max(index + offset, 0), order.count - 1)]
    }
}
