/// The main window's sidebar, as app/ROADMAP.md lays it out.
public enum AppSection: String, CaseIterable, Identifiable, Hashable, Sendable {
    case overview, sessions
    case accounts, skills, agentsMd, mcpServers, plugins
    case doctor, settings

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .overview: "Overview"
        case .sessions: "Sessions"
        case .accounts: "Accounts"
        case .skills: "Skills"
        case .agentsMd: "AGENTS.md"
        case .mcpServers: "MCP servers"
        case .plugins: "Plugins"
        case .doctor: "Doctor"
        case .settings: "Settings"
        }
    }

    /// SF Symbol name.
    public var symbol: String {
        switch self {
        case .overview: "chart.bar.xaxis"
        case .sessions: "clock.arrow.circlepath"
        case .accounts: "person.2"
        case .skills: "sparkles"
        case .agentsMd: "doc.text"
        case .mcpServers: "powerplug"
        case .plugins: "puzzlepiece.extension"
        case .doctor: "stethoscope"
        case .settings: "gearshape"
        }
    }
}

public enum SidebarGroup: CaseIterable, Identifiable, Sendable {
    case usage, manage, system

    public var id: Self { self }

    public var title: String {
        switch self {
        case .usage: "Usage"
        case .manage: "Manage"
        case .system: "System"
        }
    }

    public var sections: [AppSection] {
        switch self {
        case .usage: [.overview, .sessions]
        case .manage: [.accounts, .skills, .agentsMd, .mcpServers, .plugins]
        case .system: [.doctor, .settings]
        }
    }
}
