import Foundation

/// Adding and removing the parts of an editable plugin or skill folder.
/// Names are kebab-case ids, templates write the frontmatter a harness
/// expects, and delete only ever goes through the Trash.
public enum PrimitiveParts {
    public enum Kind: String, Sendable, CaseIterable {
        case skill, command, agent

        public var title: String {
            switch self {
            case .skill: "skill"
            case .command: "command"
            case .agent: "agent"
            }
        }
    }

    /// `some-name-2`: lowercase words joined by dashes.
    public static func isValidName(_ name: String) -> Bool {
        name.range(of: #"^[a-z0-9]+(-[a-z0-9]+)*$"#, options: .regularExpression) != nil
    }

    /// Where a part of `kind` named `name` belongs under `root`
    /// (skills get a folder, commands and agents a markdown file).
    public static func path(of kind: Kind, named name: String, in root: URL) -> URL {
        switch kind {
        case .skill: root.appending(path: "skills/\(name)/SKILL.md")
        case .command: root.appending(path: "commands/\(name).md")
        case .agent: root.appending(path: "agents/\(name).md")
        }
    }

    /// Create a part's file (and its folder) with a starter template;
    /// fails rather than overwrite something already there.
    @discardableResult
    public static func create(_ kind: Kind, named name: String, in root: URL) throws -> URL {
        let file = path(of: kind, named: name, in: root)
        guard !FileManager.default.fileExists(atPath: file.path) else {
            throw NSError(domain: "PrimitiveParts", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "\(name) already exists."])
        }
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try template(kind, named: name).write(to: file, atomically: true, encoding: .utf8)
        return file
    }

    /// The starter text for a new part.
    public static func template(_ kind: Kind, named name: String) -> String {
        let title = name.components(separatedBy: "-").map(\.capitalized).joined(separator: " ")
        switch kind {
        case .skill:
            return """
            ---
            name: \(name)
            description: What this skill does and when to use it.
            ---

            # \(title)

            """
        case .command:
            return "# \(title)\n"
        case .agent:
            return """
            ---
            name: \(name)
            description: What this agent does and when to delegate to it.
            ---

            # \(title)

            """
        }
    }

    /// Move a file or folder to the Trash; injectable so tests never
    /// touch the real one. The user confirms first in the UI, and nothing
    /// here ever deletes outright.
    public static func trash(_ url: URL,
                             using trashItem: (URL) throws -> Void = { url in
                                 _ = try FileManager.default.trashItem(at: url, resultingItemURL: nil)
                             }) throws {
        try trashItem(url)
    }
}
