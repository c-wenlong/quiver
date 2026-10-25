import AppKit
import CoreText

/// The app's typefaces: Geist for text and Geist Mono for code and IDs,
/// bundled in QuiverCore's resources as their variable fonts (every weight
/// in one file; `Geist-OFL.txt` beside them is their licence). They are
/// registered for this process on first use, so nothing has to run at
/// launch. If either file is missing or refuses to register, both lookups
/// answer nil and the app keeps the system font rather than mixing faces.
public enum AppFonts {
    public static let sans = "Geist"
    public static let mono = "Geist Mono"

    /// Whether both families are available to this process.
    public static let isAvailable: Bool = register(from: CoreResources.bundle)

    /// The family for text, or for code when `monospaced`; nil when the
    /// bundled fonts are unavailable.
    public static func family(monospaced: Bool) -> String? {
        isAvailable ? (monospaced ? mono : sans) : nil
    }

    /// An AppKit font in the app's typeface at exactly `size` points, for
    /// text that AppKit draws or measures beside a SwiftUI `Theme` font.
    /// `monospacedDigits` turns on tabular figures. Falls back to the
    /// matching system font when the bundled fonts are unavailable.
    public static func nsFont(size: CGFloat, weight: NSFont.Weight = .regular,
                              monospaced: Bool = false, monospacedDigits: Bool = false) -> NSFont {
        guard let family = family(monospaced: monospaced),
              let font = NSFont(descriptor: NSFontDescriptor(fontAttributes: [
                  .family: family, .traits: [NSFontDescriptor.TraitKey.weight: weight],
              ]), size: size)
        else {
            if monospaced { return .monospacedSystemFont(ofSize: size, weight: weight) }
            return monospacedDigits
                ? .monospacedDigitSystemFont(ofSize: size, weight: weight)
                : .systemFont(ofSize: size, weight: weight)
        }
        guard monospacedDigits, !monospaced else { return font }
        let tabular = font.fontDescriptor.addingAttributes([.featureSettings: [[
            NSFontDescriptor.FeatureKey.typeIdentifier: kNumberSpacingType,
            NSFontDescriptor.FeatureKey.selectorIdentifier: kMonospacedNumbersSelector,
        ]]])
        return NSFont(descriptor: tabular, size: size) ?? font
    }

    /// Registers `Geist.ttf` and `GeistMono.ttf` from `bundle`. A file
    /// already registered by an earlier call, or a Geist the user installed
    /// themselves (a duplicate name), counts as available.
    static func register(from bundle: Bundle) -> Bool {
        ["Geist", "GeistMono"].allSatisfy { name in
            guard let url = bundle.url(forResource: name, withExtension: "ttf") else { return false }
            var error: Unmanaged<CFError>?
            if CTFontManagerRegisterFontsForURL(url as CFURL, .process, &error) { return true }
            let code = (error?.takeRetainedValue()).map(CFErrorGetCode)
            return code == CTFontManagerError.alreadyRegistered.rawValue
                || code == CTFontManagerError.duplicatedName.rawValue
        }
    }
}
