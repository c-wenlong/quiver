import Testing
@testable import QuiverCore

@Suite struct SidebarTests {
    @Test func sectionsAreGroupedAsTheRoadmapSays() {
        #expect(SidebarGroup.allCases.map(\.title) == ["Usage", "Manage", "System"])
        #expect(SidebarGroup.usage.sections == [.overview, .sessions])
        #expect(SidebarGroup.manage.sections == [.accounts, .skills, .agentsMd, .mcpServers])
        #expect(SidebarGroup.system.sections == [.doctor, .settings])
    }

    @Test func everySectionHasATitleAndSymbol() {
        for section in AppSection.allCases {
            #expect(!section.title.isEmpty)
            #expect(!section.symbol.isEmpty)
        }
        #expect(AppSection.agentsMd.title == "AGENTS.md")
        #expect(AppSection.mcpServers.title == "MCP servers")
    }
}
