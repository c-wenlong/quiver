import Foundation

/// Whether an Accounts page Sign in should sign in or offer Install first:
/// a terminal that opens to `command not found` helps no one.
public enum AccountSignIn {
    public enum Action: Equatable, Sendable {
        case signIn
        /// The CLI is missing; show the install sheet for this harness.
        case install(harness: String)
    }

    /// `cli` is the binary the sign-in would run (`claude`, `codex`,
    /// `cursor-agent`, `gh`); nil means the account signs in some other
    /// way (Cursor's isolated app) and always signs in. `installed` is the
    /// caller's best answer for whether that binary is on PATH; nil means
    /// unknown and Sign in stays.
    public static func action(cli: String?, harness: String,
                              installed: Bool?) -> Action {
        guard let cli else { return .signIn }
        return installed == false ? .install(harness: harness) : .signIn
    }

    /// The CLI a harness's `loginCommand` would run: its first word
    /// (`gh auth login` → `gh`).
    public static func cli(loginCommand: String) -> String {
        loginCommand.split(separator: " ").map(String.init).first ?? loginCommand
    }
}
