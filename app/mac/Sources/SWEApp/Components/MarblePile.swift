import AppKit
import SWECore
import SpriteKit
import SwiftUI

/// The Overview total card's backdrop: one marble per unit of spend
/// (`MarbleScale`), each in its harness's colour and stamped with its logo,
/// dropped in from the top and piled up behind the figures. Marbles near
/// the figures fade so the text stays readable; a focused harness fades out
/// every other harness's marbles.
///
/// Only the scale goes through SwiftUI. The focused harness and the
/// figures' frame change on every hover and on every frame of the figures'
/// roll, so the card hands them to `MarbleScene.shared` directly: passing
/// them in here would rebuild this view, and the card around it, about 60
/// times a second while the numbers slide.
struct MarblePile: View, @MainActor Equatable {
    var scale: MarbleScale

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        SpriteView(scene: MarbleScene.shared, preferredFramesPerSecond: 120, options: [.allowsTransparency])
            .allowsHitTesting(false)
            .accessibilityHidden(true)
            .onAppear { MarbleScene.shared.show(scale, animated: !reduceMotion) }
            .onChange(of: scale) { MarbleScene.shared.show(scale, animated: !reduceMotion) }
    }

    static func == (lhs: MarblePile, rhs: MarblePile) -> Bool { lhs.scale == rhs.scale }
}

/// One scene for the app's life, so the pile pours once per launch and
/// leaving the Overview and coming back finds it as it was. A new scale
/// (another period, a rescan with new usage, a resize) changes only the
/// difference: new marbles drop in from the top and spare ones fade out.
@MainActor
final class MarbleScene: SKScene {
    static let shared = MarbleScene()

    static let radius: CGFloat = 19
    /// A marble right behind the figures, as a fraction of full opacity.
    static let behindText: CGFloat = 0.14
    /// How far from the figures a marble is back to full opacity.
    static let fadeDistance: CGFloat = 56
    /// Seconds between two marbles of a pour.
    static let dropInterval: TimeInterval = 0.02
    /// How fast a marble's opacity follows its target: the time constant of
    /// an exponential ease, so a focus change is about 95% done in 150ms at
    /// any frame rate.
    static let fadeTime: TimeInterval = 0.05

    /// Whether a pour from empty is running; the logos wait for it.
    let pour = PourState()
    /// The pill standing in for the logos while a pour runs.
    private weak var pourBar: PourBarView?
    /// The pour's harnesses in the logos' order, and its marble counts.
    private var pourOrder: [String] = []
    private var pourTarget: [String: Int] = [:]
    private var pourLanded: [String: Int] = [:]
    /// When the last marble of the pour dropped, for a time limit on
    /// waiting for them all to land.
    private var pourDropped: TimeInterval?
    /// The pill is folding into the logos; the pour is over bar that.
    private var pourEnding = false

    var focus: String? { didSet { if focus != oldValue { wake() } } }
    /// In SwiftUI's top-left coordinates.
    var clearRect: CGRect = .zero { didSet { if clearRect != oldValue { wake() } } }

    private var marbles: [Marble] = []
    /// Harnesses waiting to drop, popped from the end.
    private var queue: [String] = []
    private var lastDrop: TimeInterval = 0
    private var lastUpdate: TimeInterval = 0
    /// Set on wake, so the next update doesn't count the paused time.
    private var resumed = true
    private var textures: [String: SKTexture] = [:]
    private lazy var shade = MarbleArt.shading(radius: Self.radius).map(SKTexture.init(cgImage:)) ?? SKTexture()
    /// Frames in a row with nothing moving or fading; the view pauses after
    /// a few so a settled pile costs nothing.
    private var quietFrames = 0

    override init() {
        super.init(size: CGSize(width: 1, height: 1))
        scaleMode = .resizeFill
        backgroundColor = .clear
    }

    required init?(coder: NSCoder) { nil }

    func show(_ scale: MarbleScale, animated: Bool) {
        let fromEmpty = marbles.isEmpty && queue.isEmpty
        var have: [String: Int] = [:]
        for marble in marbles { have[marble.harness, default: 0] += 1 }
        for harness in queue { have[harness, default: 0] += 1 }
        let want = Dictionary(scale.counts.map { ($0.harness, $0.count) }, uniquingKeysWith: +)
        for harness in Set(have.keys).union(want.keys) {
            let difference = (want[harness] ?? 0) - (have[harness] ?? 0)
            if difference > 0 {
                queue.append(contentsOf: repeatElement(harness, count: difference))
            } else if difference < 0 {
                remove(-difference, of: harness)
            }
        }
        queue.shuffle()
        if animated, fromEmpty ? !queue.isEmpty : pour.pouring {
            // A pour from empty, or the scale changing during one (the card
            // settling its size): the pill fills to the new counts.
            // Recounted from the pile, so marbles a smaller scale just took
            // out stop counting.
            pourLanded = Dictionary(grouping: marbles.filter(\.landed), by: \.harness).mapValues(\.count)
            let restarting = fromEmpty || pourEnding
            pourEnding = false
            pourOrder = scale.counts.map(\.harness)
            pourTarget = want
            if !queue.isEmpty { pourDropped = nil }
            pour.pouring = true
            // A fresh pour, or one restarted mid-fold: the pill starts over
            // and ignores the fold that was running.
            if restarting { pourBar?.begin(order: pourOrder) }
            fillBar()
        } else if !animated || fromEmpty {
            pour.pouring = false
            pourBar?.hide()
        }
        if !animated {
            // Reduce motion: loose rows on top of the pile, a short settle
            // instead of a fall from the top.
            let r = Self.radius
            let floor = (marbles.map(\.position.y).max() ?? -r) + 2 * r
            let perRow = max(Int(max(size.width, 2 * r) / (2 * r)), 1)
            for (index, harness) in queue.reversed().enumerated() {
                let row = index / perRow, column = index % perRow
                add(harness, at: CGPoint(x: min(r + CGFloat(column) * 2 * r + CGFloat(row % 2) * r, size.width - r),
                                         y: floor + CGFloat(row) * 1.75 * r))
            }
            queue.removeAll()
        }
        wake()
    }

    /// Takes queued ones first, then the highest in the pile.
    private func remove(_ count: Int, of harness: String) {
        var left = count
        while left > 0, let index = queue.lastIndex(of: harness) {
            queue.remove(at: index)
            left -= 1
        }
        guard left > 0 else { return }
        let leaving = marbles.filter { $0.harness == harness }
            .sorted { $0.position.y > $1.position.y }
            .prefix(left)
        for marble in leaving {
            marbles.removeAll { $0 === marble }
            marble.physicsBody = nil
            marble.run(.sequence([.group([.fadeOut(withDuration: 0.25), .scale(to: 0.4, duration: 0.25)]),
                                  .removeFromParent()]))
        }
    }

    /// Drops it from just above the top, or places it at `position`.
    private func add(_ harness: String, at position: CGPoint? = nil) {
        let r = Self.radius
        let texture = textures[harness] ?? {
            let made = MarbleArt.marble(harness: harness, radius: r).map(SKTexture.init(cgImage:)) ?? SKTexture()
            textures[harness] = made
            return made
        }()
        let marble = Marble(harness: harness, texture: texture, shade: shade, radius: r)
        let width = max(size.width, 2 * r)
        marble.position = position
            ?? CGPoint(x: .random(in: r...(width - r)), y: size.height + r + .random(in: 0...40))
        marble.physicsBody?.angularVelocity = .random(in: -3...3)
        // A placed marble fades in where it lands.
        marble.alpha = position == nil ? 1 : 0
        addChild(marble)
        marbles.append(marble)
    }

    override func didChangeSize(_ oldSize: CGSize) {
        super.didChangeSize(oldSize)
        // Floor and sides only, reaching well above the top so marbles
        // can drop in from out of sight.
        let path = CGMutablePath()
        path.move(to: CGPoint(x: 0, y: size.height + 2000))
        path.addLine(to: .zero)
        path.addLine(to: CGPoint(x: size.width, y: 0))
        path.addLine(to: CGPoint(x: size.width, y: size.height + 2000))
        physicsBody = SKPhysicsBody(edgeChainFrom: path)
        let r = Self.radius
        for marble in marbles where marble.position.x > size.width - r {
            marble.position.x = max(r, size.width - r)
        }
        wake()
    }

    override func update(_ currentTime: TimeInterval) {
        if !queue.isEmpty, currentTime - lastDrop >= Self.dropInterval, let harness = queue.popLast() {
            lastDrop = currentTime
            add(harness)
        }
        // Also when a smaller scale emptied the queue by taking marbles out.
        if pour.pouring, !pourEnding, queue.isEmpty, pourDropped == nil { pourDropped = currentTime }
        // The first frame after a pause would count the whole pause, so it
        // takes one 60fps frame; after that the real time, even under load.
        let elapsed = resumed ? 1.0 / 60 : max(currentTime - lastUpdate, 0)
        resumed = false
        lastUpdate = currentTime
        let ease = CGFloat(1 - exp(-elapsed / Self.fadeTime))
        let clear = CGRect(x: clearRect.minX, y: size.height - clearRect.maxY,
                           width: clearRect.width, height: clearRect.height)
        var busy = !queue.isEmpty || pour.pouring
        var landedNow = false
        for marble in marbles {
            let p = marble.position
            // Landed: below the top and no longer falling fast, i.e. it has
            // hit the floor or the pile. (SpriteKit's y points up.)
            if !marble.landed, p.y < size.height - Self.radius,
               let body = marble.physicsBody, body.velocity.dy > -30 {
                marble.landed = true
                if pour.pouring {
                    pourLanded[marble.harness, default: 0] += 1
                    landedNow = true
                }
            }
            var target: CGFloat = 1
            if !clear.isEmpty {
                let dx = max(clear.minX - p.x, 0, p.x - clear.maxX)
                let dy = max(clear.minY - p.y, 0, p.y - clear.maxY)
                target = Self.behindText + (1 - Self.behindText) * min(1, hypot(dx, dy) / Self.fadeDistance)
            }
            if let focus, focus != marble.harness { target = 0 }
            let step = target - marble.alpha
            if abs(step) > 0.005 {
                marble.alpha += step * ease
                busy = true
            } else {
                marble.alpha = target
            }
            if let body = marble.physicsBody,
               hypot(body.velocity.dx, body.velocity.dy) > 2 || abs(body.angularVelocity) > 0.2 {
                busy = true
            }
        }
        if landedNow { fillBar() }
        if pour.pouring, !pourEnding, queue.isEmpty, let dropped = pourDropped,
           marbles.allSatisfy(\.landed) || currentTime - dropped > 2.5 {
            finishPour()
        }
        quietFrames = busy ? 0 : quietFrames + 1
        if quietFrames > 30 { view?.isPaused = true }
    }

    override func didSimulatePhysics() {
        // The texture turns with the marble; the light stays top left.
        for marble in marbles { marble.shade.zRotation = -marble.zRotation }
    }

    func attach(_ bar: PourBarView) {
        pourBar = bar
        if pour.pouring, !pourOrder.isEmpty {
            bar.begin(order: pourOrder)
            fillBar()
        } else {
            bar.hide()
        }
    }

    func detach(_ bar: PourBarView) {
        if pourBar === bar { pourBar = nil }
    }

    private func fillBar() {
        pourBar?.fill(order: pourOrder, landed: pourLanded, total: pourTarget.values.reduce(0, +))
    }

    /// Every marble of the pour is down: the pill folds into the logos, which
    /// show once the circles are in place.
    private func finishPour() {
        pourDropped = nil
        pourLanded = pourTarget
        guard let bar = pourBar, bar.window != nil else {
            pour.pouring = false
            return
        }
        pourEnding = true
        fillBar()
        bar.split { [weak self] in
            guard let self, self.pourEnding else { return }
            self.pourEnding = false
            self.pour.pouring = false
        }
    }

    private func wake() {
        quietFrames = 0
        if view?.isPaused ?? true { resumed = true }
        view?.isPaused = false
    }
}

private final class Marble: SKSpriteNode {
    let harness: String
    /// Has hit the floor or the pile since it dropped.
    var landed = false
    let shade: SKSpriteNode

    init(harness: String, texture: SKTexture, shade: SKTexture, radius: CGFloat) {
        self.harness = harness
        let size = CGSize(width: 2 * radius, height: 2 * radius)
        self.shade = SKSpriteNode(texture: shade, size: size)
        super.init(texture: texture, color: .clear, size: size)
        addChild(self.shade)
        let body = SKPhysicsBody(circleOfRadius: radius)
        body.restitution = 0.3
        body.friction = 0.2
        body.linearDamping = 0.1
        body.angularDamping = 0.4
        physicsBody = body
    }

    required init?(coder: NSCoder) { nil }
}

/// The marble images, drawn once per harness. The marble's own texture
/// (colour, glass swirls, grain, the logo in white) turns as it rolls; the
/// shading (darker rim, highlight at top left) is a second image that
/// doesn't, so every marble reads lit from the same side.
enum MarbleArt {
    /// Pixels per point: sharp on Retina without a texture per screen.
    static let scale: CGFloat = 2

    static func marble(harness: String, radius r: CGFloat) -> CGImage? {
        guard let ctx = context(radius: r) else { return nil }
        let rect = CGRect(x: -r, y: -r, width: 2 * r, height: 2 * r)
        ctx.addEllipse(in: rect)
        ctx.clip()
        let rgb = HarnessColor.of(harness)
        ctx.setFillColor(CGColor(srgbRed: rgb.red, green: rgb.green, blue: rgb.blue, alpha: 1))
        ctx.fill(rect)

        var random = SeededRandom(harness)
        for index in 0..<9 {
            ctx.setStrokeColor(index.isMultiple(of: 2) ? CGColor(gray: 0, alpha: 0.14) : CGColor(gray: 1, alpha: 0.16))
            ctx.setLineWidth(1 + random.next() * 3.5)
            let angle = random.next() * 2 * .pi
            ctx.move(to: CGPoint(x: cos(angle) * r * 1.2, y: sin(angle) * r * 1.2))
            ctx.addCurve(to: CGPoint(x: cos(angle + 2.5) * r * 1.2, y: sin(angle + 2.5) * r * 1.2),
                         control1: CGPoint(x: (random.next() - 0.5) * 2 * r, y: (random.next() - 0.5) * 2 * r),
                         control2: CGPoint(x: (random.next() - 0.5) * 2 * r, y: (random.next() - 0.5) * 2 * r))
            ctx.strokePath()
        }
        for _ in 0..<380 {
            ctx.setFillColor(CGColor(gray: random.next() > 0.5 ? 1 : 0, alpha: 0.09))
            ctx.fill(CGRect(x: (random.next() - 0.5) * 2 * r, y: (random.next() - 0.5) * 2 * r, width: 0.7, height: 0.7))
        }

        if let icon = HarnessIcons.logo(for: harness), let image = NSImage(contentsOf: icon.url) {
            let side = r * 1.12
            let logo = CGRect(x: -side / 2, y: -side / 2, width: side, height: side)
            // A faint dark copy a hair lower reads as the logo pressed in.
            stamp(image, in: logo.offsetBy(dx: 0, dy: -0.9), color: CGColor(gray: 0, alpha: 0.28), on: ctx)
            stamp(image, in: logo, color: CGColor(gray: 1, alpha: 0.95), on: ctx)
        }
        return ctx.makeImage()
    }

    static func shading(radius r: CGFloat) -> CGImage? {
        guard let ctx = context(radius: r), let space = CGColorSpace(name: CGColorSpace.sRGB) else { return nil }
        ctx.addEllipse(in: CGRect(x: -r, y: -r, width: 2 * r, height: 2 * r))
        ctx.clip()
        let body = CGGradient(colorsSpace: space,
                              colors: [CGColor(gray: 1, alpha: 0.22), CGColor(gray: 0, alpha: 0),
                                       CGColor(gray: 0, alpha: 0.28), CGColor(gray: 0, alpha: 0.55)] as CFArray,
                              locations: [0, 0.45, 0.85, 1])
        if let body {
            ctx.drawRadialGradient(body, startCenter: CGPoint(x: -0.35 * r, y: 0.4 * r), startRadius: 0.05 * r,
                                   endCenter: .zero, endRadius: r, options: [.drawsAfterEndLocation])
        }
        let glint = CGGradient(colorsSpace: space,
                               colors: [CGColor(gray: 1, alpha: 0.85), CGColor(gray: 1, alpha: 0)] as CFArray,
                               locations: [0, 1])
        if let glint {
            ctx.saveGState()
            ctx.translateBy(x: -0.38 * r, y: 0.46 * r)
            ctx.rotate(by: 0.6)
            ctx.scaleBy(x: 1, y: 0.66)
            ctx.drawRadialGradient(glint, startCenter: .zero, startRadius: 0, endCenter: .zero, endRadius: 0.36 * r,
                                   options: [])
            ctx.restoreGState()
        }
        ctx.setStrokeColor(CGColor(gray: 1, alpha: 0.18))
        ctx.setLineWidth(1.4)
        ctx.addArc(center: .zero, radius: 0.86 * r, startAngle: -1.4, endAngle: -0.2, clockwise: false)
        ctx.strokePath()
        return ctx.makeImage()
    }

    /// A square bitmap with the origin at its centre, in points.
    private static func context(radius r: CGFloat) -> CGContext? {
        let pixels = Int((2 * r * scale).rounded(.up))
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let ctx = CGContext(data: nil, width: pixels, height: pixels, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        else { return nil }
        ctx.scaleBy(x: scale, y: scale)
        ctx.translateBy(x: r, y: r)
        return ctx
    }

    /// The logo's shape filled with one colour, whatever colours it has.
    private static func stamp(_ image: NSImage, in rect: CGRect, color: CGColor, on ctx: CGContext) {
        ctx.saveGState()
        ctx.beginTransparencyLayer(auxiliaryInfo: nil)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: false)
        image.draw(in: rect)
        NSGraphicsContext.restoreGraphicsState()
        ctx.setBlendMode(.sourceIn)
        ctx.setFillColor(color)
        ctx.fill(rect.insetBy(dx: -1, dy: -1))
        ctx.endTransparencyLayer()
        ctx.restoreGState()
    }
}

/// The same swirls for a harness on every launch: `hashValue` is seeded
/// per process, so the seed is a plain FNV-1a of the id.
private struct SeededRandom {
    var state: UInt64

    init(_ text: String) {
        var hash: UInt64 = 0xcbf2_9ce4_8422_2325
        for byte in text.utf8 { hash = (hash ^ UInt64(byte)) &* 0x100_0000_01b3 }
        state = hash | 1
    }

    mutating func next() -> CGFloat {
        state ^= state << 13
        state ^= state >> 7
        state ^= state << 17
        return CGFloat(state % 1_000_000) / 1_000_000
    }
}
