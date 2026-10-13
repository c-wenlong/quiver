/// Whether the main window's sidebar shows in full. `collapsed` is the
/// user's persisted choice; the rest is transient pointer state, so the
/// whole thing stays a small testable value.
public struct SidebarExpansion: Equatable, Sendable {
    /// The persisted user choice.
    public var collapsed: Bool
    /// The pointer is inside the sidebar.
    public var hovering = false
    /// A toggle happened while the pointer was inside: stay collapsed
    /// until the pointer leaves once, or the rail snaps right back open.
    public var hoverSuppressed = false

    public init(collapsed: Bool = false) {
        self.collapsed = collapsed
    }

    /// Full width, either pinned open or hover-floating.
    public var isExpanded: Bool { !collapsed || (hovering && !hoverSuppressed) }
    /// Expanded only because the pointer is inside: an overlay, not layout.
    public var isFloating: Bool { collapsed && isExpanded }

    /// Flip the pinned state. Collapsing with the pointer inside
    /// suppresses the hover expansion it would otherwise trigger.
    public mutating func toggle() {
        collapsed.toggle()
        hoverSuppressed = hovering
    }

    /// Pointer tracking. Leaving clears the suppression so the next
    /// entry expands again.
    public mutating func pointer(inside: Bool) {
        hovering = inside
        if !inside { hoverSuppressed = false }
    }
}
