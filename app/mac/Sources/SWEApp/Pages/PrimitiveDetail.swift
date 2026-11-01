import SWECore
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
    /// One labeled form row: the label and the manifest field it edits.
    struct FieldDef: Identifiable {
        var label: String
        var field: WritableKeyPath<PluginManifestFields, String>
        var id: String { label }
    }

    /// One form field per row; the card folds to `foldLimit` of them.
    static let foldLimit = 3
    static let fieldRows: [FieldDef] = [
        FieldDef(label: "Description", field: \.description),
        FieldDef(label: "Version", field: \.version),
        FieldDef(label: "Author", field: \.authorName),
        FieldDef(label: "Email", field: \.authorEmail),
        FieldDef(label: "Author URL", field: \.authorURL),
        FieldDef(label: "Homepage", field: \.homepage),
        FieldDef(label: "Repository", field: \.repository),
        FieldDef(label: "License", field: \.license),
        FieldDef(label: "Keywords", field: \.keywords),
    ]

    var url: URL
    var onSaved: () -> Void
    @State private var document: InstructionDocument?
    @State private var fields = PluginManifestFields()
    /// Whether the fold is open for this card's stay on the page.
    @State private var showsAll = false
    /// The fields as last read or saved: dirty is `fields != loaded`,
    /// which edits to `fields` alone must drive, not `document.text`.
    @State private var loaded = PluginManifestFields()
    @State private var error: String?

    private var formDirty: Bool { fields != loaded }

    var body: some View {
        Card(title: "Manifest", subtitle: displayPath(url)) {
            if let document {
                if document.changedOnDisk {
                    PrimitiveDiskBanner(reload: { Task { await document.load(); await read() } },
                                        overwrite: { save(overwrite: true) })
                }
                if case .failed(let message) = document.state {
                    Label(message, systemImage: "exclamationmark.triangle").foregroundStyle(Theme.dangerText)
                } else {
                    Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 12, verticalSpacing: 10) {
                        ForEach(RowFold.visible(Self.fieldRows, limit: Self.foldLimit, expanded: showsAll)) { row in
                            GridRow {
                                FieldRow(label: row.label,
                                         value: Binding(get: { fields[keyPath: row.field] },
                                                        set: { fields[keyPath: row.field] = $0 }))
                            }
                        }
                    }
                    if RowFold.hiddenCount(total: Self.fieldRows.count, limit: Self.foldLimit) > 0 {
                        ExpandRow(isExpanded: showsAll,
                                  hidden: RowFold.hiddenCount(total: Self.fieldRows.count, limit: Self.foldLimit),
                                  help: (expand: "Show every manifest field",
                                         collapse: "Show the first \(Self.foldLimit) fields"),
                                  edge: .inset) {
                            withAnimation(.easeOut(duration: Theme.fadeDuration)) { showsAll.toggle() }
                        }
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
                        Button("Revert") { revert() }.secondaryButton().disabled(!formDirty)
                        Button("Save") { save(overwrite: false) }.primaryButton()
                            .disabled(!formDirty || document.changedOnDisk || !fields.problems.isEmpty)
                    }
                }
            } else {
                SkeletonLines(rows: 4, framed: false)
            }
        }
        // Keyed by the file: a card reused for another plugin or skill
        // reads that one's file, not the first one's again.
        .task(id: url) {
            guard document?.url != url else { return }
            let doc = InstructionDocument(url: url)
            await doc.load()
            // A newer file took the card while this one read.
            guard !Task.isCancelled else { return }
            document = doc
            await read()
        }
    }

    /// Pull the form's fields back out of the document's current text.
    /// Revert first: the form reads the file's text, not edits a refused
    /// save left behind, and a revert after an outside change reloads it.
    private func read() async {
        guard let document else { return }
        await document.revert()
        fields = PluginManifestFields(parsing: document.text)
        loaded = fields
        error = nil
    }

    private func revert() { Task { await read() } }

    private func save(overwrite: Bool) {
        guard let document else { return }
        do {
            document.text = try fields.apply(to: document.text)
        } catch {
            self.error = "Couldn't write the manifest: \(error.localizedDescription)"
            return
        }
        // What this save writes: a field edited while it runs stays dirty.
        let submitted = fields
        Task {
            switch await document.save(overwrite: overwrite) {
            case .saved: loaded = submitted; error = nil; onSaved()
            case .changedOnDisk: error = nil
            case .failed(let message): error = message
            }
        }
    }
}

// MARK: - Frontmatter card

/// A SKILL.md's frontmatter as a form: `key: value` lines editable (the
/// `name` key is the skill's id and stays read-only), the body and any
/// line the form can't show kept byte for byte.
struct FrontmatterCard: View {
    /// One frontmatter field per row, folded to `foldLimit` of them.
    static let foldLimit = 3

    var url: URL
    var editable: Bool
    var onSaved: () -> Void
    @State private var document: InstructionDocument?
    @State private var fields: [(key: String, value: String)] = []
    @State private var showsAll = false
    /// The fields as last read or saved; dirty is theirs alone, since
    /// editing `fields` never touches `document.text`.
    @State private var loaded: [(key: String, value: String)] = []
    @State private var error: String?

    private var formDirty: Bool {
        !fields.elementsEqual(loaded) { $0.key == $1.key && $0.value == $1.value }
    }

    var body: some View {
        Card(title: "Frontmatter", subtitle: displayPath(url)) {
            if let document {
                if document.changedOnDisk {
                    PrimitiveDiskBanner(reload: { Task { await document.load(); await read() } },
                                        overwrite: { save(overwrite: true) })
                }
                if case .failed(let message) = document.state {
                    Label(message, systemImage: "exclamationmark.triangle").foregroundStyle(Theme.dangerText)
                } else {
                    Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 12, verticalSpacing: 10) {
                        ForEach(RowFold.visible(Array(fields.indices), limit: Self.foldLimit,
                                                expanded: showsAll), id: \.self) { i in
                            GridRow {
                                FieldRow(label: fields[i].key, value: Self.value($fields, at: i),
                                         disabled: !editable || fields[i].key == "name")
                            }
                        }
                    }
                    if RowFold.hiddenCount(total: fields.count, limit: Self.foldLimit) > 0 {
                        ExpandRow(isExpanded: showsAll,
                                  hidden: RowFold.hiddenCount(total: fields.count, limit: Self.foldLimit),
                                  help: (expand: "Show every frontmatter field",
                                         collapse: "Show the first \(Self.foldLimit) fields"),
                                  edge: .inset) {
                            withAnimation(.easeOut(duration: Theme.fadeDuration)) { showsAll.toggle() }
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
                            Button("Revert") { revert() }.secondaryButton().disabled(!formDirty)
                            Button("Save") { save(overwrite: false) }.primaryButton()
                                .disabled(!formDirty || document.changedOnDisk)
                        }
                    }
                }
            } else {
                SkeletonLines(rows: 4, framed: false)
            }
        }
        // Keyed by the file: a card reused for another plugin or skill
        // reads that one's file, not the first one's again.
        .task(id: url) {
            guard document?.url != url else { return }
            let doc = InstructionDocument(url: url)
            await doc.load()
            // A newer file took the card while this one read.
            guard !Task.isCancelled else { return }
            document = doc
            await read()
        }
    }

    /// Field `i`'s value for a row that can outlive it: a reload or revert
    /// shrinks `fields` while a row built for the old list still reads its
    /// binding, and `$fields[i]` traps out of range there. Past the end it
    /// reads empty and drops writes; the row goes with the next update.
    static func value(_ fields: Binding<[(key: String, value: String)]>, at i: Int) -> Binding<String> {
        Binding(get: { fields.wrappedValue.indices.contains(i) ? fields.wrappedValue[i].value : "" },
                set: { if fields.wrappedValue.indices.contains(i) { fields.wrappedValue[i].value = $0 } })
    }

    /// Revert first: the form reads the file's text, not edits a refused
    /// save left behind, and a revert after an outside change reloads it.
    private func read() async {
        guard let document else { return }
        await document.revert()
        fields = MarkdownFrontmatter(text: document.text)?.fields
            .map { ($0.key, $0.value) } ?? []
        loaded = fields
        error = nil
    }

    private func revert() { Task { await read() } }

    private func save(overwrite: Bool) {
        guard let document else { return }
        document.text = MarkdownFrontmatter.update(document.text, set: fields)
        // What this save writes: a field edited while it runs stays dirty.
        let submitted = fields
        Task {
            switch await document.save(overwrite: overwrite) {
            case .saved: loaded = submitted; error = nil; onSaved()
            case .changedOnDisk: error = nil
            case .failed(let message): error = message
            }
        }
    }
}

// MARK: - Contents and file pane

/// A plugin or skill folder's files, grouped by kind, with add and trash
/// when the folder is the user's own. Selecting a file opens it in a
/// sheet over the page; binaries and anything over a megabyte can't
/// preview.
struct PartsCard: View {
    /// A section header or a top-level entry is one row; the card folds
    /// to `foldLimit` of them. An opened folder's children ride along
    /// with its row and never count.
    static let foldLimit = 3

    /// One foldable row.
    private enum Unit: Identifiable {
        case header(PrimitiveTree.Section)
        case entry(PrimitiveTree.Entry)

        var id: String {
            switch self {
            case .header(let section): "h:" + section.kind.rawValue
            case .entry(let entry): "e:" + entry.path
            }
        }
    }

    var root: URL
    var editable: Bool
    /// Which "New" entries the accessory menu offers; empty for a skill.
    var kinds: [PrimitiveParts.Kind] = []
    /// The open file documents, owned by the detail page so a file
    /// switch or a step away and back keeps unsaved edits.
    var drafts: PrimitiveDrafts
    var onChanged: () -> Void

    @State private var tree: PrimitiveTree?
    /// The tree's paths that stay inside `root` once symlinks resolve
    /// (`PrimitiveTree.pathsInside`), worked out with the walk off the
    /// main actor: only these may be trashed or edited.
    @State private var inside: Set<String> = []
    /// The running tree walk, kept so a new reload cancels it before a
    /// stale tree can land over a newer one.
    @State private var treeTask: Task<Void, Never>?
    @State private var selected: String?
    @State private var sheetOpen = false
    /// The main window's size when the file was picked; the sheet opens
    /// at about 90% of it.
    @State private var sheetSize = CGSize(width: 1100, height: 720)
    @State private var expanded: Set<String> = []
    /// Whether the fold is open for this card's stay on the page.
    @State private var showsAll = false
    @State private var newPart: PrimitiveParts.Kind?
    @State private var newName = ""
    @State private var newError: String?
    /// The create in flight; a New opened since makes its answer stale.
    @State private var creating: UUID?
    @State private var trashEntry: PrimitiveTree.Entry?
    @State private var error: String?

    var body: some View {
        Card(title: "Contents", subtitle: displayPath(root), monoSubtitle: true) {
            if editable && !kinds.isEmpty {
                NewPartMenu(kinds: kinds) { creating = nil; newError = nil; newPart = $0 }
            }
        } content: {
            treeContent
            if let error {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
        }
        // Keyed on the root: a catalog reload can move the plugin's
        // folder, and cancelling the old task drops a walk that still
        // lands for it.
        .task(id: root) { reload() }
        .quiverSheet(isPresented: $sheetOpen, onDismiss: {
            // Drop the pick so reopening the same file restarts the
            // size and type check; a draft still in the store reopens
            // with its edits.
            selected = nil
        }) {
            if let selected {
                PrimitiveFileSheet(path: selected, root: root, editable: editable,
                                   drafts: drafts, windowSize: sheetSize, onSaved: onChanged)
            }
        }
        .alert("New \(newPart?.title ?? "")", isPresented: .init(
            get: { newPart != nil }, set: { if !$0 { newPart = nil } })) {
            TextField("name", text: $newName)
            Button("Create") { create() }
            Button("Cancel", role: .cancel) { newPart = nil; newName = ""; newError = nil }
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

    /// The folded tree: one row per group header or top-level entry, then
    /// a Show more row for the rest.
    @ViewBuilder private var treeContent: some View {
        if let tree {
            let units = Self.units(of: tree)
            let shown = RowFold.visible(units, limit: Self.foldLimit, expanded: showsAll)
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(shown.enumerated()), id: \.element.id) { index, unit in
                    switch unit {
                    case .header(let section):
                        Text("\(section.kind.title) · \(section.entries.count)")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .padding(.top, index == 0 ? 0 : 14)
                            .padding(.bottom, 6)
                    case .entry(let entry):
                        row(entry)
                    }
                }
            }
            if RowFold.hiddenCount(total: units.count, limit: Self.foldLimit) > 0 {
                ExpandRow(isExpanded: showsAll,
                          hidden: RowFold.hiddenCount(total: units.count, limit: Self.foldLimit),
                          help: (expand: "Show every file and section",
                                 collapse: "Show the first \(Self.foldLimit) rows"),
                          edge: .inset) {
                    withAnimation(.easeOut(duration: Theme.fadeDuration)) { showsAll.toggle() }
                }
            }
        } else {
            SkeletonRows(rows: 4, framed: false, shape: .file)
        }
    }

    /// The tree flattened for the fold: a unit per group header and per
    /// top-level entry, in display order.
    private static func units(of tree: PrimitiveTree) -> [Unit] {
        tree.sections.flatMap { section in
            [Unit.header(section)] + section.entries.map { Unit.entry($0) }
        }
    }

    private func row(_ entry: PrimitiveTree.Entry) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            rowLine(entry)
            if entry.isFolder, expanded.contains(entry.path) {
                ForEach(entry.children) { child in
                    rowLine(child)
                        .padding(.leading, 18)
                }
            }
        }
    }

    private func rowLine(_ entry: PrimitiveTree.Entry) -> some View {
        HStack(spacing: 8) {
            Button {
                if entry.isFolder {
                    if expanded.contains(entry.path) {
                        expanded.remove(entry.path)
                    } else {
                        expanded.insert(entry.path)
                    }
                } else {
                    present(entry)
                }
            } label: {
                HStack(spacing: 8) {
                    if entry.isFolder {
                        Image(systemName: "chevron.right")
                            .font(Theme.font(size: 9, weight: .semibold))
                            .foregroundStyle(Theme.textMuted)
                            .rotationEffect(.degrees(expanded.contains(entry.path) ? 90 : 0))
                            .frame(width: 10)
                    }
                    Image(systemName: entry.isFolder ? "folder.fill" : "doc.text")
                        .foregroundStyle(entry.isFolder ? Theme.accent : Theme.textSecondary)
                    Text(entry.name)
                        .font(Theme.bodySmall)
                        .foregroundStyle(selected == entry.previewPath ? Theme.interactive : Theme.textPrimary)
                    Spacer()
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            // A symlink inside the folder that resolves outside it is
            // not the user's to trash.
            if editable, inside.contains(entry.path) {
                Button { trashEntry = entry } label: {
                    Image(systemName: "trash").font(Theme.font(size: 11))
                }
                .secondaryButton()
                .accessibilityLabel("Move \(entry.name) to the Trash")
            }
        }
        .padding(.vertical, 3)
    }

    /// Open `entry` in the sheet; it runs the size, type and ownership
    /// check itself once it is up, and spins until that lands.
    private func present(_ entry: PrimitiveTree.Entry) {
        present(path: entry.previewPath)
    }

    private func present(path: String) {
        selected = path
        if let window = NSApp.keyWindow ?? NSApp.mainWindow {
            sheetSize = CGSize(width: max(900, window.frame.width * 0.9),
                               height: max(640, window.frame.height * 0.9))
        }
        sheetOpen = true
    }

    private func reload() {
        // The walk is disk work, and a big marketplace tree would freeze
        // the page if it ran here: it goes off the main actor like the
        // previewability check the file sheet runs. Which paths stay
        // inside the folder is worked out with it: `realpath` on a synced
        // or stale folder can block, so it runs on a GCD queue.
        treeTask?.cancel()
        let root = root
        treeTask = Task {
            let (walked, owned) = await offMain {
                let tree = PrimitiveTree(root: root)
                return (tree, tree.pathsInside(root))
            }
            guard !Task.isCancelled else { return }
            tree = walked
            inside = owned
            if let selected {
                let all = walked.sections.flatMap(\.entries).flatMap { [$0] + $0.children }
                if !all.contains(where: { $0.previewPath == selected }) { self.selected = nil }
            }
        }
    }

    /// The alert closes after any of its buttons runs, so a failed
    /// create opens it again, name kept, with the reason as its message.
    private func create() {
        guard let kind = newPart else { return }
        guard PrimitiveParts.isValidName(newName) else {
            newError = "Use kebab-case: lowercase letters, numbers and dashes."
            // After this close lands, not inside it.
            Task { @MainActor in newPart = kind }
            return
        }
        // create resolves the folder with realpath, which a synced or
        // stale folder can block: off the main actor, like the walk.
        let root = root, name = newName
        let request = UUID()
        creating = request
        Task {
            let result = await offMain({ Result { try PrimitiveParts.create(kind, named: name, in: root) } })
            // A New opened while this waited on disk owns the alert now.
            let current = creating == request
            if current { creating = nil }
            switch result {
            case .success(let file):
                // A folded card could hide the new file, so show every row.
                showsAll = true
                reload()
                onChanged()
                guard current else { return }
                newName = ""
                newError = nil
                // Straight into the editor, as picking the file would.
                present(path: file.path)
            case .failure(let error):
                guard current else { return }
                newError = error.localizedDescription
                newPart = kind
            }
        }
    }

    private func trash() {
        guard let entry = trashEntry else { return }
        trashEntry = nil
        let root = root
        Task {
            switch await offMain({ Result { try PrimitiveParts.trash(URL(fileURLWithPath: entry.path), inside: root) } }) {
            case .success:
                drafts.remove(under: entry.path)
                if selected == entry.previewPath { selected = nil }
                reload()
                onChanged()
            case .failure(let failure):
                error = "Couldn't move \(entry.name) to the Trash: \(failure.localizedDescription)"
            }
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

/// One file in a large sheet over the detail page: about 90% of the
/// window, at least 900 by 640, resizable. The header is the editor's
/// own (path in muted mono, Raw/Rich, Unsaved, Revert, Save, Close) and
/// Esc closes. The document is the page's drafts store's, so edits
/// survive close and reopen; closing with unsaved changes asks first.
struct PrimitiveFileSheet: View {
    var path: String
    /// The folder the file must stay inside, once symlinks resolve, to be
    /// editable; the sheet rechecks it off the main actor each time it
    /// opens, since a listed file may have become an outward link since.
    var root: URL
    /// The folder is the user's own; a file in it may be edited only while
    /// the check says it stays inside.
    var editable: Bool
    var drafts: PrimitiveDrafts
    /// The window's size when the file was picked.
    var windowSize: CGSize
    var onSaved: () -> Void = {}

    @Environment(\.dismissSheet) private var dismiss
    @State private var confirmingClose = false
    /// A save in the close prompt that failed: the reason shows in the
    /// sheet, which stays open.
    @State private var saveError: String?
    /// The size, type and inside-the-folder check for `path`, run off the
    /// main actor when the sheet opens; nil while it still runs.
    @State private var check: PrimitiveFileCheck?

    private var url: URL { URL(fileURLWithPath: path) }
    private var fileEditable: Bool { editable && check?.isInside == true }
    /// Read in the body so the sheet re-renders when the check's
    /// `document(for:)` puts the file's document into the store.
    private var document: InstructionDocument? { drafts.documents[path] }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let saveError {
                ErrorCard(message: saveError)
            }
            if !fileEditable, check?.canPreview == true {
                PrimitiveLockNote(text: "Only files inside the plugin or skill's own folder can be edited, so this one is read-only.")
            }
            switch check?.canPreview {
            case .none:
                SkeletonLines(rows: 12, framed: false)
                    .padding(Theme.cardPadding)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            case .some(false):
                HStack {
                    Text(displayPath(url))
                        .font(Theme.font(size: 12, design: .monospaced))
                        .foregroundStyle(Theme.textSecondary)
                        .lineLimit(1)
                    Spacer(minLength: 8)
                    Button("Close") { dismiss() }
                        .secondaryButton()
                        .keyboardShortcut(.cancelAction)
                }
                Text("Can't preview this file.")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .some(true):
                if let document {
                    InstructionEditor(document: document, title: url.lastPathComponent,
                                      subtitle: displayPath(url), monoSubtitle: true,
                                      readOnly: !fileEditable, close: requestClose, onSaved: onSaved)
                } else {
                    SkeletonLines(rows: 12, framed: false)
                        .padding(Theme.cardPadding)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                }
            }
        }
        .padding(20)
        .frame(minWidth: 900, idealWidth: max(900, windowSize.width * 0.9),
               minHeight: 640, idealHeight: max(640, windowSize.height * 0.9))
        .presentationSizing(.fitted)
        // The sheet runs the check itself: the card that presents it hands
        // down no per-file state, since a presented card's content is a
        // snapshot the overlay's own entries keep. Size, type and where the
        // file really lives go off the main actor on a GCD queue; a document
        // opens only for a file that can preview, and editing waits for the
        // inside-the-folder check. Dismissing cancels this, so a late result
        // can never land on a sheet that is already gone.
        .task(id: path) {
            let root = root
            let result = await offMain { PrimitiveFileCheck(path: path, root: root) }
            guard !Task.isCancelled else { return }
            check = result
            // Never in the body: document(for:) writes the drafts store,
            // which a body must not do mid-update.
            if result.canPreview { drafts.document(for: path) }
        }
        .onChange(of: document?.isDirty) {
            // A save or a revert from the editor's own buttons clears a
            // save error the sheet was showing.
            if document?.isDirty != true { saveError = nil }
        }
        .onExitCommand(perform: requestClose)
        // A click outside the card or Esc asks first too.
        .onSheetDismissRequest(requestClose)
        .alert("Unsaved changes", isPresented: $confirmingClose) {
            Button("Save") { saveAndClose() }
            Button("Discard", role: .destructive) {
                // Drop the draft outright: reopening reads the file fresh.
                drafts.remove(under: path)
                dismiss()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("“\(url.lastPathComponent)” has unsaved changes.")
        }
    }

    /// Close now when the file is clean, ask first when it is not.
    private func requestClose() {
        guard document?.isDirty == true else { dismiss(); return }
        confirmingClose = true
    }

    /// A failed save or a changed-on-disk stop keeps the sheet open; the
    /// editor's own error or banner says why.
    private func saveAndClose() {
        guard let document else { dismiss(); return }
        Task {
            switch await document.save() {
            case .saved where document.isDirty:
                // Typed while the save ran: that text isn't on disk yet.
                saveError = "New edits came in while saving. Save again to keep them."
                onSaved()
            case .saved:
                saveError = nil
                onSaved()
                dismiss()
            case .changedOnDisk:
                // The editor's own banner explains it.
                saveError = nil
            case .failed(let message):
                saveError = "Couldn't save \(url.lastPathComponent): \(message)"
            }
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
    /// The last Codex manifest write; the next waits for it, so two quick
    /// renames land in the order they were made.
    @State private var manifestWrite: Task<Void, Never>?
    @State private var drafts = PrimitiveDrafts()
    @State private var leaving = false
    @State private var leaveError: String?
    /// The plugin's folders, worked out off the main actor, with the ref
    /// they were worked out for; nil until the first lands.
    @State private var found: (ref: String, folders: PluginFolders)?

    /// What `.task(id:)` recomputes the folders for: a catalog reload can
    /// move the plugin's folder or its marketplace's registration.
    private struct FolderInputs: Equatable {
        var plugin: CatalogPlugin
        var market: PluginMarketplace
        var home: URL
        /// `refresh`: a reload (trashing the manifest, say) can leave the
        /// catalog's entry as it was.
        var refresh: Int
    }
    /// Bumped by `reload()` so the folders are worked out again.
    @State private var refresh = 0

    private var plugin: CatalogPlugin { entry.row.plugin }
    /// The folders for the plugin on screen; nil while they are worked
    /// out, so a result for a plugin opened before never shows.
    private var folders: PluginFolders? {
        guard let found, found.ref == plugin.ref else { return nil }
        return found.folders
    }
    private var dir: URL? { folders?.dir }
    private var editable: Bool { folders?.editable ?? false }
    private var manifest: URL? { folders?.manifest }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let leaveError {
                ErrorCard(message: leaveError)
            }
            if folders != nil, !editable { lockNote }
            header
            if folders == nil {
                SkeletonRows(rows: 5, shape: .file, label: "Reading the plugin's folder…")
            } else {
                if let manifest { ManifestCard(url: manifest, onSaved: reload) }
                if let dir {
                    PartsCard(root: dir, editable: editable, kinds: [.skill, .command, .agent],
                              drafts: drafts, onChanged: reload)
                } else {
                    Card {
                        Text("This plugin has no local folder: its files come from a checkout swe manages.")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    }
                }
            }
        }
        // `realpath` and `fileExists` on a synced or stale folder can
        // block, so the folders are worked out on a GCD queue (#580). A
        // new plugin or catalog reload cancels this task, and a cancelled
        // task's result is dropped.
        .task(id: FolderInputs(plugin: plugin, market: market, home: home, refresh: refresh)) {
            let plugin = plugin, market = market, home = home
            let worked = await offMain { PluginFolders(plugin: plugin, market: market, home: home) }
            guard !Task.isCancelled else { return }
            found = (plugin.ref, worked)
        }
        .onAppear {
            name = app.primitiveNames.name(forPlugin: plugin.ref) ?? plugin.displayName ?? plugin.name
        }
        .onChange(of: app.navReselects) { leave() }
        // Invisible, but its shortcut goes back: reselecting the Explore
        // tab or the Plugins sidebar item does it by pointer.
        .background(Button("Back", action: leave)
            .keyboardShortcut("[", modifiers: .command)
            .opacity(0)
            .accessibilityHidden(true))
        .alert("Unsaved changes", isPresented: $leaving) {
            Button("Save all") { saveAllAndLeave() }
            Button("Discard", role: .destructive) { onBack() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("\(drafts.dirty.count) file\(drafts.dirty.count == 1 ? " has" : "s have") unsaved changes.")
        }
    }

    /// Back with nothing unsaved; otherwise ask first.
    private func leave() {
        if drafts.dirty.isEmpty { onBack() } else { leaving = true }
    }

    /// Save every draft, then leave; a file that will not save keeps the
    /// page open and says why.
    private func saveAllAndLeave() {
        Task {
            var failed: [String] = []
            for (path, document) in drafts.dirty {
                // A save that leaves the file dirty (typed while it ran)
                // hasn't kept everything either.
                if await document.save() != .saved || document.isDirty {
                    failed.append(URL(fileURLWithPath: path).lastPathComponent)
                }
            }
            if failed.isEmpty {
                leaveError = nil
                onBack()
            } else {
                leaveError = "Couldn't save \(failed.joined(separator: ", "))."
            }
        }
    }

    private var lockNote: PrimitiveLockNote {
        let github = market.harnesses.values.contains { ["github", "git"].contains($0.sourceType ?? "") }
        return PrimitiveLockNote(text: github
            ? "Updates from GitHub would undo edits, so this plugin is read-only."
            : "Only plugins in your ~/.swe/plugins hub can be edited, so this one is read-only.")
    }

    private var header: some View {
        Card {
            VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        if editable {
                            TextField("Name", text: $name, onCommit: saveName)
                                .textFieldStyle(.roundedBorder)
                                .font(Theme.font(size: 16, weight: .semibold))
                                .frame(maxWidth: 320)
                        } else {
                            Text(app.primitiveNames.name(forPlugin: plugin.ref)
                                 ?? plugin.displayName ?? plugin.name)
                                .font(Theme.font(size: 16, weight: .semibold))
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
                    Text(plugin.ref).font(Theme.font(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                    HStack(spacing: 8) {
                        Text(app.primitiveNames.name(forMarketplace: market.name) ?? market.name)
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        if let tag = market.harnesses.values.first?.sourceType {
                            Badge(tag == "directory" ? "local" : tag)
                        }
                        ForEach(entry.harnesses, id: \.self) { harness in
                            if let state = plugin.harnesses[harness] {
                                HStack(spacing: 5) {
                                    HarnessIcon(harness: harness, size: 12)
                                    Text("\(HarnessMark(harness).name) · \(state.status.title)")
                                }
                                .font(Theme.font(size: 12, weight: .medium))
                                .foregroundStyle(Theme.textLabel)
                                .lineLimit(1)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 1)
                                .background(Theme.subtle, in: .capsule)
                                .overlay(Capsule().strokeBorder(Theme.borderStrong, lineWidth: 1))
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

    /// Store the display name in SWE's overlay (the plugin's id never
    /// changes, so nothing is reinstalled), and in the Codex manifest's
    /// `interface.displayName` when the plugin ships one.
    private func saveName() {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            try app.primitiveNames.saved({ $0.set(trimmed.isEmpty ? nil : trimmed, forPlugin: plugin.ref) },
                                         to: PluginDisplayNames.file(home: home))
            nameError = nil
        } catch {
            // The write left the overlay untouched: show that name again
            // and stop, so a failed rename changes nothing.
            name = app.primitiveNames.name(forPlugin: plugin.ref) ?? plugin.displayName ?? plugin.name
            nameError = "Couldn't save the name: \(error.localizedDescription)"
            return
        }
        guard editable, let dir else { return }
        let codex = dir.appending(path: ".codex-plugin/plugin.json")
        // Only a plugin that ships a Codex manifest has one to update; a
        // manifest that exists but cannot be read or written must be
        // reported, or the rename looks saved while the file keeps the
        // old name.
        // Disk work in the plugin's folder, so off the main actor.
        let shown = trimmed.isEmpty ? plugin.name : trimmed
        let previous = manifestWrite
        manifestWrite = Task {
            await previous?.value
            let failure: String? = await offMain {
                guard FileManager.default.fileExists(atPath: codex.path) else { return nil }
                do {
                    let text = try String(contentsOf: codex, encoding: .utf8)
                    let edited = try PluginManifestFields.setCodexDisplayName(shown, in: text)
                    try Data(edited.utf8).write(to: codex, options: .atomic)
                    return nil
                } catch {
                    return error.localizedDescription
                }
            }
            if let failure { nameError = "Renamed, but couldn't update the Codex manifest: \(failure)" }
        }
    }

    private func reload() {
        refresh += 1
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

    @State private var drafts = PrimitiveDrafts()
    @State private var leaving = false
    @State private var leaveError: String?
    /// The skill's folders, worked out off the main actor, with the path
    /// they were worked out for; nil until the first lands.
    @State private var found: (path: String, folders: SkillFolders)?
    /// Bumped by `reload()` so the folders are worked out again after a
    /// change on disk that leaves the skill's path as it was.
    @State private var refresh = 0

    private var root: URL { URL(fileURLWithPath: skill.path) }
    /// The folders for the skill on screen; nil while they are worked
    /// out, so a result for a skill opened before never shows.
    private var folders: SkillFolders? {
        guard let found, found.path == skill.path else { return nil }
        return found.folders
    }
    private var editable: Bool { folders?.editable ?? false }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let leaveError {
                ErrorCard(message: leaveError)
            }
            if folders != nil, !editable {
                PrimitiveLockNote(text: "Only skills that live under ~/.swe can be edited, so \(skill.name) is read-only.")
            }
            Card {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        Text(skill.name).font(Theme.font(size: 16, weight: .semibold))
                        Badge(skill.source.title)
                        Spacer()
                        Button { NSWorkspace.shared.activateFileViewerSelecting([root]) } label: {
                            Label("Show in Finder", systemImage: "folder")
                        }
                        .secondaryButton()
                    }
                    Text(displayPath(skill.path)).font(Theme.font(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                    if !skill.description.isEmpty {
                        Text(skill.description).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let links { links }
                }
            }
            if let folders {
                if let skillFile = folders.skillFile {
                    FrontmatterCard(url: skillFile, editable: folders.skillFileEditable, onSaved: reload)
                }
                PartsCard(root: root, editable: editable, drafts: drafts, onChanged: reload)
            } else {
                SkeletonRows(rows: 5, shape: .file, label: "Reading the skill's folder…")
            }
        }
        // `realpath` and `fileExists` on a synced or stale folder can
        // block, so the folders are worked out on a GCD queue (#580). A
        // new skill or a reload cancels this task, and a cancelled task's
        // result is dropped. `refresh` is in the key because a reload
        // (trashing SKILL.md, say) can leave the path unchanged.
        .task(id: [skill.path, home.path, String(refresh)]) {
            let path = skill.path, home = home
            let worked = await offMain { SkillFolders(skillPath: path, home: home) }
            guard !Task.isCancelled else { return }
            found = (path, worked)
        }
        .onChange(of: app.navReselects) { leave() }
        // Invisible, but its shortcut goes back: reselecting the Skills
        // sidebar item does it by pointer.
        .background(Button("Back", action: leave)
            .keyboardShortcut("[", modifiers: .command)
            .opacity(0)
            .accessibilityHidden(true))
        .alert("Unsaved changes", isPresented: $leaving) {
            Button("Save all") { saveAllAndLeave() }
            Button("Discard", role: .destructive) { onBack() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("\(drafts.dirty.count) file\(drafts.dirty.count == 1 ? " has" : "s have") unsaved changes.")
        }
    }

    private func leave() {
        if drafts.dirty.isEmpty { onBack() } else { leaving = true }
    }

    private func saveAllAndLeave() {
        Task {
            var failed: [String] = []
            for (path, document) in drafts.dirty {
                // A save that leaves the file dirty (typed while it ran)
                // hasn't kept everything either.
                if await document.save() != .saved || document.isDirty {
                    failed.append(URL(fileURLWithPath: path).lastPathComponent)
                }
            }
            if failed.isEmpty {
                leaveError = nil
                onBack()
            } else {
                leaveError = "Couldn't save \(failed.joined(separator: ", "))."
            }
        }
    }

    private func reload() {
        refresh += 1
        Task { await app.skills.load(for: app.swePath) }
    }
}
