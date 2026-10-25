import AppKit
import QuiverCore
import SwiftUI

/// The hook scripts under `~/.quiver/hooks`: which harness each links into,
/// and the settings-file declarations that make one fire. Linking goes
/// through `swe init` — the only writer of links — and a new script comes
/// from `swe hooks new`. A harness's settings file is never written here:
/// the new-hook sheet shows the stanza to paste instead.
struct HooksPage: View {
    @Environment(AppModel.self) private var app
    @State private var adding = false
    /// The hook open in its detail page, nil while the list shows.
    @State private var opened: Hook?

    /// App-wide, so a list outlives a trip to another page and the last
    /// one shows at once.
    private var model: HooksModel { app.hooks }

    var body: some View {
        Page(title: "Hooks",
             subtitle: "Scripts in ~/.quiver/hooks that run when a harness fires an event, and which harnesses each is linked and declared for.") {
            Button("New hook", systemImage: "plus") { adding = true }
                .primaryButton()
                .disabled(model.creating)
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await model.load(swe: app.swe, for: app.swePath) }
            }
            .iconButton()
            .disabled(model.linking)
            .keyboardShortcut("r", modifiers: .command)
            .help("Read the hooks again (⌘R)")
        } content: {
            if let opened {
                // A reload swaps the inventory: follow the same script's row.
                let hook = model.hooks.first { $0.id == opened.id } ?? opened
                HookDetailView(hook: hook, onBack: { self.opened = nil })
            } else {
                list
            }
        }
        .task(id: app.swePath) { await model.loadIfStale(swe: app.swe, for: app.swePath) }
        // Modal: a dismiss mid-create would still write the script but
        // lose the stanza the reply carries, so only its buttons close it.
        .quiverSheet(isPresented: $adding, modal: true) {
            NewHookSheet()
        }
    }

    @ViewBuilder private var list: some View {
        if let error = model.actionError { ErrorCard(message: error) }
        switch model.state {
        case .idle, .loading:
            LoadingCard(text: "Reading hooks…")
        case .failed(let message):
            // An swe from before `hooks` existed fails the whole page with
            // its own words; say what fixes it rather than echoing argv.
            ErrorCard(message: Self.loadMessage(message),
                      actionTitle: "Refresh", actionShortcut: .init("r", modifiers: .command)) {
                Task { await model.load(swe: app.swe, for: app.swePath) }
            }
        case .loaded(let inventory):
            if let checkError = inventory.checkError {
                ErrorCard(message: "The hooks list loaded, but its check didn't run: \(checkError)")
            }
            LinksStrip(hooks: inventory.hooks)
            if !inventory.findings.isEmpty {
                FindingsCard(findings: inventory.findings)
            }
            if inventory.hooks.isEmpty {
                EmptyCard(text: "No hooks in ~/.quiver/hooks yet. A hook script runs when its harness fires an event.",
                          icon: "bolt", actionTitle: "New hook") { adding = true }
            } else {
                ForEach(model.groups) { group in
                    HookGroupCard(group: group, open: { opened = $0 })
                }
            }
        }
    }

    /// swe's refusal of a page it does not have reads friendlier said plain.
    private static func loadMessage(_ message: String) -> String {
        message.localizedCaseInsensitiveContains("unknown command")
            ? "This version of swe has no hooks page yet. Update quiver, or pick a newer swe in Settings › General."
            : message
    }
}

/// When any row waits on a link: what `swe init` would do, and the buttons
/// that run it. The same strip the AGENTS.md page carries — init also links
/// the shared skills and instructions, so the copy says so.
private struct LinksStrip: View {
    @Environment(AppModel.self) private var app
    var hooks: [Hook]
    @State private var confirmingReplace = false

    var body: some View {
        let model = app.hooks
        let pending = hooks.filter { $0.state.initFixes }
        let blocked = hooks.filter { $0.state.isBlocked }
        if !pending.isEmpty || !blocked.isEmpty || model.linking {
            Card {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text("Links").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                        Text(pending.isEmpty
                             ? "Every hook script is where its harness reads it."
                             : "\(pending.count) \(pending.count == 1 ? "hook" : "hooks") will be linked into place.")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        if !pending.isEmpty || model.linking {
                            HStack(spacing: 8) {
                                if model.linking { ProgressView().controlSize(.small) }
                                Button(model.linking ? "Linking…" : "Link with swe init", systemImage: "link") {
                                    Task { await model.link(swe: app.swe, for: app.swePath) }
                                }
                                .primaryButton()
                                .disabled(model.linking)
                                .help("Runs swe init, which also links the shared skills and instructions and never replaces a real file.")
                            }
                            .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 4 }
                        }
                    }
                    if !blocked.isEmpty {
                        HStack(spacing: 12) {
                            Text("A real file is in the way for \(blocked.count == 1 ? "a hook" : "\(blocked.count) hooks"). swe init leaves those alone.")
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            Button("Replace and back up…") { confirmingReplace = true }
                                .secondaryButton()
                                // --force also replaces real instruction files
                                // a project edit on the AGENTS.md page may be
                                // writing, so it waits for a clean model there.
                                .disabled(model.linking || app.agentsMd.projectHasUnsavedChanges)
                                .help(app.agentsMd.projectHasUnsavedChanges ? AgentsModel.saveFirst
                                      : "Runs swe init --force, which copies each real file in the way to ~/.quiver/backups before linking over it.")
                        }
                    }
                }
            }
            .confirmationDialog("Replace the real files with links?", isPresented: $confirmingReplace) {
                Button("Replace and back up", role: .destructive) {
                    Task { await model.link(force: true, swe: app.swe, for: app.swePath) }
                }
            } message: {
                Text("This runs swe init --force. Each real file in the way is copied to ~/.quiver/backups, then replaced with a link to the shared copy.")
            }
        }
    }
}

/// The `swe hooks check` report: a declaration naming a script that is
/// gone, a linked script nothing declares, or a script never linked.
private struct FindingsCard: View {
    var findings: [HookFinding]

    var body: some View {
        Card(title: "Check", subtitle: "swe hooks check compares the declarations, the links and the hub.") {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(findings.enumerated()), id: \.element.id) { index, finding in
                    if index > 0 { Divider().overlay(Theme.border) }
                    HStack(alignment: .top, spacing: 10) {
                        Badge(finding.kindTitle)
                        if !finding.harness.isEmpty {
                            HarnessMarkView(harness: finding.harness, size: 14)
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        }
                        Text(finding.message)
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(.vertical, 6)
                }
            }
        }
    }
}

/// One section of the list: the shared pool, a harness, or the scripts
/// quiver does not manage.
private struct HookGroupCard: View {
    var group: HooksModel.HookGroup
    var open: (Hook) -> Void

    var body: some View {
        Card {
            header
            VStack(alignment: .leading, spacing: 0) {
                ForEach(group.hooks) { hook in
                    HookRow(hook: hook) { open(hook) }
                    if hook.id != group.hooks.last?.id { Divider().overlay(Theme.border) }
                }
            }
        }
    }

    @ViewBuilder private var header: some View {
        switch group.key {
        case "shared":
            Text("Shared").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                .help("Scripts every harness can link")
        case "foreign":
            Text("Foreign").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                .help("Scripts swe lists but does not manage")
        default:
            HStack(spacing: 8) {
                HarnessMarkView(harness: group.key, size: 16)
                Text(HarnessMark(group.key).name).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                Text(group.hooks.count == 1 ? "1 hook" : "\(group.hooks.count) hooks")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
    }
}

/// One hook: its file name, the link state, where the harness reads it and
/// what declares it. A click opens the detail page.
private struct HookRow: View {
    var hook: Hook
    var open: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: open) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text(hook.script).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                        Badge(hook.state.title)
                        if hook.isUndeclared { Badge("Undeclared") }
                        if let enabled = hook.enabled {
                            Badge(enabled ? "Enabled" : "Disabled")
                        }
                    }
                    Text(displayPath(hook.destination))
                        .font(Theme.font(size: 12, design: .monospaced))
                        .foregroundStyle(Theme.textMuted)
                        .lineLimit(1).truncationMode(.middle)
                    if !hook.detail.isEmpty {
                        Text(hook.detail).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                    if !hook.declared.isEmpty || !(hook.eventsSupported ?? []).isEmpty {
                        FlowLayout(spacing: 6, lineSpacing: 6) {
                            ForEach(Array(hook.declared.enumerated()), id: \.offset) { _, declaration in
                                Badge(declaration.label)
                            }
                            ForEach(hook.eventsSupported ?? [], id: \.self) { event in
                                Badge(event)
                            }
                        }
                    }
                }
                Spacer(minLength: 12)
                Image(systemName: "chevron.right")
                    .font(Theme.font(size: 11, weight: .semibold))
                    .foregroundStyle(Theme.textMuted)
            }
            .contentShape(.rect)
        }
        .plainButton(cornerRadius: Theme.controlRadius)
        .padding(.vertical, 10)
        .background {
            RoundedRectangle(cornerRadius: Theme.controlRadius)
                .fill(hovering ? Theme.panelHover : .clear)
                .padding(.horizontal, -8)
        }
        .onHover { hovering = $0 }
        .help("Open \(hook.script)")
    }
}

// MARK: - Hook detail

/// One hook as a page: its link state, the harness and destination, the
/// declarations that fire it, and the script itself in the editor. The
/// script is the hub copy under ~/.quiver/hooks; edits to it reach every
/// link `swe init` made. InstructionDocument preserves the file's
/// executable bit across a save.
private struct HookDetailView: View {
    @Environment(AppModel.self) private var app
    var hook: Hook
    var onBack: () -> Void

    /// The model's, not the view's: leaving the page keeps the drafts so
    /// an edit survives a trip to another section and the app's quit
    /// check still sees it.
    private var drafts: PrimitiveDrafts { app.hooks.drafts }
    @State private var leaving = false
    @State private var leaveError: String?
    /// The script's ownership, worked out off the main actor, with the
    /// path it was worked out for; nil until the first lands.
    @State private var found: (path: String, check: HookScript)?
    /// `swe hooks info`'s take on the script, for the docstring summary.
    @State private var info: HookInfo?
    /// Bumped by `reload()` so the check is worked out again after a save.
    @State private var refresh = 0

    private var home: URL { FileManager.default.homeDirectoryForCurrentUser }
    private var source: URL { URL(fileURLWithPath: hook.source) }
    /// The check for the hook on screen; nil while it is worked out, so a
    /// result for a hook opened before never shows.
    private var check: HookScript? {
        guard let found, found.path == hook.source else { return nil }
        return found.check
    }
    /// The placement swe's info call reports for this harness.
    private var placement: HookInfo.Placement? {
        info?.harnesses.first { $0.harness == hook.harness }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let leaveError {
                ErrorCard(message: leaveError)
            }
            if let check, !check.editable {
                PrimitiveLockNote(text: "Only scripts inside ~/.quiver/hooks can be edited, so \(hook.script) is read-only.")
            }
            header
            if let check {
                if !check.exists {
                    Card {
                        Text("The script is missing on disk; swe init may have removed it.")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    }
                } else if let document = drafts.documents[hook.source] {
                    InstructionEditor(
                        document: document,
                        title: hook.script,
                        subtitle: displayPath(source),
                        monoSubtitle: true,
                        readOnly: !check.editable,
                        onSaved: reload)
                } else {
                    LoadingCard(text: "Opening the script…")
                }
            } else {
                LoadingCard(text: "Checking the script…")
            }
        }
        .task(id: [hook.source, home.path, String(refresh)]) {
            let source = hook.source, home = home
            let check = await offMain { HookScript(source: source, home: home) }
            guard !Task.isCancelled else { return }
            // Open the document here, never in body: document(for:) writes
            // the drafts store, which body must not do mid-update.
            if check.exists { drafts.document(for: source) }
            found = (source, check)
        }
        // Refetch on a refreshed row or a save: the row the list hands
        // down may differ from what the first look fetched, and the
        // docstring summary changes with the script.
        .task(id: InfoKey(hook: hook, refresh: refresh)) {
            info = try? await app.swe.hookInfo(name: hook.script)
        }
        .onChange(of: app.navReselects) { leave() }
        // Invisible, but its shortcut goes back: reselecting the Hooks
        // sidebar item does it by pointer.
        .background(Button("Back", action: leave)
            .keyboardShortcut("[", modifiers: .command)
            .opacity(0)
            .accessibilityHidden(true))
        .alert("Unsaved changes", isPresented: $leaving) {
            Button("Save") { saveAndLeave() }
            // Discard drops the document, so reopening reads disk, not
            // the edit that was thrown away.
            Button("Discard", role: .destructive) {
                drafts.remove(under: hook.source)
                onBack()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("“\(hook.script)” has unsaved changes.")
        }
    }

    /// Re-asks `swe hooks info` when the hook row changes under the open
    /// detail (a Refresh) or after a save (`reload` bumps `refresh`).
    private struct InfoKey: Equatable {
        var hook: Hook
        var refresh: Int
    }

    private var header: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Text(hook.script).font(Theme.font(size: 16, weight: .semibold))
                    Badge(hook.state.title)
                    if hook.isUndeclared { Badge("Undeclared") }
                    if let enabled = hook.enabled {
                        Badge(enabled ? "Enabled" : "Disabled")
                    }
                    Spacer()
                    Button { NSWorkspace.shared.activateFileViewerSelecting([source]) } label: {
                        Label("Show in Finder", systemImage: "folder")
                    }
                    .secondaryButton()
                }
                HStack(spacing: 8) {
                    HarnessMarkView(harness: hook.harness, size: 14)
                    Text(HarnessMark(hook.harness).name)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    if let tier = hook.tier {
                        Badge(tier.title)
                    }
                }
                if let summary = placement?.summary, !summary.isEmpty {
                    Text(summary).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                VStack(alignment: .leading, spacing: 4) {
                    LabeledContent {
                        Text(displayPath(hook.destination))
                            .font(Theme.font(size: 12, design: .monospaced))
                            .foregroundStyle(Theme.textSecondary)
                            .lineLimit(1).truncationMode(.middle)
                    } label: {
                        Text("Linked at").font(Theme.caption).foregroundStyle(Theme.textMuted)
                    }
                }
                if !hook.detail.isEmpty {
                    Text(hook.detail).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                let declared = placement?.declared ?? hook.declared
                if !declared.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Declared in").font(Theme.caption).foregroundStyle(Theme.textMuted)
                        ForEach(Array(declared.enumerated()), id: \.offset) { _, declaration in
                            VStack(alignment: .leading, spacing: 2) {
                                HStack(spacing: 8) {
                                    Badge(declaration.label)
                                    if let timeout = declaration.timeout {
                                        Text("timeout \(timeout.formatted())s")
                                            .font(Theme.caption).foregroundStyle(Theme.textMuted)
                                    }
                                    Text(declaration.command)
                                        .font(Theme.font(size: 12, design: .monospaced))
                                        .foregroundStyle(Theme.textSecondary)
                                        .lineLimit(1).truncationMode(.middle)
                                }
                                Text(displayPath(declaration.settings))
                                    .font(Theme.font(size: 12, design: .monospaced))
                                    .foregroundStyle(Theme.textMuted)
                                    .lineLimit(1).truncationMode(.middle)
                            }
                        }
                    }
                }
                if !(hook.eventsSupported ?? []).isEmpty {
                    HStack(spacing: 6) {
                        Text("Events").font(Theme.caption).foregroundStyle(Theme.textMuted)
                        ForEach(hook.eventsSupported ?? [], id: \.self) { event in
                            Badge(event)
                        }
                    }
                }
            }
        }
    }

    /// Back with nothing unsaved; otherwise ask first.
    private func leave() {
        if drafts.dirty.isEmpty { onBack() } else { leaving = true }
    }

    /// Save the script, then leave; a save that fails keeps the page open
    /// and says why.
    private func saveAndLeave() {
        var failed: [String] = []
        for (path, document) in drafts.dirty {
            if document.save() != .saved {
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

    private func reload() {
        refresh += 1
        Task { await app.hooks.load(swe: app.swe, for: app.swePath) }
    }
}

// MARK: - New hook sheet

/// `swe hooks new` writes the executable skeleton under
/// `~/.quiver/hooks/<harness>`; the sheet then shows the stanza the reply
/// carries for the user to paste into a settings file — the app never
/// writes one — plus where that file is.
private struct NewHookSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    /// The harnesses to pick from: the ones already holding hooks, plus
    /// every active harness swe knows.
    @State private var harnesses: [String] = []
    @State private var harness = "claude"
    @State private var name = ""
    @State private var error: String?
    @State private var working = false
    @State private var created: NewHookReply?

    /// `swe hooks new`'s own rule, from create.py: letters, digits, `_` and
    /// `-`, letter or digit first. Looser than a skill name on purpose.
    static let nameRule = /^[A-Za-z0-9][A-Za-z0-9_-]*$/
    var nameProblem: String? {
        if name.isEmpty { return "Letters, digits, '_' and '-': prose-guard" }
        if name.count > 64 { return "At most 64 characters" }
        return name.wholeMatch(of: Self.nameRule) == nil
            ? "Letters, digits, '_' and '-', starting with a letter or digit" : nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("New hook").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            FormField("Harness") {
                SelectMenu(options: harnesses.map { ($0, HarnessMark($0).name, $0) },
                           selection: $harness, width: nil)
            }
            FormField("Name") {
                TextField("prose-guard", text: $name)
                    .quiverField(monospaced: true)
                if let nameProblem {
                    Text(nameProblem).font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            if let created {
                VStack(alignment: .leading, spacing: 8) {
                    Label("Created \(displayPath(created.path))", systemImage: "checkmark.circle")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("Declare it yourself — quiver never writes a settings file. Paste this into \(settingsList(created)):")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(alignment: .top, spacing: 6) {
                        Text(created.stanza)
                            .font(Theme.font(size: 12, design: .monospaced))
                            .lineSpacing(2)
                            .foregroundStyle(Theme.textSecondary)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.vertical, 6)
                            .padding(.horizontal, 8)
                            .background(Theme.code, in: .rect(cornerRadius: Theme.smallRadius))
                            .overlay(RoundedRectangle(cornerRadius: Theme.smallRadius)
                                .strokeBorder(Theme.border, lineWidth: 1))
                        CopyButton { copyToPasteboard(created.stanza) }
                            .foregroundStyle(Theme.textSecondary)
                            .frame(width: 24, height: 28)
                            .contentShape(.rect)
                            .plainButton(cornerRadius: Theme.smallRadius)
                    }
                    Text("Then swe init links it into place.")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            if let error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            HStack {
                Spacer()
                if let created, let editor = app.editor {
                    Button("Open in \(editor.name)") {
                        NSWorkspace.shared.open([URL(fileURLWithPath: created.path)],
                                                withApplicationAt: editor.appURL,
                                                configuration: .init())
                    }
                    .secondaryButton()
                }
                Button(created == nil ? "Close" : "Done") { dismiss() }
                    .secondaryButton()
                    .disabled(working)
                if created == nil {
                    Button("Create") {
                        working = true
                        error = nil
                        Task {
                            do {
                                created = try await app.hooks.create(
                                    harness: harness, name: name,
                                    swe: app.swe, for: app.swePath)
                            } catch { self.error = error.localizedDescription }
                            working = false
                        }
                    }
                    .primaryButton()
                    .disabled(working || nameProblem != nil || harnesses.isEmpty)
                }
            }
        }
        .sheetScaffold(width: 460)
        .task {
            var names = Set(app.hooks.hooks.map(\.harness))
            if let listed = try? await app.swe.harnesses(withUsage: false) {
                names.formUnion(listed.filter { $0.state != .archived }.map(\.name))
            }
            harnesses = names.sorted()
            if !harnesses.contains(harness) { harness = harnesses.first ?? "claude" }
        }
    }

    /// "settings.json or hooks.json", a compact take on the reply's paths.
    private func settingsList(_ reply: NewHookReply) -> String {
        let names = reply.settings.map { displayPath($0) }
        return names.isEmpty ? "the harness's settings file" : names.joined(separator: " or ")
    }
}
