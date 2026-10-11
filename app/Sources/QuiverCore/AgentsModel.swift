import Foundation
import Observation

/// The AGENTS.md page: the shared instruction file, where it is linked, and
/// the instruction files of one project folder.
///
/// Lives as long as the app, so leaving the page keeps unsaved edits.
@MainActor
@Observable
public final class AgentsModel {
    /// The files a project folder can hold, in the order the page lists them.
    public static let projectFileNames = ["AGENTS.md", "CLAUDE.md"]

    public struct ProjectFile: Equatable, Sendable, Identifiable {
        public var id: String { name }
        public let name: String
        public let url: URL
        public let exists: Bool
        /// When this file is a symlink to another file in the list, its name.
        public let linksTo: String?
    }

    public private(set) var check: Loadable<InitCheck> = .idle
    public private(set) var linking = false
    public private(set) var linkError: String?
    /// `~/.quiver/AGENTS.md`, once `swe init --check` has said where it is.
    public private(set) var global: InstructionDocument?
    public private(set) var projectFolder: URL?
    public private(set) var projectFiles: [ProjectFile] = []
    /// Open project documents by the path they write to, so CLAUDE.md
    /// linked to AGENTS.md opens the same document rather than a second copy.
    /// Not observed: `document(for:)` fills it while a view reads it.
    @ObservationIgnored private var projectDocuments: [URL: InstructionDocument] = [:]
    @ObservationIgnored private var generation = 0

    public init() {}

    public func load(swe: SweClient) async {
        generation += 1
        let mine = generation
        if case .loaded = check {} else { check = .loading }
        let next: Loadable<InitCheck>
        do {
            next = .loaded(try await swe.initCheck())
        } catch is CancellationError {
            return
        } catch {
            next = .failed(error.localizedDescription)
        }
        guard mine == generation else { return }
        check = next
        if case .loaded(let report) = next {
            let url = URL(fileURLWithPath: report.agentsFile)
            if global?.url != url {
                let document = InstructionDocument(url: url)
                document.load()
                global = document
            }
        }
    }

    /// Runs `swe init`, then checks again.
    public func link(swe: SweClient) async {
        guard !linking else { return }
        linking = true
        linkError = nil
        defer { linking = false }
        do {
            try await swe.runInit()
        } catch {
            linkError = error.localizedDescription
        }
        await load(swe: swe)
    }

    public var projectHasUnsavedChanges: Bool {
        projectDocuments.values.contains { $0.isDirty }
    }

    public var hasUnsavedChanges: Bool {
        (global?.isDirty ?? false) || projectHasUnsavedChanges
    }

    /// Switches to `folder`, dropping the documents of the previous one.
    /// The page asks before calling this while edits are unsaved.
    public func openProject(_ folder: URL) {
        projectFolder = folder
        projectDocuments = [:]
        rescanProject()
    }

    /// Lists the folder's instruction files again, after one was created.
    public func rescanProject() {
        guard let folder = projectFolder else {
            projectFiles = []
            return
        }
        let fm = FileManager.default
        let urls: [URL] = Self.projectFileNames.map { folder.appending(path: $0) }
        projectFiles = urls.map { url in
            // CLAUDE.md -> AGENTS.md: this file's link ends at another listed file.
            let target = InstructionDocument.resolveLinks(url).standardizedFileURL
            let other = urls.first { $0 != url && $0.standardizedFileURL == target }
            return ProjectFile(name: url.lastPathComponent, url: url,
                               exists: fm.fileExists(atPath: url.path),
                               linksTo: other?.lastPathComponent)
        }
    }

    /// The document for one of `projectFiles`, read on first use.
    public func document(for file: ProjectFile) -> InstructionDocument {
        let key = InstructionDocument.resolveLinks(file.url).standardizedFileURL
        if let open = projectDocuments[key] { return open }
        let document = InstructionDocument(url: file.url)
        document.load()
        projectDocuments[key] = document
        return document
    }
}
