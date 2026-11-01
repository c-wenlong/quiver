import Foundation
import Observation

/// Text edits on `~/.swe/AGENTS_MD/index.txt`, kept pure so the model
/// applies them through an InstructionDocument and its conflict checks.
///
/// The file is an ordered list: one relative .md path per line, blank
/// lines and `#` comments ignored. Every edit keeps the comment and blank
/// lines where they were and moves only the chapter entries.
public enum ChapterIndex {
    static func lines(_ text: String) -> [String] {
        text.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
    }

    /// A chapter entry line: not blank, not a comment.
    static func isEntry(_ line: String) -> Bool {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        return !trimmed.isEmpty && !trimmed.hasPrefix("#")
    }

    /// The chapter names in index order.
    public static func entries(_ text: String) -> [String] {
        lines(text).filter(isEntry).map { $0.trimmingCharacters(in: .whitespaces) }
    }

    /// `text` with `name` moved to just before `before` — to the end when
    /// `before` is nil, which means before a final newline's empty line.
    public static func move(_ text: String, name: String, before: String?) -> String {
        var lines = lines(text)
        guard let from = lines.firstIndex(where: {
            isEntry($0) && $0.trimmingCharacters(in: .whitespaces) == name
        }) else { return text }
        let entry = lines.remove(at: from)
        let to: Int
        if let before,
           let index = lines.firstIndex(where: {
               isEntry($0) && $0.trimmingCharacters(in: .whitespaces) == before
           }) {
            to = index
        } else {
            // A trailing "" is the file's last newline; land before it.
            to = lines.last == "" ? lines.count - 1 : lines.endIndex
        }
        lines.insert(entry, at: to)
        return lines.joined(separator: "\n")
    }

    /// `text` without `name`'s line. The file it points at is not touched:
    /// an unlisted chapter is simply not compiled.
    public static func removing(_ text: String, name: String) -> String {
        var lines = lines(text)
        guard let index = lines.firstIndex(where: {
            isEntry($0) && $0.trimmingCharacters(in: .whitespaces) == name
        }) else { return text }
        lines.remove(at: index)
        return lines.joined(separator: "\n")
    }

    /// `text` with `name` appended, adding the newline a file without one
    /// needs first.
    public static func appending(_ text: String, name: String) -> String {
        var result = text
        if !result.hasSuffix("\n") { result += "\n" }
        return result + name + "\n"
    }

    /// Why `name` cannot be a chapter, mirroring the compiler's rules; nil
    /// means it can. Kept a client-side pre-check — the build still decides.
    public static func nameProblem(_ name: String) -> String? {
        if name.isEmpty { return "Name the chapter." }
        if name.contains("\n") || name.contains("\r") || name.contains("\0") {
            return "A chapter name is one line."
        }
        if name.hasPrefix("/") { return "The path must be relative, inside AGENTS_MD." }
        if name.hasPrefix("#") { return "A # name reads as a comment in index.txt." }
        if name.contains("-->") { return "The name cannot contain -->." }
        if !name.lowercased().hasSuffix(".md") { return "Chapters end in .md." }
        if name.split(separator: "/").contains("..") {
            return "The path cannot leave AGENTS_MD."
        }
        // ".md" alone has no stem; the compiler's .md suffix check rejects it.
        if (name as NSString).lastPathComponent.dropLast(3).isEmpty {
            return "The name needs letters before .md."
        }
        return nil
    }
}

/// The chapter-based way of editing the shared AGENTS.md: an ordered list
/// of `~/.swe/AGENTS_MD/*.md` files compiled by `swe instructions build`.
///
/// Edits land in the chapter files through InstructionDocument; order,
/// add and remove edits land in index.txt the same way, so a watcher or
/// hand edit racing them gets the changed-on-disk stop, not a silent loss.
/// Every successful index or chapter save rebuilds through swe, so the
/// output the harnesses read never goes stale from an edit made here.
@MainActor
@Observable
public final class ChaptersModel {
    public private(set) var status: Loadable<InstructionsCheck> = .idle
    public private(set) var building = false
    /// What the last build did, or why it failed; one of the two is set.
    public private(set) var buildNote: String?
    public private(set) var buildError: String?
    public private(set) var converting = false
    public private(set) var convertError: String?
    /// The selected chapter's index.txt name.
    public var selected: String?
    /// A read-only view of the generated output, loaded for the preview.
    public private(set) var output: InstructionDocument?

    /// index.txt as a document: programmatic edits go through its save so
    /// they keep the read-compare-write conflict check.
    @ObservationIgnored private var indexDocument: InstructionDocument?
    /// Open chapter editors by index name, kept for the page's lifetime.
    @ObservationIgnored private var documents: [String: InstructionDocument] = [:]
    /// A build request that arrived while one ran, collapsed into one rerun.
    @ObservationIgnored private var buildAgain = false
    @ObservationIgnored private var generation = 0

    public init() {}

    /// Whether the shared file is chapter-managed, once `status` knows.
    public var chapterMode: Bool {
        if case .loaded(let check) = status { return check.indexExists }
        return false
    }

    /// Drafts the quit check asks about: any chapter holding unsaved text.
    public var hasUnsavedChanges: Bool {
        documents.values.contains { $0.isDirty } || (indexDocument?.isDirty ?? false)
    }

    public func load(swe: SweClient) async {
        generation += 1
        let mine = generation
        if case .loaded = status {} else { status = .loading }
        let next: Loadable<InstructionsCheck>
        do {
            next = .loaded(try await swe.instructionsCheck())
        } catch is CancellationError {
            return
        } catch {
            next = .failed(error.localizedDescription)
        }
        guard mine == generation else { return }
        status = next
        guard case .loaded(let check) = next, check.indexExists else { return }

        let names = Set(check.chapters.map(\.name))
        // Drop editors for unlisted chapters — but never a dirty one: an
        // index edit elsewhere must not throw unsaved text away.
        documents = documents.filter { names.contains($0.key) || $0.value.isDirty }
        if selected.map({ !names.contains($0) }) ?? true {
            selected = check.chapters.first?.name
        }
        if indexDocument?.url.path != check.index {
            let document = InstructionDocument(url: URL(fileURLWithPath: check.index))
            indexDocument = document
            Task { await document.load() }
        }
        if output?.url.path != check.output {
            let document = InstructionDocument(url: URL(fileURLWithPath: check.output))
            output = document
            Task { await document.load() }
        }
    }

    /// The chapter's editor document, read on first use. nil when the
    /// index entry escapes AGENTS_MD (`../x.md`, a symlinked parent):
    /// `survey` still lists the broken row so it can be removed, but an
    /// editor on it would write outside the chapter folder.
    public func document(for chapter: InstructionsCheck.Chapter) -> InstructionDocument? {
        if let open = documents[chapter.name] { return open }
        guard case .loaded(let check) = status,
              let inside = Self.inside(chapter.name, under: check.directory) else { return nil }
        let document = InstructionDocument(url: inside)
        documents[chapter.name] = document
        Task { await document.load() }
        return document
    }

    /// `name` resolved inside `directory` — links in every component, not
    /// just the file's own — or nil when it leaves the folder.
    static func inside(_ name: String, under directory: String) -> URL? {
        let root = URL(fileURLWithPath: directory).resolvingSymlinksInPath().standardizedFileURL
        let resolved = URL(fileURLWithPath: directory).appending(path: name)
            .resolvingSymlinksInPath().standardizedFileURL
        guard resolved.path == root.path || resolved.path.hasPrefix(root.path + "/") else { return nil }
        return resolved
    }

    public func isDirty(_ name: String) -> Bool { documents[name]?.isDirty ?? false }

    /// Reload chapters and the output preview that changed with nothing
    /// to lose; flag the ones holding unsaved edits.
    public func checkDisk() async {
        await indexDocument?.checkDisk()
        for document in documents.values { await document.checkDisk() }
        await output?.checkDisk()
    }

    /// Drag `name` to just before `before` in the index (end when nil),
    /// then rebuild.
    public func move(chapter name: String, before: String?, swe: SweClient) async {
        guard let document = await readyIndex() else { return }
        let text = ChapterIndex.move(document.text, name: name, before: before)
        guard text != document.text else { return }
        document.text = text
        if await document.save() == .saved {
            await build(swe: swe)
        } else {
            // Drop the rejected edit and resync: index.txt has no editor of
            // its own, so a dirty flag nobody can clear must not wedge ops.
            await document.load()
            buildError = "index.txt changed on disk; drag again."
        }
    }

    /// A new chapter named for `raw` (with `.md` added when missing):
    /// the file is created with a heading, listed at the end of the index,
    /// selected, and the output rebuilt. Returns why it was refused.
    @discardableResult
    public func addChapter(named raw: String, swe: SweClient) async -> String? {
        var name = raw.trimmingCharacters(in: .whitespaces)
        if !name.isEmpty && !name.lowercased().hasSuffix(".md") { name += ".md" }
        if let problem = ChapterIndex.nameProblem(name) { return problem }
        guard case .loaded(let check) = status else { return "The chapters aren't loaded yet." }
        if check.chapters.contains(where: { $0.name == name }) {
            return "A chapter named \(name) is already listed."
        }
        guard let document = await readyIndex() else {
            return "index.txt hasn't been read, so it can't be updated."
        }
        guard let file = Self.inside(name, under: check.directory) else {
            return "The path cannot leave AGENTS_MD."
        }
        let starter = Self.starter(for: name)
        let created = await offMain { () -> Bool in
            // A folder chapter (communication/rules.md) may need parents.
            try? FileManager.default.createDirectory(
                at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            // Exclusive create: a file that already exists is never
            // overwritten with the starter heading.
            let descriptor = open(file.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o644)
            if descriptor < 0 { return errno == EEXIST }
            let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
            return (try? handle.write(contentsOf: Data(starter.utf8))) != nil
        }
        guard created else { return "Couldn't create \(name)." }
        document.text = ChapterIndex.appending(document.text, name: name)
        guard await document.save() == .saved else {
            await document.load()
            return "index.txt changed on disk; try again."
        }
        // A dirty doc kept from an unlisted chapter keeps its draft too.
        if documents[name]?.isDirty != true { documents[name] = nil }
        selected = name
        await build(swe: swe)
        return nil
    }

    /// Drops `name` from the index, then rebuilds. The chapter file stays
    /// on disk, unlisted — removing it is the user's call in Finder. A
    /// chapter holding unsaved text refuses: unlisting must not discard it.
    public func remove(chapter name: String, swe: SweClient) async {
        if documents[name]?.isDirty == true {
            buildError = "Save or revert \(name) before removing it."
            return
        }
        guard let document = await readyIndex() else { return }
        let text = ChapterIndex.removing(document.text, name: name)
        guard text != document.text else { return }
        document.text = text
        guard await document.save() == .saved else {
            await document.load()
            buildError = "index.txt changed on disk; try again."
            return
        }
        documents[name] = nil
        if selected == name {
            selected = ChapterIndex.entries(text).first
        }
        await build(swe: swe)
    }

    /// Compiles the chapters into the shared AGENTS.md and refreshes the
    /// preview and the staleness state. Called after every index save and
    /// offered as Rebuild for edits made elsewhere. A request that lands
    /// while one runs queues a single rerun: a save that goes unbuilt
    /// would leave the output stale.
    public func build(swe: SweClient) async {
        guard !building else { buildAgain = true; return }
        building = true
        buildError = nil
        do {
            buildNote = try await swe.instructionsBuild()
                ? "Rebuilt ~/.swe/AGENTS.md" : "Already current"
        } catch {
            buildNote = nil
            buildError = error.localizedDescription
        }
        await output?.load()
        // The build may have failed on a source problem the last check
        // did not know; a fresh status shows either answer.
        if case .loaded = status { await reload(swe: swe) }
        building = false
        if buildAgain {
            buildAgain = false
            await build(swe: swe)
        }
    }

    /// Turns the flat shared file into chapters (`swe instructions init`),
    /// then reloads into chapter mode.
    public func convert(swe: SweClient) async {
        guard !converting else { return }
        converting = true
        convertError = nil
        defer { converting = false }
        do {
            _ = try await swe.instructionsInit()
        } catch {
            convertError = error.localizedDescription
        }
        await load(swe: swe)
    }

    /// A chapter's first lines: a Markdown title from its file name.
    nonisolated static func starter(for name: String) -> String {
        let base = ((name as NSString).lastPathComponent as NSString).deletingPathExtension
            .replacingOccurrences(of: "[-_]+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
        let title = base.isEmpty ? "Chapter" : base.prefix(1).uppercased() + base.dropFirst()
        return "# \(title)\n"
    }

    /// index.txt once it has been read. Programmatic edits only, so a
    /// reload costs nothing when it is still loading.
    private func readyIndex() async -> InstructionDocument? {
        guard let document = indexDocument else { return nil }
        if document.state != .ready { await document.load() }
        return document.state == .ready ? document : nil
    }

    /// `load` without the loading skeleton: the list refreshes in place.
    private func reload(swe: SweClient) async {
        guard let check = try? await swe.instructionsCheck() else { return }
        status = .loaded(check)
    }
}
