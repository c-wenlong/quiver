import Foundation

/// Where updates come from: the local checkout a dev build was made in,
/// or GitHub releases for a prod build.
public enum UpdateChannel: String, CaseIterable, Sendable {
    case dev
    case prod

    public var title: String {
        switch self {
        case .dev: "Local checkout"
        case .prod: "GitHub releases"
        }
    }
}

/// What the build stamped into Info.plist. Every install made before this
/// stamping existed is a local build, so a missing channel reads as dev.
public struct BuildInfo: Sendable, Equatable {
    public var channel: UpdateChannel
    /// The commit the bundle was built from, nil when unstamped.
    public var commit: String?
    /// The checkout build-app.sh ran in, nil for prod or unstamped builds.
    public var sourcePath: String?
    /// The checkout had uncommitted changes at build time.
    public var dirty: Bool
    /// CFBundleShortVersionString, "0.0.0" when unstamped.
    public var version: String
    /// The `git describe` string ("v0.2.9-296-g571fbd8"), nil when absent.
    public var describe: String?

    public init(channel: UpdateChannel = .dev, commit: String? = nil,
                sourcePath: String? = nil, dirty: Bool = false,
                version: String = "0.0.0", describe: String? = nil) {
        self.channel = channel
        self.commit = commit
        self.sourcePath = sourcePath
        self.dirty = dirty
        self.version = version
        self.describe = describe
    }

    public init(infoDictionary: [String: Any]?) {
        let raw = infoDictionary?["SWEChannel"] as? String
        channel = UpdateChannel(rawValue: raw ?? "") ?? .dev
        commit = Self.value(infoDictionary?["SWECommit"])
        sourcePath = Self.value(infoDictionary?["SWESourcePath"])
        dirty = infoDictionary?["SWEDirty"] as? Bool ?? false
        version = Self.value(infoDictionary?["CFBundleShortVersionString"]) ?? "0.0.0"
        describe = Self.value(infoDictionary?["SWEVersion"])
    }

    private static func value(_ any: Any?) -> String? {
        guard let string = any as? String, !string.isEmpty else { return nil }
        return string
    }
}

/// The Updates section's saved choices: a channel override and the
/// checkout dev updates compare against. Stored values win over what the
/// build stamped, except the channel on a prod build: a release build
/// must not offer the dev controls, so it ignores the override.
public struct UpdatePreferences {
    public static let channelKey = "updateChannel"
    public static let checkoutKey = "sourceCheckoutPath"

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public var channelOverride: String {
        get { defaults.string(forKey: Self.channelKey) ?? "" }
        nonmutating set { defaults.set(newValue, forKey: Self.channelKey) }
    }

    public var checkoutOverride: String {
        get { defaults.string(forKey: Self.checkoutKey) ?? "" }
        nonmutating set { defaults.set(newValue, forKey: Self.checkoutKey) }
    }

    /// The stored channel when it names one, else the build's. A prod
    /// build is always prod, whatever was saved — a release has no
    /// source checkout, so resolving to dev would offer a rebuild it
    /// cannot do.
    public func channel(build: BuildInfo) -> UpdateChannel {
        guard build.channel == .dev else { return build.channel }
        return UpdateChannel(rawValue: channelOverride) ?? build.channel
    }

    /// The checkout to compare against: the stored path when one is saved
    /// (tilde-expanded), else the folder the build came from when it still
    /// exists, else nil.
    public func checkout(build: BuildInfo,
                         fileManager: FileManager = .default) -> String? {
        let stored = checkoutOverride.trimmingCharacters(in: .whitespaces)
        if !stored.isEmpty, PathSafety.rejection(of: stored) == nil {
            return (stored as NSString).expandingTildeInPath
        }
        if let stamped = build.sourcePath,
           fileManager.fileExists(atPath: stamped) {
            return stamped
        }
        return nil
    }
}
