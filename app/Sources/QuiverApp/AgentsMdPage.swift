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
        Page(title: "AGENTS.md", subtitle: "The instructions every harness reads, and each project's own.") {
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
            ProgressView().frame(maxWidth: .infinity)
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded(let report):
            HStack(alignment: .top, spacing: Theme.gutter) {
                if let document = model.global {
                    let linked = report.instructionRows.filter { $0.instructions?.state == .linked }.count
                    InstructionEditor(
                        document: document,
                        title: displayPath(document.url),
                        subtitle: linked == 1 ? "Linked into 1 harness" : "Linked into \(linked) harnesses")
                }
                LinksCard(report: report)
                    .frame(width: 300)
            }
        }
    }
}

/// Where each harness's instruction file points, from `swe init --check`.
private struct LinksCard: View {
    @Environment(AppModel.self) private var app
    var report: InitCheck

    var body: some View {
        let model = app.agentsMd
        Card(title: "Harnesses", subtitle: "From swe init --check") {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(report.instructionRows) { row in
                    if let link = row.instructions {
                        LinkRow(name: row.name, link: link)
                    }
                }
            }
            if report.instructionRows.contains(where: { $0.instructions?.state.isBlocked == true }) {
                Text("A real file is in the way for some harnesses. swe init leaves those alone; swe init --force in Terminal replaces them.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let error = model.linkError {
                Text(error).font(Theme.caption).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if report.instructionsNeedInit || model.linking {
                Divider().overlay(Theme.border)
                HStack(spacing: 8) {
                    Button(model.linking ? "Linking…" : "Link with swe init", systemImage: "link") {
                        Task { await model.link(swe: app.swe) }
                    }
                    .primaryButton()
                    .disabled(model.linking)
                    if model.linking { ProgressView().controlSize(.small) }
                }
                Text("Runs swe init, which links what is missing and never replaces a real file.")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
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
        HStack(spacing: 8) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary).lineLimit(1)
            Spacer(minLength: 8)
            Text(link.state.title).font(Theme.caption).foregroundStyle(Theme.textSecondary)
        }
        .help(displayPath(URL(fileURLWithPath: link.path)))
        .accessibilityElement(children: .combine)
    }
}

private struct ProjectInstructions: View {
    static let folderKey = "agentsProjectFolder"

    @Environment(AppModel.self) private var app
    @State private var selectedFile = AgentsModel.projectFileNames[0]
    @State private var pendingFolder: URL?

    var body: some View {
        let model = app.agentsMd
        VStack(alignment: .leading, spacing: Theme.gutter) {
            Card {
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
                ProgressView().frame(maxWidth: .infinity)
            case .failed(let message):
                Label(message, systemImage: "exclamationmark.triangle").foregroundStyle(Theme.danger)
            case .ready:
                if document.changedOnDisk {
                    ChangedOnDiskBanner(
                        reload: { document.load(); error = nil },
                        overwrite: { save(overwrite: true) })
                }
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(Theme.caption).foregroundStyle(Theme.danger)
                }
                TextEditor(text: $document.text)
                    .font(.system(size: 13, design: .monospaced))
                    .scrollContentBackground(.hidden)
                    .padding(10)
                    .background(Theme.subtle, in: .rect(cornerRadius: Theme.controlRadius))
                    .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))
                    .frame(height: 540)
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
            Image(systemName: "exclamationmark.triangle").foregroundStyle(Theme.warning)
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
