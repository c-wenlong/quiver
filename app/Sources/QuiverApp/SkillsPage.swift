import AppKit
import QuiverCore
import SwiftUI

/// Skills, after TokenTracker's: installed skills with the harnesses that
/// load them, and a browse view of the quiver hub. Links and unlinks go
/// through `swe skills`, never written from here.
struct SkillsPage: View {
    enum Tab: Hashable { case installed, hub }

    @Environment(AppModel.self) private var app
    @State private var model: SkillsModel?
    @State private var tab: Tab = .installed

    var body: some View {
        Page(title: "Skills",
             subtitle: "Every skill on this Mac, which harnesses load it, and what the quiver hub offers.") {
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task {
                    await model?.load()
                    if tab == .hub { await model?.loadHub() }
                }
            }
            .iconButton()
            .help("Refresh")
        } content: {
            Tabs(options: [(Tab.installed, "Installed"), (Tab.hub, "Quiver hub")], selection: $tab, style: .underline)
                // The harness links fold into one line on the tab row, so the
                // first skill sits right under the filter bar.
                .overlay(alignment: .trailing) {
                    if tab == .installed, let model, case .loaded(let inventory) = model.inventory {
                        HarnessLinksSummary(model: model, sharedDir: inventory.sharedDir)
                    }
                }
            if let model {
                switch tab {
                case .installed: InstalledSkills(model: model)
                case .hub: HubBrowser(model: model)
                }
            }
        }
        .task(id: app.swePath) {
            let skills = SkillsModel(swe: app.swe)
            model = skills
            await skills.load()
        }
        .task(id: tab) {
            if tab == .hub, let model, model.hub == .idle { await model.loadHub() }
        }
    }
}

// MARK: - Installed

private struct InstalledSkills: View {
    @Bindable var model: SkillsModel

    var body: some View {
        switch model.inventory {
        case .idle, .loading:
            LoadingCard(text: "Reading skills…")
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded(let inventory):
            if let error = model.actionError { ErrorCard(message: error) }
            filters(total: inventory.skills.count)
            let skills = model.visibleSkills
            if skills.isEmpty {
                EmptyCard(text: inventory.skills.isEmpty ? "No skills on this Mac yet." : "No skills match.",
                          icon: "sparkles")
            } else {
                // Straight on the panel, like TokenTracker's lists: hairlines
                // between rows, no card around them.
                LazyVStack(spacing: 0) {
                    ForEach(skills) { skill in
                        SkillRow(skill: skill, model: model)
                        if skill.id != skills.last?.id { Rectangle().fill(Theme.border).frame(height: 1) }
                    }
                }
            }
        }
    }

    /// The page's one filter bar: selects, then search taking the slack,
    /// then the count.
    private func filters(total: Int) -> some View {
        let harnesses = Array(Set(model.allSkills.flatMap(\.harnesses))).sorted()
        let sources = Skill.Source.allCases.filter { source in model.allSkills.contains { $0.source == source } }
        return HStack(spacing: 8) {
            // Closed, the menus say "All" so "Harness: All harnesses" never
            // truncates in a 168pt select.
            SelectMenu(label: "Harness",
                       options: [(String?.none, "All harnesses")] + harnesses.map { (String?.some($0), $0) },
                       selection: $model.harness,
                       title: model.harness ?? "All")
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

/// "13 of 14 linked" on the tab row. A click opens the harness skills
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
                Image(systemName: "link").font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                Text("Harness links").foregroundStyle(Theme.textPrimary)
                Text(roots.isEmpty ? "None found" : "\(linked) of \(roots.count) linked")
                    .foregroundStyle(Theme.textSecondary).monospacedDigit()
                if blocked { LinkDot(link: .blocked) }
                Image(systemName: "chevron.down").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.textMuted)
            }
            .font(Theme.caption)
            .padding(.horizontal, 8)
            .frame(height: Theme.controlHeight)
            .background(hovering || open ? Theme.tabHover : .clear, in: .rect(cornerRadius: Theme.controlRadius))
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
                    Text("No harness skills folders found. Run swe init to set them up.")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                } else {
                    FlowLayout(spacing: 6, lineSpacing: 6) {
                        ForEach(roots) { root in
                            Button { selected = root.id } label: {
                                LinkChipLabel(name: root.name, link: root.link, busy: model.busy.contains(root.path))
                            }
                            .plainButton(cornerRadius: 999)
                            .help("\(root.name): \(root.link.title.lowercased())")
                        }
                    }
                    LinkLegend()
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
                Text(root.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                Badge(root.link.title)
            }
            Text(displayPath(root.path))
                .font(.system(size: 12, design: .monospaced))
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
            Text(name)
                .font(.system(size: compact ? 11 : 12, weight: .medium))
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
        case .linked: Circle().fill(Theme.accentFill).frame(width: 7, height: 7)
        case .notLinked: Circle().strokeBorder(Theme.textMuted, lineWidth: 1.2).frame(width: 7, height: 7)
        case .blocked: Circle().fill(Theme.warning).frame(width: 7, height: 7)
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
    @State private var expanded = false
    @State private var hovering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button { withAnimation(.easeOut(duration: 0.15)) { expanded.toggle() } } label: {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(skill.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                        if !skill.description.isEmpty {
                            Text(skill.description)
                                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                                .lineLimit(expanded ? nil : 1)
                                .multilineTextAlignment(.leading)
                        }
                        Text(displayPath(skill.path))
                            .font(Theme.caption).foregroundStyle(Theme.textMuted)
                            .lineLimit(1).truncationMode(.middle)
                    }
                    Spacer(minLength: 12)
                    HStack(spacing: 10) {
                        Badge(skill.source.title)
                        Text(seenBy)
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .monospacedDigit()
                            .frame(minWidth: 86, alignment: .trailing)
                        Image(systemName: "chevron.right")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(Theme.textMuted)
                            .rotationEffect(.degrees(expanded ? 90 : 0))
                    }
                }
                .contentShape(.rect)
            }
            .plainButton(cornerRadius: Theme.controlRadius)

            if expanded {
                VStack(alignment: .leading, spacing: 8) {
                    Text(note).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    let chips = model.chips(for: skill)
                    if !chips.isEmpty {
                        FlowLayout(spacing: 6, lineSpacing: 6) {
                            ForEach(chips, id: \.root.id) { chip in
                                LinkChipLabel(name: chip.root.name, link: chip.link, compact: true)
                                    .help("\(chip.root.name): \(chip.link.title.lowercased())")
                            }
                        }
                    }
                    Button("Show in Finder", systemImage: "folder") {
                        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: skill.path)])
                    }
                    .secondaryButton()
                }
                .padding(.leading, 2)
            }
        }
        .padding(.vertical, 12)
        // The fill reaches past the text so the row reads as one target.
        .background {
            RoundedRectangle(cornerRadius: Theme.controlRadius)
                .fill(hovering ? Theme.rowHover : .clear)
                .padding(.horizontal, -8)
        }
        .onHover { hovering = $0 }
    }

    private var seenBy: String {
        switch skill.harnesses.count {
        case 0: "No harness"
        case 1: skill.harnesses[0]
        default: "\(skill.harnesses.count) harnesses"
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

extension SkillRoot.Link {
    var title: String {
        switch self {
        case .linked: "Linked"
        case .notLinked: "Not linked"
        case .blocked: "Blocked"
        }
    }
}

// MARK: - Hub

private struct HubBrowser: View {
    @Bindable var model: SkillsModel

    var body: some View {
        // Outside the switch: when the reload after a failed fix also fails,
        // the reason the fix failed must stay on screen.
        if let error = model.hubError { ErrorCard(message: error) }
        switch model.hub {
        case .idle, .loading:
            LoadingCard(text: "Reading the hub…")
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded(let hub):
            let plugins = model.hubPlugins
            let markets = Set(hub.plugins.map(\.marketplace)).count
            HStack(spacing: 8) {
                SearchField(prompt: "Search plugins and skills", text: $model.query)
                    .frame(maxWidth: .infinity)
                Text("\(plugins.count) of \(hub.plugins.count) plugins")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .monospacedDigit()
                    .lineLimit(1)
                    .fixedSize()
                    .help("From \(markets) marketplaces in \(displayPath(hub.hub))/plugins")
            }
            if plugins.isEmpty {
                EmptyCard(text: hub.plugins.isEmpty ? "No plugins in the hub yet." : "No plugins match.",
                          icon: "shippingbox")
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 320), spacing: Theme.gutter, alignment: .top)],
                          spacing: Theme.gutter) {
                    ForEach(plugins) { plugin in
                        PluginCard(plugin: plugin, model: model)
                    }
                }
            }
        }
    }
}

private struct PluginCard: View {
    var plugin: HubPlugin
    var model: SkillsModel
    @State private var copied = false
    @State private var confirming = false

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 4) {
                Text(plugin.name).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                Text([plugin.marketplace, plugin.version.map { "v\($0)" }].compactMap { $0 }.joined(separator: " · "))
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            if !plugin.description.isEmpty {
                Text(plugin.description)
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !plugin.skills.isEmpty {
                FlowLayout(spacing: 4, lineSpacing: 4) {
                    ForEach(plugin.skills.prefix(8)) { skill in
                        Badge(skill.name).help(skill.description)
                    }
                    if plugin.skills.count > 8 { Badge("+\(plugin.skills.count - 8) more") }
                }
            }
            Text(contents).font(Theme.caption).foregroundStyle(Theme.textMuted)
            Divider().overlay(Theme.border)
            HStack(spacing: 14) {
                ForEach(plugin.harnesses, id: \.self) { harness in
                    if let install = plugin.install[harness] {
                        HStack(spacing: 6) {
                            Circle()
                                .fill(install.state == .installed ? Theme.accentFill : Theme.warning)
                                .frame(width: 7, height: 7)
                            Text(harness).font(Theme.caption.weight(.medium)).foregroundStyle(Theme.textPrimary)
                            Text(install.state.title.lowercased()).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        }
                        .help(install.fix ?? "\(harness) has the current version")
                    }
                }
                Spacer(minLength: 0)
            }
            if !plugin.fixes.isEmpty {
                HStack(spacing: 8) {
                    if !plugin.runnableFixes.isEmpty {
                        let running = model.fixingPlugin == plugin.id
                        Button { confirming = true } label: {
                            if running {
                                HStack(spacing: 6) { ProgressView().controlSize(.mini); Text("Fixing") }
                            } else {
                                Label("Fix", systemImage: "wrench.and.screwdriver")
                            }
                        }
                        .primaryButton()
                        .disabled(model.fixingPlugin != nil)
                        .help(runnableText)
                        .confirmationDialog("Fix \(plugin.name)?", isPresented: $confirming) {
                            Button("Run") { Task { await model.fixHub(plugin) } }
                            Button("Cancel", role: .cancel) {}
                        } message: {
                            Text("swe checks the gap is still there, then runs:\n\n\(runnableText)")
                        }
                    }
                    if !plugin.manualFixes.isEmpty {
                        // An edit by hand (codex has no enable command): nothing to run.
                        Button(copied ? "Copied" : "Copy command", systemImage: copied ? "checkmark" : "doc.on.doc") {
                            copyToPasteboard(plugin.manualFixes.joined(separator: "\n"))
                            copied = true
                        }
                        .secondaryButton()
                        .help(plugin.manualFixes.joined(separator: "\n"))
                    }
                }
            }
        }
    }

    private var runnableText: String {
        plugin.runnableFixes.compactMap(\.fix).joined(separator: "\n")
    }

    private var contents: String {
        let order = ["skills", "agents", "commands", "hooks"]
        let parts = order.compactMap { kind -> String? in
            guard let n = plugin.components[kind], n > 0 else { return nil }
            let noun = n == 1 ? String(kind.dropLast()) : kind
            return "\(n) \(noun)"
        }
        return parts.isEmpty ? "No components" : parts.joined(separator: " · ")
    }
}
