import Foundation
import Testing
@testable import QuiverCore

/// An in-memory stand-in for UserDefaults.
final class MemoryStore: KeyValueStoring, @unchecked Sendable {
    var values: [String: [String]] = [:]
    func strings(forKey key: String) -> [String]? { values[key] }
    func set(_ strings: [String], forKey key: String) { values[key] = strings }
}

@Suite struct ProviderCatalogTests {
    @Test func tokenTrackersProvidersAndQuiversHarnessesAreListed() {
        let ids = ProviderCatalog.all.map(\.id)
        // TokenTracker's order first.
        #expect(Array(ids.prefix(5)) == ["claude", "codex", "devin", "cursor", "antigravity"])
        for id in ["kimi", "zcode", "commandcode", "qoder", "qoder-cn", "ark-coding-plan", "ark-agent-plan", "gemini",
                   "qwen-code", "goose", "droid"] {
            #expect(ids.contains(id), "\(id)")
        }
        #expect(Set(ids).count == ids.count)
        #expect(ProviderCatalog.provider("copilot")?.name == "GitHub Copilot")
    }

    @Test func everyProviderHasALogoOrAnInitial() {
        let withLogo = ProviderCatalog.all.filter { HarnessIcons.icon(for: $0.icon) != nil }.map(\.id)
        #expect(withLogo.contains("ark-agent-plan"))
        #expect(Set(ProviderCatalog.all.map(\.id)).subtracting(withLogo) == ["aider", "crush"])
    }
}

@Suite struct ProviderDetectionTests {
    let home = URL(fileURLWithPath: "/Users/k")

    @Test func harnessesComeFromSweAndTheRestFromTheirOwnSigns() {
        let found = ProviderDetection.installed(
            sweHarnesses: ["claude", "codex", "devin"],
            commandExists: { $0 == "arkcli" },
            pathExists: { ["/Users/k/.zcode", "/Applications/Qoder.app"].contains($0) },
            home: home)
        #expect(found == ["claude", "codex", "devin", "zcode", "qoder", "qoder-cn", "ark-coding-plan", "ark-agent-plan"])
    }

    @Test func nothingFoundWhenNothingIsThere() {
        let found = ProviderDetection.installed(sweHarnesses: [], commandExists: { _ in false },
                                                pathExists: { _ in false }, home: home)
        #expect(found.isEmpty)
    }
}

@MainActor
@Suite struct ProviderVisibilityTests {
    func model(installed: Set<String>, store: MemoryStore = MemoryStore()) async -> ProviderVisibilityModel {
        let model = ProviderVisibilityModel(store: store, detect: { installed })
        await model.load()
        return model
    }

    @Test func installedProvidersStartEnabledAndTheRestCannotBe() async {
        let model = await model(installed: ["claude", "codex", "devin"])
        #expect(model.enabledCount == 3)
        #expect(model.rows.count == ProviderCatalog.all.count)
        let cursor = model.rows.first { $0.id == "cursor" }
        #expect(cursor?.isInstalled == false)
        #expect(cursor?.isEnabled == false)
        model.setEnabled("cursor", true)
        #expect(model.isVisible("cursor") == false)
    }

    @Test func togglesAndBulkActionsPersist() async {
        let store = MemoryStore()
        let model = await model(installed: ["claude", "codex", "devin"], store: store)
        model.setEnabled("codex", false)
        #expect(model.enabledCount == 2)
        #expect(model.isVisible("codex") == false)

        let reopened = await self.model(installed: ["claude", "codex", "devin"], store: store)
        #expect(reopened.isVisible("codex") == false)

        reopened.disableAll()
        #expect(reopened.enabledCount == 0)
        reopened.enableAll()
        #expect(reopened.enabledCount == 3)
        #expect(reopened.isVisible("cursor") == false)
    }

    @Test func nothingIsCalledMissingBeforeTheFirstCheck() async {
        let model = ProviderVisibilityModel(store: MemoryStore(), detect: { ["claude"] })
        #expect(model.hasChecked == false)
        await model.load()
        #expect(model.hasChecked)
    }

    @Test func usageViewsHideOnlyWhatWasSwitchedOff() async {
        let model = await model(installed: ["claude"])
        #expect(model.isHidden("opencode") == false)
        model.setEnabled("claude", false)
        #expect(model.isHidden("claude"))
    }

    @Test func aProviderInstalledLaterTurnsOnByItself() async {
        let store = MemoryStore()
        _ = await model(installed: ["claude"], store: store)
        let later = await model(installed: ["claude", "cursor"], store: store)
        #expect(later.isVisible("cursor"))
    }

    @Test func dragMovesARowAndTheOrderIsKept() async {
        let store = MemoryStore()
        let model = await model(installed: ["claude"], store: store)
        model.move("gemini", before: "claude")
        #expect(model.rows.first?.id == "gemini")
        #expect(model.rows[1].id == "claude")
        let reopened = await self.model(installed: ["claude"], store: store)
        #expect(reopened.rows.first?.id == "gemini")
        // A provider added to the catalog later still shows up, at the end.
        store.values["providerOrder"] = ["gemini", "claude"]
        let partial = await self.model(installed: ["claude"], store: store)
        #expect(partial.rows.count == ProviderCatalog.all.count)
        #expect(Array(partial.rows.prefix(2).map(\.id)) == ["gemini", "claude"])
    }

    @Test func orderingFollowsTheUsersOrder() async {
        let model = await model(installed: ["claude", "codex"])
        model.move("codex", before: "claude")
        #expect(model.ordered(["claude", "codex", "devin"]) == ["codex", "claude", "devin"])
    }
}
