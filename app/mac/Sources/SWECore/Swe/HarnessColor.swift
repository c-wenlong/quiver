import Foundation
import os

/// An sRGB colour as plain components, so SWECore never imports a UI
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

    /// "#RRGGBB", for surfaces that can't resolve a colour themselves.
    public var hex: String {
        String(format: "#%02x%02x%02x",
               Int((red * 255).rounded()), Int((green * 255).rounded()), Int((blue * 255).rounded()))
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

/// The one colour each harness draws in, on every page: the Overview
/// marble pile, the menu bar donut, Calendar blocks, Compute trend lines,
/// widgets and the Models chart's Anthropic bars. A hand-picked hue for
/// the harnesses in `fixed`; anything else takes a hue from its bundled
/// colour logo, else a stable palette pick, lifted to read on the dark
/// card. Change `fixed` and every page follows.
public enum HarnessColor {
    /// The lowest luminance a derived colour keeps.
    public static let luminanceFloor = 0.25

    /// The hand-picked palette, chosen together so neighbouring harnesses
    /// stay apart on the dark and light surfaces. It wins over swe's
    /// `primary_color`, which would otherwise give Calendar and Compute a
    /// second colour for the same harness.
    public static let fixed: [String: HarnessRGB] = [
        "claude": HarnessRGB(hex: "#d97757")!,
        "codex": HarnessRGB(hex: "#3b7ff0")!,
        "opencode": HarnessRGB(hex: "#139f8b")!,
        "devin": HarnessRGB(hex: "#c4860f")!,
        "cursor": HarnessRGB(hex: "#c08ce0")!,
        "grok-bot": HarnessRGB(hex: "#8a73e8")!,
        "pi": HarnessRGB(hex: "#5fb3d9")!,
        "droid": HarnessRGB(hex: "#e06c8f")!,
        "freebuff": HarnessRGB(hex: "#8ac74f")!,
        "cline": HarnessRGB(hex: "#9aa0aa")!,
        "gemini": HarnessRGB(hex: "#e3b51e")!,
    ]

    /// Ten hues that all read on the dark theme, for harnesses outside
    /// `fixed` with no colour logo.
    public static let palette: [HarnessRGB] = [
        HarnessRGB(hex: "#5aa2ff")!, HarnessRGB(hex: "#f0a04b")!,
        HarnessRGB(hex: "#4ec9b0")!, HarnessRGB(hex: "#e06c8f")!,
        HarnessRGB(hex: "#9b7ff0")!, HarnessRGB(hex: "#8ac74f")!,
        HarnessRGB(hex: "#5ac8e0")!, HarnessRGB(hex: "#f0755f")!,
        HarnessRGB(hex: "#d9b44a")!, HarnessRGB(hex: "#c08ce0")!,
    ]

    private static let cache = OSAllocatedUnfairLock<[String: HarnessRGB]>(initialState: [:])

    /// The harness's colour. Cached: a harness outside `fixed` reads its
    /// logo's SVG off disk, and views ask on every body pass.
    public static func of(_ id: String) -> HarnessRGB {
        if let rgb = fixed[id] { return rgb }
        if let rgb = cache.withLock({ $0[id] }) { return rgb }
        var svg: String?
        if let icon = HarnessIcons.icon(for: id), icon.isColor {
            svg = try? String(contentsOf: icon.url, encoding: .utf8)
        }
        let rgb = resolve(id: id, colorSVG: svg)
        cache.withLock { $0[id] = rgb }
        return rgb
    }

    /// The chain with the logo passed in: `fixed`, then the colour
    /// logo's strongest fill, then a stable palette pick.
    public static func resolve(id: String, colorSVG: String?) -> HarnessRGB {
        if let rgb = fixed[id] { return rgb }
        if let svg = colorSVG, let rgb = pick(in: svg) {
            return rgb.lifted(toLuminance: luminanceFloor)
        }
        return palette[stableIndex(of: id)].lifted(toLuminance: luminanceFloor)
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
