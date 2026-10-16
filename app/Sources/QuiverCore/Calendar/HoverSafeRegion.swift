import CoreGraphics
import Foundation

/// Decides whether a hover card stays open for a pointer position, so a
/// slow crossing through the gap between anchor and card never closes
/// it. The view polls the pointer's screen position every 50 ms and
/// feeds it here; the card closes only after the pointer has been
/// outside the safe region for `hideGrace` continuously.
public struct HoverSafeRegion: Sendable, Equatable {
    /// Chip ∪ card in screen coordinates: the union covers the arrow gap.
    public var rect: CGRect
    /// How long the pointer may stay outside before the card closes.
    public var hideGrace: Duration
    /// While the card's window has not reported its frame (the popover
    /// is still appearing) `rect` is null and the pointer cannot be
    /// outside a rect that does not exist — the card stays open. Once
    /// `pendingGrace` has passed a missing rect stops protecting it.
    public var pendingGrace: Duration
    private var outsideSince: ContinuousClock.Instant?
    private var pendingSince: ContinuousClock.Instant?

    public init(rect: CGRect = .null, hideGrace: Duration = .milliseconds(150),
                pendingGrace: Duration = .seconds(1)) {
        self.rect = rect
        self.hideGrace = hideGrace
        self.pendingGrace = pendingGrace
    }

    /// Feed the pointer's position. True once it has been outside for
    /// the whole grace; stepping back inside resets the timer.
    public mutating func update(point: CGPoint, now: ContinuousClock.Instant) -> Bool {
        var inside = rect.contains(point)
        if rect.isNull {
            if pendingSince == nil { pendingSince = now }
            inside = now - pendingSince! < pendingGrace
        } else {
            pendingSince = nil
        }
        if inside {
            outsideSince = nil
            return false
        }
        guard let since = outsideSince else {
            outsideSince = now
            return false
        }
        return now - since >= hideGrace
    }
}
