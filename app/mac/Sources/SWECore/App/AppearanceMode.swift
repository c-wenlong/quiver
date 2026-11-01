/// Light or dark for SWE's windows and menu bar panel, or whatever macOS
/// is set to. Widgets always follow macOS: WidgetKit draws them.
public enum AppearanceMode: String, CaseIterable, Sendable {
    case system, light, dark

    public static let defaultsKey = "appearance"

    public init(saved: String?) {
        self = saved.flatMap(Self.init(rawValue:)) ?? .system
    }

    public var title: String {
        switch self {
        case .system: "System"
        case .light: "Light"
        case .dark: "Dark"
        }
    }
}
