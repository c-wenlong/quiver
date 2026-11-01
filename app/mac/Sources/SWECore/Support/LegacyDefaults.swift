import Foundation

/// One-shot carry of the pre-rename app's preferences. The bundle id
/// changed (`com.cwenlong.quiver` to `com.cwenlong.swe`), and
/// `UserDefaults.standard` follows it: without a copy, first launch of
/// SWE.app resets every setting Quiver.app stored — the swe path, the
/// provider order, the menu bar layout.
public enum LegacyDefaults {
    /// Quiver.app's defaults domain.
    public static let legacyDomain = "com.cwenlong.quiver"
    /// Set once the copy ran, so it never runs twice or overwrites a
    /// change made in SWE.app afterwards.
    public static let flagKey = "didMigrateQuiverDefaults"

    /// Copy every key the old domain holds into `into`, leaving alone
    /// any already set there, then flag the copy done. Keys the old
    /// domain lacks stay unset, as a fresh install's would.
    public static func migrate(into defaults: UserDefaults = .standard,
                               from domain: String = legacyDomain,
                               flag: String = flagKey) {
        guard Bundle.main.bundleIdentifier != domain, !defaults.bool(forKey: flag) else { return }
        defer { defaults.set(true, forKey: flag) }
        guard let values = UserDefaults(suiteName: domain)?.persistentDomain(forName: domain)
        else { return }
        for (name, value) in values where name != flag && defaults.object(forKey: name) == nil {
            defaults.set(value, forKey: name)
        }
    }
}
