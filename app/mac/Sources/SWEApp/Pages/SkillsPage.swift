import AppKit
import SWECore
import SwiftUI

/// Skills, after TokenTracker's: the skills that stand alone, with the
/// harnesses that load them. Skills shipped inside a plugin are listed with
/// it under Plugins > Explore. Links and unlinks go through `swe skills`,
/// never written from here.
struct SkillsPage: View {
    @Environment(AppModel.self) private var app
    @State private var adding = false

    /// App-wide, so a list outlives a trip to another page and the last
    /// one shows at once.
    private var model: SkillsModel { app.skills }

    var body: some View {
        Page(title: "Skills",
             subtitle: "Every skill on this Mac outside a plugin, and which harnesses load it.") {
            // The harness links fold into one line in the header, so the
            // first skill sits right under the filter bar.
            if case .loaded(let inventory) = model.inventory {
                HarnessLinksSummary(model: model, sharedDir: inventory.sharedDir)
            }
            Button("New skill", systemImage: "plus") { adding = true }
                .primaryButton()
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task {
                    async let catalog: Void = app.pluginCatalog.load(swe: app.swe, for: app.swePath)
                    await model.load(for: app.swePath)
                    await catalog
                }
            }
            .iconButton()
            .keyboardShortcut("r", modifiers: .command)
            .help("Refresh (⌘R)")
        } content: {
            InstalledSkills(model: model, newSkill: { adding = true }) {
                app.pluginsTab = .explore
                app.selection = .plugins
            }
        }
        // Both at once: which skills stay here depends on the catalog. The
        // app hands the model each new catalog (AppModel.start).
        .task(id: app.swePath) {
            async let catalog: Void = app.pluginCatalog.loadIfStale(swe: app.swe, for: app.swePath)
            await model.loadIfStale(for: app.swePath)
            await catalog
        }
        .quiverSheet(isPresented: $adding) {
            NewSkillSheet()
        }
    }
}

/// Writes ~/.swe/skills/<name>/SKILL.md through `swe skills new`.
private struct NewSkillSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    @State private var name = ""
    @State private var description = ""
    @State private var error: String?
    @State private var working = false
    @State private var created: String?

    static let nameRule = /^[a-z0-9]+(-[a-z0-9]+)*$/
    var nameProblem: String? {
        if name.isEmpty { return "Lowercase, digits and single hyphens: my-skill" }
        if name.count > 64 { return "At most 64 characters" }
        return name.wholeMatch(of: Self.nameRule) == nil
            ? "Lowercase letters, digits, and single hyphens between them" : nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("New skill").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            FormField("Name") {
                TextField("my-skill", text: $name)
                    .sweField(monospaced: true)
                if let nameProblem {
                    Text(nameProblem).font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            FormField("Description") {
                TextField("When should an agent use this skill?", text: $description)
                    .sweField()
            }
            if let created {
                Text("Created \(displayPath(created))")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            if let error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            HStack {
                Spacer()
                if let created, let editor = app.editor {
                    Button("Open in \(editor.name)") {
                        NSWorkspace.shared.open([URL(fileURLWithPath: created)],
                                                withApplicationAt: editor.appURL,
                                                configuration: .init())
                    }
                    .secondaryButton()
                }
                Button(created == nil ? "Close" : "Done") { dismiss() }
                    .secondaryButton()
                if created == nil {
                    Button("Create") {
                        working = true
                        error = nil
                        Task {
                            do {
                                created = try await app.skills.create(name: name, description: description).path
                            } catch { self.error = error.localizedDescription }
                            working = false
                        }
                    }
                    .primaryButton()
                    .disabled(working || nameProblem != nil)
                }
            }
        }
        .sheetScaffold(width: 420)
    }
}

// MARK: - Installed

private struct InstalledSkills: View {
    @Environment(AppModel.self) private var app
    @Bindable var model: SkillsModel
    var newSkill: () -> Void
    var openPlugins: () -> Void
    /// The skill open in its detail page, nil while the list shows.
    @State private var opened: Skill?
    /// Rows before the list folds behind a Show more row.
    private static let foldLimit = 15
    @State private var showsAll = false

    var body: some View {
        if let opened {
            // A reload swaps the inventory: follow the same folder's row.
            let skill = model.allSkills.first { $0.path == opened.path } ?? opened
            SkillDetailView(skill: skill, home: FileManager.default.homeDirectoryForCurrentUser,
                            onBack: { self.opened = nil },
                            links: AnyView(SkillLinksBlock(skill: skill, model: model)))
        } else {
            switch model.inventory {
            case .idle, .loading:
                SkeletonRows(rows: 6, framed: false, shape: .skill, label: "Reading skills…")
            case .failed(let message):
                ErrorCard(message: message)
            case .loaded(let inventory):
                if model.placement == nil {
                    SkeletonRows(rows: 4, framed: false, shape: .skill, label: "Finding the skills that ship inside plugins…")
                } else {
                    list(empty: inventory.skills.isEmpty)
                }
            }
        }
    }

    @ViewBuilder private func list(empty: Bool) -> some View {
        if let error = model.actionError { ErrorCard(message: error) }
        filters(total: model.listedSkills.count)
        if model.pluginSkillCount > 0 { PluginSkillsNote(count: model.pluginSkillCount, open: openPlugins) }
        let skills = model.visibleSkills
        if skills.isEmpty {
            EmptyCard(text: empty ? "No skills on this Mac yet."
                          : model.listedSkills.isEmpty ? "Every skill on this Mac ships inside a plugin."
                          : "No skills match.",
                      icon: "sparkles",
                      actionTitle: empty ? "New skill" : nil, action: empty ? newSkill : nil)
        } else {
            // Straight on the panel, like TokenTracker's lists: hairlines
            // between rows, no card around them.
            let shown = RowFold.visible(skills, limit: Self.foldLimit, expanded: showsAll)
            LazyVStack(spacing: 0) {
                ForEach(shown) { skill in
                    SkillRow(skill: skill, model: model) { opened = skill }
                    if skill.id != shown.last?.id { Rectangle().fill(Theme.border).frame(height: Theme.hairline) }
                }
                if RowFold.hiddenCount(total: skills.count, limit: Self.foldLimit) > 0 {
                    if !shown.isEmpty { Rectangle().fill(Theme.border).frame(height: Theme.hairline) }
                    ExpandRow(isExpanded: showsAll,
                              hidden: RowFold.hiddenCount(total: skills.count, limit: Self.foldLimit),
                              help: (expand: "Show every skill", collapse: "Show only the first \(Self.foldLimit)"),
                              edge: .inset) {
                        withAnimation(.easeOut(duration: Theme.fadeDuration)) { showsAll.toggle() }
                    }
                }
            }
        }
    }

    /// The page's one filter bar: selects, then search taking the slack,
    /// then the count.
    private func filters(total: Int) -> some View {
        let harnesses = Array(Set(model.listedSkills.flatMap(\.harnesses))).sorted()
        // Plugin skills are under Plugins, so Plugin is offered only for one
        // no catalog plugin claims, which stays here rather than vanish.
        let sources = Skill.Source.allCases.filter { source in model.listedSkills.contains { $0.source == source } }
        return HStack(spacing: 8) {
            // Closed, the menus say "All" so "Harness: All harnesses" never
            // truncates in a 168pt select.
            SelectMenu(label: "Harness",
                       options: [(String?.none, "All harnesses", nil)]
                           + harnesses.map { (String?.some($0), HarnessMark($0).name, $0) },
                       selection: $model.harness,
                       title: model.harness.map { HarnessMark($0).name } ?? "All")
            SelectMenu(label: "Source",
                       options: [(Skill.Source?.none, "All sources")] + sources.map { (Skill.Source?.some($0), $0.title) },
                       selection: $model.source,
                       title: model.source?.title ?? "All")
            SearchField(prompt: "Filter skills", text: $model.query)
                .frame(maxWidth: .infinity)
            Text("\(model.visibleSkills.count) of \(total) skills")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .monospacedDigit()
                .lineLimit(1)
                .fixedSize()
        }
    }
}

/// One line under the filter bar: how many skills ship inside plugins, and
/// a way to them on the Plugins page's Explore tab.
private struct PluginSkillsNote: View {
    var count: Int
    var open: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: open) {
            HStack(spacing: 6) {
                Image(systemName: "puzzlepiece.extension").font(Theme.font(size: 12))
                Text(count == 1 ? "1 skill ships inside a plugin;" : "\(count) skills ship inside plugins;")
                    .monospacedDigit()
                Text(count == 1 ? "see it under Plugins" : "see them under Plugins")
                    .foregroundStyle(hovering ? Theme.textPrimary : Theme.textLabel)
                    .underline(hovering)
                Image(systemName: "arrow.right").font(Theme.font(size: 10, weight: .semibold))
            }
            .font(Theme.caption)
            .foregroundStyle(Theme.textSecondary)
            .contentShape(.rect)
        }
        .plainButton(cornerRadius: Theme.smallRadius)
        .onHover { hovering = $0 }
        .hoverTip("Open Plugins > Explore, where each plugin lists the skills it ships", replacesLabel: false)
    }
}

/// "13 of 14 linked" in the page header. A click opens the harness skills
/// folders, and a folder opens what can be done with it, in the same popover.
private struct HarnessLinksSummary: View {
    var model: SkillsModel
    var sharedDir: String
    @State private var open = false
    @State private var hovering = false

    var body: some View {
        let roots = model.roots
        let linked = roots.filter { $0.link == .linked }.count
        let blocked = roots.contains { $0.link == .blocked }
        Button { open = true } label: {
            HStack(spacing: 6) {
                Image(systemName: "link").font(Theme.font(size: 12)).foregroundStyle(Theme.textSecondary)
                Text("Harness links").foregroundStyle(Theme.textPrimary)
                Text(roots.isEmpty ? "None found" : "\(linked) of \(roots.count) linked")
                    .foregroundStyle(Theme.textSecondary).monospacedDigit()
                if blocked { LinkDot(link: .blocked) }
                Image(systemName: "chevron.down").font(Theme.font(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.textMuted)
            }
            .font(Theme.caption)
            .padding(.horizontal, 8)
            .frame(height: Theme.controlHeight)
            .background(hovering || open ? Theme.panelHover : .clear, in: .rect(cornerRadius: Theme.controlRadius))
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        .plainButton(cornerRadius: Theme.controlRadius)
        .onHover { hovering = $0 }
        .help("Which harnesses load the shared skills")
        .popover(isPresented: $open, arrowEdge: .bottom) {
            HarnessLinksPanel(model: model, sharedDir: sharedDir)
        }
    }
}

/// One chip per harness skills folder; a chip opens its detail in place.
private struct HarnessLinksPanel: View {
    @Environment(AppModel.self) private var app
    var model: SkillsModel
    var sharedDir: String
    @State private var selected: SkillRoot.ID?

    var body: some View {
        let roots = model.roots
        if let id = selected, let root = roots.first(where: { $0.id == id }) {
            VStack(alignment: .leading, spacing: 0) {
                Button("Harness links", systemImage: "chevron.left") { selected = nil }
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .plainButton()
                    .padding([.top, .horizontal], 16)
                RootDetail(root: root, model: model) { selected = nil }
            }
            .frame(width: 320, alignment: .leading)
        } else {
            VStack(alignment: .leading, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Harness links").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                    Text("Each harness reads one skills folder. A linked folder points at \(displayPath(sharedDir)), so every shared skill reaches it.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if roots.isEmpty {
                    // No harness is known yet, so there is nothing to link;
                    // registering one is the step that helps.
                    HStack(spacing: 10) {
                        Text("No harness skills folders found. Register a harness first.")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        Button("Open Harnesses") { app.selection = .harnesses }
                            .secondaryButton()
                    }
                } else {
                    FlowLayout(spacing: 6, lineSpacing: 6) {
                        ForEach(roots) { root in
                            Button { selected = root.id } label: {
                                LinkChipLabel(name: root.name, link: root.link, busy: model.busy.contains(root.path))
                            }
                            .plainButton(cornerRadius: 999)
                            .hoverTip("\(HarnessMark(root.name).name): \(root.link.title.lowercased())")
                        }
                    }
                    LinkLegend()
                    let (targets, _) = SkillsModel.linkAllTargets(roots)
                    if !targets.isEmpty {
                        Button("Link all (\(targets.count))") {
                            Task { await model.linkAll() }
                        }
                        .secondaryButton()
                        .disabled(!model.busy.isEmpty)
                    }
                }
                if let summary = model.linkAllSummary {
                    Text(summary)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                // A link or unlink started here reports back here too, since
                // the page's own error card is behind the popover.
                if let error = model.actionError {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(Theme.caption).foregroundStyle(Theme.dangerText)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
            }
            .padding(16)
            .frame(width: 440, alignment: .leading)
        }
    }
}

private struct RootDetail: View {
    var root: SkillRoot
    var model: SkillsModel
    var done: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                if HarnessIcon.exists(for: root.name) { HarnessIcon(harness: root.name, size: 16) }
                Text(HarnessMark(root.name).name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                Badge(root.link.title)
            }
            Text(displayPath(root.path))
                .font(Theme.font(size: 12, design: .monospaced))
                .foregroundStyle(Theme.textSecondary)
                .textSelection(.enabled)
            Text(root.explanation)
                .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                switch root.link {
                case .linked:
                    Button("Unlink") { act { await model.unlink(root) } }
                        .secondaryButton()
                case .notLinked:
                    Button("Link to shared skills") { act { await model.link(root) } }
                        .primaryButton()
                case .blocked:
                    EmptyView()
                }
                Button("Show in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: root.path)])
                }
                .secondaryButton()
            }
            .disabled(model.busy.contains(root.path))
        }
        .padding(16)
        .frame(width: 320, alignment: .leading)
    }

    private func act(_ work: @escaping () async -> Void) {
        done()
        Task { await work() }
    }
}

/// A capsule with a state dot: green when the harness loads the skills
/// (an "on" state), a hollow ring when it doesn't, amber when quiver won't
/// link it.
private struct LinkChipLabel: View {
    var name: String
    var link: SkillRoot.Link
    var busy = false
    var compact = false

    var body: some View {
        HStack(spacing: 6) {
            if busy {
                ProgressView().controlSize(.mini)
            } else {
                LinkDot(link: link)
            }
            HarnessMarkView(harness: name, size: compact ? 12 : 14)
                .font(Theme.font(size: compact ? 11 : 12, weight: .medium))
                .foregroundStyle(link == .linked ? Theme.textPrimary : Theme.textSecondary)
        }
        .padding(.horizontal, compact ? 8 : 10)
        .frame(height: compact ? 22 : 26)
        .background(link == .linked ? Theme.subtle : .clear, in: .capsule)
        .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
        .contentShape(.capsule)
    }
}

private struct LinkDot: View {
    var link: SkillRoot.Link

    var body: some View {
        switch link {
        case .linked: Circle().fill(Theme.accentFill).frame(width: Theme.statusDot, height: Theme.statusDot)
        case .notLinked: Circle().strokeBorder(Theme.textMuted, lineWidth: 1.2).frame(width: Theme.statusDot, height: Theme.statusDot)
        case .blocked: Circle().fill(Theme.warning).frame(width: Theme.statusDot, height: Theme.statusDot)
        }
    }
}

private struct LinkLegend: View {
    var body: some View {
        HStack(spacing: 14) {
            ForEach([SkillRoot.Link.linked, .notLinked, .blocked], id: \.self) { link in
                HStack(spacing: 6) {
                    LinkDot(link: link)
                    Text(link.title).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            Text("Click one to link or unlink it.")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
        }
    }
}

private struct SkillRow: View {
    var skill: Skill
    var model: SkillsModel
    /// A click opens the skill's detail page.
    var open: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: open) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(skill.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    if !skill.description.isEmpty {
                        Text(skill.description)
                            .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                            .lineLimit(1)
                            .multilineTextAlignment(.leading)
                    }
                    Text(displayPath(skill.path))
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                        .lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 12)
                HStack(spacing: 10) {
                    Badge(skill.source.title)
                    SeenBy(harnesses: skill.harnesses)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .monospacedDigit()
                        .frame(minWidth: 86, alignment: .trailing)
                    Image(systemName: "chevron.right")
                        .font(Theme.font(size: 11, weight: .semibold))
                        .foregroundStyle(Theme.textMuted)
                }
            }
            .contentShape(.rect)
        }
        .plainButton(cornerRadius: Theme.controlRadius)
        .padding(.vertical, 12)
        // The fill reaches past the text so the row reads as one target.
        .background {
            RoundedRectangle(cornerRadius: Theme.controlRadius)
                .fill(hovering ? Theme.panelHover : .clear)
                .padding(.horizontal, -8)
        }
        .onHover { hovering = $0 }
        .help("Open \(skill.name)")
    }
}

/// What the skill's detail page shows under its header: which harnesses
/// load it, the link chips a shared skill carries, and its note.
private struct SkillLinksBlock: View {
    var skill: Skill
    var model: SkillsModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(note).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            let chips = model.chips(for: skill)
            if !chips.isEmpty {
                FlowLayout(spacing: 6, lineSpacing: 6) {
                    ForEach(chips, id: \.root.id) { chip in
                        LinkChipLabel(name: chip.root.name, link: chip.link, compact: true)
                            .hoverTip("\(HarnessMark(chip.root.name).name): \(chip.link.title.lowercased())")
                    }
                }
            }
        }
    }

    private var note: String {
        switch skill.source {
        case .shared:
            "In the shared library. Every harness whose folder is linked loads it."
        case .plugin:
            "Shipped by a plugin, so only \(skill.harnesses.first ?? "its harness") loads it."
        case .builtin:
            "Built into \(skill.harnesses.first ?? "its harness")."
        case .harness:
            skill.harnesses.isEmpty
                ? "In \(skill.root)'s own folder, which no linked harness reads."
                : "In \(skill.root)'s own folder, outside the shared library."
        case .catalog:
            "In the \(skill.root) catalog. Harnesses load it only once it is moved or linked into a skills folder."
        case .project:
            "In a project's own skills folder."
        }
    }
}

/// Which harnesses load a skill: one harness as its mark, several as up to
/// three logos and the count. The names are the tooltip and what VoiceOver
/// reads.
private struct SeenBy: View {
    var harnesses: [String]

    var body: some View {
        switch harnesses.count {
        case 0:
            Text("No harness")
        case 1:
            HarnessMarkView(harness: harnesses[0], size: 14)
        default:
            HStack(spacing: 6) {
                HStack(spacing: 3) {
                    ForEach(logos, id: \.self) { HarnessIcon(harness: $0, size: 14) }
                }
                Text("\(harnesses.count) harnesses")
            }
            .hoverTip(harnessNames(harnesses))
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(harnesses.count) harnesses: \(harnessNames(harnesses))")
        }
    }

    /// The first three distinct logos: Claude Code and Claude Desktop share one.
    private var logos: [String] {
        var seen = Set<URL>()
        return Array(harnesses.filter { harness in
            guard let url = HarnessIcons.logo(for: harness)?.url else { return false }
            return seen.insert(url).inserted
        }.prefix(3))
    }
}

extension SkillRoot.Link {
    var title: String {
        switch self {
        case .linked: "Linked"
        case .notLinked: "Not linked"
        case .blocked: "Blocked"
        }
    }
}
