import Foundation

/// Harness logos, bundled from app/script/import-harness-icons.py. A logo
/// that has brand colours keeps them (`harness-<id>-color.svg`), as
/// TokenTracker shows it; the rest are single-colour marks
/// (`harness-<id>.svg`) for the app to draw in the text colour. Where each
/// came from is in `harness-icons.tsv` beside them.
public enum HarnessIcons {
    public struct Icon: Equatable, Sendable {
        public var url: URL
        /// Draw as is; otherwise draw as a template in the text colour.
        public var isColor: Bool
    }

    /// Other names the same harness goes by.
    static let aliases = ["claude-code": "claude", "github-copilot": "copilot", "factory": "droid",
                          "qwen": "qwen-code", "mistral": "mistral-vibe", "codebuff": "freebuff"]

    /// Ids with a bundled icon, sorted.
    public static let available: [String] = {
        let urls = CoreResources.bundle.urls(forResourcesWithExtension: "svg", subdirectory: nil) ?? []
        let ids = urls.map(\.lastPathComponent)
            .filter { $0.hasPrefix("harness-") }
            .map { name in
                var id = String(name.dropFirst("harness-".count).dropLast(".svg".count))
                if id.hasSuffix("-color") { id.removeLast("-color".count) }
                return id
            }
        return Array(Set(ids)).sorted()
    }()

    public static func icon(for harness: String) -> Icon? {
        let id = aliases[harness.lowercased()] ?? harness.lowercased()
        if let url = CoreResources.url("harness-\(id)-color", "svg") { return Icon(url: url, isColor: true) }
        if let url = CoreResources.url("harness-\(id)", "svg") { return Icon(url: url, isColor: false) }
        return nil
    }
}
