// Draws the DMG window's background: the Quiver wordmark, an arrow from
// the app's slot to the Applications slot, and the app icon's five glyphs
// scattered faintly around the edges. make-dmg.sh renders it at 1x and 2x
// and joins the two into one HiDPI TIFF for Finder.
//
//   swift script/make-dmg-background.swift <scale> <out.png>
//
// The geometry has to match dmg-settings.py: a 640x400pt window with the
// app centred at (170, 200) and Applications at (470, 200).
import AppKit
import Foundation

let args = CommandLine.arguments
guard args.count == 3, let scale = Double(args[1]), scale > 0 else {
    FileHandle.standardError.write(Data("usage: make-dmg-background <scale> <out.png>\n".utf8))
    exit(2)
}

let width = 640.0, height = 400.0
let appSlot = CGPoint(x: 170, y: 200), applicationsSlot = CGPoint(x: 470, y: 200)

// TokenTracker's neutral ramp for the page, the icon's own colours for the glyphs.
func rgb(_ hex: UInt32, _ alpha: Double = 1) -> NSColor {
    NSColor(srgbRed: Double((hex >> 16) & 0xff) / 255, green: Double((hex >> 8) & 0xff) / 255,
            blue: Double(hex & 0xff) / 255, alpha: alpha)
}
let page = rgb(0xfafafa), ink = rgb(0x171717), muted = rgb(0x737373), arrowInk = rgb(0xa3a3a3)
let red = 0xff1d25 as UInt32, green = 0x00c832 as UInt32, blue = 0x008cff as UInt32
let yellow = 0xffcc00 as UInt32, purple = 0xa30dff as UInt32

let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(width * scale), pixelsHigh: Int(height * scale),
                           bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                           colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
let context = NSGraphicsContext(bitmapImageRep: rep)!
// Flip to Finder's top-left origin so every coordinate here reads the
// same as the icon positions in dmg-settings.py.
let flipped = NSGraphicsContext(cgContext: context.cgContext, flipped: true)
flipped.cgContext.translateBy(x: 0, y: height * scale)
flipped.cgContext.scaleBy(x: scale, y: -scale)
NSGraphicsContext.current = flipped
flipped.imageInterpolation = .high

page.setFill()
NSRect(x: 0, y: 0, width: width, height: height).fill()

// One glyph from the icon, drawn as a stroke around `centre`, `size` wide.
func glyph(_ kind: Int, at centre: CGPoint, size: Double, angle: Double, color: UInt32, alpha: Double) {
    let gc = NSGraphicsContext.current!.cgContext
    gc.saveGState()
    gc.translateBy(x: centre.x, y: centre.y)
    gc.rotate(by: angle * .pi / 180)
    let s = size / 2
    let path = NSBezierPath()
    path.lineWidth = size * 0.2
    path.lineCapStyle = .round
    path.lineJoinStyle = .round
    rgb(color, alpha).set()
    switch kind {
    case 0:  // >
        path.move(to: CGPoint(x: -s * 0.6, y: -s))
        path.line(to: CGPoint(x: s * 0.6, y: 0))
        path.line(to: CGPoint(x: -s * 0.6, y: s))
        path.stroke()
    case 1:  // square
        path.appendRoundedRect(NSRect(x: -s * 0.8, y: -s * 0.8, width: s * 1.6, height: s * 1.6),
                               xRadius: s * 0.12, yRadius: s * 0.12)
        path.stroke()
    case 2:  // ^
        path.move(to: CGPoint(x: -s * 0.8, y: s * 0.6))
        path.line(to: CGPoint(x: 0, y: -s * 0.6))
        path.line(to: CGPoint(x: s * 0.8, y: s * 0.6))
        path.stroke()
    case 3:  // ring
        path.appendOval(in: NSRect(x: -s * 0.8, y: -s * 0.8, width: s * 1.6, height: s * 1.6))
        path.stroke()
    default:  // { }
        let text = NSAttributedString(string: "{ }", attributes: [
            .font: NSFont.systemFont(ofSize: size * 1.1, weight: .heavy),
            .foregroundColor: rgb(color, alpha),
        ])
        let bounds = text.size()
        text.draw(at: CGPoint(x: -bounds.width / 2, y: -bounds.height / 2))
    }
    gc.restoreGState()
}

// Scattered along the top and bottom bands, clear of the icons, their
// labels and the wordmark. The bottom band stays above y=360 so a Finder
// path bar, which covers the window's last 28pt, does not clip it.
let glyphs: [(Int, CGPoint, Double, Double, UInt32)] = [
    (0, CGPoint(x: 48, y: 52), 26, -8, red),
    (1, CGPoint(x: 116, y: 96), 18, 12, green),
    (3, CGPoint(x: 590, y: 60), 24, 0, yellow),
    (2, CGPoint(x: 522, y: 104), 20, 10, blue),
    (4, CGPoint(x: 66, y: 346), 22, -6, purple),
    (3, CGPoint(x: 150, y: 322), 14, 0, yellow),
    (1, CGPoint(x: 592, y: 338), 22, -14, green),
    (0, CGPoint(x: 498, y: 350), 16, 6, red),
    (2, CGPoint(x: 330, y: 352), 14, -4, blue),
]
for (kind, centre, size, angle, color) in glyphs {
    glyph(kind, at: centre, size: size, angle: angle, color: color, alpha: 0.22)
}

func drawCentred(_ string: String, y: Double, font: NSFont, color: NSColor, kern: Double = 0) {
    let text = NSAttributedString(string: string, attributes: [.font: font, .foregroundColor: color, .kern: kern])
    let size = text.size()
    text.draw(at: CGPoint(x: (width - size.width) / 2, y: y - size.height / 2))
}
drawCentred("Quiver", y: 62, font: .systemFont(ofSize: 30, weight: .semibold), color: ink, kern: -0.4)

// The arrow arcs over the gap between the two icons, as in Docker's.
let start = CGPoint(x: appSlot.x + 88, y: appSlot.y - 4)
let end = CGPoint(x: applicationsSlot.x - 88, y: applicationsSlot.y - 4)
let control = CGPoint(x: (start.x + end.x) / 2, y: appSlot.y - 38)
let arc = NSBezierPath()
arc.lineWidth = 3
arc.lineCapStyle = .round
arc.lineJoinStyle = .round
arc.move(to: start)
arc.curve(to: end, controlPoint1: control, controlPoint2: control)
arrowInk.set()
arc.stroke()
// The head points along the curve's tangent at its end.
let heading = atan2(end.y - control.y, end.x - control.x)
let head = NSBezierPath()
head.lineWidth = 3
head.lineCapStyle = .round
head.lineJoinStyle = .round
for side in [-1.0, 1.0] {
    let a = heading + .pi + side * 0.5
    head.move(to: end)
    head.line(to: CGPoint(x: end.x + 12 * cos(a), y: end.y + 12 * sin(a)))
}
head.stroke()

drawCentred("Drag Quiver to Applications to install", y: 318, font: .systemFont(ofSize: 13), color: muted)

NSGraphicsContext.current = nil
// Points, not pixels: the 2x render then carries 144 dpi, which is how
// tiffutil -cathidpicheck and Finder tell the two apart.
rep.size = NSSize(width: width, height: height)
try rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: args[2]))
