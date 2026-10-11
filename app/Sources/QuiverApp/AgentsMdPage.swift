import AppKit
import QuiverCore
import SwiftUI

/// The shared `~/.quiver/AGENTS.md`, where swe has linked it, and the
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
                model.checkDisk()
            }
            .iconButton()
            .help("Check links and reread the file")
        } content: {
            Tabs(options: [(Scope.global, "Global"), (Scope.project, "Project")], selection: $scope, style: .underline)
            switch scope {
            case .global: GlobalInstructions()
            case .project: ProjectInstructions()
            }
        }
        .task(id: app.swePath) { await model.load(swe: app.swe) }
        // Agents rewrite these files while they work; pick that up on return.
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            model.checkDisk()
        }
    }
}

private struct GlobalInstructions: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let model = app.agentsMd
        switch model.check {
        case .idle, .loading:
            LoadingCard(text: "Checking links…")
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded(let report):
            VStack(alignment: .leading, spacing: 12) {
                LinksStrip(report: report)
                if let document = model.global {
                    let linked = report.instructionRows.filter { $0.instructions?.state == .linked }.count
                    InstructionEditor(
                        document: document,
                        title: displayPath(document.url),
                        subtitle: linked == 1 ? "Linked into 1 harness" : "Linked into \(linked) harnesses")
                }
            }
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
            .background(Theme.card, in: .rect(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.border, lineWidth: 1))
    }
}

/// Where each harness's instruction file points, from `swe init --check`,
/// as one strip of dots and names so the editor keeps the full width.
private struct LinksStrip: View {
    @Environment(AppModel.self) private var app
    var report: InitCheck

    var body: some View {
        let model = app.agentsMd
        Strip {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text("Harnesses").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                    .help("From swe init --check")
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
                        .help("Runs swe init, which links what is missing and never replaces a real file.")
                    }
                    .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 4 }
                }
            }
            if report.instructionRows.contains(where: { $0.instructions?.state.isBlocked == true }) {
                Text("A real file is in the way for some harnesses. swe init leaves those alone; swe init --force in Terminal replaces them.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
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
            Circle().fill(color).frame(width: 7, height: 7)
            Text(name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary).lineLimit(1)
            // Only what needs attention gets words; a dot says linked.
            if link.state != .linked {
                Text(link.state.title.lowercased()).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
        .help("\(link.state.title): \(displayPath(URL(fileURLWithPath: link.path)))")
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
                Tabs(options: files.map { ($0.name, tabTitle($0)) }, selection: $selectedFile, style: .table)
                if let file = files.first(where: { $0.name == selectedFile }) {
                    let document = model.document(for: file)
                    InstructionEditor(document: document, title: file.name, subtitle: subtitle(file, document)) {
                        model.rescanProject()
                    }
                    .id(file.name)
                }
            }
        }
        .onAppear(perform: restore)
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

    private func restore() {
        guard app.agentsMd.projectFolder == nil,
              let path = UserDefaults.standard.string(forKey: Self.folderKey) else { return }
        var isDirectory: ObjCBool = false
        if FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory), isDirectory.boolValue {
            app.agentsMd.openProject(URL(fileURLWithPath: path))
        }
    }
}

/// A plain-text editor for one instruction file: save, revert, an unsaved
/// dot, and a stop when the file changed on disk since it was opened.
struct InstructionEditor: View {
    @Bindable var document: InstructionDocument
    var title: String
    var subtitle: String
    var onSaved: () -> Void = {}
    @State private var error: String?
    @FocusState private var editing: Bool

    var body: some View {
        Card(title: title, subtitle: subtitle) {
            HStack(spacing: 8) {
                if document.isDirty {
                    HStack(spacing: 5) {
                        Circle().fill(Theme.warning).frame(width: 7, height: 7)
                        Text("Unsaved").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                    .accessibilityElement(children: .combine)
                }
                Button("Revert") { document.revert(); error = nil }
                    .secondaryButton()
                    .disabled(!document.isDirty)
                Button("Save") { save(overwrite: false) }
                    .primaryButton()
                    .keyboardShortcut("s", modifiers: .command)
                    .disabled(!document.isDirty || document.changedOnDisk)
            }
        } content: {
            switch document.state {
            case .loading:
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed(let message):
                Label(message, systemImage: "exclamationmark.triangle").foregroundStyle(Theme.dangerText)
            case .ready:
                if document.changedOnDisk {
                    ChangedOnDiskBanner(
                        reload: { document.load(); error = nil },
                        overwrite: { save(overwrite: true) })
                }
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(Theme.caption).foregroundStyle(Theme.dangerText)
                }
                // Fills the card, and the card fills what the page has left.
                TextEditor(text: $document.text)
                    .font(.system(size: 13, design: .monospaced))
                    .scrollContentBackground(.hidden)
                    .focused($editing)
                    .focusEffectDisabled()
                    .padding(10)
                    .background(Theme.code, in: .rect(cornerRadius: Theme.controlRadius))
                    .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
                        .strokeBorder(editing ? Theme.borderHover : Theme.border, lineWidth: 1))
                    .focusRing(editing, cornerRadius: Theme.controlRadius, afterPointer: true)
                    .frame(minHeight: 160, maxHeight: .infinity)
                    .accessibilityLabel(title)
            }
        }
    }

    private func save(overwrite: Bool) {
        switch document.save(overwrite: overwrite) {
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
