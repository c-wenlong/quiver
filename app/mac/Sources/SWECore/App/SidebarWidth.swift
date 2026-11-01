/// The user's chosen width for the main window's pinned-open sidebar,
/// clamped to a range that keeps the longest labels and the page panel
/// usable. AppModel persists it; the value stays CoreGraphics-free like
/// the rest of SWECore.
public enum SidebarWidth {
    public static let standard = 196.0
    public static let range = 160.0...360.0
    /// One keyboard or VoiceOver step.
    public static let step = 20.0

    public static func clamped(_ width: Double) -> Double {
        min(max(width, range.lowerBound), range.upperBound)
    }
}
