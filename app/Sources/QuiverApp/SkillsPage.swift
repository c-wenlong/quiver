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
            .buttonStyle(IconButtonStyle())
            .help("Refresh")
        } content: {
            Tabs(options: [(Tab.installed, "Installed"), (Tab.hub, "Quiver hub")], selection: $tab)
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
            ProgressView().frame(maxWidth: .infinity)
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded(let inventory):
            if let error = model.actionError { ErrorCard(message: error) }
            HarnessLinks(model: model, sharedDir: inventory.sharedDir)
            filters(total: inventory.skills.count)
            let skills = model.visibleSkills
            if skills.isEmpty {
                Card {
                    Text(inventory.skills.isEmpty ? "No skills on this Mac yet." : "No skills match.")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                }
            } else {
                VStack(spacing: 0) {
                    LazyVStack(spacing: 0) {
                        ForEach(skills) { skill in
                            SkillRow(skill: skill, model: model)
                            if skill.id != skills.last?.id { Divider().overlay(Theme.border) }
                        }
                    }
                }
                .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
            }
        }
    }

    private func filters(total: Int) -> some View {
        let harnesses = Array(Set(model.allSkills.flatMap(\.harnesses))).sorted()
        let sources = Skill.Source.allCases.filter { source in model.allSkills.contains { $0.source == source } }
        return HStack(spacing: 8) {
            SelectMenu(label: "Harness",
                       options: [(String?.none, "All harnesses")] + harnesses.map { (String?.some($0), $0) },
                       selection: $model.harness)
            SelectMenu(label: "Source",
                       options: [(Skill.Source?.none, "All sources")] + sources.map { (Skill.Source?.some($0), $0.title) },
                       selection: $model.source)
            SearchField(prompt: "Filter skills", text: $model.query)
                .frame(maxWidth: 320)
            Text("\(model.visibleSkills.count) of \(total) skills")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .monospacedDigit()
            Spacer(minLength: 0)
        }
    }
}

/// One chip per harness skills folder. A click opens what can be done.
private struct HarnessLinks: View {
    var model: SkillsModel
    var sharedDir: String

    var body: some View {
        let roots = model.roots
        let linked = roots.filter { $0.link == .linked }.count
        Card(title: "Harness links",
             subtitle: "Each harness reads one skills folder. A linked folder points at \(abbreviated(sharedDir)), so every shared skill reaches it.") {
            Text("\(linked) of \(roots.count) linked")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
        } content: {
            if roots.isEmpty {
                Text("No harness skills folders found. Run swe init to set them up.")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            } else {
                FlowLayout(spacing: 6, lineSpacing: 6) {
                    ForEach(roots) { root in
                        RootChip(root: root, model: model)
                    }
                }
                LinkLegend()
            }
        }
    }
}

private struct RootChip: View {
    var root: SkillRoot
    var model: SkillsModel
    @State private var open = false

    var body: some View {
        Button { open = true } label: {
            LinkChipLabel(name: root.name, link: root.link, busy: model.busy.contains(root.path))
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: 999))
        .help("\(root.name): \(root.link.title.lowercased())")
        .popover(isPresented: $open, arrowEdge: .bottom) {
            RootDetail(root: root, model: model) { open = false }
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
            Text(abbreviated(root.path))
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
                        .buttonStyle(SecondaryButtonStyle())
                case .notLinked:
                    Button("Link to shared skills") { act { await model.link(root) } }
                        .buttonStyle(PrimaryButtonStyle())
                case .blocked:
                    EmptyView()
                }
                Button("Show in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: root.path)])
                }
                .buttonStyle(SecondaryButtonStyle())
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
            Text("Click a harness to link or unlink it.")
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
                                .lineLimit(expanded ? nil : 2)
                                .multilineTextAlignment(.leading)
                        }
                        Text(abbreviated(skill.path))
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
            .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))

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
                    .buttonStyle(SecondaryButtonStyle())
                }
                .padding(.leading, 2)
            }
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, 16)
        .background(hovering ? Theme.rowHover : .clear)
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
            ProgressView().frame(maxWidth: .infinity)
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded(let hub):
            let plugins = model.hubPlugins
            let markets = Set(hub.plugins.map(\.marketplace)).count
            HStack(spacing: 8) {
                SearchField(prompt: "Search plugins and skills", text: $model.query)
                    .frame(maxWidth: 360)
                Text("\(hub.plugins.count) plugins from \(markets) marketplaces in \(abbreviated(hub.hub))/plugins")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                Spacer(minLength: 0)
            }
            if plugins.isEmpty {
                Card {
                    Text(hub.plugins.isEmpty ? "No plugins in the hub yet." : "No plugins match.")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                }
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
                Text(plugin.name).font(.system(size: 16, weight: .semibold)).foregroundStyle(Theme.textPrimary)
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
                        .buttonStyle(PrimaryButtonStyle())
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
                        .buttonStyle(SecondaryButtonStyle())
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

struct ErrorCard: View {
    var message: String

    var body: some View {
        Card {
            Label(message, systemImage: "exclamationmark.triangle")
                .font(Theme.bodySmall)
                .foregroundStyle(Theme.danger)
                .textSelection(.enabled)
        }
    }
}
