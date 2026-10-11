import Foundation

/// One-time rewrites of saved menu bar choices when a reading's meaning
/// changes under them.
public enum MenuBarMigrations {
    static let cursorIncludedKey = "migratedCursorAutoToIncluded"

    public static func run(on defaults: UserDefaults) {
        cursorAutoToIncluded(defaults)
    }

    /// swe used to label Cursor's Included figure "auto"; it now lists
    /// Included, Auto and API separately. A choice saved against the old
    /// "auto" meant Included, so it follows that figure.
    private static func cursorAutoToIncluded(_ defaults: UserDefaults) {
        guard !defaults.bool(forKey: cursorIncludedKey) else { return }
        defer { defaults.set(true, forKey: cursorIncludedKey) }
        if defaults.string(forKey: MenuBarDisplay.Key.watchedLimit) == "cursor|auto" {
            defaults.set("cursor|included", forKey: MenuBarDisplay.Key.watchedLimit)
        }
        if var hidden = defaults.dictionary(forKey: MenuBarPopup.Key.hiddenWindows) as? [String: [String]],
           let cursor = hidden["cursor"], cursor.contains("auto") {
            hidden["cursor"] = cursor.map { $0 == "auto" ? "included" : $0 }
            defaults.set(hidden, forKey: MenuBarPopup.Key.hiddenWindows)
        }
    }
}
