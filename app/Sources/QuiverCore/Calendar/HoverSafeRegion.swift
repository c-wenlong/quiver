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
    private var outsideSince: ContinuousClock.Instant?

    public init(rect: CGRect = .null, hideGrace: Duration = .milliseconds(150)) {
        self.rect = rect
        self.hideGrace = hideGrace
    }

    /// Feed the pointer's position. True once it has been outside for
    /// the whole grace; stepping back inside resets the timer.
    public mutating func update(point: CGPoint, now: ContinuousClock.Instant) -> Bool {
        if rect.contains(point) {
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
