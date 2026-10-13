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
    /// The pointer left while suppressed, so the next entry may expand
    /// even if the delayed exit never ran.
    public var leftSinceSuppressed = false

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
        leftSinceSuppressed = false
    }

    /// Called immediately on every pointer exit, before the delayed
    /// collapse: remember the leave so a quick return can re-expand.
    public mutating func pointerLeft() {
        if hoverSuppressed { leftSinceSuppressed = true }
    }

    /// Pointer tracking. A real exit clears the suppression so the next
    /// entry expands again; an entry after a recorded leave clears it
    /// too, since the delayed exit may not have run yet.
    public mutating func pointer(inside: Bool) {
        if inside {
            if leftSinceSuppressed {
                hoverSuppressed = false
                leftSinceSuppressed = false
            }
            hovering = true
        } else {
            hovering = false
            hoverSuppressed = false
            leftSinceSuppressed = false
        }
    }
}
