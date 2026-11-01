import Foundation
import Testing
@testable import SWECore

/// The machine switcher (#681): parsing a hub's address, which navigations
/// the hub's web view takes, and the persisted hub list and pick.
@Suite struct HubOriginTests {
    @Test func keepsOnlyTheOrigin() {
        let origin = HubOrigin("https://Box.Tailnet.ts.net:8477/app/?x=1#pair=abc")
        #expect(origin?.string == "https://box.tailnet.ts.net:8477")
        #expect(origin?.url.absoluteString == "https://box.tailnet.ts.net:8477/")
        #expect(origin?.address == "box.tailnet.ts.net:8477")
        #expect(origin?.name == "box")
    }

    @Test func aBareHostMeansHttps() {
        #expect(HubOrigin("box.tailnet.ts.net:8477")?.string == "https://box.tailnet.ts.net:8477")
        #expect(HubOrigin("  http://100.64.0.2:8477 ")?.string == "http://100.64.0.2:8477")
    }

    @Test func defaultPortsDrop() {
        #expect(HubOrigin("https://box.ts.net:443") == HubOrigin("https://box.ts.net"))
        #expect(HubOrigin("http://box.ts.net:80")?.string == "http://box.ts.net")
        #expect(HubOrigin("http://box.ts.net:443")?.port == 443)
    }

    @Test func refusesWhatIsNotAnHttpHub() {
        for text in ["", "   ", "file:///etc/passwd", "ftp://box", "javascript:alert(1)",
                     "https://", "https://kai:secret@box.ts.net", "quiver://issue/a/b/1"] {
            #expect(HubOrigin(text) == nil, "\(text)")
        }
    }

    @Test func ipAddressesKeepTheirName() {
        #expect(HubOrigin("http://100.64.0.2:8477")?.name == "100.64.0.2")
        let v6 = HubOrigin("http://[fd7a:115c::1]:8477")
        #expect(v6?.host == "fd7a:115c::1")
        #expect(v6?.string == "http://[fd7a:115c::1]:8477")
        #expect(v6?.name == "fd7a:115c::1")
    }

    @Test func dataStoreIsStablePerHub() {
        let a = HubOrigin("https://box.ts.net:8477")!
        #expect(a.dataStoreID == HubOrigin("box.ts.net:8477/app/")!.dataStoreID)
        #expect(a.dataStoreID != HubOrigin("https://studio.ts.net:8477")!.dataStoreID)
        #expect(a.dataStoreID != HubOrigin("http://box.ts.net:8477")!.dataStoreID)
    }
}

@Suite struct HubNavigationTests {
    let hub = HubOrigin("https://box.ts.net:8477")!

    func decide(_ text: String, clicked: Bool = false) -> HubNavigation {
        HubNavigation.decide(URL(string: text), hub: hub, userInitiated: clicked)
    }

    @Test func theHubsOwnPagesLoad() {
        #expect(decide("https://box.ts.net:8477/") == .allow)
        #expect(decide("https://BOX.ts.net:8477/sessions?x=1#chat") == .allow)
        #expect(decide("blob:https://box.ts.net:8477/5f0c") == .allow)
        #expect(decide("about:blank") == .allow)
        #expect(decide("about:srcdoc") == .allow)
    }

    @Test func otherSitesGoToTheBrowserOnlyOnAClick() {
        #expect(decide("https://github.com/c-wenlong/swe", clicked: true) == .openExternally)
        #expect(decide("mailto:kai@example.com", clicked: true) == .openExternally)
        // A redirect or a script can't move the view or open a browser.
        #expect(decide("https://github.com/c-wenlong/swe") == .cancel)
        #expect(decide("mailto:kai@example.com") == .cancel)
    }

    @Test func aDifferentPortOrSchemeIsAnotherSite() {
        #expect(decide("https://box.ts.net:8478/") == .cancel)
        #expect(decide("http://box.ts.net:8477/") == .cancel)
        #expect(decide("https://box.ts.net/", clicked: true) == .openExternally)
    }

    @Test func otherSchemesNeverLoad() {
        for text in ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,hi",
                     "quiver://issue/a/b/1", "blob:https://evil.example/5f0c", "about:config"] {
            #expect(decide(text, clicked: true) == .cancel, "\(text)")
        }
        #expect(HubNavigation.decide(nil, hub: hub, userInitiated: true) == .cancel)
    }
}

@MainActor
@Suite struct MachineSwitcherTests {
    @Test func listsTheAgentsHubThenTheAddedOnes() {
        let store = MemoryStore()
        let switcher = MachineSwitcher(store: store)
        #expect(switcher.hubs.isEmpty)
        #expect(switcher.add("studio.ts.net:8477") == nil)
        switcher.setAgentHub("https://box.ts.net:8477")
        #expect(switcher.hubs.map(\.origin.string) == ["https://box.ts.net:8477", "https://studio.ts.net:8477"])
        #expect(switcher.hubs.map(\.source) == [.agent, .added])
        #expect(store.values[MachineSwitcher.addedKey] == ["https://studio.ts.net:8477"])
        #expect(store.values[MachineSwitcher.agentKey] == ["https://box.ts.net:8477"])
    }

    @Test func addRefusesJunkAndDuplicates() {
        let switcher = MachineSwitcher(store: MemoryStore())
        #expect(switcher.add("file:///tmp") != nil)
        #expect(switcher.add("https://box.ts.net:8477") == nil)
        #expect(switcher.add("box.ts.net:8477/app/") != nil)
        #expect(switcher.hubs.count == 1)
    }

    @Test func thePickIsRememberedAcrossLaunches() {
        let store = MemoryStore()
        let first = MachineSwitcher(store: store)
        first.add("https://box.ts.net:8477")
        first.select(first.hubs[0].origin)
        #expect(first.showsHub)
        let next = MachineSwitcher(store: store)
        #expect(next.selected == HubOrigin("https://box.ts.net:8477"))
        next.showThisMac()
        #expect(MachineSwitcher(store: store).selected == nil)
    }

    @Test func aHubThatGoesAwayFallsBackToThisMac() {
        let store = MemoryStore()
        let switcher = MachineSwitcher(store: store)
        switcher.setAgentHub("https://box.ts.net:8477")
        switcher.select(switcher.hubs[0].origin)
        // The agent unpaired: its hub leaves the list and the pick with it.
        switcher.setAgentHub(nil)
        #expect(switcher.selected == nil)
        #expect(store.values[MachineSwitcher.selectedKey] == [])
        // A stale saved pick never shows a hub that isn't listed.
        store.values[MachineSwitcher.selectedKey] = ["https://gone.ts.net"]
        #expect(MachineSwitcher(store: store).selected == nil)
    }

    @Test func onlyAddedHubsCanBeRemoved() {
        let switcher = MachineSwitcher(store: MemoryStore())
        switcher.setAgentHub("https://box.ts.net:8477")
        switcher.remove(HubOrigin("https://box.ts.net:8477")!)
        #expect(switcher.hubs.count == 1)
        switcher.add("studio.ts.net")
        switcher.select(HubOrigin("studio.ts.net"))
        switcher.remove(HubOrigin("studio.ts.net")!)
        #expect(switcher.hubs.map(\.source) == [.agent])
        #expect(switcher.selected == nil)
    }

    @Test func cannotPickAnUnlistedHub() {
        let switcher = MachineSwitcher(store: MemoryStore())
        switcher.select(HubOrigin("https://evil.example"))
        #expect(switcher.selected == nil)
    }
}
