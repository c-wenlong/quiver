import Foundation

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

/// The delayed hover-expand the window sidebar and the calendar's side
/// panel share: a collapsed rail floats open 0.12 s after the pointer
/// enters, hides 0.2 s after it leaves, and a toggle settles a pending
/// hover change first so a collapse right after entering still
/// suppresses the expansion and a late entry can't reopen it.
@MainActor @Observable
public final class HoverExpansion {
    public var expansion: SidebarExpansion
    /// The delayed hover change; a new pointer event replaces it.
    private var pending: Task<Void, Never>?

    /// The in-flight delayed hover change, for tests that await it.
    var pendingTaskForTesting: Task<Void, Never>? { pending }
    /// Where the pointer is right now, ahead of the delayed hover change.
    private var pointerInside = false
    /// How long the pointer must stay before expanding or hiding.
    /// Injected only so tests can run against short delays.
    private let enterDelay: Duration
    private let exitDelay: Duration
    /// Injected so tests drive the delays with a manual clock instead of
    /// wall time. Production sleeps on the task clock.
    private let sleep: @Sendable (Duration) async throws -> Void

    public init(collapsed: Bool = false,
                enterDelay: Duration = .milliseconds(120),
                exitDelay: Duration = .milliseconds(200),
                sleep: @escaping @Sendable (Duration) async throws -> Void
                    = { try await Task.sleep(for: $0) }) {
        expansion = SidebarExpansion(collapsed: collapsed)
        self.enterDelay = enterDelay
        self.exitDelay = exitDelay
        self.sleep = sleep
    }

    public var isExpanded: Bool { expansion.isExpanded }
    public var isFloating: Bool { expansion.isFloating }

    /// `collapsed` is the persisted choice the pointer logic keys off:
    /// a pinned-open sidebar tracks the pointer for suppression but only
    /// a collapsed one schedules the delayed expand and hide.
    /// A repeat `inside` value is a no-op: callers that report on every
    /// pointer move must not keep pushing the delayed change out.
    public func hover(_ inside: Bool, collapsed: Bool) {
        guard inside != pointerInside else { return }
        pending?.cancel()
        pointerInside = inside
        if !inside { expansion.pointerLeft() }
        guard collapsed else {
            expansion.pointer(inside: inside)
            return
        }
        pending = Task { @MainActor [sleep] in
            try? await sleep(inside ? enterDelay : exitDelay)
            guard !Task.isCancelled else { return }
            expansion.pointer(inside: inside)
        }
    }

    public func toggle() {
        pending?.cancel()
        expansion.pointer(inside: pointerInside)
        expansion.toggle()
    }
}
