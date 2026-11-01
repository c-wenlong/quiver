import Foundation
import Testing
@testable import QuiverCore

/// A `~/.claude` synced from another Mac registers marketplaces whose
/// folders never came along (#580): one quiet line, not a warning each and
/// a phantom Local section.
@Suite struct PluginExploreProblemsTests {
    private let listed = PluginHarnessState(available: true, installed: false)

    private func missing(_ name: String, harness: String = "claude") -> PluginCatalog.Problem {
        .init(harness: harness, marketplace: name, detail: "no marketplace manifest on disk")
    }

    /// Seven registrations synced from another Mac, six of them hub
    /// folders, next to one real marketplace and one real problem.
    private var syncedCatalog: PluginCatalog {
        let gone = ["cooking", "garden", "hiking", "music", "poetry", "woodwork"]
        var markets = gone.map { name in
            PluginMarketplace(name: name, hub: true, path: "/Users/someone/.swe/plugins/\(name)",
                              harnesses: ["claude": .init(sourceType: "directory",
                                                          location: "/Users/someone/.swe/plugins/\(name)",
                                                          readable: false)],
                              plugins: [])
        }
        markets.append(PluginMarketplace(name: "claude-code-warp",
                                         harnesses: ["claude": .init(sourceType: "github", location: "warpdotdev/x",
                                                                     readable: false)],
                                         plugins: []))
        markets.append(PluginMarketplace(name: "official", harnesses: ["claude": .init(sourceType: "github")],
                                         plugins: [CatalogPlugin(name: "lsp", marketplace: "official",
                                                                 harnesses: ["claude": listed])]))
        let broken = PluginCatalog.Problem(harness: "codex", marketplace: "odd",
                                           detail: "/x/marketplace.json is not a readable marketplace manifest")
        return PluginCatalog(harnesses: ["claude", "codex"], actions: ["claude", "codex"], marketplaces: markets,
                             errors: (gone + ["claude-code-warp"]).map { missing($0) } + [broken])
    }

    @Test func missingMarketplacesAreOneLineAndOtherProblemsStay() {
        let problems = CatalogProblems(syncedCatalog)
        #expect(problems.missing == ["claude-code-warp", "cooking", "garden", "hiking",
                                     "music", "poetry", "woodwork"])
        #expect(problems.missingLine == "7 marketplaces are registered but missing on this Mac")
        #expect(problems.other.map(\.marketplace) == ["odd"])
    }

    @Test func aMarketplaceMissingForTwoHarnessesCountsOnce() {
        let catalog = PluginCatalog(harnesses: ["claude", "codex"], actions: [], marketplaces: [],
                                    errors: [missing("kit"), missing("kit", harness: "codex")])
        let problems = CatalogProblems(catalog)
        #expect(problems.missing == ["kit"])
        #expect(problems.missingLine == "1 marketplace is registered but missing on this Mac")
        #expect(problems.other.isEmpty)
    }

    @Test func noMissingMarketplaceNoLine() {
        let catalog = PluginCatalog(harnesses: ["claude"], actions: [], marketplaces: [],
                                    errors: [.init(harness: "claude", detail: "settings.json is unreadable")])
        #expect(CatalogProblems(catalog).missingLine == nil)
        #expect(CatalogProblems(catalog).other.count == 1)
    }

    @Test func emptyMissingMarketplacesLeaveNoPhantomLocalSection() {
        let index = PluginExploreIndex(catalog: syncedCatalog, skills: [])
        #expect(index.sources.map(\.id) == ["claude"])
        #expect(index.sources[0].marketplaces.map(\.id) == ["claude/official"])
        #expect(index.problems.missing.count == 7)
    }

    @Test func theMissingLineSitsAboveAListOnly() {
        let index = PluginExploreIndex(catalog: syncedCatalog, skills: [])
        #expect(index.missingNote == "7 marketplaces are registered but missing on this Mac")
    }

    @Test func whenEveryMarketplaceIsMissingTheEmptyPageSaysSoOnce() {
        // Nothing else registered: no list, so no line above one, and the
        // empty card says why instead of "none registered".
        let catalog = PluginCatalog(harnesses: ["claude"], actions: ["claude"], marketplaces: [
            PluginMarketplace(name: "garden", hub: true, harnesses: ["claude": .init(sourceType: "directory",
                                                                                     readable: false)],
                              plugins: []),
            PluginMarketplace(name: "poetry", hub: true, harnesses: ["claude": .init(sourceType: "directory",
                                                                                     readable: false)],
                              plugins: []),
        ], errors: [missing("garden"), missing("poetry")])
        let index = PluginExploreIndex(catalog: catalog, skills: [])
        #expect(index.total == 0)
        #expect(index.sources.isEmpty)
        #expect(index.missingNote == nil)
        #expect(index.emptyText == "No plugins to show. 2 marketplaces are registered but missing on this Mac.")

        let none = PluginExploreIndex(catalog: PluginCatalog(harnesses: ["claude"], actions: [], marketplaces: []),
                                      skills: [])
        #expect(none.emptyText == "No plugin marketplaces registered in Claude Code, Codex or Cursor.")
    }

    @Test func aMissingMarketplaceWithAnInstalledCopyStillShows() {
        // Its folder is gone, but something installed from it is not:
        // that copy can still be turned off or removed.
        let catalog = PluginCatalog(harnesses: ["claude"], actions: ["claude"], marketplaces: [
            PluginMarketplace(name: "mine", hub: true, path: "/h/.swe/plugins/mine",
                              harnesses: ["claude": .init(sourceType: "directory", readable: false)],
                              plugins: [CatalogPlugin(name: "tool", marketplace: "mine", harnesses: [
                                  "claude": PluginHarnessState(available: false, installed: true, enabled: true)])]),
        ], errors: [missing("mine")])
        let index = PluginExploreIndex(catalog: catalog, skills: [])
        #expect(index.sources.map(\.id) == ["local"])
        #expect(index.total == 1)
    }

    @Test func repeatedRefsAndMarketplacesGetOneRowEach() {
        // swe never repeats either, but a ForEach on a repeated id breaks,
        // so the index guarantees it whatever a snapshot holds.
        let tool = CatalogPlugin(name: "tool", marketplace: "kit", harnesses: ["claude": listed])
        let kit = PluginMarketplace(name: "kit", harnesses: ["claude": .init(sourceType: "github")],
                                    plugins: [tool, tool])
        let catalog = PluginCatalog(harnesses: ["claude"], actions: ["claude"], marketplaces: [kit, kit])
        let index = PluginExploreIndex(catalog: catalog, skills: [])
        #expect(index.total == 1)
        let markets = index.sources.flatMap(\.marketplaces)
        #expect(markets.map(\.id) == ["claude/kit"])
        #expect(markets[0].plugins.map(\.id) == ["claude/tool@kit"])
        for source in index.sources {
            #expect(Set(source.marketplaces.map(\.id)).count == source.marketplaces.count)
            for market in source.marketplaces {
                #expect(Set(market.plugins.map(\.id)).count == market.plugins.count)
            }
        }
    }

    @Test func onlyAMarketplaceInTheHubCanBeRenamed() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let home = temp.url
        let hubFolder = home.appending(path: ".swe/plugins/mine")
        try FileManager.default.createDirectory(at: hubFolder, withIntermediateDirectories: true)
        let catalog = PluginCatalog(harnesses: ["claude"], actions: ["claude"], marketplaces: [
            PluginMarketplace(name: "mine", hub: true, path: hubFolder.path,
                              harnesses: ["claude": .init(sourceType: "directory", location: hubFolder.path)],
                              plugins: [CatalogPlugin(name: "a", marketplace: "mine", harnesses: ["claude": listed])]),
            PluginMarketplace(name: "official", harnesses: ["claude": .init(sourceType: "github", location: "acme/x")],
                              plugins: [CatalogPlugin(name: "b", marketplace: "official", harnesses: ["claude": listed])]),
        ])
        let index = PluginExploreIndex(catalog: catalog, skills: [], home: home)
        #expect(index.isEditable(marketplace: "mine"))
        #expect(!index.isEditable(marketplace: "official"))
        // Without a home, nothing is.
        #expect(PluginExploreIndex(catalog: catalog, skills: []).editableMarketplaces.isEmpty)
    }
}
