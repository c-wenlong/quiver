import QuiverCore
import ServiceManagement
import SwiftUI

/// Settings, after TokenTracker's: a 176pt sub-nav of grouped sections, and
/// for the current one a 20/28 title, a description, a hairline, then a card
/// of rows with the label on the left and the control on the right. Below
/// about 720pt the sub-nav turns into underline tabs above the section.
struct SettingsPage: View {
    @AppStorage("settingsSection") private var saved = SettingsSection.general.rawValue
    @State private var width: CGFloat = 0
    /// Unsaved path edits, kept here so switching sections or crossing the
    /// narrow-layout cutoff doesn't drop them.
    @State private var drafts = PathDrafts()
    /// Why Open at login couldn't change, kept here for the same reason.
    @State private var loginFailure: String?
    @Environment(AppModel.self) private var app

    private var section: Binding<SettingsSection> {
        Binding(get: { SettingsSection(saved: saved) }, set: { saved = $0.rawValue })
    }

    var body: some View {
        Page(title: "Settings", subtitle: "How Quiver starts, what it shows, and where it finds things.") {
            Group {
                if width == 0 || width >= 720 {
                    HStack(alignment: .top, spacing: 40) {
                        SettingsSubNav(selection: section).frame(width: 176)
                        SettingsSectionView(section: section.wrappedValue, drafts: $drafts, loginFailure: $loginFailure)
                    }
                } else {
                    VStack(alignment: .leading, spacing: 24) {
                        Tabs(options: SettingsGroup.order.map { ($0, $0.title) }, selection: section, style: .underline)
                        SettingsSectionView(section: section.wrappedValue, drafts: $drafts, loginFailure: $loginFailure)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
            SettingsFooter()
        }
        .onAppear { drafts = PathDrafts(swe: app.swePath, apiKeys: app.apiKeysPath,
                                        mcpServers: app.mcpServersPath,
                                        sourceCheckout: app.updates.checkoutOverride) }
    }
}

struct PathDrafts: Equatable {
    var swe = ""
    var apiKeys = ""
    var mcpServers = ""
    var sourceCheckout = ""
}

// MARK: - Sub-nav

private struct SettingsSubNav: View {
    @Binding var selection: SettingsSection
    @FocusState private var focused: SettingsSection?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ForEach(SettingsGroup.allCases) { group in
                VStack(alignment: .leading, spacing: 2) {
                    Text(group.title)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Theme.textMuted)
                        .padding(.horizontal, 12)
                        .padding(.bottom, 4)
                    ForEach(group.sections) { section in
                        SubNavItem(section: section, isSelected: selection == section,
                                   isFocused: focused == section) { selection = section }
                            .focused($focused, equals: section)
                    }
                }
            }
        }
        .onKeyPress(.upArrow) { move(-1) }
        .onKeyPress(.downArrow) { move(1) }
    }

    private func move(_ offset: Int) -> KeyPress.Result {
        let next = (focused ?? selection).step(offset)
        selection = next
        if focused != nil { focused = next }
        return .handled
    }
}

/// 40pt, 8/12 padding, radius 8, a 16pt symbol and 14/20 at 500. The current
/// one on `bg.selected`; hover lifts the text and adds the sidebar's
/// `bg.hover` wash.
private struct SubNavItem: View {
    var section: SettingsSection
    var isSelected: Bool
    var isFocused: Bool
    var action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: section.symbol).font(.system(size: 14)).frame(width: 16)
                Text(section.title).font(.system(size: 14, weight: .medium))
                Spacer(minLength: 0)
            }
            .foregroundStyle(isSelected ? Theme.textStrong : hovering ? Theme.textLabel : Theme.textSecondary)
            .padding(.horizontal, 12)
            .frame(height: 40)
            .background(isSelected ? Theme.selected : hovering ? Theme.hover : .clear,
                        in: .rect(cornerRadius: Theme.controlRadius))
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
            .animation(.easeOut(duration: Theme.fadeDuration), value: hovering)
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))
        .focusRing(isFocused, cornerRadius: Theme.controlRadius)
        .onHover { hovering = $0 }
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

// MARK: - Sections

private struct SettingsSectionView: View {
    var section: SettingsSection
    @Binding var drafts: PathDrafts
    @Binding var loginFailure: String?
    /// The draft "Refresh only what failed for" value, in minutes.
    @State private var refreshMinutes = 0
    @FocusState private var refreshFocused: Bool
    @Environment(AppModel.self) private var app

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(section.title)
                        .font(Theme.sectionTitle).tracking(-0.5)
                        .foregroundStyle(Theme.textStrong)
                    Text(section.detail)
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                }
                Rectangle().fill(Theme.border).frame(height: Theme.hairline)
            }
            switch section {
            case .general: generalCard
            case .preferences: preferencesCard
            case .notifications: notificationsCard
            case .updates: updatesCard
            case .providers: ProvidersSection()
            case .services: ServicesCard(model: app.services)
            case .paths: pathsCard
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var generalCard: some View {
        SettingsCard {
            LoginItemRow(failure: $loginFailure)
            SettingsDivider()
            @Bindable var app = app
            SettingRow(title: "Terminal", detail: "Used by Terminal, Sign in and Resume.") {
                Tabs(options: TerminalApp.allCases.map { ($0, $0.title) }, selection: $app.terminal)
            }
            SettingsDivider()
            SettingRow(title: "Show in Dock",
                       detail: "Off keeps Quiver out of the Dock and the app switcher. Open it from the menu bar.",
                       warning: !app.showsInDock && !app.menuBar.isVisible
                           ? "The menu bar item is off in Menu bar, so Quiver stays in the Dock." : nil) {
                SettingSwitch(title: "Show in Dock", isOn: $app.showsInDock)
            }
        }
    }

    private var preferencesCard: some View {
        SettingsCard {
            @Bindable var app = app
            SettingRow(title: "Appearance",
                       detail: "System follows macOS. Widgets always follow macOS.") {
                Tabs(options: AppearanceMode.allCases.map { ($0, $0.title) }, selection: $app.appearance)
            }
            SettingsDivider()
            SettingRow(title: "App icon", detail: "The Dock shows this one. Finder keeps the light icon.") {
                Tabs(options: AppIconStyle.allCases.map { ($0, $0.title) }, selection: $app.appIcon)
            }
            SettingsDivider()
            SettingRow(title: "Hide accidental sessions",
                       detail: "Sessions with no responses, no tokens, or a one-shot probe drop out of Sessions and the calendar.") {
                SettingSwitch(title: "Hide accidental sessions", isOn: $app.hideTrivialSessions)
            }
        }
    }

    private var notificationsCard: some View {
        SettingsCard {
            @Bindable var limits = app.limits
            SettingRow(title: "Notify about limits",
                       detail: "When a limit reaches 90%, and again when it resets after that. Once per window.",
                       warning: app.limits.notificationsBlocked && app.limits.notifyEnabled
                           ? "Notifications for Quiver are off in System Settings > Notifications." : nil) {
                SettingSwitch(title: "Notify about limits", isOn: $limits.notifyEnabled)
            }
            SettingsDivider()
            SettingRow(title: "Refresh only what failed for",
                       detail: "Within this many minutes of a good reading, Refresh re-checks only the usage that failed.") {
                HStack(spacing: 6) {
                    TextField("", value: $refreshMinutes, format: .number)
                        .quiverField()
                        .frame(width: 56)
                        .multilineTextAlignment(.trailing)
                        .focused($refreshFocused)
                        .onSubmit(commitRefreshMinutes)
                        .onChange(of: refreshFocused) { _, focused in
                            if !focused { commitRefreshMinutes() }
                        }
                    Text("min").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
        }
        .onAppear { refreshMinutes = app.limits.refreshFreshMinutes }
        .onDisappear(perform: commitRefreshMinutes)
    }

    /// The field writes only on Return, focus loss or the popover closing;
    /// an entry outside the range clamps on commit.
    private func commitRefreshMinutes() {
        let minutes = RefreshFreshWindow.minutes(refreshMinutes)
        refreshMinutes = minutes
        if minutes != app.limits.refreshFreshMinutes {
            app.limits.refreshFreshMinutes = minutes
        }
    }

    private var updatesCard: some View {
        SettingsCard {
            SettingRow(title: "Version",
                       detail: versionLine,
                       warning: app.updates.versionMismatch.map {
                           "swe is \($0.swe), the app is \($0.app)."
                       }) {
                EmptyView()
            }
            SettingsDivider()
            SettingRow(title: "Updates from",
                       detail: "This build defaults to \(app.updates.build.channel.title).") {
                Tabs(options: UpdateChannel.allCases.map { ($0, $0.title) },
                     selection: Binding(get: { app.updates.channel },
                                        set: { app.updates.setChannel($0) }))
            }
            if app.updates.channel == .dev {
                SettingsDivider()
                PathRow(title: "Source checkout",
                        placeholder: "~/code/quiver",
                        stored: app.updates.checkoutOverride,
                        draft: $drafts.sourceCheckout,
                        running: app.updates.checkout ?? "",
                        hint: app.updates.stampedCheckout.map {
                            "Empty uses the folder this build came from: \($0)"
                        } ?? "Empty: no source folder recorded in this build.",
                        folders: true) { app.updates.setCheckout($0) }
                SettingsDivider()
                localCodeRow
            }
            SettingsDivider()
            releaseRow
        }
        .task { await app.updates.refresh(force: false) }
    }

    private var versionLine: String {
        let updates = app.updates
        var line = "Quiver \(updates.build.version)"
        if let describe = updates.build.describe { line += " (\(describe))" }
        line += " · swe " + (updates.sweVersionKnown ? (updates.sweVersion ?? "?") : "unknown")
        return line
    }

    private var localCodeRow: some View {
        let updates = app.updates
        let result = updates.local
        return SettingRow(
            title: "Local code",
            detail: localDetail(result),
            warning: {
                if case .failed(let message) = updates.rebuildState { return message }
                return updates.rebuildNotice
            }()) {
                HStack(spacing: 12) {
                    Button("Check now") { Task { await updates.refresh(force: true) } }
                        .secondaryButton()
                        .disabled(updates.checking)
                    rebuildControl
                }
            }
    }

    private func localDetail(_ result: LocalBuildCheck.Result?) -> String {
        guard let result else { return "No source checkout to compare against." }
        var text: String
        switch result.state {
        case .upToDate:
            text = result.buildDirty
                ? "Built from this commit plus uncommitted edits; can't tell if they match yours"
                : "Built from current code"
        case .behind(let n):
            text = "\(n) commit\(n == 1 ? "" : "s") not built yet"
        case .diverged(let buildOnly, _):
            text = "Built from another branch (\(buildOnly) commit\(buildOnly == 1 ? "" : "s") not in this checkout)"
        case .notARepo:
            text = "Not a git checkout"
        case .unknownBuild:
            text = "This build has no commit recorded"
        case .builtCommitMissing:
            text = "Built from a commit this checkout doesn't have"
        }
        if result.buildDirty, result.state != .upToDate {
            text += " · built with uncommitted edits"
        }
        if result.checkoutDirty { text += " · uncommitted changes not built" }
        if let upstream = result.upstreamBehind, upstream > 0 {
            text += "\n\(upstream) new commit\(upstream == 1 ? "" : "s") upstream, not pulled"
        }
        return text
    }

    @ViewBuilder
    private var rebuildControl: some View {
        let updates = app.updates
        switch updates.rebuildState {
        case .idle:
            Button("Rebuild") { Task { await updates.rebuild() } }
                .primaryButton()
                .disabled(updates.checkout == nil)
        case .building:
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Building…").font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            }
        case .built:
            Button("Relaunch to update") {
                if let url = updates.relaunchURL() {
                    AppRelauncher.relaunch(into: url)
                }
            }
            .primaryButton()
        case .failed:
            Button("Rebuild") {
                updates.resetRebuild()
                Task { await updates.rebuild() }
            }
            .primaryButton()
            .disabled(updates.checkout == nil)
        }
    }

    private var releaseRow: some View {
        let updates = app.updates
        let prod = updates.channel == .prod
        return SettingRow(
            title: "Latest release",
            detail: releaseDetail(updates.release)) {
                HStack(spacing: 12) {
                    if updates.release?.comparison == .available,
                       let url = updates.release?.release?.url {
                        Link("Open", destination: url).secondaryButton()
                    }
                    if prod {
                        Button("Check now") { Task { await updates.refresh(force: true) } }
                            .secondaryButton()
                            .disabled(updates.checking)
                    }
                }
            }
            .opacity(prod ? 1 : 0.6)
    }

    private func releaseDetail(_ report: ReleaseCheck.ReleaseReport?) -> String {
        var text: String
        if let release = report?.release {
            switch report?.comparison {
            case .available:
                text = "\(release.tag) available"
            case .upToDate:
                text = "\(release.tag) · up to date"
            default:
                text = "\(release.tag) · can't compare with \(app.updates.build.version)"
            }
        } else {
            text = "Not checked yet."
        }
        if let error = report?.error {
            text += " · couldn't check: \(error)"
        }
        return text
    }

    private var pathsCard: some View {
        SettingsCard {
            PathRow(title: "swe executable", placeholder: "/opt/homebrew/bin/swe",
                    stored: app.swePath, draft: $drafts.swe, running: SweLocation.executable(override: app.swePath),
                    hint: "Empty uses the swe your login shell finds.") { app.swePath = $0 }
            SettingsDivider()
            PathRow(title: "API keys file", placeholder: "~/.quiver/secrets/providers/.api_keys",
                    stored: app.apiKeysPath, draft: $drafts.apiKeys, running: app.apiKeys.path,
                    hint: "Empty uses ~/.quiver/secrets/providers/.api_keys.") { app.apiKeysPath = $0 }
            SettingsDivider()
            PathRow(title: "MCP servers folder", placeholder: "~/.quiver/mcp/servers",
                    stored: app.mcpServersPath, draft: $drafts.mcpServers,
                    running: AppModel.mcpServersFolder(path: app.mcpServersPath).path,
                    hint: "Empty uses ~/.quiver/mcp/servers.", folders: true) { app.mcpServersPath = $0 }
            SettingsDivider()
            SettingRow(title: "Editor", detail: "Opens a server folder from the MCP servers page.") {
                let editors = CodeEditors.installed()
                if editors.isEmpty {
                    Text("No code editor found")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                } else {
                    SelectMenu(options: editors.map { ($0.name, $0.name) },
                               selection: Binding(get: { app.codeEditor }, set: { app.codeEditor = $0 }),
                               title: app.editor?.name)
                }
            }
        }
    }
}

/// Settings > Services: one group per known service — its help line, a
/// row per key (a SecureField until saved, then dates with Replace and
/// Remove), and its Test button.
private struct ServicesCard: View {
    let model: ServicesModel

    var body: some View {
        SettingsCard {
            ForEach(ServiceCredentials.services) { service in
                if service.id != ServiceCredentials.services.first?.id { SettingsDivider() }
                serviceGroup(service)
            }
            if let error = model.error {
                SettingsDivider()
                Text(error)
                    .font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .padding(.vertical, 10)
            }
        }
        .task { await model.reload() }
    }

    private func serviceGroup(_ service: KnownService) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 8) {
                    if HarnessIcon.exists(for: service.id) { HarnessIcon(harness: service.id, size: 16) }
                    Text(service.name).font(Theme.rowTitle).foregroundStyle(Theme.textStrong)
                }
                Text(service.help).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            .padding(.vertical, 14)
            ForEach(service.fields, id: \.self) { field in
                ServiceFieldRow(field: field, model: model)
            }
            if let probe = service.probe {
                HStack(spacing: 10) {
                    Button(model.probing.contains(service.id) ? "Testing…" : probe.label) {
                        Task { await model.probe(service) }
                    }
                    .secondaryButton()
                    .disabled(!model.canProbe(service) || model.probing.contains(service.id))
                    .help(model.canProbe(service)
                          ? "Calls the service with the saved key."
                          : "Save \(probe.field) first.")
                    if let result = model.probeResults[service.id] {
                        Text(resultText(result))
                            .font(Theme.caption)
                            .foregroundStyle(resultColor(result))
                    }
                }
                .padding(.bottom, 14)
            }
        }
    }

    private func resultText(_ result: ServiceCredentials.ProbeResult) -> String {
        switch result {
        case .works: "Works"
        case .httpError(let status): "HTTP \(status)"
        case .unavailable(let message): message
        }
    }

    private func resultColor(_ result: ServiceCredentials.ProbeResult) -> Color {
        switch result {
        case .works: Theme.success
        case .httpError, .unavailable: Theme.warningText
        }
    }
}

/// One service key: a paste field until saved, then the saved line with
/// Replace and Remove.
private struct ServiceFieldRow: View {
    let field: String
    let model: ServicesModel
    @State private var draft = ""
    @State private var editing = false

    private var status: ServiceCredentials.Status {
        model.statuses[field] ?? ServiceCredentials.Status(isSet: false, added: nil, lastUsed: nil)
    }

    private var savedDetail: String {
        var line = "Saved"
        if let added = status.added {
            line += " · added \(added.formatted(.dateTime.month(.abbreviated).day().year()))"
        }
        if let lastUsed = status.lastUsed {
            line += " · last used \(lastUsed.formatted(.dateTime.month(.abbreviated).day().year()))"
        }
        return line
    }

    var body: some View {
        SettingRow(title: field,
                   detail: status.isSet && !editing ? savedDetail : "In the Keychain, not in the API keys file.") {
            if status.isSet && !editing {
                HStack(spacing: 8) {
                    Button("Replace") { editing = true; draft = "" }
                        .secondaryButton()
                    Button("Remove") { Task { await model.remove(field) } }
                        .secondaryButton()
                }
            } else {
                HStack(spacing: 8) {
                    SecureField(editing ? "New value" : "Paste the value", text: $draft)
                        .quiverField(monospaced: true)
                        .frame(width: 240)
                        .onSubmit(save)
                    Button("Save", action: save)
                        .primaryButton()
                        .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    if editing {
                        Button("Cancel") { editing = false; draft = "" }
                            .secondaryButton()
                    }
                }
            }
        }
    }

    private func save() {
        let value = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return }
        draft = ""
        editing = false
        Task { await model.set(field, value: value) }
    }
}

/// A bordered card of rows with hairlines between them.
struct SettingsCard<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0) { content }
            .padding(.horizontal, 20)
            .padding(.vertical, 4)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
    }
}

struct SettingsDivider: View {
    var body: some View { Rectangle().fill(Theme.border).frame(height: Theme.hairline) }
}

/// Label 14/20 in `text.label` over a 12/16 description, the control on the
/// right. A warning takes the description's place, marked with an icon.
struct SettingRow<Control: View>: View {
    var title: String
    var detail: String
    var warning: String? = nil
    var danger = false
    @ViewBuilder var control: Control

    var body: some View {
        HStack(alignment: .center, spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
                if let warning {
                    Label {
                        Text(warning).foregroundStyle(Theme.textSecondary)
                    } icon: {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(danger ? Theme.dangerText : Theme.warningText)
                    }
                    .font(Theme.caption)
                } else {
                    Text(detail).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            control
        }
        .padding(.vertical, 14)
    }
}

struct SettingSwitch: View {
    var title: String
    @Binding var isOn: Bool

    var body: some View {
        Toggle(title, isOn: $isOn).labelsHidden().toggleStyle(GreenSwitchStyle())
    }
}

/// A path setting: label and the path in use (one line, truncated in the
/// middle), then a 36pt mono field with Choose… and Use.
private struct PathRow: View {
    var title: String
    var placeholder: String
    var stored: String
    @Binding var draft: String
    var running: String
    var hint: String
    /// Choose… picks a folder instead of a file.
    var folders = false
    var apply: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
                Text("In use: \(displayPath(running)). \(hint)")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .lineLimit(1).truncationMode(.middle)
                    .help(running)
            }
            HStack(spacing: 8) {
                TextField(placeholder, text: $draft)
                    .quiverField(monospaced: true)
                    .onSubmit(use)
                Button("Choose…", action: choose).secondaryButton()
                Button("Use", action: use).primaryButton()
                    .disabled(draft.trimmingCharacters(in: .whitespaces) == stored)
            }
        }
        .padding(.vertical, 14)
        .onChange(of: stored) { draft = stored }
    }

    private func use() {
        apply(draft.trimmingCharacters(in: .whitespaces))
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = folders
        panel.canChooseFiles = !folders
        panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true
        if panel.runModal() == .OK, let url = panel.url {
            draft = url.path
            use()
        }
    }
}

extension TerminalApp {
    var title: String {
        switch self {
        case .terminal: "Terminal"
        case .iterm: "iTerm"
        case .ghostty: "Ghostty"
        case .wezterm: "WezTerm"
        }
    }
}

/// Open at login through `SMAppService.mainApp`. macOS owns the setting, so
/// the switch shows what it reports rather than a saved preference.
private struct LoginItemRow: View {
    @State private var status = SMAppService.mainApp.status
    @Binding var failure: String?

    var body: some View {
        SettingRow(title: "Open at login",
                   detail: "Start Quiver in the menu bar when you log in.",
                   warning: failure ?? (status == .requiresApproval
                       ? "macOS needs you to allow Quiver in Login Items." : nil),
                   danger: failure != nil) {
            HStack(spacing: 12) {
                if status == .requiresApproval {
                    Button("Open Login Items") { SMAppService.openSystemSettingsLoginItems() }
                        .secondaryButton()
                }
                SettingSwitch(title: "Open at login",
                              isOn: Binding(get: { status == .enabled || status == .requiresApproval },
                                            set: { on in update(on) }))
            }
        }
        // The user may approve or remove the item in System Settings.
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            status = SMAppService.mainApp.status
        }
    }

    private func update(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            failure = nil
        } catch {
            failure = "Couldn't change it: \(error.localizedDescription)"
        }
        status = SMAppService.mainApp.status
    }
}

// MARK: - Footer

/// Version · Doctor · Service status, 12/16 muted, centred under the page.
private struct SettingsFooter: View {
    @Environment(AppModel.self) private var app

    private var version: String {
        let info = Bundle.main.infoDictionary
        return info?["QuiverVersion"] as? String ?? info?["CFBundleShortVersionString"] as? String ?? "dev"
    }

    var body: some View {
        HStack(spacing: 8) {
            Text("Quiver \(version)")
            Text("·")
            FooterLink(title: "Doctor", symbol: AppSection.doctor.symbol) { app.selection = .doctor }
            Text("·")
            FooterLink(title: "Service status", symbol: AppSection.serviceStatus.symbol) { app.selection = .serviceStatus }
        }
        .font(Theme.caption)
        .foregroundStyle(Theme.textMuted)
        .frame(maxWidth: .infinity)
        .padding(.top, 8)
    }
}

private struct FooterLink: View {
    var title: String
    var symbol: String
    var action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Label(title, systemImage: symbol)
                .foregroundStyle(hovering ? Theme.footerLinkHover : Theme.textMuted)
                .animation(.easeOut(duration: Theme.fadeDuration), value: hovering)
        }
        .plainButton(cornerRadius: Theme.smallRadius)
        .onHover { hovering = $0 }
    }
}
