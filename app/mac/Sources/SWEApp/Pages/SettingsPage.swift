import AppKit
import SWECore
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
        Page(title: "Settings", subtitle: "How SWE starts, what it shows, and where it finds things.") {
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
                        .font(Theme.font(size: 11, weight: .medium))
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
                Image(systemName: section.symbol).font(Theme.font(size: 14)).frame(width: 16)
                Text(section.title).font(Theme.font(size: 14, weight: .medium))
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
    /// Terminals LaunchServices can't find, checked when General appears.
    @State private var missingTerminals: Set<TerminalApp> = []
    /// Each terminal's icon, looked up with missingTerminals, not per body
    /// pass: a body runs on every keystroke in a Files path field.
    @State private var terminalIcons: [TerminalApp: NSImage] = [:]
    /// The editors in /Applications and their icons, looked up when Files
    /// appears and when the app comes back to the front.
    @State private var editors: (installed: [CodeEditor], icons: [String: NSImage]) = ([], [:])
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
            case .general:
                VStack(alignment: .leading, spacing: 20) {
                    generalCard
                    SettingsCardGroup(title: "Display") { preferencesCard }
                    SettingsCardGroup(title: "Updates") { updatesCard }
                    SettingsCardGroup(title: "Doctor",
                                      detail: "Checks on PATH, Node and drift between quiver and your harnesses.") {
                        DoctorSection()
                    }
                    // Below Doctor, so a finding it shows is one click from
                    // the report, and the copied diagnostics carry it.
                    SettingsCardGroup(title: "Feedback") { feedbackCard }
                }
            case .sidebar: SidebarSection()
            case .notifications: notificationsCard
            case .providers: ProvidersSection()
            case .integrations:
                VStack(alignment: .leading, spacing: 28) {
                    SettingsCardGroup(title: "GitHub",
                                      detail: "The gh install behind the GitHub pages, and the synced repo catalog the Add-repo search reads.",
                                      icons: ["github"]) {
                        githubCard
                    }
                    SettingsCardGroup(title: "Services",
                                      detail: "Keys for the services SWE itself calls, kept in the Keychain.",
                                      icons: ServiceCredentials.services.map(\.id)) {
                        ServicesCard(model: app.services)
                    }
                    SettingsCardGroup(title: "Remote",
                                      detail: "Pair a phone to watch and steer sessions, and see which devices are paired.",
                                      icons: ["tailscale"]) {
                        RemoteSection()
                    }
                }
            case .files: filesCard
            case .experimental: ExperimentalSection(sourceCheckout: $drafts.sourceCheckout)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var terminalDetail: String {
        let missing = TerminalApp.allCases.filter(missingTerminals.contains).map(\.title)
        guard !missing.isEmpty else { return "Used by Terminal, Sign in and Resume." }
        let names = ListFormatter.localizedString(byJoining: missing)
        return "Used by Terminal, Sign in and Resume. \(names) \(missing.count == 1 ? "isn't" : "aren't") installed."
    }

    private var generalCard: some View {
        SettingsCard {
            LoginItemRow(failure: $loginFailure)
            SettingsDivider()
            @Bindable var app = app
            SettingRow(title: "Terminal", detail: terminalDetail,
                       warning: missingTerminals.contains(app.terminal)
                           ? app.terminal.notInstalledMessage : nil) {
                Tabs(options: TerminalApp.allCases.map { ($0, $0.title) }, selection: $app.terminal,
                     iconImages: terminalIcons, disabled: missingTerminals)
            }
            SettingsDivider()
            SettingRow(title: "Show in Dock",
                       detail: "Off keeps SWE out of the Dock and the app switcher. Open it from the menu bar.",
                       warning: !app.showsInDock && !app.menuBar.isVisible
                           ? "The menu bar item is off in Menu bar, so SWE stays in the Dock." : nil) {
                SettingSwitch(title: "Show in Dock", isOn: $app.showsInDock)
            }
            SettingsDivider()
            SettingRow(title: "First-run setup",
                       detail: "Pick the agents you use, link AGENTS.md and skills into each one, and choose the look and notifications.") {
                Button("Run setup") { app.showsFirstRunSetup = true }
                    .secondaryButton()
            }
        }
        .onAppear(perform: checkTerminals)
        // A terminal installed while General stays open counts on return.
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            checkTerminals()
        }
    }

    private func checkTerminals() {
        missingTerminals = Set(TerminalApp.allCases.filter { !$0.isInstalled })
        var icons: [TerminalApp: NSImage] = [:]
        for terminal in TerminalApp.allCases {
            icons[terminal] = terminal.icon
                ?? terminal.toolIconID.flatMap(ToolIcons.image(for:))
                ?? missingAppIcon
        }
        terminalIcons = icons
    }

    /// The installed editors (a stat per candidate, so off the main actor)
    /// and every candidate's icon.
    private func checkEditors() async {
        let installed = await offMain { CodeEditors.installed() }
        editors = (installed, editorIcons(installed))
    }

    private var preferencesCard: some View {
        SettingsCard {
            @Bindable var app = app
            SettingRow(title: "Appearance",
                       detail: "System follows macOS. Widgets always follow macOS.") {
                Tabs(options: AppearanceMode.allCases.map { ($0, $0.title) }, selection: $app.appearance)
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
            @Bindable var githubNotify = app.githubNotify
            SettingRow(title: "Notify about limits",
                       detail: "When a limit reaches 90%, and again when it resets after that. Once per window.",
                       warning: app.limits.notificationsBlocked && app.limits.notifyEnabled
                           ? "Notifications for SWE are off. In System Settings › Notifications, find SWE in the list and turn on Allow notifications." : nil) {
                SettingSwitch(title: "Notify about limits", isOn: $limits.notifyEnabled)
            }
            SettingsDivider()
            let inboxHidden = app.sidebar.isHidden(.inbox)
            SettingRow(title: "Notify about GitHub",
                       detail: "New inbox notifications banner while the app runs; a click opens the repo's board. Hiding Inbox in Sidebar turns this off.",
                       warning: inboxHidden ? "Inbox is hidden in Sidebar. Show it to turn this on." : nil) {
                // Banners open the Inbox, so they stay off while it is hidden.
                SettingSwitch(title: "Notify about GitHub", isOn: $githubNotify.bannersEnabled)
                    .disabled(inboxHidden)
            }
            SettingsDivider()
            SettingRow(title: "Refresh only what failed for",
                       detail: "Within this many minutes of a good reading, Refresh re-checks only the usage that failed.") {
                HStack(spacing: 6) {
                    TextField("", value: $refreshMinutes, format: .number)
                        .sweField()
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

    /// The gh install behind the GitHub pages, plus the one-shot sync
    /// that fills the local repo catalog the Add-repo search reads.
    private var githubCard: some View {
        let model = app.github
        return SettingsCard {
            SettingRow(title: "GitHub CLI", detail: ghDetail,
                       warning: ghWarning) {
                Button { Task { await model.load(swe: app.swe) } } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .iconButton()
                .help("Check gh again")
            }
            SettingsDivider()
            SettingRow(title: "Repository catalog", detail: catalogDetail,
                       warning: model.syncError) {
                HStack(spacing: 8) {
                    if model.syncing { ProgressView().controlSize(.small) }
                    Button("Sync now") {
                        Task { await model.syncGitHub(swe: app.swe) }
                    }
                    .primaryButton()
                    .disabled(model.syncing || model.envSetup != nil)
                }
            }
        }
        .task(id: app.swePath) { await model.loadPinsIfNeeded(swe: app.swe) }
    }

    private var ghDetail: String {
        let model = app.github
        guard let env = model.env else {
            return model.probeError ?? "Not checked yet."
        }
        return switch env.status {
        case .ok: env.login.map { "Signed in as \($0)." } ?? "Signed in."
        case .unavailable: "Not installed."
        case .authRequired: "Not signed in."
        case .upstreamError: env.error ?? "The gh probe failed."
        }
    }

    private var ghWarning: String? {
        switch app.github.env?.status {
        case .unavailable: "Download gh cli: brew install gh"
        case .authRequired: "Run gh auth login"
        default: nil
        }
    }

    private var catalogDetail: String {
        guard let catalog = app.github.catalog else {
            return "Not synced yet — the catalog is the repo list the Add-repository search reads."
        }
        let ago = catalog.syncedAt?.date
            .map { " · synced \(UsageFormat.ago($0, now: .now))" } ?? ""
        return "\(catalog.repos) repos\(ago) — the Add-repository search reads this list."
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
                       detail: app.updates.versionLine,
                       warning: app.updates.versionMismatch.map {
                           "swe is \($0.swe), the app is \($0.app)."
                       }) {
                EmptyView()
            }
            SettingsDivider()
            releaseRow
            if app.updates.build.channel == .prod {
                SettingsDivider()
                SettingRow(title: "Automatic checks",
                           detail: "Sparkle checks the appcast once a day.") {
                    SettingSwitch(title: "Check automatically", isOn: Binding(
                        get: { AppUpdater.shared.automaticallyChecksForUpdates },
                        set: { AppUpdater.shared.automaticallyChecksForUpdates = $0 }))
                }
            }
        }
        .task { await app.updates.refresh(force: false) }
    }

    /// The same actions as the Help menu, for when the Dock icon (and with
    /// it the menu bar) is hidden.
    private var feedbackCard: some View {
        SettingsCard {
            SettingRow(title: "Report a bug",
                       detail: "Opens a GitHub issue with your SWE and macOS versions filled in. You read it before you send it.") {
                HStack(spacing: 12) {
                    Button("Request a feature") { Feedback.openIssueForm(.feature, app: app) }
                        .secondaryButton()
                    Button("Report a bug") { Feedback.openIssueForm(.bug, app: app) }
                        .secondaryButton()
                }
            }
            SettingsDivider()
            SettingRow(title: "Diagnostics",
                       detail: "Versions and the Doctor findings above, for the report's diagnostics field. Your user name, account names and email addresses are masked.") {
                // Waits out a check or fix in flight, which would leave
                // the copy without the report it is about to show.
                CopyButton(help: "Copy diagnostics") { copyToPasteboard(Feedback.diagnostics(app: app)) }
                    .iconButton()
                    .disabled(app.doctor.isBusy)
            }
        }
    }

    private var releaseRow: some View {
        let updates = app.updates
        // Sparkle is gated on the stamped channel, not the saved pick: a
        // dev build pointed at "GitHub releases" still gets the plain
        // release controls, since its updater never starts.
        let prod = updates.build.channel == .prod
        return SettingRow(
            title: "Latest release",
            detail: releaseDetail(updates.release)) {
                HStack(spacing: 12) {
                    if prod {
                        Button("Check for Updates…") { AppUpdater.shared.checkForUpdates() }
                            .secondaryButton()
                            .disabled(!AppUpdater.shared.canCheckForUpdates)
                    } else {
                        if updates.release?.comparison == .available,
                           let url = updates.release?.release?.url {
                            Link("Open", destination: url).secondaryButton()
                        }
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

    /// The stand-in for an app LaunchServices can't find: a dashed app
    /// shape, so a greyed-out option still lines up with the rest.
    private var missingAppIcon: NSImage? {
        NSImage(systemSymbolName: "app.dashed", accessibilityDescription: nil)
    }

    /// Every candidate editor's icon — its bundled mark when it isn't
    /// installed, the placeholder when it has neither.
    private func editorIcons(_ installed: [CodeEditor]) -> [String: NSImage] {
        var icons: [String: NSImage] = [:]
        for (name, _, icon) in CodeEditors.candidates {
            icons[name] = installed.first { $0.name == name }
                .map { NSWorkspace.shared.icon(forFile: $0.appURL.path) }
                ?? ToolIcons.image(for: icon)
                ?? missingAppIcon
        }
        return icons
    }

    private var filesCard: some View {
        SettingsCard {
            PathRow(title: "swe executable", placeholder: "/opt/homebrew/bin/swe",
                    stored: app.swePath, draft: $drafts.swe, running: SweLocation.executable(override: app.swePath),
                    hint: SweLocation.bundled == nil
                        ? "Empty uses the swe your login shell finds."
                        : "Empty uses the swe that comes with SWE.") { app.swePath = $0 }
            SettingsDivider()
            PathRow(title: "API keys file", placeholder: "~/.swe/secrets/providers/.api_keys",
                    stored: app.apiKeysPath, draft: $drafts.apiKeys, running: app.apiKeys.path,
                    hint: "Empty uses ~/.swe/secrets/providers/.api_keys.") { app.apiKeysPath = $0 }
            SettingsDivider()
            PathRow(title: "MCP servers folder", placeholder: "~/.swe/mcp/servers",
                    stored: app.mcpServersPath, draft: $drafts.mcpServers,
                    running: AppModel.mcpServersFolder(path: app.mcpServersPath).path,
                    hint: "Empty uses ~/.swe/mcp/servers.", folders: true) { app.mcpServersPath = $0 }
            SettingsDivider()
            // Every candidate is listed so the row can say what it supports;
            // one that isn't installed greys out and can't be picked. Seven
            // titled tabs don't fit beside a label, so they take the row;
            // too narrow for even that, they fold into rows of three.
            VStack(alignment: .leading, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Editor").font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
                    Text("Opens a server folder from the MCP servers page.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                let installed = editors.installed
                let missing = Set(CodeEditors.candidates.map { $0.name })
                    .subtracting(installed.map { $0.name })
                let options = CodeEditors.candidates.map {
                    ($0.name, $0.name == "Visual Studio Code" ? "VS Code" : $0.name)
                }
                // app.editor runs CodeEditors.installed() on each read, and
                // Tabs reads this per option: pick from the looked-up list.
                let selection = Binding(get: {
                    (installed.first { $0.name == app.codeEditor } ?? installed.first)?.name ?? ""
                }, set: { app.codeEditor = $0 })
                ViewThatFits(in: .horizontal) {
                    Tabs(options: options, selection: selection,
                         iconImages: editors.icons, disabled: missing)
                    Tabs(options: options, selection: selection,
                         iconImages: editors.icons, fillWidth: true,
                         columns: 3, disabled: missing)
                }
            }
            .padding(.vertical, 14)
        }
        .task { await checkEditors() }
        // An editor installed while Files stays open counts on return.
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            Task { await checkEditors() }
        }
    }
}

/// Settings > Integrations > Services: one group per known service — its
/// help line, a row per key (a SecureField until saved, then dates with
/// Replace and Remove), and its Test button.
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
                Link(destination: service.keyURL) {
                    Text("Get a key").font(Theme.caption).foregroundStyle(Theme.link)
                }
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
                    .hoverTip(model.canProbe(service)
                              ? "Calls the service with the saved key."
                              : "Save \(probe.field) first.",
                              replacesLabel: false)
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
                        .sweField(monospaced: true)
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

/// A card under a small heading, for a section that holds more than one.
struct SettingsCardGroup<Content: View>: View {
    var title: String
    var detail: String? = nil
    /// Harness-icons marks before the title: the integration's own logo,
    /// or each service's own when a group holds more than one.
    var icons: [String] = []
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    ForEach(icons, id: \.self) { HarnessIcon(harness: $0, size: 14) }
                    Text(title).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                }
                if let detail { Text(detail).font(Theme.caption).foregroundStyle(Theme.textSecondary) }
            }
            content
        }
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
    /// An SF Symbol leading the row, like the paired-device kind glyphs.
    var icon: String? = nil
    var warning: String? = nil
    var danger = false
    @ViewBuilder var control: Control

    var body: some View {
        HStack(alignment: .center, spacing: 16) {
            if let icon {
                Image(systemName: icon)
                    .font(Theme.font(size: 15))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 20)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
                if let warning {
                    Label {
                        Text(warning).foregroundStyle(Theme.textSecondary)
                            .textSelection(.enabled)
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
        Toggle(title, isOn: $isOn).labelsHidden().toggleStyle(AccentSwitchStyle())
    }
}

/// A path setting: label and the path in use (one line, truncated in the
/// middle), then a 36pt mono field with Choose… and Use.
struct PathRow: View {
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
                    .hoverTip(running, replacesLabel: false)
            }
            HStack(spacing: 8) {
                TextField(placeholder, text: $draft)
                    .sweField(monospaced: true)
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

/// Open at login through `SMAppService.mainApp`. macOS owns the setting, so
/// the switch shows what it reports rather than a saved preference.
private struct LoginItemRow: View {
    @State private var status = SMAppService.mainApp.status
    @Binding var failure: String?

    var body: some View {
        SettingRow(title: "Open at login",
                   detail: "Start SWE in the menu bar when you log in.",
                   warning: failure ?? (status == .requiresApproval
                       ? "macOS needs you to allow SWE in Login Items." : nil),
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
    @AppStorage("settingsSection") private var settingsSection = ""

    private var version: String {
        let info = Bundle.main.infoDictionary
        return info?["SWEVersion"] as? String ?? info?["CFBundleShortVersionString"] as? String ?? "dev"
    }

    var body: some View {
        HStack(spacing: 8) {
            Text("SWE \(version)")
            Text("·")
            FooterLink(title: "Doctor", symbol: "stethoscope") {
                settingsSection = SettingsSection.general.rawValue
                app.selection = .settings
            }
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
