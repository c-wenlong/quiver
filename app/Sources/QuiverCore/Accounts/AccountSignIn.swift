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
        guard cli != nil else { return .signIn }
        return installed == false ? .install(harness: harness) : .signIn
    }

    /// The CLI a harness's `loginCommand` would run: its first word
    /// (`gh auth login` → `gh`).
    public static func cli(loginCommand: String) -> String {
        loginCommand.split(separator: " ").map(String.init).first ?? loginCommand
    }

    /// Which installed answer applies to a harness row's Sign in.
    /// `cliInstalled` is keyed by harness name, so a row whose login
    /// binary is its harness's own command (cursor → cursor-agent via
    /// `Vendor.cliCommand`) looks up its harness name, falling back to the
    /// vendor's `cliOnPath` answer. A login run by a different binary
    /// (copilot → gh) looks the binary up under its own name.
    public static func installed(harness: String, cli: String,
                                 cliInstalled: [String: Bool],
                                 vendorInstalled: Bool?) -> Bool? {
        if Vendor(rawValue: harness)?.cliCommand == cli {
            return cliInstalled[harness] ?? vendorInstalled
        }
        return cliInstalled[cli]
    }
}
