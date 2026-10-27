import Foundation

public extension URL {
    /// Only http and https may leave the app through `openURL` or
    /// `NSWorkspace.open`: the strings behind those buttons arrive in swe
    /// replies and remote API payloads, where `file:`, `smb:` or an app
    /// scheme would hand the click to another program instead of a
    /// browser.
    var isWebURL: Bool {
        let scheme = scheme?.lowercased()
        return scheme == "http" || scheme == "https"
    }
}
