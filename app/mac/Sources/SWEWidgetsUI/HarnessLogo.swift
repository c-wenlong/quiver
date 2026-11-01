import AppKit
import SWECore
import SwiftUI
import WidgetKit

/// A harness's logo, as the app's `HarnessIcon` draws it: brand colours
/// where the logo has them, otherwise the single-colour mark in the text
/// colour. A harness with no bundled logo draws nothing, so the title beside
/// it carries the name alone. The logos come from SWECore's resource
/// bundle, which build-app.sh copies into the extension's own Resources.
public struct HarnessLogo: View {
    public var harness: String
    public var size: CGFloat = 12

    public init(harness: String, size: CGFloat = 12) {
        self.harness = harness
        self.size = size
    }

    public var body: some View {
        if let logo = HarnessLogoImage.load(harness, size: size) {
            Group {
                let image = Image(decorative: logo.image, scale: HarnessLogoImage.scale)
                    .resizable()
                    .interpolation(.high)
                if logo.isColor {
                    // Accented mode would otherwise wash the brand colours out.
                    image.widgetAccentedRenderingMode(.fullColor)
                } else {
                    image.renderingMode(.template).foregroundStyle(Palette.textPrimary)
                }
            }
            .scaledToFit()
            .frame(width: size, height: size)
            .accessibilityHidden(true)
        }
    }
}

/// Logos drawn to bitmaps once per harness and size. WidgetKit archives
/// the view to draw it in another process, so a plain bitmap is the safe
/// thing to hand it, where an SVG-backed NSImage may not survive the trip.
enum HarnessLogoImage {
    struct Logo {
        var image: CGImage
        var isColor: Bool
    }

    /// Pixels per point: sharp on a Retina display with room to spare.
    static let scale: CGFloat = 3

    @MainActor private static var cache: [String: Logo] = [:]

    @MainActor static func load(_ harness: String, size: CGFloat) -> Logo? {
        let key = "\(harness)@\(size)"
        if let cached = cache[key] { return cached }
        guard let icon = HarnessIcons.logo(for: harness), let svg = NSImage(contentsOf: icon.url),
              let image = rasterize(svg, side: Int((size * scale).rounded(.up))) else { return nil }
        let logo = Logo(image: image, isColor: icon.isColor)
        cache[key] = logo
        return logo
    }

    /// `image` fitted, centred, into a transparent square `side` pixels wide.
    static func rasterize(_ image: NSImage, side: Int) -> CGImage? {
        guard side > 0, image.size.width > 0, image.size.height > 0,
              let space = CGColorSpace(name: CGColorSpace.sRGB),
              let context = CGContext(data: nil, width: side, height: side, bitsPerComponent: 8, bytesPerRow: 0,
                                      space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        else { return nil }
        let box = CGFloat(side)
        let fit = min(box / image.size.width, box / image.size.height)
        let drawn = CGSize(width: image.size.width * fit, height: image.size.height * fit)
        let rect = CGRect(x: (box - drawn.width) / 2, y: (box - drawn.height) / 2,
                          width: drawn.width, height: drawn.height)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
        image.draw(in: rect)
        NSGraphicsContext.restoreGraphicsState()
        return context.makeImage()
    }
}
