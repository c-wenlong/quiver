import SWECore
import Testing
@testable import SWEApp

/// The launch sheets' Model menu must draw even when the recent list
/// names a model twice.
@Suite @MainActor struct ModelMenuIconsTests {
    @Test func aRepeatedRecentModelKeepsOneIcon() {
        let icons = ModelMenu.icons(for: ["claude-opus-4-1", "claude-sonnet-4-5", "claude-opus-4-1"])
        #expect(icons[.recent("claude-opus-4-1")] != nil)
        #expect(icons.count == 2)
    }

    @Test func aRepeatedRecentModelIsOneRowInOrder() {
        #expect(ModelMenu.options(for: ["gpt-5", "claude-opus-4-1", "gpt-5"])
                == [.harnessDefault, .recent("gpt-5"), .recent("claude-opus-4-1"), .other])
    }
}
