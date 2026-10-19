import CoreGraphics
import Foundation

/// Decides whether a hover card stays open for a pointer position, so a
/// slow crossing through the gap between anchor and card never closes
/// it. The view polls the pointer's screen position every 50 ms and
/// feeds it here; the card closes only after the pointer has been
/// outside the safe region for `hideGrace` continuously.
public struct HoverSafeRegion: Sendable, Equatable {
    /// The block, chip or month entry under the pointer, in screen
    /// coordinates. nil until the view reports in.
    public var anchor: CGRect?
    /// The popover's window frame in screen coordinates. nil while the
    /// card is still appearing — the pointer cannot be outside a rect
    /// that does not exist yet, so the card gets `pendingGrace` cover.
    public var card: CGRect?
    /// How long the pointer may stay outside before the card closes.
    public var hideGrace: Duration
    /// While `card` is still unknown the pointer counts as inside; once
    /// `pendingGrace` has passed a missing card rect stops protecting.
    public var pendingGrace: Duration
    private var outsideSince: ContinuousClock.Instant?
    private var pendingSince: ContinuousClock.Instant?

    public init(anchor: CGRect? = nil, card: CGRect? = nil,
                hideGrace: Duration = .milliseconds(150),
                pendingGrace: Duration = .seconds(1)) {
        self.anchor = anchor
        self.card = card
        self.hideGrace = hideGrace
        self.pendingGrace = pendingGrace
    }

    /// Feed the pointer's position. True once it has been outside for
    /// the whole grace; stepping back inside resets the timer.
    public mutating func update(point: CGPoint, now: ContinuousClock.Instant) -> Bool {
        let region = (anchor ?? .null).union(card ?? .null)
        var inside = region.contains(point)
        if card == nil {
            if pendingSince == nil { pendingSince = now }
            inside = inside || now - pendingSince! < pendingGrace
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

/// How long the pointer rests on a block, chip or month entry before its
/// card opens. With another anchor's card already up the wait is a swap:
/// the pointer crossing a neighbour on its way into the open card must
/// not steal it, so a swap waits too instead of happening on entry.
public enum HoverCardTiming {
    /// No card up: a passing pointer never pops one.
    public static let show: Duration = .milliseconds(350)
    /// Another anchor's card is up.
    public static let swap: Duration = .milliseconds(300)
    /// After every scroll comes to rest, no card opens for this long, so
    /// cards never pop up over a grid that just moved under the pointer.
    public static let afterScroll: Duration = .seconds(1)

    /// The wait before `key`'s card opens; nil when it is already open.
    public static func delay(entering key: String, open: String?) -> Duration? {
        guard let open else { return show }
        return open == key ? nil : swap
    }
}
