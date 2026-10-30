import AppKit
import Testing
@testable import QuiverCore

/// The bundled Geist fonts register and resolve the weights the app uses,
/// so `Theme` never silently falls back to the system font.
@Suite struct AppFontsTests {
    @Test func theBundledFontsRegister() {
        #expect(AppFonts.isAvailable)
        #expect(AppFonts.family(monospaced: false) == "Geist")
        #expect(AppFonts.family(monospaced: true) == "Geist Mono")
        // A second registration of the same files still counts.
        #expect(AppFonts.register(from: CoreResources.bundle))
    }

    @Test func aBundleWithoutTheFontsIsUnavailable() {
        #expect(!AppFonts.register(from: Bundle(for: NSObject.self)))
    }

    @Test(arguments: [(NSFont.Weight.regular, "Geist-Regular"), (.medium, "Geist-Medium"),
                      (.semibold, "Geist-SemiBold"), (.bold, "Geist-Bold")])
    func everyWeightResolves(weight: NSFont.Weight, name: String) {
        #expect(AppFonts.nsFont(size: 14, weight: weight).fontName == name)
    }

    @Test func monospacedIsGeistMono() {
        let font = AppFonts.nsFont(size: 12, monospaced: true)
        #expect(font.familyName == "Geist Mono")
        #expect(font.pointSize == 12)
    }

    /// Geist's tabular 7 is a hair wider than its other digits, so the
    /// check is that the spread shrinks to a fraction of a point.
    @Test func monospacedDigitsAreTabular() {
        func spread(_ font: NSFont) -> CGFloat {
            let widths = "0123456789".map { (String($0) as NSString).size(withAttributes: [.font: font]).width }
            return widths.max()! - widths.min()!
        }
        let tabular = AppFonts.nsFont(size: 20, weight: .bold, monospacedDigits: true)
        #expect(tabular.familyName == "Geist")
        // The tabular copy keeps the weight and size asked for.
        #expect(tabular.fontName == "Geist-Bold")
        #expect(tabular.pointSize == 20)
        #expect(spread(tabular) < 0.5)
        #expect(spread(AppFonts.nsFont(size: 20, weight: .bold)) > 3)
    }
}
