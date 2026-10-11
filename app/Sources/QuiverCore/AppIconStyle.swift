/// Which of Quiver's two icons the Dock shows. The bundle's own icon, used
/// by Finder and Launchpad, is always the light one: changing it would mean
/// writing into the signed app.
public enum AppIconStyle: String, CaseIterable, Sendable {
    case light, dark

    public init(saved: String?) {
        self = saved.flatMap(Self.init(rawValue:)) ?? .light
    }

    public var title: String {
        switch self {
        case .light: "Light"
        case .dark: "Dark"
        }
    }

    /// The .icns in Contents/Resources, without its extension.
    public var resourceName: String { "AppIcon-\(rawValue)" }
}
