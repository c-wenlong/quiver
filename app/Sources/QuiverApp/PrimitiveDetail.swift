import QuiverCore
import SwiftUI

// MARK: - Shared detail pieces

/// The lock line over anything that isn't the user's own: a plugin out of
/// a GitHub marketplace or a skill in a checkout gets its edits undone by
/// whatever manages that copy, so the page shows it read-only.
struct PrimitiveLockNote: View {
    var text: String

    var body: some View {
        Label(text, systemImage: "lock.fill")
            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            .fixedSize(horizontal: false, vertical: true)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.field, in: .rect(cornerRadius: Theme.controlRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
                .strokeBorder(Theme.border, lineWidth: 1))
    }
}

/// "‹ Plugins": back to the list the detail page covers.
struct PrimitiveBack: View {
    var title: String
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Image(systemName: "chevron.left").font(.system(size: 11, weight: .semibold))
                Text(title)
            }
        }
        .secondaryButton()
    }
}

/// The changed-on-disk banner, the same one the AGENTS.md editor shows:
/// reload the version on disk or overwrite it, never save over it blind.
private struct PrimitiveDiskBanner: View {
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

/// One labeled row of a manifest or frontmatter form.
private struct FieldRow: View {
    var label: String
    @Binding var value: String
    var disabled = false

    var body: some View {
        LabeledContent {
            TextField(label, text: $value)
                .textFieldStyle(.roundedBorder)
                .font(Theme.bodySmall)
                .disabled(disabled)
        } label: {
            Text(label).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                .frame(width: 90, alignment: .leading)
        }
    }
}

// MARK: - Manifest card

/// The editable fields of `.claude-plugin/plugin.json` as a form. Writes
/// go through PluginManifestFields.apply, which changes only the fields
/// the form shows and leaves every other key and the file's order alone;
/// the changed-on-disk guard is InstructionDocument's, the same stamp
/// the file editors use.
struct ManifestCard: View {
    var url: URL
    var onSaved: () -> Void
    @State private var document: InstructionDocument?
    @State private var fields = PluginManifestFields()
    @State private var error: String?

    var body: some View {
        Card(title: "Manifest", subtitle: displayPath(url)) {
            if let document {
                if document.changedOnDisk {
                    PrimitiveDiskBanner(reload: { document.load(); read() },
                                        overwrite: { save(overwrite: true) })
                }
                if case .failed(let message) = document.state {
                    Label(message, systemImage: "exclamationmark.triangle").foregroundStyle(Theme.dangerText)
                } else {
                    Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 12, verticalSpacing: 10) {
                        GridRow { FieldRow(label: "Description", value: $fields.description) }
                        GridRow { FieldRow(label: "Version", value: $fields.version) }
                        GridRow { FieldRow(label: "Author", value: $fields.authorName) }
                        GridRow { FieldRow(label: "Email", value: $fields.authorEmail) }
                        GridRow { FieldRow(label: "Author URL", value: $fields.authorURL) }
                        GridRow { FieldRow(label: "Homepage", value: $fields.homepage) }
                        GridRow { FieldRow(label: "Repository", value: $fields.repository) }
                        GridRow { FieldRow(label: "License", value: $fields.license) }
                        GridRow { FieldRow(label: "Keywords", value: $fields.keywords) }
                    }
                    ForEach(fields.problems, id: \.self) { problem in
                        Label(problem, systemImage: "exclamationmark.triangle")
                            .font(Theme.caption).foregroundStyle(Theme.dangerText)
                    }
                    if let error {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .font(Theme.caption).foregroundStyle(Theme.dangerText)
                    }
                    HStack(spacing: 8) {
                        Spacer()
                        Button("Revert") { read() }.secondaryButton().disabled(!document.isDirty)
                        Button("Save") { save(overwrite: false) }.primaryButton()
                            .disabled(!document.isDirty || document.changedOnDisk || !fields.problems.isEmpty)
                    }
                }
            } else {
                ProgressView()
            }
        }
        .task {
            guard document == nil else { return }
            let doc = InstructionDocument(url: url)
            doc.load()
            document = doc
            read()
        }
    }

    /// Pull the form's fields back out of the document's current text.
    private func read() {
        guard let document else { return }
        fields = PluginManifestFields(parsing: document.text)
        document.revert()
        error = nil
    }

    private func save(overwrite: Bool) {
        guard let document else { return }
        do {
            document.text = try fields.apply(to: document.text)
        } catch {
            self.error = "Couldn't write the manifest: \(error.localizedDescription)"
            return
        }
        switch document.save(overwrite: overwrite) {
        case .saved: error = nil; onSaved()
        case .changedOnDisk: error = nil
        case .failed(let message): error = message
        }
    }
}

// MARK: - Frontmatter card

/// A SKILL.md's frontmatter as a form: `key: value` lines editable (the
/// `name` key is the skill's id and stays read-only), the body and any
/// line the form can't show kept byte for byte.
struct FrontmatterCard: View {
    var url: URL
    var editable: Bool
    var onSaved: () -> Void
    @State private var document: InstructionDocument?
    @State private var fields: [(key: String, value: String)] = []
    @State private var error: String?

    var body: some View {
        Card(title: "Frontmatter", subtitle: displayPath(url)) {
            if let document {
                if document.changedOnDisk {
                    PrimitiveDiskBanner(reload: { document.load(); read() },
                                        overwrite: { save(overwrite: true) })
                }
                if case .failed(let message) = document.state {
                    Label(message, systemImage: "exclamationmark.triangle").foregroundStyle(Theme.dangerText)
                } else {
                    Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 12, verticalSpacing: 10) {
                        ForEach(fields.indices, id: \.self) { i in
                            GridRow {
                                FieldRow(label: fields[i].key, value: $fields[i].value,
                                         disabled: !editable || fields[i].key == "name")
                            }
                        }
                    }
                    if fields.isEmpty {
                        Text("No editable fields.").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                    if let error {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .font(Theme.caption).foregroundStyle(Theme.dangerText)
                    }
                    if editable {
                        HStack(spacing: 8) {
                            Spacer()
                            Button("Revert") { read() }.secondaryButton().disabled(!document.isDirty)
                            Button("Save") { save(overwrite: false) }.primaryButton()
                                .disabled(!document.isDirty || document.changedOnDisk)
                        }
                    }
                }
            } else {
                ProgressView()
            }
        }
        .task {
            guard document == nil else { return }
            let doc = InstructionDocument(url: url)
            doc.load()
            document = doc
            read()
        }
    }

    private func read() {
        guard let document else { return }
        fields = MarkdownFrontmatter(text: document.text)?.fields
            .map { ($0.key, $0.value) } ?? []
        document.revert()
        error = nil
    }

    private func save(overwrite: Bool) {
        guard let document else { return }
        document.text = MarkdownFrontmatter.update(document.text, set: fields)
        switch document.save(overwrite: overwrite) {
        case .saved: error = nil; onSaved()
        case .changedOnDisk: error = nil
        case .failed(let message): error = message
        }
    }
}

// MARK: - Contents and file pane

/// A plugin or skill folder's files, grouped by kind, with add and trash
/// when the folder is the user's own. Selecting a file opens it in the
/// InstructionEditor below; binaries and anything over a megabyte can't
/// preview.
struct PartsCard: View {
    var root: URL
    var editable: Bool
    /// Which "New" entries the accessory menu offers; empty for a skill.
    var kinds: [PrimitiveParts.Kind] = []
    var onChanged: () -> Void

    @State private var tree: PrimitiveTree?
    @State private var selected: String?
    @State private var newPart: PrimitiveParts.Kind?
    @State private var newName = ""
    @State private var newError: String?
    @State private var trashEntry: PrimitiveTree.Entry?
    @State private var error: String?

    var body: some View {
        Card(title: "Contents", subtitle: displayPath(root)) {
            if editable && !kinds.isEmpty {
                NewPartMenu(kinds: kinds) { newPart = $0 }
            }
        } content: {
            if let tree {
                ForEach(tree.sections) { section in
                    VStack(alignment: .leading, spacing: 0) {
                        Text("\(section.kind.title) · \(section.entries.count)")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .padding(.bottom, 6)
                        ForEach(section.entries) { entry in
                            row(entry)
                        }
                    }
                }
            } else {
                ProgressView()
            }
            if let selected {
                PrimitiveFilePane(path: selected, editable: editable)
            }
            if let error {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
        }
        .task { reload() }
        .alert("New \(newPart?.title ?? "")", isPresented: .init(
            get: { newPart != nil }, set: { if !$0 { newPart = nil } })) {
            TextField("name", text: $newName)
            Button("Create") { create() }
            Button("Cancel", role: .cancel) { newPart = nil; newName = "" }
        } message: {
            Text(newError ?? "A new \(newPart?.title ?? "part") under \(displayPath(root)).")
        }
        .alert("Move “\(trashEntry?.name ?? "")” to the Trash?", isPresented: .init(
            get: { trashEntry != nil }, set: { if !$0 { trashEntry = nil } })) {
            Button("Move to Trash", role: .destructive) { trash() }
            Button("Cancel", role: .cancel) { trashEntry = nil }
        } message: {
            Text("You can restore it from the Trash.")
        }
    }

    private func row(_ entry: PrimitiveTree.Entry) -> some View {
        HStack(spacing: 8) {
            Button {
                selected = entry.previewPath
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: entry.isFolder ? "folder.fill" : "doc.text")
                        .foregroundStyle(entry.isFolder ? Theme.accent : Theme.textSecondary)
                    Text(entry.name)
                        .font(Theme.bodySmall)
                        .foregroundStyle(selected == entry.previewPath ? Theme.accent : Theme.textPrimary)
                    Spacer()
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            if editable {
                Button { trashEntry = entry } label: {
                    Image(systemName: "trash").font(.system(size: 11))
                }
                .secondaryButton()
            }
        }
        .padding(.vertical, 3)
    }

    private func reload() {
        tree = PrimitiveTree(root: root)
        if let selected, !(tree?.sections.flatMap(\.entries).contains { $0.previewPath == selected } ?? false) {
            self.selected = nil
        }
    }

    private func create() {
        guard let kind = newPart else { return }
        guard PrimitiveParts.isValidName(newName) else {
            newError = "Use kebab-case: lowercase letters, numbers and dashes."
            return
        }
        do {
            let file = try PrimitiveParts.create(kind, named: newName, in: root)
            newPart = nil
            newName = ""
            newError = nil
            selected = file.path
            reload()
            onChanged()
        } catch {
            newError = error.localizedDescription
        }
    }

    private func trash() {
        guard let entry = trashEntry else { return }
        do {
            try PrimitiveParts.trash(URL(fileURLWithPath: entry.path))
            if selected == entry.previewPath { selected = nil }
            trashEntry = nil
            reload()
            onChanged()
        } catch let failure {
            error = "Couldn't move \(entry.name) to the Trash: \(failure.localizedDescription)"
            trashEntry = nil
        }
    }
}

/// The "＋ New" menu a detail page shows for an editable folder.
struct NewPartMenu: View {
    var kinds: [PrimitiveParts.Kind]
    var pick: (PrimitiveParts.Kind) -> Void

    var body: some View {
        Menu {
            ForEach(kinds, id: \.self) { kind in
                Button("New \(kind.title)") { pick(kind) }
            }
        } label: {
            Label("New", systemImage: "plus")
        }
        .secondaryButton()
    }
}

/// One file in the InstructionEditor, reloading when a different file is
/// picked. Editable only where the page says the folder is the user's.
struct PrimitiveFilePane: View {
    var path: String
    var editable: Bool
    var onSaved: () -> Void = {}
    @State private var document: InstructionDocument?

    private var url: URL { URL(fileURLWithPath: path) }

    var body: some View {
        Group {
            if !PrimitiveTree.isTextFile(url) {
                Text("Can't preview this file.")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(8)
            } else if let document {
                InstructionEditor(document: document, title: url.lastPathComponent,
                                  subtitle: displayPath(url), readOnly: !editable, onSaved: onSaved)
            } else {
                ProgressView().frame(maxWidth: .infinity, minHeight: 120)
            }
        }
        .task(id: path) {
            let doc = InstructionDocument(url: url)
            doc.load()
            document = doc
        }
    }
}

// MARK: - Plugin detail

/// One plugin as a page: header with its editable display name, its
/// harnesses, a lock note when it isn't the user's, the manifest form and
/// the folder contents opened in the InstructionEditor.
struct PluginDetailView: View {
    @Environment(AppModel.self) private var app
    var entry: ExplorePlugin
    var market: PluginMarketplace
    var home: URL
    var onBack: () -> Void

    @State private var name = ""
    @State private var nameError: String?

    private var plugin: CatalogPlugin { entry.row.plugin }
    private var editableDir: URL? {
        PrimitiveOwnership.pluginDirectory(plugin, in: market, home: home)
    }
    private var dir: URL? {
        editableDir ?? plugin.sourcePath.map { URL(fileURLWithPath: $0) }
    }
    private var editable: Bool { editableDir != nil }
    private var manifest: URL? {
        editableDir.map { $0.appending(path: ".claude-plugin/plugin.json") }
            .flatMap { FileManager.default.fileExists(atPath: $0.path) ? $0 : nil }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            PrimitiveBack(title: "Plugins", action: onBack)
            if !editable { lockNote }
            header
            if let manifest { ManifestCard(url: manifest, onSaved: reload) }
            if let dir {
                PartsCard(root: dir, editable: editable,
                          kinds: [.skill, .command, .agent], onChanged: reload)
            } else {
                Text("This plugin has no local folder: its files come from a checkout swe manages.")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            }
        }
        .onAppear {
            name = app.primitiveNames.name(forPlugin: plugin.ref) ?? plugin.displayName ?? plugin.name
        }
    }

    private var lockNote: PrimitiveLockNote {
        let github = market.harnesses.values.contains { ["github", "git"].contains($0.sourceType ?? "") }
        return PrimitiveLockNote(text: github
            ? "Updates from GitHub would undo edits, so this plugin is read-only."
            : "Only plugins in your ~/.quiver/plugins hub can be edited, so this one is read-only.")
    }

    private var header: some View {
        Card {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: "puzzlepiece.extension.fill")
                    .font(.system(size: 28)).foregroundStyle(Theme.accent)
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        if editable {
                            TextField("Name", text: $name, onCommit: saveName)
                                .textFieldStyle(.roundedBorder)
                                .font(.system(size: 16, weight: .semibold))
                                .frame(maxWidth: 320)
                        } else {
                            Text(app.primitiveNames.name(forPlugin: plugin.ref)
                                 ?? plugin.displayName ?? plugin.name)
                                .font(.system(size: 16, weight: .semibold))
                        }
                        if let version = plugin.version {
                            Text("v\(version)").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        }
                        Spacer()
                        if let dir {
                            Button { NSWorkspace.shared.activateFileViewerSelecting([dir]) } label: {
                                Label("Show in Finder", systemImage: "folder")
                            }
                            .secondaryButton()
                        }
                    }
                    Text(plugin.ref).font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                    HStack(spacing: 8) {
                        Text(app.primitiveNames.name(forMarketplace: market.name) ?? market.name)
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        if let tag = market.harnesses.values.first?.sourceType {
                            Badge(tag == "directory" ? "local" : tag)
                        }
                        ForEach(entry.harnesses, id: \.self) { harness in
                            if let state = plugin.harnesses[harness] {
                                Badge("\(HarnessMark(harness).name) · \(state.status.title)")
                            }
                        }
                    }
                    if let description = plugin.description, !description.isEmpty {
                        Text(description).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let nameError {
                        Label(nameError, systemImage: "exclamationmark.triangle")
                            .font(Theme.caption).foregroundStyle(Theme.dangerText)
                    }
                }
            }
        }
    }

    /// Store the display name in Quiver's overlay (the plugin's id never
    /// changes, so nothing is reinstalled), and in the Codex manifest's
    /// `interface.displayName` when the plugin ships one.
    private func saveName() {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        app.primitiveNames.set(trimmed.isEmpty ? nil : trimmed, forPlugin: plugin.ref)
        do {
            try app.primitiveNames.save(to: PluginDisplayNames.file(home: home))
            nameError = nil
        } catch {
            nameError = "Couldn't save the name: \(error.localizedDescription)"
        }
        guard editable, let dir else { return }
        let codex = dir.appending(path: ".codex-plugin/plugin.json")
        let shown = trimmed.isEmpty ? plugin.name : trimmed
        if let text = try? String(contentsOf: codex, encoding: .utf8),
           let edited = try? PluginManifestFields.setCodexDisplayName(shown, in: text) {
            try? Data(edited.utf8).write(to: codex, options: .atomic)
        }
    }

    private func reload() {
        Task {
            await app.pluginCatalog.load(swe: app.swe, for: app.swePath)
            await app.skills.load(for: app.swePath)
        }
    }
}

// MARK: - Skill detail

/// One skill as a page: its id and source, the harnesses that load it,
/// the editable frontmatter of its SKILL.md and its folder's files.
struct SkillDetailView: View {
    @Environment(AppModel.self) private var app
    var skill: Skill
    var home: URL
    var onBack: () -> Void
    /// The link chips a shared skill carries, from the Skills page.
    var links: AnyView?

    private var root: URL { URL(fileURLWithPath: skill.path) }
    private var editable: Bool {
        PrimitiveOwnership.isEditable(skillPath: skill.path, home: home)
    }
    private var skillFile: URL? {
        let file = root.appending(path: "SKILL.md")
        return FileManager.default.fileExists(atPath: file.path) ? file : nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            PrimitiveBack(title: "Skills", action: onBack)
            if !editable {
                PrimitiveLockNote(text: "Only skills that live under ~/.quiver can be edited, so \(skill.name) is read-only.")
            }
            Card {
                HStack(alignment: .top, spacing: 14) {
                    Image(systemName: "graduationcap.fill")
                        .font(.system(size: 28)).foregroundStyle(Theme.accent)
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 8) {
                            Text(skill.name).font(.system(size: 16, weight: .semibold))
                            Badge(skill.source.title)
                            Spacer()
                            Button { NSWorkspace.shared.activateFileViewerSelecting([root]) } label: {
                                Label("Show in Finder", systemImage: "folder")
                            }
                            .secondaryButton()
                        }
                        Text(displayPath(skill.path)).font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                        if !skill.description.isEmpty {
                            Text(skill.description).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if let links { links }
                    }
                }
            }
            if let skillFile { FrontmatterCard(url: skillFile, editable: editable, onSaved: reload) }
            PartsCard(root: root, editable: editable, onChanged: reload)
        }
    }

    private func reload() {
        Task { await app.skills.load(for: app.swePath) }
    }
}
