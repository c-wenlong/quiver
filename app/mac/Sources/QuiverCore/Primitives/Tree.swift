import Foundation

/// A plugin or skill folder as the detail view lists it: files grouped
/// under Skills, Commands, Agents, Hooks, then Other, each section with a
/// count. Dotfiles and `.git` are never listed; a `skills/<name>` folder is
/// one entry that opens its SKILL.md.
public struct PrimitiveTree: Sendable, Equatable {
    public enum Kind: String, Sendable, CaseIterable {
        case skills, commands, agents, hooks, other

        public var title: String {
            switch self {
            case .skills: "Skills"
            case .commands: "Commands"
            case .agents: "Agents"
            case .hooks: "Hooks"
            case .other: "Other files"
            }
        }
    }

    /// One row: a file, or a skill folder standing for its contents.
    public struct Entry: Sendable, Equatable, Identifiable {
        public var id: String { path }
        /// The path below the section folder (`tdd`, `review.md`).
        public var name: String
        /// The absolute path, symlinks unresolved.
        public var path: String
        /// A skill folder, rather than a plain file.
        public var isFolder: Bool
        /// The file this entry opens: itself, or a folder's SKILL.md.
        public var previewPath: String
        /// A folder's files (`SKILL.md`, `scripts/run.sh`), each name
        /// relative to the folder; empty for a plain file.
        public var children: [Entry] = []
    }

    /// One group of entries under a kind, with its count.
    public struct Section: Sendable, Equatable, Identifiable {
        public var id: Kind { kind }
        public var kind: Kind
        public var entries: [Entry]
    }

    public var sections: [Section]

    /// Walk `root` two levels down per section folder; everything outside
    /// the known folders lands in Other with its name relative to root.
    public init(root: URL) {
        let fm = FileManager.default
        var sections: [(Kind, [Entry])] = []
        for kind in [Kind.skills, .commands, .agents, .hooks] {
            let folder = root.appending(path: kind.rawValue)
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: folder.path, isDirectory: &isDir), isDir.boolValue else { continue }
            var entries: [Entry] = []
            let top = (try? fm.contentsOfDirectory(atPath: folder.path).sorted()) ?? []
            for name in top where !name.hasPrefix(".") {
                let item = folder.appending(path: name)
                var itemIsDir: ObjCBool = false
                fm.fileExists(atPath: item.path, isDirectory: &itemIsDir)
                if kind == .skills, itemIsDir.boolValue {
                    entries.append(Entry(name: name, path: item.path, isFolder: true,
                                         previewPath: item.appending(path: "SKILL.md").path,
                                         children: PrimitiveTree.files(in: item, prefix: "")))
                } else if itemIsDir.boolValue {
                    // A nested folder lists its files with relative names.
                    let inside = (try? fm.contentsOfDirectory(atPath: item.path).sorted()) ?? []
                    for inner in inside where !inner.hasPrefix(".") {
                        let file = item.appending(path: inner)
                        var innerDir: ObjCBool = false
                        fm.fileExists(atPath: file.path, isDirectory: &innerDir)
                        guard !innerDir.boolValue else { continue }
                        entries.append(Entry(name: name + "/" + inner, path: file.path,
                                             isFolder: false, previewPath: file.path))
                    }
                } else {
                    entries.append(Entry(name: name, path: item.path, isFolder: false,
                                         previewPath: item.path))
                }
            }
            if !entries.isEmpty { sections.append((kind, entries)) }
        }
        sections.append((.other, PrimitiveTree.otherFiles(root: root)))
        self.sections = sections.filter { !$0.1.isEmpty }
            .map { Section(kind: $0.0, entries: $0.1) }
    }

    /// Every file under `dir`, names relative to it (`SKILL.md`,
    /// `scripts/run.sh`), dotfiles skipped at every level. A skill folder
    /// is more than its SKILL.md, and the detail view expands the folder
    /// to all of them.
    private static func files(in dir: URL, prefix: String, depth: Int = 0) -> [Entry] {
        let fm = FileManager.default
        guard depth <= 6,
              let names = try? fm.contentsOfDirectory(atPath: dir.path).sorted() else { return [] }
        var out: [Entry] = []
        for name in names where !name.hasPrefix(".") {
            let item = dir.appending(path: name)
            var isDir: ObjCBool = false
            fm.fileExists(atPath: item.path, isDirectory: &isDir)
            if isDir.boolValue {
                out += files(in: item, prefix: prefix + name + "/", depth: depth + 1)
            } else {
                out.append(Entry(name: prefix + name, path: item.path,
                                 isFolder: false, previewPath: item.path))
            }
        }
        return out
    }

    /// Files outside the section folders, paths relative to root,
    /// dotfiles and `.git` skipped at every level.
    private static func otherFiles(root: URL) -> [Entry] {
        let fm = FileManager.default
        let known: Set<String> = ["skills", "commands", "agents", "hooks"]
        var entries: [Entry] = []
        func walk(_ dir: URL, depth: Int, prefix: String) {
            guard depth <= 4,
                  let names = try? fm.contentsOfDirectory(atPath: dir.path).sorted() else { return }
            for name in names where !name.hasPrefix(".") {
                let item = dir.appending(path: name)
                var isDir: ObjCBool = false
                fm.fileExists(atPath: item.path, isDirectory: &isDir)
                if isDir.boolValue {
                    if depth == 0, known.contains(name) { continue }
                    walk(item, depth: depth + 1, prefix: prefix + name + "/")
                } else {
                    entries.append(Entry(name: prefix + name, path: item.path,
                                         isFolder: false, previewPath: item.path))
                }
            }
        }
        walk(root, depth: 0, prefix: "")
        return entries
    }

    /// Whether `url` can open in the text editor: under a megabyte and
    /// decodable UTF-8, so binaries show "Can't preview" instead.
    public static func isTextFile(_ url: URL) -> Bool {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
              let size = attrs[.size] as? Int, size <= 1_000_000,
              let handle = try? FileHandle(forReadingFrom: url)
        else { return false }
        defer { try? handle.close() }
        var head = (try? handle.read(upToCount: 8192)) ?? Data()
        if head.contains(0) { return false }
        // The cut at 8192 can land inside a multi-byte character, which
        // alone would fail the decode; the file goes on past it.
        if size > head.count { head = trimmingPartialCharacter(head) }
        return String(data: head, encoding: .utf8) != nil
    }

    /// `data` without a UTF-8 sequence its end cut short, so a prefix of
    /// valid text decodes. Only a real start of a character is trimmed:
    /// a byte no character can begin with (0xFF, 0xC0) stays and fails
    /// the decode, as it should.
    static func trimmingPartialCharacter(_ data: Data) -> Data {
        let tail = [UInt8](data.suffix(4))
        var lead = tail.count - 1
        var continuation = 0
        while lead >= 0, tail[lead] & 0xC0 == 0x80, continuation < 3 {
            lead -= 1
            continuation += 1
        }
        guard lead >= 0 else { return data }
        let byte = tail[lead]
        let length: Int
        switch byte {
        case 0xC2...0xDF: length = 2
        case 0xE0...0xEF: length = 3
        case 0xF0...0xF4: length = 4
        default: return data
        }
        guard length > continuation + 1 else { return data }
        if continuation >= 1 {
            // The few leads whose second byte has a narrower range.
            let second: ClosedRange<UInt8>
            switch byte {
            case 0xE0: second = 0xA0...0xBF
            case 0xED: second = 0x80...0x9F
            case 0xF0: second = 0x90...0xBF
            case 0xF4: second = 0x80...0x8F
            default: second = 0x80...0xBF
            }
            guard second.contains(tail[lead + 1]) else { return data }
        }
        return data.dropLast(continuation + 1)
    }
}
