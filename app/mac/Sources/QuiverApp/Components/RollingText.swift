import AppKit
import QuartzCore
import SwiftUI

/// One line of text that rolls to its new value, odometer style: changed
/// characters slide out and the new ones slide in, a beat apart from the
/// right, while the line re-centres.
///
/// SwiftUI's `.numericText` does this by laying the text out again on every
/// frame, and on the Overview every frame of that laid out the whole page,
/// so a hover took about 20 frames of full page layouts and lagged. Here the
/// line keeps one size (the width SwiftUI offers, one line high) and the
/// roll is Core Animation on layers, so SwiftUI updates once per change and
/// the frames run on the render server.
struct RollingText: NSViewRepresentable {
    var text: String
    var font: NSFont
    var color: NSColor
    /// Extra space after each character, as `.tracking` would add.
    var kern: CGFloat = 0
    /// Whether each character rolls on its own (figures) or the line rolls
    /// as one (a label that changes word).
    var perCharacter = true
    /// New text comes up from below; false brings it down from above.
    var up = true

    func makeNSView(context: Context) -> RollingTextView { RollingTextView() }

    func updateNSView(_ view: RollingTextView, context: Context) {
        view.show(text, style: .init(font: font, color: color, kern: kern),
                  perCharacter: perCharacter, up: up)
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView: RollingTextView, context: Context) -> CGSize? {
        let style = RollingTextView.Style(font: font, color: color, kern: kern)
        return CGSize(width: proposal.width ?? style.width(of: text), height: style.lineHeight)
    }

    /// The width `text` takes, for placing something beside a line.
    static func width(of text: String, font: NSFont, kern: CGFloat = 0) -> CGFloat {
        RollingTextView.Style(font: font, color: .black, kern: kern).width(of: text)
    }
}

final class RollingTextView: NSView {
    struct Style: Equatable {
        var font: NSFont
        var color: NSColor
        var kern: CGFloat

        var lineHeight: CGFloat { ceil(font.ascender - font.descender + font.leading) }

        func attributes(_ color: CGColor) -> [NSAttributedString.Key: Any] {
            [.font: font, .kern: kern, .foregroundColor: color]
        }

        func width(of text: String) -> CGFloat {
            ceil(NSAttributedString(string: text, attributes: [.font: font, .kern: kern]).size().width)
        }
    }

    /// One character (or the whole line) in a clipping box, so a glyph
    /// rolling out of it disappears at the line's edge.
    private final class Cell {
        let box = CALayer()
        var glyph: CATextLayer
        var text: String
        var width: CGFloat

        init(text: String, width: CGFloat, glyph: CATextLayer) {
            self.text = text
            self.width = width
            self.glyph = glyph
            box.masksToBounds = true
            box.addSublayer(glyph)
        }
    }

    static let duration: CFTimeInterval = 0.32
    /// Delay between neighbouring characters, rightmost first.
    static let stagger: CFTimeInterval = 0.03

    private var style: Style?
    private var cells: [Cell] = []
    private var text = ""
    private var perCharacter = true
    /// Bumped per change, so a finished roll doesn't tidy up after a newer one.
    private var generation = 0
    /// The glyphs the latest roll is sliding out, removed once it lands.
    private var leaving: [CALayer] = []
    /// Runs the tidy after a roll; tests swap in timers they fire by hand.
    var schedule: MainTimer = MainTimer.runLoop

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layerContentsRedrawPolicy = .never
        setAccessibilityElement(true)
        setAccessibilityRole(.staticText)
    }

    required init?(coder: NSCoder) { nil }

    override var isFlipped: Bool { true }

    override var intrinsicContentSize: NSSize {
        NSSize(width: NSView.noIntrinsicMetric, height: NSView.noIntrinsicMetric)
    }

    func show(_ newText: String, style newStyle: Style, perCharacter: Bool, up: Bool) {
        setAccessibilityValue(newText)
        let restyled = newStyle != style
        style = newStyle
        guard newText != text || restyled || cells.isEmpty else { return }
        let animated = !restyled && !cells.isEmpty && window != nil
            && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        let old = text
        text = newText
        self.perCharacter = perCharacter
        if animated {
            roll(from: old, to: newText, perCharacter: perCharacter, up: up)
        } else {
            rebuild(perCharacter: perCharacter)
        }
    }

    override func layout() {
        super.layout()
        // A new width (a window resize) re-centres without animating.
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        place(cells)
        CATransaction.commit()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        if !cells.isEmpty { rebuild(perCharacter: perCharacter) }
    }

    override func viewDidChangeBackingProperties() {
        super.viewDidChangeBackingProperties()
        let scale = window?.backingScaleFactor ?? 2
        for cell in cells { cell.glyph.contentsScale = scale }
    }

    // MARK: For tests

    /// The text the boxes hold now (a roll in flight shows its target).
    var shownText: String { cells.map(\.text).joined() }
    /// One per character box, or one for a whole-line label.
    var boxCount: Int { layer?.sublayers?.count ?? 0 }
    /// Every glyph layer in every box, rolling out ones included.
    var glyphCount: Int { (layer?.sublayers ?? []).reduce(0) { $0 + ($1.sublayers?.count ?? 0) } }
    var glyphOpacities: [Float] { (layer?.sublayers ?? []).flatMap { $0.sublayers ?? [] }.map(\.opacity) }

    // MARK: Building

    private func pieces(_ text: String, perCharacter: Bool) -> [String] {
        perCharacter ? text.map(String.init) : [text]
    }

    private func makeGlyph(_ piece: String) -> CATextLayer {
        let glyph = CATextLayer()
        guard let style else { return glyph }
        var color = style.color.cgColor
        effectiveAppearance.performAsCurrentDrawingAppearance { color = style.color.cgColor }
        glyph.string = NSAttributedString(string: piece, attributes: style.attributes(color))
        glyph.contentsScale = window?.backingScaleFactor ?? 2
        let pad = Self.pad(style)
        glyph.frame = CGRect(x: pad, y: 0, width: style.width(of: piece) + pad, height: style.lineHeight)
        // Glyphs only move by the explicit roll animations below.
        glyph.actions = ["contents": NSNull(), "position": NSNull(), "bounds": NSNull(), "opacity": NSNull(),
                         "onOrderIn": NSNull(), "onOrderOut": NSNull(), "hidden": NSNull()]
        return glyph
    }

    private func makeCell(_ piece: String) -> Cell {
        Cell(text: piece, width: style?.width(of: piece) ?? 0, glyph: makeGlyph(piece))
    }

    /// Snaps to `text` with no animation.
    private func rebuild(perCharacter: Bool) {
        generation += 1
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for cell in cells { cell.box.removeFromSuperlayer() }
        cells = pieces(text, perCharacter: perCharacter).map(makeCell)
        for cell in cells { layer?.addSublayer(cell.box) }
        place(cells)
        CATransaction.commit()
    }

    /// Room either side of a character's box, so a glyph that reaches past
    /// its advance (bold digits with tight tracking) isn't clipped. The
    /// boxes clip only what rolls above or below the line.
    private static func pad(_ style: Style) -> CGFloat { ceil(style.font.pointSize * 0.2) }

    /// Lays the cells out centred, each in its own box.
    private func place(_ cells: [Cell]) {
        guard let style else { return }
        let pad = Self.pad(style)
        var x = ((bounds.width - cells.reduce(0) { $0 + $1.width }) / 2).rounded()
        for cell in cells {
            cell.box.frame = CGRect(x: x - pad, y: 0, width: cell.width + 2 * pad, height: style.lineHeight)
            x += cell.width
        }
    }

    // MARK: Rolling

    private func roll(from old: String, to new: String, perCharacter: Bool, up: Bool) {
        guard let style else { return }
        generation += 1
        let current = generation
        // A change mid-roll starts from where the last one was heading.
        settle()

        // Right-aligned, as an odometer: "35.3B" to "7.0B" compares the
        // B with the B and the 3 with the 0.
        var before = pieces(old, perCharacter: perCharacter)
        var after = pieces(new, perCharacter: perCharacter)
        let count = max(before.count, after.count)
        before.insert(contentsOf: repeatElement("", count: count - before.count), at: 0)
        after.insert(contentsOf: repeatElement("", count: count - after.count), at: 0)
        var oldCells = cells
        while oldCells.count < count {
            let empty = makeCell("")
            layer?.addSublayer(empty.box)
            oldCells.insert(empty, at: 0)
        }
        // Where every box starts, before the line re-centres.
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        place(oldCells)
        CATransaction.commit()

        let now = CACurrentMediaTime()
        let shift = style.lineHeight * (up ? 1 : -1)
        leaving = []
        CATransaction.begin()
        CATransaction.setAnimationDuration(Self.duration)
        CATransaction.setAnimationTimingFunction(CAMediaTimingFunction(controlPoints: 0.2, 0.8, 0.2, 1))
        for index in 0..<count where before[index] != after[index] {
            let cell = oldCells[index]
            let delay = Double(count - 1 - index) * Self.stagger
            let outgoing = cell.glyph
            let incoming = makeGlyph(after[index])
            cell.box.addSublayer(incoming)
            slide(outgoing, from: 0, to: -shift, fadeOut: true, at: now + delay)
            slide(incoming, from: shift, to: 0, fadeOut: false, at: now + delay)
            leaving.append(outgoing)
            cell.glyph = incoming
            cell.text = after[index]
            cell.width = style.width(of: after[index])
        }
        // The boxes move and resize to the new line, centred; implicit
        // animations within this transaction.
        place(oldCells)
        CATransaction.commit()
        cells = oldCells
        // Tidy once the last character has landed. On a timer of the roll's
        // own length rather than the transaction's completion block, which
        // never comes for a window that isn't drawing. A newer roll bumps
        // `generation`, so this one's tidy can't touch its glyphs.
        let end = Self.duration + Double(count) * Self.stagger + 0.05
        schedule.after(end) { [weak self] in
            guard let self, self.generation == current else { return }
            self.tidy()
        }
    }

    /// Moves a glyph vertically and fades it, starting at `time`.
    private func slide(_ glyph: CATextLayer, from: CGFloat, to: CGFloat, fadeOut: Bool, at time: CFTimeInterval) {
        let move = CABasicAnimation(keyPath: "position.y")
        move.fromValue = glyph.position.y + from
        move.toValue = glyph.position.y + to
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = fadeOut ? 1 : 0
        fade.toValue = fadeOut ? 0 : 1
        let group = CAAnimationGroup()
        group.animations = [move, fade]
        group.beginTime = time
        group.duration = Self.duration
        group.fillMode = .both
        group.isRemovedOnCompletion = false
        group.timingFunction = CAMediaTimingFunction(controlPoints: 0.2, 0.8, 0.2, 1)
        glyph.add(group, forKey: "roll")
        if fadeOut { glyph.opacity = 0 }  // no implicit action: see `makeGlyph`
    }

    /// After a roll: drop the glyphs that rolled out and the boxes that
    /// ended empty, and clear the finished animations.
    private func tidy() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for glyph in leaving { glyph.removeFromSuperlayer() }
        leaving = []
        cells.removeAll { cell in
            guard cell.text.isEmpty else { return false }
            cell.box.removeFromSuperlayer()
            return true
        }
        for cell in cells { cell.glyph.removeAnimation(forKey: "roll") }
        CATransaction.commit()
    }

    /// Ends any roll in flight at its final state.
    private func settle() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for cell in cells {
            for sublayer in cell.box.sublayers ?? [] where sublayer !== cell.glyph {
                sublayer.removeFromSuperlayer()
            }
            cell.glyph.removeAllAnimations()
            cell.glyph.opacity = 1
            cell.box.removeAllAnimations()
        }
        cells.removeAll { cell in
            guard cell.text.isEmpty else { return false }
            cell.box.removeFromSuperlayer()
            return true
        }
        CATransaction.commit()
    }
}

/// Runs a closure on the main actor after a delay. `runLoop` is a timer
/// in the run loop's common modes, so it fires during a mouse drag or
/// scroll too; tests use their own and fire it by hand.
struct MainTimer {
    let after: @MainActor (TimeInterval, @escaping @MainActor () -> Void) -> Void

    static let runLoop = MainTimer { delay, body in
        let timer = Timer(timeInterval: delay, repeats: false) { _ in
            MainActor.assumeIsolated { body() }
        }
        RunLoop.main.add(timer, forMode: .common)
    }
}
