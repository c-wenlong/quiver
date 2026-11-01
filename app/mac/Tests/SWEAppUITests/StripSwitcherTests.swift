import AppKit
import SWECore
import Testing
@testable import SWEApp

/// The hub strip's machine menu: while a hub fills the window it is the
/// only machine chrome, so its rows carry the way back to This Mac, the
/// other hubs and the shown hub's actions. `StripSwitcher.SwitcherButton
/// .menu` builds it from the switcher's state.
@MainActor
@Suite struct StripSwitcherTests {
    /// An in-memory stand-in for UserDefaults, like SWECoreTests'.
    private final class MemoryStore: KeyValueStoring, @unchecked Sendable {
        var values: [String: [String]] = [:]
        func strings(forKey key: String) -> [String]? { values[key] }
        func set(_ strings: [String], forKey key: String) { values[key] = strings }
    }

    private func menu(for machines: MachineSwitcher, shown: String) -> NSMenu {
        StripSwitcher.SwitcherButton.menu(machines: machines,
                                          shown: HubOrigin(shown)!,
                                          reload: {}, addHub: {})
    }

    private func fire(_ item: NSMenuItem) {
        (item.target as? NSObject)?.perform(item.action!, with: item)
    }

    @Test func theRowsCoverEveryMachineAndTheShownHubsActions() {
        let switcher = MachineSwitcher(store: MemoryStore())
        switcher.setAgentHub("https://box.ts.net:8477")
        switcher.add("https://den.ts.net:8477")
        switcher.select(HubOrigin("https://den.ts.net:8477"))
        let menu = menu(for: switcher, shown: "https://den.ts.net:8477")
        #expect(menu.items.filter { !$0.isSeparatorItem }
            .map { $0.title.components(separatedBy: "\n").first! } == [
            "This Mac", "box", "den", "Reload den", "Open in Browser", "Add a Hub…"])
        // The shown hub wears the check; This Mac never does on a hub.
        #expect(menu.items[2].state == .on)
        #expect(menu.items[0].state == .off && menu.items[1].state == .off)
        // A hub row's second line is its address.
        #expect(menu.items[1].attributedTitle?.string == "box\nbox.ts.net:8477")
    }

    @Test func pickingAnotherHubOrThisMacSwitchesTheWindow() {
        let switcher = MachineSwitcher(store: MemoryStore())
        switcher.setAgentHub("https://box.ts.net:8477")
        switcher.add("https://den.ts.net:8477")
        switcher.select(HubOrigin("https://den.ts.net:8477"))
        let menu = menu(for: switcher, shown: "https://den.ts.net:8477")
        fire(menu.items[1])
        #expect(switcher.selected == HubOrigin("https://box.ts.net:8477"))
        fire(menu.items[0])
        #expect(switcher.selected == nil)
    }

    @Test func theShownHubsActionsFireTheirClosures() {
        let switcher = MachineSwitcher(store: MemoryStore())
        switcher.add("https://den.ts.net:8477")
        var reloaded = false, added = false
        let menu = StripSwitcher.SwitcherButton.menu(
            machines: switcher, shown: HubOrigin("https://den.ts.net:8477")!,
            reload: { reloaded = true }, addHub: { added = true })
        fire(menu.items.first { $0.title == "Reload den" }!)
        fire(menu.items.first { $0.title == "Add a Hub…" }!)
        #expect(reloaded && added)
    }
}
