/// Whether focus is being moved from the keyboard, the rule behind CSS
/// `:focus-visible`: a plain key press (Tab, Shift-Tab, an arrow, typing)
/// turns the focus rings on and a click turns them off. A key pressed with
/// Command, Control or Option is a shortcut (⌘S, a menu key) and says
/// nothing about how focus moves, so it leaves the rings as they were.
public enum FocusModality {
    public enum Input: Equatable, Sendable {
        case key(command: Bool, control: Bool, option: Bool)
        case pointer
    }

    /// True for keyboard, false for pointer, nil when the input changes
    /// nothing. Shift and the function-key flag that every arrow key
    /// carries don't count as a shortcut.
    public static func fromKeyboard(after input: Input) -> Bool? {
        switch input {
        case .pointer:
            return false
        case let .key(command, control, option):
            return command || control || option ? nil : true
        }
    }
}
