import Foundation

/// SWECore's resource bundle.
///
/// SwiftPM's `Bundle.module` looks beside the executable's bundle root and
/// then in the build folder. A signed .app can't hold anything at its root,
/// so `script/build-app.sh` puts the bundle in `Contents/Resources`, which is
/// checked first: SWE.app's own, and a copy in the widget extension's
/// (`Contents/PlugIns/SWEWidgets.appex/Contents/Resources`), since an
/// extension's `Bundle.main` is the .appex. Tests and `swift run` fall
/// through to `Bundle.module`.
enum CoreResources {
    static let bundleName = "SWE_SWECore.bundle"

    static let bundle: Bundle = resolve(main: .main, fallback: { .module })

    /// The bundle in `main`'s Contents/Resources, else `fallback`. A packaged
    /// .app or .appex never takes the fallback: `Bundle.module` would look in
    /// a build folder that exists only on the Mac that built it, and stop the
    /// process with a fatalError anywhere else. Returning `main` means a
    /// missing resource is a nil lookup (no logo, no bundled prices) rather
    /// than a crash.
    static func resolve(main: Bundle, fallback: () -> Bundle) -> Bundle {
        if let resources = main.resourceURL,
           let packaged = Bundle(url: resources.appendingPathComponent(bundleName)) {
            return packaged
        }
        if ["app", "appex"].contains(main.bundleURL.pathExtension) { return main }
        return fallback()
    }

    static func url(_ name: String, _ ext: String) -> URL? {
        bundle.url(forResource: name, withExtension: ext)
    }
}
