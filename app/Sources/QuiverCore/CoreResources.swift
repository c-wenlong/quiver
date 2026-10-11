import Foundation

/// QuiverCore's resource bundle.
///
/// SwiftPM's `Bundle.module` looks beside the executable's bundle root and
/// then in the build folder. A signed .app can't hold anything at its root,
/// so `script/build-app.sh` puts the bundle in `Contents/Resources`, which is
/// checked first. Tests and `swift run` fall through to `Bundle.module`.
enum CoreResources {
    static let bundleName = "Quiver_QuiverCore.bundle"

    static let bundle: Bundle = {
        if let resources = Bundle.main.resourceURL,
           let packaged = Bundle(url: resources.appendingPathComponent(bundleName)) {
            return packaged
        }
        return Bundle.module
    }()

    static func url(_ name: String, _ ext: String) -> URL? {
        bundle.url(forResource: name, withExtension: ext)
    }
}
