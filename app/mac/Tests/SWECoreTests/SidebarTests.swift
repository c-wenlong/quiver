import Testing
@testable import SWECore

@Suite struct SidebarTests {
    @Test func sectionsAreGroupedAsTheRoadmapSays() {
        #expect(SidebarGroup.allCases.map(\.title) == ["Usage", "GitHub", "Manage", "Primitives", "System"])
        #expect(SidebarGroup.usage.sections == [.overview, .sessions, .calendar, .pricing])
        #expect(SidebarGroup.github.sections == [.repositories, .tasks, .inbox])
        #expect(SidebarGroup.manage.sections == [.accounts, .harnesses, .compute])
        #expect(SidebarGroup.primitives.sections == [.plugins, .skills, .agentsMd, .mcpServers, .hooks, .models])
        #expect(SidebarGroup.system.sections == [.menuBar, .widgets, .serviceStatus, .settings])
    }

    @Test func everySectionHasATitleAndSymbol() {
        for section in AppSection.allCases {
            #expect(!section.title.isEmpty)
            #expect(!section.symbol.isEmpty)
        }
        #expect(AppSection.agentsMd.title == "AGENTS.md")
        #expect(AppSection.mcpServers.title == "MCP servers")
        #expect(AppSection.serviceStatus.title == "Service status")
        #expect(AppSection.menuBar.title == "Menu bar")
        #expect(AppSection.widgets.title == "Widgets")
    }

    @Test func arrowKeysWalkTheSidebarInOrderAcrossGroups() {
        #expect(SidebarGroup.order.first == .overview)
        #expect(AppSection.sessions.step(1) == .calendar)
        #expect(AppSection.calendar.step(1) == .pricing)
        #expect(AppSection.pricing.step(1) == .repositories)
        #expect(AppSection.repositories.step(1) == .tasks)
        #expect(AppSection.tasks.step(1) == .inbox)
        #expect(AppSection.inbox.step(1) == .accounts)
        #expect(AppSection.accounts.step(-1) == .inbox)
        #expect(AppSection.pricing.step(-1) == .calendar)
        // Models closes the Primitives group.
        #expect(AppSection.mcpServers.step(1) == .hooks)
        #expect(AppSection.hooks.step(1) == .models)
        #expect(AppSection.models.step(1) == .menuBar)
        // The ends stay put.
        #expect(AppSection.overview.step(-1) == .overview)
        #expect(SidebarGroup.order.last?.step(1) == SidebarGroup.order.last)
    }
}
