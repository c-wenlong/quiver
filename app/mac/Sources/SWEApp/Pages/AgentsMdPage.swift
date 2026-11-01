import AppKit
import SWECore
import SwiftUI

/// The shared `~/.swe/AGENTS.md`, where swe has linked it, and the
/// instruction files of one project folder.
struct AgentsMdPage: View {
    enum Scope: Hashable { case global, project }

    @Environment(AppModel.self) private var app
    @State private var scope: Scope = .global

    var body: some View {
        let model = app.agentsMd
        // The editor fills the page and scrolls itself, so the page doesn't.
        Page(title: "AGENTS.md", subtitle: "The instructions every harness reads, and each project's own.",
             scrolls: false) {
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await model.load(swe: app.swe) }
                Task { await model.checkDisk() }
                Task { await app.chapters.load(swe: app.swe) }
                Task { await app.chapters.checkDisk() }
            }
            .iconButton()
            .keyboardShortcut("r", modifiers: .command)
            .help("Check links and reread the file (⌘R)")
        } content: {
            Tabs(options: [(Scope.global, "Global"), (Scope.project, "Project")], selection: $scope, style: .underline)
            switch scope {
            case .global: GlobalInstructions()
            case .project: ProjectInstructions()
            }
        }
        .task(id: app.swePath) {
            async let links: () = model.load(swe: app.swe)
            async let chaptered: () = app.chapters.load(swe: app.swe)
            _ = await (links, chaptered)
        }
        // Agents rewrite these files while they work; pick that up on
        // return. The chapter report refreshes too: an `instructions init`
        // run elsewhere swaps which editor this page should be showing.
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            Task { await model.checkDisk() }
            Task { await app.chapters.checkDisk() }
            Task { await app.chapters.load(swe: app.swe) }
        }
    }
}

private struct GlobalInstructions: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let model = app.agentsMd
        let chapters = app.chapters
        switch model.check {
        case .idle, .loading:
            SkeletonRows(rows: 5, shape: .link, label: "Checking links…")
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded(let report):
            VStack(alignment: .leading, spacing: 12) {
                LinksStrip(report: report)
                switch chapters.status {
                case .loaded(let check) where check.indexExists:
                    ChaptersEditor(check: check)
                case .idle, .loading:
                    SkeletonRows(rows: 8, shape: .file, label: "Reading chapters…")
                default:
                    // A failed check means an swe without instructions: the
                    // flat file is the only thing there is to edit.
                    FlatInstructions(report: report, document: model.global)
                }
            }
        }
    }
}

/// The flat-file editing path: the shared AGENTS.md itself, plus the offer
/// to split it into chapters once `swe instructions check` says none exist.
private struct FlatInstructions: View {
    @Environment(AppModel.self) private var app
    var report: InitCheck
    var document: InstructionDocument?

    var body: some View {
        let chapters = app.chapters
        // A loaded, non-chapter check is the only state where converting is
        // meaningful; a failed one is an old swe that cannot build anyway.
        if case .loaded(let check) = chapters.status, !check.indexExists {
            Strip {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Chapters").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                        Text("Split AGENTS.md into ordered sections you edit separately; swe rebuilds the file every harness reads.")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    if chapters.converting { ProgressView().controlSize(.small) }
                    let unsaved = document?.isDirty == true
                    Button(chapters.converting ? "Converting…" : "Convert to chapters", systemImage: "square.split.2x1") {
                        Task { await chapters.convert(swe: app.swe) }
                    }
                    .secondaryButton()
                    // init folds the file on disk; an unsaved draft in the
                    // editor would never reach the chapter it becomes.
                    .disabled(chapters.converting || unsaved)
                    .hoverTip(unsaved ? AgentsModel.saveFirst
                              : "Runs swe instructions init: your AGENTS.md becomes main.md under ~/.swe/AGENTS_MD.",
                              replacesLabel: false)
                }
                if let error = chapters.convertError {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(Theme.caption).foregroundStyle(Theme.dangerText)
                }
            }
        }
        if let document {
            let linked = report.instructionRows.filter { $0.instructions?.state == .linked }.count
            InstructionEditor(
                document: document,
                title: displayPath(document.url),
                subtitle: linked == 1 ? "Linked into 1 harness" : "Linked into \(linked) harnesses")
        }
    }
}

/// Chapter mode: the ordered sources of the generated AGENTS.md. The rail
/// edits index.txt (order, add, remove); the editor edits the selected
/// chapter; the strip previews and rebuilds the output.
private struct ChaptersEditor: View {
    @Environment(AppModel.self) private var app
    var check: InstructionsCheck
    @State private var adding = false
    @State private var newName = ""
    @State private var addError: String?

    var body: some View {
        let chapters = app.chapters
        HStack(alignment: .top, spacing: 12) {
            rail
            VStack(alignment: .leading, spacing: 12) {
                if let name = chapters.selected,
                   let chapter = check.uniqueChapters.first(where: { $0.name == name }) {
                    let position = check.uniqueChapters.firstIndex(where: { $0.name == name })! + 1
                    if let document = chapters.document(for: chapter) {
                        InstructionEditor(
                            document: document,
                            title: chapter.name,
                            subtitle: "Chapter \(position) of \(check.uniqueChapters.count)") {
                            Task { await chapters.build(swe: app.swe) }
                        }
                        .id(chapter.name)
                    } else {
                        Card(title: chapter.name, subtitle: "The path leaves AGENTS_MD") {
                            EmptyView()
                        } content: {
                            Text("index.txt lists a chapter outside its folder. Fix or remove the entry there.")
                                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        }
                    }
                } else {
                    Card(title: "No chapters", subtitle: "index.txt lists none yet") {
                        EmptyView()
                    } content: {
                        Text("Add a chapter to start the shared instruction file.")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    }
                }
                OutputStrip(check: check)
            }
            .frame(maxWidth: .infinity)
        }
        .alert("New chapter", isPresented: $adding) {
            TextField("name or folder/name (.md added)", text: $newName)
            Button("Cancel", role: .cancel) { newName = "" }
            Button("Add") {
                Task {
                    if let problem = await chapters.addChapter(named: newName, swe: app.swe) {
                        addError = problem
                    } else {
                        newName = ""
                        addError = nil
                    }
                }
            }
        } message: {
            Text("A .md file under ~/.swe/AGENTS_MD, added at the end of index.txt.")
        }
    }

    /// The ordered chapter list: drag to reorder (rewrites index.txt),
    /// select to edit, remove to unlist without deleting the file.
    private var rail: some View {
        let chapters = app.chapters
        return Strip {
            HStack(spacing: 6) {
                Text("Chapters").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                Text("\(check.uniqueChapters.count)").font(Theme.caption).foregroundStyle(Theme.textMuted)
                Spacer(minLength: 4)
            }
            if let addError {
                Text(addError).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ScrollView {
                ReorderableRows(check.uniqueChapters) { name, before in
                    Task { await chapters.move(chapter: name, before: before, swe: app.swe) }
                } row: { chapter in
                    ChapterRow(chapter: chapter, chapters: chapters)
                }
            }
            .frame(maxHeight: 330)
            .fixedSize(horizontal: false, vertical: true)
            // The footer add, like the repos rail's "Add repository".
            Button { adding = true } label: {
                HStack(spacing: 8) {
                    Image(systemName: "plus").font(Theme.font(size: 12))
                    Text("Add chapter").font(Theme.nav)
                    Spacer(minLength: 0)
                }
                .foregroundStyle(Theme.textMuted)
                .padding(.horizontal, 8)
                .frame(height: Theme.navRowHeight)
                .contentShape(.rect)
            }
            .plainButton(cornerRadius: Theme.controlRadius)
            .hoverTip("Add a chapter at the end of index.txt", replacesLabel: false)
            .padding(.top, 4)
        }
        .frame(width: 236)
    }

    /// A rail row: the basename is the identity and never truncates, so
    /// the folder drops to a muted line under it rather than squeezing it.
    private struct ChapterRow: View {
        var chapter: InstructionsCheck.Chapter
        var chapters: ChaptersModel
        @Environment(AppModel.self) private var app
        @State private var hovering = false

        private var base: String { (chapter.name as NSString).lastPathComponent }
        private var folder: String { (chapter.name as NSString).deletingLastPathComponent }

        var body: some View {
            let selected = chapters.selected == chapter.name
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 6) {
                    Text(base)
                        .font(Theme.nav)
                        .lineLimit(1).truncationMode(.tail)
                    if chapters.isDirty(chapter.name) {
                        Circle().fill(Theme.warning)
                            .frame(width: Theme.statusDot, height: Theme.statusDot)
                    }
                    Spacer(minLength: 4)
                    if !chapter.exists {
                        Text("missing").font(Theme.caption).foregroundStyle(Theme.dangerText)
                    } else if hovering {
                        Button {
                            Task { await chapters.remove(chapter: chapter.name, swe: app.swe) }
                        } label: {
                            Image(systemName: "xmark")
                                .font(Theme.font(size: 10, weight: .semibold))
                                .foregroundStyle(Theme.textMuted)
                                .frame(width: 20, height: 20)
                                .contentShape(.rect)
                        }
                        .plainButton(cornerRadius: Theme.controlRadius)
                        .hoverTip("Remove from index.txt; the file stays on disk", replacesLabel: false)
                    }
                }
                if !folder.isEmpty {
                    Text(folder)
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                        .lineLimit(1).truncationMode(.head)
                }
            }
            .foregroundStyle(selected ? Theme.textPrimary : Theme.textNav)
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .background(selected ? Theme.navActive : hovering ? Theme.hover : .clear,
                        in: .rect(cornerRadius: Theme.controlRadius))
            .contentShape(.rect)
            .onTapGesture { chapters.selected = chapter.name }
            .onHover { hovering = $0 }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
            .accessibilityAction(.default) { chapters.selected = chapter.name }
            .accessibilityHint("Drag to reorder")
            .contextMenu {
                Button("Remove from index") {
                    Task { await chapters.remove(chapter: chapter.name, swe: app.swe) }
                }
            }
        }
    }
}

/// The generated AGENTS.md: build state, a Rebuild for edits made outside
/// the app, and a read-only preview of what the harnesses get.
private struct OutputStrip: View {
    @Environment(AppModel.self) private var app
    var check: InstructionsCheck
    @State private var showOutput = false

    var body: some View {
        let chapters = app.chapters
        return VStack(alignment: .leading, spacing: 12) {
            Strip {
                HStack(spacing: 10) {
                    Text("Generated AGENTS.md").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                    stateLabel
                    Spacer(minLength: 8)
                    if let note = chapters.buildNote {
                        Text(note).font(Theme.caption).foregroundStyle(Theme.textMuted)
                    }
                    if chapters.building { ProgressView().controlSize(.small) }
                    Button(chapters.building ? "Building…" : "Rebuild", systemImage: "arrow.clockwise") {
                        Task { await chapters.build(swe: app.swe) }
                    }
                    .secondaryButton()
                    .disabled(chapters.building)
                    .hoverTip("Runs swe instructions build", replacesLabel: false)
                    Button(showOutput ? "Hide" : "Preview", systemImage: showOutput ? "chevron.up" : "chevron.down") {
                        withAnimation { showOutput.toggle() }
                    }
                    .iconButton()
                }
                if let error = check.error {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(Theme.caption).foregroundStyle(Theme.dangerText)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let error = chapters.buildError {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(Theme.caption).foregroundStyle(Theme.dangerText)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if showOutput, let document = chapters.output {
                InstructionEditor(document: document, title: displayPath(document.url),
                                  subtitle: "Built from the chapters — read-only",
                                  readOnly: true)
                    .frame(height: 220)
            }
        }
    }

    @ViewBuilder private var stateLabel: some View {
        if check.error != nil {
            Label("Source problem", systemImage: "exclamationmark.triangle")
                .font(Theme.caption).foregroundStyle(Theme.dangerText)
        } else if check.stale == true || !check.outputExists {
            Label(check.outputExists ? "Stale" : "Not built", systemImage: "exclamationmark.circle")
                .font(Theme.caption).foregroundStyle(Theme.warningText)
        } else {
            Label("Current", systemImage: "checkmark.circle")
                .font(Theme.caption).foregroundStyle(Theme.success)
        }
    }
}

/// A compact bordered band above the editor: a label, then what it's about.
private struct Strip<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) { content }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
    }
}

/// Where each harness's instruction file points, from `swe init --check`,
/// as one strip of dots and names so the editor keeps the full width.
private struct LinksStrip: View {
    @Environment(AppModel.self) private var app
    var report: InitCheck
    @State private var confirmingReplace = false

    var body: some View {
        let model = app.agentsMd
        Strip {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text("Harnesses").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                    .hoverTip("From swe init --check", replacesLabel: false)
                FlowLayout(spacing: 14, lineSpacing: 6) {
                    ForEach(report.instructionRows) { row in
                        if let link = row.instructions {
                            LinkRow(name: row.name, link: link)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if report.instructionsNeedInit || model.linking {
                    HStack(spacing: 8) {
                        if model.linking { ProgressView().controlSize(.small) }
                        Button(model.linking ? "Linking…" : "Link with swe init", systemImage: "link") {
                            Task { await model.link(swe: app.swe) }
                        }
                        .primaryButton()
                        .disabled(model.linking)
                        .hoverTip("Runs swe init, which links what is missing and never replaces a real file.", replacesLabel: false)
                    }
                    .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 4 }
                }
            }
            if report.instructionRows.contains(where: { $0.instructions?.state.isBlocked == true }) {
                HStack(spacing: 12) {
                    Text("A real file is in the way for some harnesses. swe init leaves those alone.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button("Replace and back up…") { confirmingReplace = true }
                        .secondaryButton()
                        .disabled(model.linking || model.projectHasUnsavedChanges)
                        .hoverTip(model.projectHasUnsavedChanges ? AgentsModel.saveFirst
                                  : "Runs swe init --force, which copies each real file in the way to ~/.swe/backups before linking over it.",
                                  replacesLabel: false)
                }
            }
            if let error = model.linkError {
                // swe init can fail with pages of output; bounded, so the
                // editor below keeps its room on this non-scrolling page.
                ScrollView {
                    Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 64)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
        .confirmationDialog("Replace the real files with links?", isPresented: $confirmingReplace) {
            Button("Replace and back up", role: .destructive) {
                Task { await model.link(swe: app.swe, force: true) }
            }
        } message: {
            Text("This runs swe init --force. Each real file or folder in the way is copied to ~/.swe/backups, then replaced with a link to the shared copy.")
        }
    }
}

private struct LinkRow: View {
    var name: String
    var link: InitCheck.Link

    var color: Color {
        if link.state == .linked { return Theme.success }
        if link.state.isBlocked { return Theme.danger }
        if link.state.initFixes { return Theme.warning }
        return Theme.textMuted
    }

    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: Theme.statusDot, height: Theme.statusDot)
            HarnessMarkView(harness: name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            // Only what needs attention gets words; a dot says linked.
            if link.state != .linked {
                Text(link.state.title.lowercased()).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
        .hoverTip("\(HarnessMark(name).name), \(link.state.title.lowercased()): \(displayPath(URL(fileURLWithPath: link.path)))")
        .accessibilityElement(children: .combine)
        // The dot alone says linked, so VoiceOver gets the state in words.
        .accessibilityValue(link.state.title)
    }
}

private struct ProjectInstructions: View {
    static let folderKey = "agentsProjectFolder"

    @Environment(AppModel.self) private var app
    @State private var selectedFile = AgentsModel.projectFileNames[0]
    @State private var pendingFolder: URL?
    /// The selected file's document, opened in a task and never in body:
    /// document(for:) follows symlinks and reads the file on first use.
    @State private var opened: (file: AgentsModel.ProjectFile, document: InstructionDocument)?
    /// Bumped when a link may have moved under an unchanged file entry
    /// (the app comes back to the front, a link run ends), so the task
    /// asks the model again and follows the link to its new target.
    @State private var reopen = 0

    var body: some View {
        let model = app.agentsMd
        VStack(alignment: .leading, spacing: 12) {
            Strip {
                HStack(spacing: 12) {
                    Image(systemName: "folder").foregroundStyle(Theme.textSecondary)
                    if let folder = model.projectFolder {
                        Text(displayPath(folder)).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                            .lineLimit(1).truncationMode(.middle)
                    } else {
                        Text("Choose a project folder to edit its AGENTS.md and CLAUDE.md.")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    }
                    Spacer(minLength: 8)
                    Button("Choose folder…") { choose() }
                        .secondaryButton()
                }
            }
            if model.projectFolder != nil {
                let files = model.projectFiles
                Tabs(options: files.map { ($0.name, tabTitle($0)) }, selection: $selectedFile, style: .table, onPanel: true)
                if let file = files.first(where: { $0.name == selectedFile }),
                   let opened, opened.file == file {
                    InstructionEditor(document: opened.document, title: file.name,
                                      subtitle: subtitle(file, opened.document)) {
                        model.rescanProject()
                    }
                    .id(file.name)
                }
            }
        }
        // A new folder, tab or rescan of the file opens its document; the
        // model keeps each one, so a return to a tab keeps its edits.
        .task(id: OpenKey(file: model.projectFolder == nil ? nil : model.projectFiles.first { $0.name == selectedFile },
                          reopen: reopen)) {
            guard model.projectFolder != nil,
                  let file = model.projectFiles.first(where: { $0.name == selectedFile }) else {
                opened = nil
                return
            }
            opened = (file, model.document(for: file))
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            reopen += 1
        }
        .onChange(of: model.linking) { _, linking in if !linking { reopen += 1 } }
        .task { await restore() }
        .confirmationDialog("Discard unsaved changes?", isPresented: .init(
            get: { pendingFolder != nil }, set: { if !$0 { pendingFolder = nil } })) {
            Button("Discard and switch", role: .destructive) {
                if let folder = pendingFolder { open(folder) }
                pendingFolder = nil
            }
        } message: {
            Text("The open project has edits that aren't saved.")
        }
    }

    private struct OpenKey: Equatable {
        var file: AgentsModel.ProjectFile?
        var reopen: Int
    }

    private func tabTitle(_ file: AgentsModel.ProjectFile) -> String {
        if let target = file.linksTo { return "\(file.name) → \(target)" }
        return file.exists ? file.name : "\(file.name) (new)"
    }

    private func subtitle(_ file: AgentsModel.ProjectFile, _ document: InstructionDocument) -> String {
        if let target = file.linksTo { return "Links to \(target); edits land there." }
        if !document.existsOnDisk { return "Doesn't exist yet. Saving creates it." }
        return "In the folder above"
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose"
        panel.directoryURL = app.agentsMd.projectFolder
        guard panel.runModal() == .OK, let url = panel.url else { return }
        if app.agentsMd.projectHasUnsavedChanges {
            pendingFolder = url
        } else {
            open(url)
        }
    }

    private func open(_ folder: URL) {
        app.agentsMd.openProject(folder)
        UserDefaults.standard.set(folder.path, forKey: Self.folderKey)
    }

    /// Reopens the last folder. The check runs off the main actor: the
    /// folder may sit on a stale mount or a file provider.
    private func restore() async {
        guard app.agentsMd.projectFolder == nil,
              let path = UserDefaults.standard.string(forKey: Self.folderKey) else { return }
        let isFolder = await offMain {
            var isDirectory: ObjCBool = false
            return FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory) && isDirectory.boolValue
        }
        if isFolder, app.agentsMd.projectFolder == nil {
            app.agentsMd.openProject(URL(fileURLWithPath: path))
        }
    }
}

/// A plain-text editor for one instruction file: save, revert, an unsaved
/// dot, and a stop when the file changed on disk since it was opened.
/// Raw and Rich edit the same text, so nothing is lost switching.
struct InstructionEditor: View {
    private static let modeKey = "agentsMdView"

    @Bindable var document: InstructionDocument
    var title: String
    var subtitle: String
    /// The subtitle is a path: draw it in mono.
    var monoSubtitle = false
    /// View-only: no mode tabs or save controls, just the text.
    var readOnly = false
    /// Code, not prose: always the raw editor, with no Rich tab. A hook
    /// script read as Markdown turns its docstring into paragraphs.
    var rawOnly = false
    /// A Close button in the header, for the editor inside a sheet.
    var close: (() -> Void)? = nil
    var onSaved: () -> Void = {}
    @State private var error: String?
    @FocusState private var editing: Bool
    @State private var mode = UserDefaults.standard.string(forKey: Self.modeKey) ?? "raw"
    @State private var richFocused = false
    @State private var richFocusToken = 0

    var body: some View {
        Card(title: title, subtitle: subtitle, monoSubtitle: monoSubtitle) {
            HStack(spacing: 8) {
                if !readOnly && !rawOnly {
                    Tabs(options: [("raw", "Raw"), ("rich", "Rich")], selection: $mode, style: .segmented)
                }
                if document.isDirty {
                    HStack(spacing: 5) {
                        Circle().fill(Theme.warning).frame(width: Theme.statusDot, height: Theme.statusDot)
                        Text("Unsaved").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                    .accessibilityElement(children: .combine)
                }
                if !readOnly {
                    Button("Revert") { Task { await document.revert(); error = nil } }
                        .secondaryButton()
                        .disabled(!document.isDirty)
                    Button("Save") { save(overwrite: false) }
                        .primaryButton()
                        .keyboardShortcut("s", modifiers: .command)
                        .disabled(!document.isDirty || document.changedOnDisk)
                }
                if let close {
                    Button("Close") { close() }
                        .secondaryButton()
                        .keyboardShortcut(.cancelAction)
                }
            }
        } content: {
            switch document.state {
            case .loading:
                SkeletonLines(rows: 12, framed: false)
                    .padding(Theme.cardPadding)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            case .failed(let message):
                Label(message, systemImage: "exclamationmark.triangle").foregroundStyle(Theme.dangerText)
            case .ready:
                editorContent
            }
        }
    }

    /// The ready state: the changed-on-disk banner, a save error, and the
    /// raw or rich editor filling what the card has left.
    @ViewBuilder private var editorContent: some View {
        if document.changedOnDisk {
            ChangedOnDiskBanner(
                reload: { Task { await document.load(); error = nil } },
                overwrite: { save(overwrite: true) })
        }
        if let error {
            Label(error, systemImage: "exclamationmark.triangle")
                .font(Theme.caption).foregroundStyle(Theme.dangerText)
        }
        // Fills the card, and the card fills what the page has left.
        let raw = rawOnly || mode == "raw"
        let focused = raw ? editing : richFocused
        Group {
            if readOnly {
                TextEditor(text: .constant(document.text))
                    .font(Theme.font(size: 13, design: .monospaced))
                    .scrollContentBackground(.hidden)
            } else if raw {
                TextEditor(text: $document.text)
                    .font(Theme.font(size: 13, design: .monospaced))
                    .scrollContentBackground(.hidden)
                    .focused($editing)
            } else {
                MarkdownTextView(text: $document.text, isFocused: $richFocused,
                                 focusToken: richFocusToken, accessibilityLabel: title)
            }
        }
        .disabled(readOnly)
        .focusEffectDisabled()
        .padding(10)
        .background(raw ? Theme.code : Theme.field,
                    in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
            .strokeBorder(focused ? Theme.borderHover : Theme.border, lineWidth: 1))
        .focusRing(focused, cornerRadius: Theme.controlRadius, afterPointer: true)
        .frame(minHeight: 160, maxHeight: .infinity)
        .accessibilityLabel(title)
        .onChange(of: mode) {
            UserDefaults.standard.set(mode, forKey: Self.modeKey)
            if mode == "raw" { editing = true } else { richFocusToken += 1 }
        }
    }

    private func save(overwrite: Bool) {
        Task {
            switch await document.save(overwrite: overwrite) {
            case .saved:
                error = nil
                onSaved()
            case .changedOnDisk:
                error = nil
            case .failed(let message):
                error = message
            }
        }
    }
}

private struct ChangedOnDiskBanner: View {
    var reload: () -> Void
    var overwrite: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle").foregroundStyle(Theme.warningText)
            Text("This file changed on disk since you opened it. Reload to see that version, or overwrite it with yours.")
                .font(Theme.caption).foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            Button("Reload", action: reload).secondaryButton()
            Button("Overwrite", action: overwrite).secondaryButton()
        }
        .padding(12)
        .background(Theme.warning.opacity(0.08), in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.warning.opacity(0.4), lineWidth: 1))
    }
}
