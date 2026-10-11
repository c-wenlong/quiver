import Foundation

/// Harness logos, bundled as single-colour SVGs (`harness-<id>.svg`) for the
/// app to draw as template images in the harness colour. Where each came
/// from is in `harness-icons.tsv` beside them; see
/// app/script/import-harness-icons.py.
public enum HarnessIcons {
    /// Other names the same harness goes by.
    static let aliases = ["claude-code": "claude", "github-copilot": "copilot", "factory": "droid",
                          "qwen": "qwen-code", "mistral": "mistral-vibe", "codebuff": "freebuff"]

    /// Ids with a bundled icon, sorted.
    public static let available: [String] = {
        let urls = CoreResources.bundle.urls(forResourcesWithExtension: "svg", subdirectory: nil) ?? []
        return urls.map(\.lastPathComponent)
            .filter { $0.hasPrefix("harness-") }
            .map { String($0.dropFirst("harness-".count).dropLast(".svg".count)) }
            .sorted()
    }()

    public static func url(for harness: String) -> URL? {
        let id = harness.lowercased()
        return CoreResources.url("harness-\(aliases[id] ?? id)", "svg")
    }
}
