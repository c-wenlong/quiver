import AppKit

/// A real titled window that stays off every screen while a test shows
/// it. AppKit moves a titled window onto a display when it is created,
/// ordered in or resized, so a content rect at (-10000, -10000) alone
/// still put the test window on the user's desktop. Placing it after
/// `init` and skipping the constraint keeps it out of sight while layout
/// runs exactly as it does in the app.
final class OffscreenWindow: NSWindow {
    init(size: CGSize, styleMask: NSWindow.StyleMask) {
        super.init(contentRect: NSRect(origin: .zero, size: size),
                   styleMask: styleMask, backing: .buffered, defer: false)
        setFrameOrigin(NSPoint(x: -10_000, y: -10_000))
    }

    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect {
        frameRect
    }
}
