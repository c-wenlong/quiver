/// Whether Quiver keeps its Dock icon. Hiding it makes Quiver a menu bar app,
/// so the icon only goes while the menu bar item is there to open it from.
public enum DockPresence {
    public static let defaultsKey = "showsInDock"

    public static func showsIcon(requested: Bool, menuBarVisible: Bool) -> Bool {
        requested || !menuBarVisible
    }
}
