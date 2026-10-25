import AppKit
import Foundation

/// Brand marks for the apps Settings offers that may not be installed —
/// the terminal and editor tabs draw them greyed, instead of a generic
/// dashed placeholder. Bundled as `tool-<id>.svg` (template-drawn, like
/// the mono harness marks) or `tool-<id>.png` where only a raster exists;
/// each file's comment says where it came from.
public enum ToolIcons {
    /// `id`s that draw a differently-named bundled mark: Devin's editor
    /// entry reuses its harness logo.
    static let resourceNames = ["devin": "harness-devin"]

    /// The bundled mark for `id`, nil when there is none. SVGs are left as
    /// template images so a disabled tab dims them like its title.
    public static func image(for id: String) -> NSImage? {
        let base = resourceNames[id] ?? "tool-\(id)"
        if let url = CoreResources.url(base, "svg"), let image = NSImage(contentsOf: url) {
            image.isTemplate = true
            return image
        }
        if let url = CoreResources.url(base, "png"), let image = NSImage(contentsOf: url) {
            return image
        }
        return nil
    }
}
