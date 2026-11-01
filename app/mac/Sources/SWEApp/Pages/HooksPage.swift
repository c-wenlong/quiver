import AppKit
import SWECore
import SwiftUI

/// The hook scripts under `~/.swe/hooks`, laid out by the event that
/// fires them: pick a harness, read its session from SessionStart to
/// SessionEnd, and click a script to open it in the panel beside the list.
/// Linking goes through `swe init` — the only writer of links — and a new
/// script comes from `swe hooks new`. A harness's settings file is never
/// written here: the new-hook sheet shows the stanza to paste instead.
struct HooksPage: View {
    @Environment(AppModel.self) private var app
    @State private var adding = false
    /// The hook open in the side panel, nil when none is.
    @State private var opened: Hook?
    /// The harness tab; one that's gone falls back to the first tab.
    @AppStorage("hooksHarness") private var harness = ""
    /// Lay out herdr's, Agent Relay's and other installers' hooks too.
    @AppStorage("hooksShowForeign") private var showForeign = false
    /// The board's width, for the narrow layout; 0 until the first layout.
    @State private var boardWidth: CGFloat = 0

    /// Under this, the list and a 440pt panel can't sit side by side: the
    /// open hook takes the board's place until it's closed. 900pt windows
    /// leave about 610pt here.
    private static let sideBySideWidth: CGFloat = 980

    /// App-wide, so a list outlives a trip to another page and the last
    /// one shows at once.
    private var model: HooksModel { app.hooks }

    var body: some View {
        Page(title: "Hooks",
             subtitle: "Scripts in ~/.swe/hooks, by the event that fires them in each harness.") {
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
            list
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
            SkeletonRows(rows: 6, shape: .hook, label: "Reading hooks…")
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
                EmptyCard(text: "No hooks in ~/.swe/hooks yet. A hook script runs when its harness fires an event.",
                          icon: "bolt", actionTitle: "New hook") { adding = true }
            } else {
                board(inventory.hooks)
            }
        }
    }

    /// The harness tabs and its timeline, the scripts nothing fires, and
    /// the open hook's panel to the right; in a narrow window the open
    /// hook takes the board's place instead.
    private func board(_ hooks: [Hook]) -> some View {
        let tabs = HookTimeline.harnesses(in: hooks)
        let current = tabs.contains(harness) ? harness : (tabs.first ?? "")
        let timeline = HookTimeline(harness: current, hooks: hooks, includeForeign: showForeign)
        let idle = HookTimeline.notRunning(in: hooks)
        let wide = boardWidth == 0 || boardWidth >= Self.sideBySideWidth
        return Group {
            if let opened, !wide {
                panel(for: opened, in: hooks)
            } else {
                HStack(alignment: .top, spacing: 16) {
                    VStack(alignment: .leading, spacing: 16) {
                        if !tabs.isEmpty {
                            // The tabs refuse to shrink: past one row they
                            // wrap into a grid, the checkbox below them.
                            ViewThatFits(in: .horizontal) {
                                HStack(spacing: 12) {
                                    harnessTabs(tabs, current: current)
                                    Spacer(minLength: 12)
                                    foreignToggle(timeline.foreignCount)
                                }
                                VStack(alignment: .leading, spacing: 8) {
                                    harnessTabs(tabs, current: current, columns: 4)
                                    foreignToggle(timeline.foreignCount)
                                }
                            }
                            TimelineCard(timeline: timeline, opened: opened) { opened = $0 }
                        }
                        if !idle.isEmpty {
                            NotRunningCard(hooks: idle, opened: opened) { opened = $0 }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    if let opened {
                        panel(for: opened, in: hooks).frame(width: 440)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { boardWidth = $0 }
    }

    private func panel(for opened: Hook, in hooks: [Hook]) -> some View {
        // A reload swaps the inventory: follow the same script's row.
        let hook = hooks.first { $0.rowKey == opened.rowKey } ?? opened
        return HookDetailView(hook: hook, onBack: { self.opened = nil })
    }

    private func harnessTabs(_ tabs: [String], current: String, columns: Int = 1) -> some View {
        Tabs(options: tabs.map { ($0, HarnessMark($0).name) },
             selection: Binding(get: { current }, set: { harness = $0 }),
             icons: Dictionary(uniqueKeysWithValues: tabs.map { ($0, $0) }),
             fillWidth: columns > 1, columns: columns)
    }

    @ViewBuilder private func foreignToggle(_ count: Int) -> some View {
        if count > 0 {
            Toggle("Other tools (\(count))", isOn: $showForeign)
                .toggleStyle(.checkbox)
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize()
                .help("Scripts herdr, Agent Relay and other installers declared. SWE lists them but doesn't manage them.")
        }
    }

    /// swe's refusal of a page it does not have reads friendlier said plain.
    private static func loadMessage(_ message: String) -> String {
        message.localizedCaseInsensitiveContains("unknown command")
            ? "This version of swe has no hooks page yet. Update swe, or pick a newer swe in Settings › General."
            : message
    }
}

private extension Hook {
    /// One row of the list: the same script can sit in two harnesses
    /// (herdr's in Claude Code and Codex), so the source alone isn't enough.
    var rowKey: String { "\(harness)|\(id)" }

    /// What's wrong with it, in a badge's words; nil when it's linked and on.
    /// Other installers' scripts have no link of swe's to be wrong.
    var problem: String? {
        if enabled == false { return "Disabled" }
        if tier == .foreign || state == .linked { return nil }
        return state.title
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
                                .hoverTip("Runs swe init, which also links the shared skills and instructions and never replaces a real file.",
                                          replacesLabel: false)
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
                                .hoverTip(app.agentsMd.projectHasUnsavedChanges ? AgentsModel.saveFirst
                                          : "Runs swe init --force, which copies each real file in the way to ~/.swe/backups before linking over it.",
                                          replacesLabel: false)
                        }
                    }
                }
            }
            .confirmationDialog("Replace the real files with links?", isPresented: $confirmingReplace) {
                Button("Replace and back up", role: .destructive) {
                    Task { await model.link(force: true, swe: app.swe, for: app.swePath) }
                }
            } message: {
                Text("This runs swe init --force. Each real file in the way is copied to ~/.swe/backups, then replaced with a link to the shared copy.")
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
                        Text(finding.displayMessage(home: FileManager.default.homeDirectoryForCurrentUser.path))
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

// MARK: - Timeline

/// One harness's session, event by event: each lane names the event and
/// lists the scripts declared on it. Events nothing fires on fold into one
/// line at the bottom and open in place, in session order.
private struct TimelineCard: View {
    var timeline: HookTimeline
    var opened: Hook?
    var open: (Hook) -> Void
    @State private var showQuiet = false

    var body: some View {
        let quiet = timeline.quietEvents
        let lanes = Dictionary(uniqueKeysWithValues: timeline.lanes.map { ($0.event, $0) })
        let events = showQuiet ? HookEvents.sorted(Array(lanes.keys) + quiet) : timeline.lanes.map(\.event)
        Card {
            VStack(alignment: .leading, spacing: 0) {
                if timeline.lanes.isEmpty {
                    Text(timeline.foreignCount > 0
                         ? "Only other tools' hooks are declared for \(HarnessMark(timeline.harness).name). Turn on Other tools to see them."
                         : "Nothing is declared for \(HarnessMark(timeline.harness).name) yet.")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        .padding(.vertical, 8)
                }
                ForEach(Array(events.enumerated()), id: \.element) { index, event in
                    if index > 0 { Divider().overlay(Theme.border) }
                    LaneRow(event: event, caption: HookEvents.caption(for: event)) {
                        if let lane = lanes[event] {
                            ForEach(lane.entries) { entry in
                                EntryRow(entry: entry, isOpen: opened?.rowKey == entry.hook.rowKey) {
                                    open(entry.hook)
                                }
                            }
                        } else {
                            Text("Nothing runs")
                                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                                .padding(.vertical, 5)
                        }
                    }
                }
                if !quiet.isEmpty {
                    Divider().overlay(Theme.border)
                    Button { showQuiet.toggle() } label: {
                        Label(showQuiet ? "Hide events with nothing on them"
                                        : "\(quiet.count) more \(quiet.count == 1 ? "event has" : "events have") nothing on them",
                              systemImage: showQuiet ? "chevron.up" : "chevron.down")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .padding(.vertical, 4)
                            .contentShape(.rect)
                    }
                    .plainButton(cornerRadius: Theme.smallRadius)
                    .padding(.top, 8)
                }
            }
        }
    }
}

/// The event's name and when it fires on the left, its scripts on the right.
private struct LaneRow<Content: View>: View {
    var event: String
    var caption: String?
    @ViewBuilder var content: Content

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                Text(event)
                    .font(Theme.font(size: 13, weight: .medium, design: .monospaced))
                    .foregroundStyle(Theme.textPrimary)
                if let caption {
                    Text(caption).font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            .frame(width: 168, alignment: .leading)
            .padding(.top, 5)
            VStack(alignment: .leading, spacing: 2) { content }
                .frame(maxWidth: .infinity, alignment: .leading)
                // Rows pad themselves for their hover fill; this keeps
                // their text in line with the event name's column edge.
                .padding(.leading, -8)
        }
        .padding(.vertical, 8)
    }
}

/// One script on one event: its name, the tool it's gated to, anything
/// wrong with it, and its header line. Another installer's script reads
/// quieter, behind a puzzle-piece mark.
private struct EntryRow: View {
    var entry: HookTimeline.Entry
    var isOpen: Bool
    var open: () -> Void

    var body: some View {
        let hook = entry.hook
        let summary = HookTimeline.shortSummary(hook.summary)
        HookButton(isOpen: isOpen, help: hook.summary.isEmpty ? "Open \(hook.script)" : hook.summary, action: open) {
            HStack(spacing: 8) {
                if entry.isForeign {
                    Image(systemName: "puzzlepiece.extension")
                        .font(Theme.font(size: 11))
                        .foregroundStyle(Theme.textMuted)
                        .hoverTip("Declared by another tool")
                }
                Text(hook.script)
                    .font(Theme.font(size: 13, weight: .medium, design: .monospaced))
                    .foregroundStyle(entry.isForeign ? Theme.textSecondary : Theme.textPrimary)
                    .lineLimit(1)
                    .layoutPriority(1)
                if let matcher = entry.matcherLabel { MatcherPill(text: matcher) }
                if let problem = hook.problem { Badge(problem) }
                if !summary.isEmpty {
                    Text(summary)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1).truncationMode(.tail)
                }
                Spacer(minLength: 0)
            }
        }
    }
}

/// The tool or pattern a declaration is gated to, tinted so it reads as
/// the one thing that decides whether the script runs.
private struct MatcherPill: View {
    var text: String

    var body: some View {
        Text(text)
            .font(Theme.font(size: 11, weight: .medium, design: .monospaced))
            .foregroundStyle(Theme.link)
            .lineLimit(1)
            .padding(.horizontal, 7)
            .padding(.vertical, 1)
            .background(Theme.link.opacity(0.12), in: .capsule)
    }
}

/// Your scripts no settings file declares: a skeleton not pasted in yet, a
/// dormant shared script, a folder that is no harness.
private struct NotRunningCard: View {
    var hooks: [Hook]
    var opened: Hook?
    var open: (Hook) -> Void

    var body: some View {
        Card(title: "Not running", subtitle: "No settings file declares these, so no harness fires them.") {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(hooks, id: \.rowKey) { hook in
                    HookButton(isOpen: opened?.rowKey == hook.rowKey, help: "Open \(hook.script)") {
                        open(hook)
                    } label: {
                        HStack(spacing: 8) {
                            Text(hook.script)
                                .font(Theme.font(size: 13, weight: .medium, design: .monospaced))
                                .foregroundStyle(Theme.textPrimary)
                                .lineLimit(1)
                                .layoutPriority(1)
                            // A folder that is no harness has no mark to show.
                            if HarnessMark(hook.harness).icon != nil {
                                HarnessMarkView(harness: hook.harness, size: 13)
                                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            }
                            // "Not installed" is skipped's word for a missing
                            // harness; the detail says the real reason.
                            if let problem = hook.problem, hook.state != .skipped { Badge(problem) }
                            Text(reason(hook))
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                .lineLimit(1).truncationMode(.tail)
                            Spacer(minLength: 0)
                        }
                    }
                }
            }
            .padding(.horizontal, -8)
        }
    }

    /// swe's note on the row, else the script's header line, else the plain fact.
    private func reason(_ hook: Hook) -> String {
        if !hook.detail.isEmpty { return hook.detail.prefix(1).uppercased() + hook.detail.dropFirst() }
        let summary = HookTimeline.shortSummary(hook.summary)
        return summary.isEmpty ? "No settings file declares it" : summary
    }
}

/// A clickable hook row: a hover fill, and the selected fill while its
/// panel is open.
private struct HookButton<Label: View>: View {
    var isOpen: Bool
    var help: String
    var action: () -> Void
    @ViewBuilder var label: Label
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            label
                .padding(.vertical, 5)
                .padding(.horizontal, 8)
                .contentShape(.rect)
        }
        .plainButton(cornerRadius: Theme.controlRadius)
        .background(isOpen ? Theme.selected : hovering ? Theme.panelHover : .clear,
                    in: .rect(cornerRadius: Theme.controlRadius))
        .onHover { hovering = $0 }
        .help(help)
    }
}

// MARK: - Hook panel

/// One hook beside the list: what fires it and where it's declared, where
/// it's linked, and the script itself in a raw editor. The script is the
/// hub copy under ~/.swe/hooks; edits to it reach every link `swe init`
/// made. InstructionDocument preserves the file's executable bit across a
/// save.
private struct HookDetailView: View {
    @Environment(AppModel.self) private var app
    var hook: Hook
    var onBack: () -> Void

    /// The model's, not the view's: closing the panel keeps the drafts so
    /// an edit survives a trip to another hook and the app's quit check
    /// still sees it.
    private var drafts: PrimitiveDrafts { app.hooks.drafts }
    @State private var leaving = false
    @State private var leaveError: String?
    /// The script's ownership, worked out off the main actor, with the
    /// path it was worked out for; nil until the first lands.
    @State private var found: (path: String, check: HookScript)?
    /// `swe hooks info`'s take on the script, for the command line and
    /// timeout the list leaves out, with the source it was asked for: the
    /// panel stays up while another hook loads, and must not show the
    /// last one's details under the new name.
    @State private var info: (source: String, info: HookInfo)?
    /// Bumped by `reload()` so the check is worked out again after a save.
    @State private var refresh = 0
    /// A directory hook (a bundle of scripts) has no one file to edit.
    @State private var isFolder = false

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
        guard let info, info.source == hook.source else { return nil }
        return info.info.harnesses.first { $0.harness == hook.harness }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let leaveError {
                ErrorCard(message: leaveError)
            }
            if let check, !check.editable {
                PrimitiveLockNote(text: "Only scripts inside ~/.swe/hooks can be edited, so \(hook.script) is read-only.")
            }
            header
            if let check {
                if !check.exists {
                    Card {
                        Text("The script is missing on disk; swe init may have removed it.")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    }
                } else if isFolder {
                    Card {
                        Text("\(hook.script) is a folder, so there's no single script to show. Show it in Finder to see what's inside.")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else if let document = drafts.documents[hook.source] {
                    InstructionEditor(
                        document: document,
                        title: hook.script,
                        subtitle: displayPath(source),
                        monoSubtitle: true,
                        readOnly: !check.editable,
                        rawOnly: true,
                        onSaved: reload)
                        .frame(height: 460)
                } else {
                    SkeletonLines(rows: 10, label: "Opening the script…")
                }
            } else {
                SkeletonDetail(label: "Checking the script…")
            }
        }
        .task(id: [hook.source, home.path, String(refresh)]) {
            let source = hook.source, home = home
            let (check, folder) = await offMain {
                var directory: ObjCBool = false
                let folder = FileManager.default.fileExists(atPath: source, isDirectory: &directory)
                    && directory.boolValue
                return (HookScript(source: source, home: home), folder)
            }
            guard !Task.isCancelled else { return }
            // Open the document here, never in body: document(for:) writes
            // the drafts store, which body must not do mid-update.
            if check.exists && !folder { drafts.document(for: source) }
            isFolder = folder
            found = (source, check)
        }
        // Refetch on a refreshed row or a save: the row the list hands
        // down may differ from what the first look fetched.
        .task(id: InfoKey(hook: hook, refresh: refresh)) {
            let source = hook.source
            let fetched = try? await app.swe.hookInfo(name: hook.script)
            guard !Task.isCancelled else { return }
            info = fetched.map { (source, $0) }
        }
        .onChange(of: app.navReselects) { leave() }
        // Invisible, but its shortcut closes the panel: reselecting the
        // Hooks sidebar item does it by pointer.
        .background(Button("Close", action: leave)
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
    /// panel (a Refresh) or after a save (`reload` bumps `refresh`).
    private struct InfoKey: Equatable {
        var hook: Hook
        var refresh: Int
    }

    private var header: some View {
        let declared = placement?.declared ?? hook.declared
        let summary = [placement?.summary ?? "", hook.summary].first { !$0.isEmpty } ?? ""
        let settings = declared.map(\.settings).reduce(into: [String]()) { if !$0.contains($1) { $0.append($1) } }
        let others = (hook.eventsSupported ?? []).filter { event in !declared.contains { $0.event == event } }
        return Card {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .center, spacing: 6) {
                    Text(hook.script)
                        .font(Theme.font(size: 16, weight: .semibold, design: .monospaced))
                        .lineLimit(1).truncationMode(.middle)
                    Spacer(minLength: 8)
                    Button { NSWorkspace.shared.activateFileViewerSelecting([source]) } label: {
                        Image(systemName: "folder")
                    }
                    .iconButton()
                    .help("Show in Finder")
                    Button(action: leave) { Image(systemName: "xmark") }
                        .iconButton()
                        .help("Close (⌘[)")
                }
                HStack(spacing: 6) {
                    HarnessMarkView(harness: hook.harness, size: 14)
                    Text(HarnessMark(hook.harness).name)
                    // "Harness" is every row's tier but two; only those say so.
                    if let tier = hook.tier, tier != .harness { Badge(tier.title) }
                    if let problem = hook.problem { Badge(problem) }
                }
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                if !summary.isEmpty {
                    Text(summary).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                VStack(alignment: .leading, spacing: 8) {
                    if declared.isEmpty {
                        InfoLine("Runs") {
                            Text("Never: no settings file declares it")
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        }
                    } else {
                        InfoLine("Runs") {
                            FlowLayout(spacing: 6, lineSpacing: 6) {
                                ForEach(Array(declared.enumerated()), id: \.offset) { _, declaration in
                                    HStack(spacing: 4) {
                                        Badge(declaration.label)
                                        if let timeout = declaration.timeout {
                                            Text("\(timeout.formatted())s")
                                                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                                                .help("Timeout")
                                        }
                                    }
                                }
                            }
                        }
                        if let command = declared.first(where: { !$0.command.isEmpty })?.command {
                            InfoLine("Command") { mono(command) }
                        }
                        InfoLine("Declared in") {
                            VStack(alignment: .leading, spacing: 2) {
                                ForEach(settings, id: \.self) { path in
                                    HStack(spacing: 4) {
                                        mono(displayPath(path))
                                        if isManaged(path) {
                                            Image(systemName: "lock")
                                                .font(Theme.font(size: 10))
                                                .foregroundStyle(Theme.textMuted)
                                                .hoverTip("Another tool owns this file; SWE never writes it.")
                                        }
                                    }
                                }
                            }
                        }
                    }
                    InfoLine("Linked at") { mono(displayPath(hook.destination)) }
                    if !hook.detail.isEmpty {
                        InfoLine("Note") {
                            Text(hook.detail).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if !others.isEmpty {
                        InfoLine("Can also run") {
                            Text("\(others.count) other \(others.count == 1 ? "event" : "events")")
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                .hoverTip(others.joined(separator: ", "), title: "Events this script handles")
                        }
                    }
                }
            }
        }
    }

    private func mono(_ text: String) -> some View {
        Text(text)
            .font(Theme.font(size: 12, design: .monospaced))
            .foregroundStyle(Theme.textSecondary)
            .lineLimit(1).truncationMode(.middle)
    }

    /// `swe hooks check` notes a settings file another tool owns (nix,
    /// dotfiles) as `managed`, its path first.
    private func isManaged(_ path: String) -> Bool {
        app.hooks.findings.contains { $0.kind == "managed" && $0.message.hasPrefix(path) }
    }

    /// This hook's own draft. Another hook's edit stays in the drafts
    /// store when the panel switches to this one: the app's quit check
    /// still sees it, and reopening that hook brings it back. So closing
    /// asks about, saves or discards this script only.
    private var draft: InstructionDocument? {
        drafts.documents[hook.source].flatMap { $0.isDirty ? $0 : nil }
    }

    /// Close with nothing unsaved here; otherwise ask first.
    private func leave() {
        if draft == nil { onBack() } else { leaving = true }
    }

    /// Save the script, then close; a save that fails keeps the panel open
    /// and says why.
    private func saveAndLeave() {
        Task {
            if let draft, await draft.save() != .saved || draft.isDirty {
                leaveError = "Couldn't save \(hook.script)."
            } else {
                leaveError = nil
                onBack()
            }
        }
    }

    private func reload() {
        refresh += 1
        Task { await app.hooks.load(swe: app.swe, for: app.swePath) }
    }
}

/// A label column and its value, one fact per line in the hook panel.
private struct InfoLine<Content: View>: View {
    var label: String
    @ViewBuilder var content: Content

    init(_ label: String, @ViewBuilder content: () -> Content) {
        self.label = label
        self.content = content()
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(label).font(Theme.caption).foregroundStyle(Theme.textMuted)
                .frame(width: 84, alignment: .leading)
            content.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

// MARK: - New hook sheet

/// `swe hooks new` writes the executable skeleton under
/// `~/.swe/hooks/<harness>`; the sheet then shows the stanza the reply
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
                    .sweField(monospaced: true)
                if let nameProblem {
                    Text(nameProblem).font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            if let created {
                VStack(alignment: .leading, spacing: 8) {
                    Label("Created \(displayPath(created.path))", systemImage: "checkmark.circle")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("Declare it yourself — swe never writes a settings file. Paste this into \(settingsList(created)):")
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
