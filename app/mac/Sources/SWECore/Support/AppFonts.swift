import AppKit
import CoreText

/// The app's typefaces: Geist for text and Geist Mono for code and IDs,
/// bundled in SWECore's resources as their variable fonts (every weight
/// in one file, upright and italic; `Geist-OFL.txt` beside them is their
/// licence). The italics are what `NSFontManager` finds for emphasis in
/// the instruction editor; without them italic text would stay upright. They are
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
    ///
    /// Built through Core Text rather than `NSFont(descriptor:size:)`:
    /// that answered nil now and then while another thread converted a
    /// font through `NSFontManager`, and a nil here meant the system font,
    /// so a caller asking for Geist got San Francisco. Core Text always
    /// answers, and once `isAvailable` holds the family is registered, so
    /// its answer is Geist.
    public static func nsFont(size: CGFloat, weight: NSFont.Weight = .regular,
                              monospaced: Bool = false, monospacedDigits: Bool = false) -> NSFont {
        guard let family = family(monospaced: monospaced) else {
            if monospaced { return .monospacedSystemFont(ofSize: size, weight: weight) }
            return monospacedDigits
                ? .monospacedDigitSystemFont(ofSize: size, weight: weight)
                : .systemFont(ofSize: size, weight: weight)
        }
        let descriptor = NSFontDescriptor(fontAttributes: [
            .family: family, .traits: [NSFontDescriptor.TraitKey.weight: weight],
        ])
        let font = CTFontCreateWithFontDescriptor(descriptor as CTFontDescriptor, size, nil)
        guard monospacedDigits, !monospaced else { return font as NSFont }
        let tabular = NSFontDescriptor(fontAttributes: [.featureSettings: [[
            NSFontDescriptor.FeatureKey.typeIdentifier: kNumberSpacingType,
            NSFontDescriptor.FeatureKey.selectorIdentifier: kMonospacedNumbersSelector,
        ]]])
        return CTFontCreateCopyWithAttributes(font, size, nil, tabular as CTFontDescriptor) as NSFont
    }

    static let files = ["Geist", "Geist-Italic", "GeistMono", "GeistMono-Italic"]

    /// Registers every file in `files` from `bundle`. A file
    /// already registered by an earlier call, or a Geist the user installed
    /// themselves (a duplicate name), counts as available.
    static func register(from bundle: Bundle) -> Bool {
        files.allSatisfy { name in
            guard let url = bundle.url(forResource: name, withExtension: "ttf") else { return false }
            var error: Unmanaged<CFError>?
            if CTFontManagerRegisterFontsForURL(url as CFURL, .process, &error) { return true }
            let code = (error?.takeRetainedValue()).map(CFErrorGetCode)
            return code == CTFontManagerError.alreadyRegistered.rawValue
                || code == CTFontManagerError.duplicatedName.rawValue
        }
    }
}
