import CoreGraphics
import Foundation
import Observation

/// The calendar's one hover card: whose card is up, the pending show,
/// and the pointer poller that owns an open card's lifetime. Only `key`
/// is observed, so pointer traffic redraws the anchors that read it and
/// never the page body or its day columns. The pointer, the views'
/// screen rects and the waits come in from outside, so the page passes
/// AppKit's and tests pass their own.
@MainActor @Observable public final class HoverCardState {
    /// The open card's anchor: a segment, "+N" chip or month entry id.
    public private(set) var key: String?
    /// Wraps each open and close, so the page can animate them.
    @ObservationIgnored public var animate: (() -> Void) -> Void = { $0() }

    /// The anchor under the pointer. A pending show re-checks it at fire
    /// time, so a stale event can never open a card the pointer left.
    @ObservationIgnored private var inside: String?
    @ObservationIgnored private var pending: (key: String, task: Task<Void, Never>)?
    /// The newest show or swap, kept after a cancel so `settled()` can
    /// wait for a dropped one to run out too.
    @ObservationIgnored private var lastShow: Task<Void, Never>?
    @ObservationIgnored private var watch: Task<Void, Never>?

    @ObservationIgnored private let pointer: () -> CGPoint
    @ObservationIgnored private let rects: (String) -> (anchor: CGRect?, card: CGRect?)
    @ObservationIgnored private let wait: (Duration) async -> Void
    @ObservationIgnored private let pollWait: (Duration) async -> Void

    /// While a card is open, the pointer's screen position is polled
    /// this often against the anchor ∪ card safe region.
    public static let pollInterval: Duration = .milliseconds(50)

    /// - Parameters:
    ///   - pointer: the pointer's screen position.
    ///   - rects: an anchor's and its open card's screen rects, nil until
    ///     each view reports in.
    ///   - wait: the show and swap delays.
    ///   - pollWait: the poller's tick.
    public init(pointer: @escaping () -> CGPoint,
                rects: @escaping (String) -> (anchor: CGRect?, card: CGRect?),
                wait: @escaping (Duration) async -> Void = { try? await Task.sleep(for: $0) },
                pollWait: @escaping (Duration) async -> Void = { try? await Task.sleep(for: $0) }) {
        self.pointer = pointer
        self.rects = rects
        self.wait = wait
        self.pollWait = pollWait
    }

    /// Pointer entered or left an anchor. A card opens after a beat; with
    /// another card up the swap waits too, so crossing a neighbour on the
    /// way into the open card never steals it. Leaving an anchor only
    /// drops its own pending show: once a card is open the poller
    /// decides when it closes, so a slow crossing cannot collapse it.
    public func hover(_ key: String, inside entered: Bool) {
        guard entered else {
            if inside == key { inside = nil }
            if pending?.key == key { cancelPending() }
            return
        }
        inside = key
        cancelPending()
        guard let delay = HoverCardTiming.delay(entering: key, open: self.key) else { return }
        let show = Task { [weak self, wait] in
            await wait(delay)
            guard let self, !Task.isCancelled, inside == key else { return }
            pending = nil
            animate { self.set(key) }
        }
        pending = (key, show)
        lastShow = show
    }

    /// Opens a card at once, skipping the hover delay ("+N" taps).
    public func open(_ key: String) {
        cancelPending()
        animate { set(key) }
    }

    public func dismiss() {
        cancelPending()
        set(nil)
        inside = nil
    }

    /// AppKit closed `key`'s popover: a click outside it, or its own
    /// window taking a mouse event. The pointer still inside the card's
    /// safe region overrides the second. A late close of a card already
    /// swapped away must not dismiss the one that replaced it.
    public func closed(_ key: String) {
        guard self.key == key else { return }
        let (anchor, card) = rects(key)
        guard !(card ?? .null).union(anchor ?? .null).contains(pointer()) else { return }
        dismiss()
    }

    /// Waits for the newest show or swap to fire or drop, so a test can
    /// wait for it instead of sleeping.
    func settled() async {
        await lastShow?.value
    }

    private func cancelPending() {
        pending?.task.cancel()
        pending = nil
    }

    /// Opens or closes the card and starts or stops the pointer poller
    /// that keeps an open card alive across the arrow gap.
    private func set(_ key: String?) {
        self.key = key
        watch?.cancel()
        watch = nil
        guard let key else { return }
        watch = Task { [weak self, pollWait] in
            var region = HoverSafeRegion()
            while let self, !Task.isCancelled, self.key == key {
                (region.anchor, region.card) = rects(key)
                if region.update(point: pointer(), now: ContinuousClock.now) {
                    animate { self.set(nil) }
                    return
                }
                await pollWait(Self.pollInterval)
            }
        }
    }
}
