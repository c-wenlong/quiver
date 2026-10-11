import Testing
@testable import QuiverCore

@Suite struct AppIconStyleTests {
    @Test func lightIsTheDefaultAndEachStyleNamesItsIcon() {
        #expect(AppIconStyle(saved: nil) == .light)
        #expect(AppIconStyle(saved: "neon") == .light)
        #expect(AppIconStyle(saved: "dark") == .dark)
        #expect(AppIconStyle.light.resourceName == "AppIcon-light")
        #expect(AppIconStyle.dark.resourceName == "AppIcon-dark")
        #expect(AppIconStyle.allCases.map(\.title) == ["Light", "Dark"])
    }
}
