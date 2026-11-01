/// The main window's sidebar, as app/mac/ROADMAP.md lays it out.
public enum AppSection: String, CaseIterable, Identifiable, Hashable, Sendable {
    case overview, sessions, calendar, pricing
    case repositories, tasks, inbox
    case accounts, harnesses, compute, skills, agentsMd, mcpServers, plugins, hooks, models
    case menuBar, widgets, serviceStatus, settings

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .overview: "Overview"
        case .sessions: "Sessions"
        case .calendar: "Calendar"
        case .pricing: "Pricing"
        case .repositories: "Repositories"
        case .tasks: "Tasks"
        case .inbox: "Inbox"
        case .accounts: "Accounts"
        case .harnesses: "Harnesses"
        case .compute: "Compute"
        case .skills: "Skills"
        case .agentsMd: "AGENTS.md"
        case .mcpServers: "MCP servers"
        case .plugins: "Plugins"
        case .hooks: "Hooks"
        case .models: "Models"
        case .menuBar: "Menu bar"
        case .widgets: "Widgets"
        case .serviceStatus: "Service status"
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
        case .repositories: "shippingbox"
        case .tasks: "checklist"
        case .inbox: "tray"
        case .accounts: "person.2"
        case .harnesses: "terminal"
        case .compute: "cpu"
        case .skills: "sparkles"
        case .agentsMd: "doc.text"
        case .mcpServers: "powerplug"
        case .plugins: "puzzlepiece.extension"
        case .hooks: "bolt"
        case .models: "cube.transparent"
        case .menuBar: "menubar.rectangle"
        case .widgets: "square.grid.2x2"
        case .serviceStatus: "waveform.path.ecg"
        case .settings: "gearshape"
        }
    }

    /// Core pages always show in the sidebar — Settings must stay
    /// reachable so a hidden page can be brought back. Every other page
    /// can be switched off in Settings › Sidebar.
    public var isCore: Bool {
        switch self {
        case .overview, .accounts, .settings: true
        default: false
        }
    }

    /// The group the sidebar lists this page under.
    public var group: SidebarGroup {
        for group in SidebarGroup.allCases where group.sections.contains(self) {
            return group
        }
        preconditionFailure("AppSection \(rawValue) is in no SidebarGroup")
    }
}

public enum SidebarGroup: CaseIterable, Identifiable, Sendable {
    case usage, github, manage, primitives, system

    public var id: Self { self }

    public var title: String {
        switch self {
        case .usage: "Usage"
        case .github: "GitHub"
        case .manage: "Manage"
        case .primitives: "Primitives"
        case .system: "System"
        }
    }

    public var sections: [AppSection] {
        switch self {
        case .usage: [.overview, .sessions, .calendar, .pricing]
        case .github: [.repositories, .tasks, .inbox]
        case .manage: [.accounts, .harnesses, .compute]
        case .primitives: [.plugins, .skills, .agentsMd, .mcpServers, .hooks, .models]
        case .system: [.menuBar, .widgets, .serviceStatus, .settings]
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
