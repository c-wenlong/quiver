import Testing
@testable import SWECore

@Suite struct DockPresenceTests {
    @Test func theIconGoesOnlyWhenTheMenuBarItemCanStandInForIt() {
        #expect(DockPresence.showsIcon(requested: true, menuBarVisible: true))
        #expect(!DockPresence.showsIcon(requested: false, menuBarVisible: true))
        // No menu bar item: the Dock is the only way back, so it stays.
        #expect(DockPresence.showsIcon(requested: false, menuBarVisible: false))
        #expect(DockPresence.showsIcon(requested: true, menuBarVisible: false))
    }
}
