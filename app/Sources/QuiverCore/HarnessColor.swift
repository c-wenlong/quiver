import Foundation

/// An sRGB colour as plain components, so QuiverCore never imports a UI
/// framework; the app wraps it in SwiftUI `Color`.
public struct HarnessRGB: Sendable, Equatable {
    public var red: Double
    public var green: Double
    public var blue: Double

    public init(red: Double, green: Double, blue: Double) {
        self.red = red
        self.green = green
        self.blue = blue
    }

    /// "#RRGGBB" or "#RGB"; anything else is nil.
    public init?(hex: String) {
        var text = hex.trimmingCharacters(in: .whitespaces)
        guard text.hasPrefix("#") else { return nil }
        text.removeFirst()
        guard let value = UInt64(text, radix: 16) else { return nil }
        switch text.count {
        case 6:
            red = Double((value >> 16) & 0xff) / 255
            green = Double((value >> 8) & 0xff) / 255
            blue = Double(value & 0xff) / 255
        case 3:
            red = Double((value >> 8) & 0xf) / 15
            green = Double((value >> 4) & 0xf) / 15
            blue = Double(value & 0xf) / 15
        default:
            return nil
        }
    }

    /// Rec. 709 luma: what "reads on the dark card" measures.
    public var luminance: Double {
        0.2126 * red + 0.7152 * green + 0.0722 * blue
    }

    /// HSV saturation: 0 for a grey of any lightness.
    public var saturation: Double {
        let high = max(red, green, blue)
        let low = min(red, green, blue)
        return high == 0 ? 0 : (high - low) / high
    }

    /// Mixed toward white until its luminance clears `floor`. Dark brand
    /// marks (Cursor's black, GitHub's near-black) would vanish on
    /// Theme.card without this.
    public func lifted(toLuminance floor: Double) -> HarnessRGB {
        guard luminance < floor else { return self }
        let mix = (floor - luminance) / (1 - luminance)
        return HarnessRGB(red: red + (1 - red) * mix,
                          green: green + (1 - green) * mix,
                          blue: blue + (1 - blue) * mix)
    }
}

/// The colour a trend line draws in: the harness's own declared colour
/// when swe sends one, else a hue taken from its bundled colour logo,
/// else a stable pick from a small palette. Whatever wins is lifted to
/// read on the dark card.
public enum HarnessColor {
    /// The lowest luminance a trend colour keeps.
    public static let luminanceFloor = 0.25

    /// Ten hues that all read on the dark theme.
    public static let palette: [HarnessRGB] = [
        HarnessRGB(hex: "#5aa2ff")!, HarnessRGB(hex: "#f0a04b")!,
        HarnessRGB(hex: "#4ec9b0")!, HarnessRGB(hex: "#e06c8f")!,
        HarnessRGB(hex: "#9b7ff0")!, HarnessRGB(hex: "#8ac74f")!,
        HarnessRGB(hex: "#5ac8e0")!, HarnessRGB(hex: "#f0755f")!,
        HarnessRGB(hex: "#d9b44a")!, HarnessRGB(hex: "#c08ce0")!,
    ]

    /// The full chain: a valid `primaryColor`, then the colour logo's
    /// strongest fill, then a stable palette pick.
    public static func resolve(id: String, primaryColor: String?, colorSVG: String?) -> HarnessRGB {
        if let hex = primaryColor, let rgb = HarnessRGB(hex: hex) {
            return rgb.lifted(toLuminance: luminanceFloor)
        }
        if let svg = colorSVG, let rgb = pick(in: svg) {
            return rgb.lifted(toLuminance: luminanceFloor)
        }
        return palette[stableIndex(of: id)].lifted(toLuminance: luminanceFloor)
    }

    /// Resolve against the bundled icon: colour SVGs only, since a
    /// single-colour mark has no brand hue to offer.
    public static func resolve(id: String, primaryColor: String?) -> HarnessRGB {
        var svg: String?
        if let icon = HarnessIcons.icon(for: id), icon.isColor {
            svg = try? String(contentsOf: icon.url, encoding: .utf8)
        }
        return resolve(id: id, primaryColor: primaryColor, colorSVG: svg)
    }

    /// The most saturated non-grey fill the SVG declares, scoring
    /// saturation times luminance so a muddy dark or a pale tint loses
    /// to the brand's vivid mark. Reads `fill=` and `stop-color=` values.
    public static func pick(in svg: String) -> HarnessRGB? {
        var best: (score: Double, rgb: HarnessRGB)?
        for match in svg.matches(of: /(?:fill|stop-color)\s*=\s*"([^"]+)"/) {
            let raw = String(match.1)
            guard raw != "none", let rgb = HarnessRGB(hex: raw), rgb.saturation > 0.15 else { continue }
            let score = rgb.saturation * rgb.luminance
            if score > (best?.score ?? 0) { best = (score, rgb) }
        }
        return best?.rgb
    }

    /// FNV-1a over the id: the same harness always lands on the same
    /// palette colour, across launches (`hashValue` is per-run).
    static func stableIndex(of id: String) -> Int {
        var hash: UInt64 = 0xcbf29ce484222325
        for byte in id.utf8 {
            hash = (hash ^ UInt64(byte)) &* 0x100000001b3
        }
        return Int(hash % UInt64(palette.count))
    }
}
