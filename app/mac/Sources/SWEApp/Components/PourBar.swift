import AppKit
import Observation
import QuartzCore
import SWECore
import SwiftUI

/// Whether the marble pile is pouring in from empty. The Overview hides its
/// harness logos meanwhile and shows `PourBar` where they sit; the scene
/// flips this twice per pour, so SwiftUI updates twice, not per marble.
@MainActor
@Observable
final class PourState {
    /// True until the first pour lands, so the logos never flash in for a
    /// frame before it starts.
    var pouring = true
}

/// A pill where the harness logos will be, filling in each harness's colour
/// as its marbles land, in the logos' left-to-right order. Once the pour
/// ends each colour folds into its logo's circle and the logos take over.
/// Driven by `MarbleScene` directly, on Core Animation layers: SwiftUI
/// state per landed marble would lay the page out on every landing.
struct PourBar: NSViewRepresentable {
    func makeNSView(context: Context) -> PourBarView {
        let view = PourBarView()
        MarbleScene.shared.attach(view)
        return view
    }

    func updateNSView(_ view: PourBarView, context: Context) {}

    static func dismantleNSView(_ view: PourBarView, coordinator: ()) {
        MarbleScene.shared.detach(view)
    }
}

final class PourBarView: NSView {
    /// The logo row's geometry: a 16pt logo with 7pt padding, 6pt apart.
    static let circle: CGFloat = 30
    static let gap: CGFloat = 6
    static let barHeight: CGFloat = 8
    static let splitDuration: CFTimeInterval = 0.45
    /// Delay between neighbouring circles, left first.
    static let splitStagger: CFTimeInterval = 0.045

    private let track = CALayer()
    /// Clips the filling segments to the pill's rounded ends.
    private let clip = CALayer()
    private var segments: [String: CALayer] = [:]
    private var order: [String] = []
    private var landed: [String: Int] = [:]
    private var total = 0
    private var splitting = false
    /// Bumped by `begin`, so a fold's timer from an earlier pour neither
    /// calls its `done` nor fades the new pill.
    private var generation = 0
    /// Ends the fold; tests swap in timers they fire by hand.
    var schedule: MainTimer = MainTimer.runLoop

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layerContentsRedrawPolicy = .never
        clip.masksToBounds = true
        layer?.addSublayer(track)
        layer?.addSublayer(clip)
        layer?.opacity = 0
        setAccessibilityElement(false)
    }

    required init?(coder: NSCoder) { nil }

    override var isFlipped: Bool { true }

    /// The logos under it take the pointer once they show.
    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    override func layout() {
        super.layout()
        guard !splitting else { return }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        let bar = CGRect(x: 0, y: (Self.circle - Self.barHeight) / 2, width: bounds.width, height: Self.barHeight)
        track.frame = bar
        clip.frame = bar
        track.cornerRadius = Self.barHeight / 2
        clip.cornerRadius = Self.barHeight / 2
        track.backgroundColor = resolved(NSColor(Theme.border))
        placeSegments()
        CATransaction.commit()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        needsLayout = true
    }

    // MARK: Driven by the scene

    /// An empty pill for a pour of `order`, left to right.
    func begin(order: [String]) {
        generation += 1
        splitting = false
        landed = [:]
        total = 0
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for segment in segments.values { segment.removeFromSuperlayer() }
        segments = [:]
        self.order = order
        layer?.removeAllAnimations()
        layer?.opacity = 1
        track.opacity = 1
        needsLayout = true
        CATransaction.commit()
    }

    func fill(order: [String], landed: [String: Int], total: Int) {
        guard !splitting else { return }
        self.order = order
        self.landed = landed
        self.total = total
        // A harness the new scale dropped takes its colour with it.
        for (harness, segment) in segments where !order.contains(harness) {
            segment.removeFromSuperlayer()
            segments[harness] = nil
        }
        CATransaction.begin()
        CATransaction.setAnimationDuration(0.12)
        CATransaction.setAnimationTimingFunction(CAMediaTimingFunction(name: .linear))
        placeSegments()
        CATransaction.commit()
    }

    /// Hidden, for when no pour is running.
    func hide() {
        splitting = false
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        layer?.opacity = 0
        CATransaction.commit()
    }

    /// Folds each colour into its logo's circle, then calls `done` (which
    /// shows the logos) and fades itself out under them.
    func split(done: @escaping @MainActor () -> Void) {
        let count = order.count
        let row = CGFloat(count) * Self.circle + CGFloat(max(count - 1, 0)) * Self.gap
        // The logos wrapped onto two lines (or haven't laid out to the
        // pill's width): no circle to fold into, so fade across instead.
        guard count > 0, abs(row - bounds.width) < 2, bounds.height < Self.circle + 4 else {
            done()
            fadeOut()
            return
        }
        splitting = true
        landed = Dictionary(uniqueKeysWithValues: order.map { ($0, max(landed[$0] ?? 0, 1)) })
        total = landed.values.reduce(0, +)

        // Out of the clipping pill, at the same place, before they move.
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        placeSegments()
        for harness in order {
            guard let segment = segments[harness] else { continue }
            segment.frame = clip.convert(segment.frame, to: layer)
            layer?.addSublayer(segment)
        }
        CATransaction.commit()

        let now = CACurrentMediaTime()
        let card = resolved(NSColor(Theme.card).withAlphaComponent(0.85))
        let border = resolved(NSColor(Theme.borderStrong))
        for (index, harness) in order.enumerated() {
            guard let segment = segments[harness] else { continue }
            let from = segment.frame
            let to = CGRect(x: CGFloat(index) * (Self.circle + Self.gap), y: 0, width: Self.circle, height: Self.circle)
            let begin = now + Double(index) * Self.splitStagger
            let spring = { (key: String, from: Any, to: Any) -> CASpringAnimation in
                let animation = CASpringAnimation(keyPath: key)
                animation.fromValue = from
                animation.toValue = to
                animation.damping = 14
                animation.stiffness = 220
                animation.mass = 1
                animation.duration = Self.splitDuration
                animation.beginTime = begin
                animation.fillMode = .backwards
                return animation
            }
            let ease = { (key: String, from: Any, to: Any) -> CABasicAnimation in
                let animation = CABasicAnimation(keyPath: key)
                animation.fromValue = from
                animation.toValue = to
                animation.duration = Self.splitDuration * 0.8
                animation.beginTime = begin
                animation.fillMode = .backwards
                animation.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                return animation
            }
            segment.add(spring("position", NSValue(point: CGPoint(x: from.midX, y: from.midY)),
                               NSValue(point: CGPoint(x: to.midX, y: to.midY))), forKey: "position")
            segment.add(spring("bounds.size", NSValue(size: from.size), NSValue(size: to.size)), forKey: "size")
            segment.add(ease("cornerRadius", 0, Self.circle / 2), forKey: "corner")
            segment.add(ease("backgroundColor", segment.backgroundColor as Any, card), forKey: "fill")
            segment.add(ease("borderWidth", 0, 1), forKey: "border")
            CATransaction.begin()
            CATransaction.setDisableActions(true)
            segment.frame = to
            segment.cornerRadius = Self.circle / 2
            segment.backgroundColor = card
            segment.borderColor = border
            segment.borderWidth = 1
            CATransaction.commit()
        }
        CATransaction.begin()
        CATransaction.setAnimationDuration(0.2)
        track.opacity = 0
        CATransaction.commit()

        let end = Self.splitDuration + Double(count) * Self.splitStagger
        let current = generation
        schedule.after(end) { [weak self] in
            guard let self, self.generation == current else { return }
            done()
            self.fadeOut()
        }
    }

    // MARK: For tests

    /// Each harness's segment frame, in the pill's order, in this view's
    /// coordinates.
    var segmentFrames: [(harness: String, frame: CGRect)] {
        order.compactMap { harness in
            segments[harness].map { segment in
                (harness, segment.superlayer === clip ? clip.convert(segment.frame, to: layer) : segment.frame)
            }
        }
    }

    /// Coloured segment layers present, wherever they sit.
    var segmentLayerCount: Int { segments.count }

    // MARK: Drawing

    private func placeSegments() {
        let width = clip.bounds.width
        var x: CGFloat = 0
        for harness in order {
            let segment = segments[harness] ?? makeSegment(harness)
            let part = total > 0 ? CGFloat(landed[harness] ?? 0) / CGFloat(total) : 0
            segment.frame = CGRect(x: x, y: 0, width: (width * part).rounded(), height: Self.barHeight)
            x += segment.frame.width
        }
    }

    private func makeSegment(_ harness: String) -> CALayer {
        let segment = CALayer()
        let rgb = HarnessColor.of(harness)
        segment.backgroundColor = CGColor(srgbRed: rgb.red, green: rgb.green, blue: rgb.blue, alpha: 1)
        segment.actions = ["onOrderIn": NSNull(), "onOrderOut": NSNull()]
        clip.addSublayer(segment)
        segments[harness] = segment
        return segment
    }

    private func fadeOut() {
        CATransaction.begin()
        CATransaction.setAnimationDuration(0.2)
        CATransaction.setCompletionBlock { [weak self] in
            MainActor.assumeIsolated { self?.splitting = false }
        }
        layer?.opacity = 0
        CATransaction.commit()
    }

    private func resolved(_ color: NSColor) -> CGColor {
        var cg = color.cgColor
        effectiveAppearance.performAsCurrentDrawingAppearance { cg = color.cgColor }
        return cg
    }
}
