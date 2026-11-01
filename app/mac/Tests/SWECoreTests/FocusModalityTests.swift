import Testing
@testable import SWECore

@Suite struct FocusModalityTests {
    @Test func aPlainKeyTurnsTheRingsOn() {
        #expect(FocusModality.fromKeyboard(after: .key(command: false, control: false, option: false)) == true)
    }

    @Test func aClickTurnsThemOff() {
        #expect(FocusModality.fromKeyboard(after: .pointer) == false)
    }

    @Test func aShortcutChangesNothing() {
        // ⌘S, ⌃Tab in a menu, ⌥ shortcuts: none of them moves focus.
        #expect(FocusModality.fromKeyboard(after: .key(command: true, control: false, option: false)) == nil)
        #expect(FocusModality.fromKeyboard(after: .key(command: false, control: true, option: false)) == nil)
        #expect(FocusModality.fromKeyboard(after: .key(command: false, control: false, option: true)) == nil)
        #expect(FocusModality.fromKeyboard(after: .key(command: true, control: false, option: true)) == nil)
    }
}
