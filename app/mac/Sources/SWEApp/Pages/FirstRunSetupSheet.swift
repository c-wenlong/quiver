import AppKit
import SWECore
import SwiftUI
import UserNotifications

/// The first-run setup window, in five steps: the agents you use, one
/// shared AGENTS.md, skills and MCP servers, the look, notifications, and
/// an optional offer to set up phone access once the window closes.
/// The second step is the only one that writes outside SWE's own
/// settings, through one `swe setup --apply` call.
struct FirstRunSetupSheet: View {
    @Environment(AppModel.self) private var app
    @State private var model: FirstRunSetupModel?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let model {
                Content(model: model)
            } else {
                Spacer()
            }
        }
        .frame(width: 820, height: 680)
        .background(Theme.panel)
        .task {
            let starred = app.others.starred ?? []
            let setup = FirstRunSetupModel(swe: { [app] in app.swe }, starred: starred)
            model = setup
            await app.providers.loadIfNeeded(for: app.swePath)
            await app.limits.checkNotificationPermission()
            await setup.load()
        }
    }
}

private struct Content: View {
    @Environment(AppModel.self) private var app
    @Bindable var model: FirstRunSetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
                .padding(.horizontal, 40)
                .padding(.top, 32)
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    titles
                    switch model.step {
                    case .agents: AgentsStep(model: model)
                    case .links: LinksStep(model: model)
                    case .look: LookStep()
                    case .notifications: NotificationsStep()
                    case .phone: PhoneStep()
                    }
                }
                .padding(.horizontal, 40)
                .padding(.vertical, 24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider().overlay(Theme.border)
            footer
                .padding(.horizontal, 40)
                .padding(.vertical, 18)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 24) {
            HStack(spacing: 10) {
                Image(nsImage: NSApp.applicationIconImage)
                    .resizable().frame(width: 28, height: 28)
                    .accessibilityHidden(true)
                Text("SWE").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            }
            HStack(spacing: 6) {
                ForEach(FirstRunSetupModel.Step.allCases, id: \.self) { step in
                    Capsule()
                        .fill(step == model.step ? Theme.textPrimary : Theme.borderStrong)
                        .frame(width: step == model.step ? 36 : 22, height: 3)
                }
                Text("\(model.step.number) of \(FirstRunSetupModel.Step.allCases.count)")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .padding(.leading, 8)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Step \(model.step.number) of \(FirstRunSetupModel.Step.allCases.count)")
        }
    }

    private var titles: some View {
        VStack(alignment: .leading, spacing: 8) {
            if model.step == .agents {
                Text("Welcome to SWE").font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            Text(title).font(Theme.pageTitle).tracking(-0.8).foregroundStyle(Theme.textPrimary)
            Text(subtitle).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var title: String {
        switch model.step {
        case .agents: "Pick the agents you use"
        case .links: "One setup for all of them"
        case .look: "Pick a look"
        case .notifications: "Set up notifications"
        case .phone: "Phone access"
        }
    }

    private var subtitle: String {
        switch model.step {
        case .agents:
            "SWE shows usage, limits and sessions for every coding agent. The ones you pick go in the menu bar and on Accounts. Change them any time on Harnesses."
        case .links:
            "SWE keeps one AGENTS.md, one skills folder and one list of MCP servers, and links them into each agent."
        case .look:
            "Change it later in Settings › General."
        case .notifications:
            "SWE tells you when a limit nears its end and when it resets."
        case .phone:
            "Watch and steer sessions from your phone, over Tailscale. Optional: skip it now and set it up any time in Settings › Integrations › Remote."
        }
    }

    private var footer: some View {
        HStack(spacing: 10) {
            Button("Skip setup") { app.closeFirstRunSetup(reload: model.outcome != nil) }
                .buttonStyle(.plain)
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                .hoverTip("Close this and set things up later from Settings › General", replacesLabel: false)
                .disabled(model.applying || model.cloning)
            Spacer()
            if model.step != .agents {
                Button("Back", systemImage: "chevron.left") { model.back() }
                    .secondaryButton()
                    .disabled(model.applying || model.cloning)
            }
            if model.step == .phone {
                Button("Later") { app.closeFirstRunSetup(reload: model.outcome != nil) }
                    .secondaryButton()
                Button("Set up now") {
                    app.closeFirstRunSetup(reload: model.outcome != nil, remoteSetup: true)
                }
                .primaryButton()
                .keyboardShortcut(.return, modifiers: .command)
            } else {
                Button(continueTitle) {
                    Task { await model.next(order: app.providers.rows.map(\.provider.id)) }
                }
                .primaryButton()
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(model.applying || model.cloning || (model.step == .links && !previewReady))
            }
        }
    }

    private var previewReady: Bool {
        if case .loaded = model.preview { return true }
        return false
    }

    private var continueTitle: String {
        switch model.step {
        case .links where model.applying: "Setting up…"
        case .links where model.needsApply: "Set up"
        default: "Continue"
        }
    }
}

// MARK: - Step 1: agents

private struct AgentsStep: View {
    @Environment(AppModel.self) private var app
    @Bindable var model: FirstRunSetupModel
    @State private var showingMore = false
    @State private var installing: Provider?

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 12), count: 3)

    private var installed: [Provider] { app.providers.rows.filter(\.isInstalled).map(\.provider) }
    private var others: [Provider] { app.providers.rows.filter { !$0.isInstalled }.map(\.provider) }
    private var pickedMissing: [Provider] { others.filter { model.selected.contains($0.id) } }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if !app.providers.hasChecked {
                SkeletonRows(rows: 4, framed: false, shape: .tiles,
                             label: "Looking for coding agents on this Mac…")
            } else {
                if let error = app.providers.checkError { ErrorCard(message: error) }
                ForEach(pickedMissing) { provider in missingBanner(provider) }
                HStack(spacing: 6) {
                    Text("Found on this Mac").font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    Text("\(installed.count)").font(Theme.bodySmall).foregroundStyle(Theme.textMuted)
                }
                if installed.isEmpty {
                    Text("No coding agents found yet. Pick the ones you plan to use below.")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                } else {
                    grid(installed)
                }
                if !others.isEmpty {
                    Button(showingMore ? "Hide agents" : "Show \(others.count) more agents") {
                        showingMore.toggle()
                    }
                    .buttonStyle(.plain)
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .underline()
                    if showingMore { grid(others) }
                }
            }
        }
        .quiverSheet(item: $installing) { provider in
            ProviderSetupSheet(provider: provider, isInstalled: false).environment(app)
        }
    }

    private func grid(_ providers: [Provider]) -> some View {
        LazyVGrid(columns: columns, spacing: 12) {
            ForEach(providers) { provider in
                AgentTile(provider: provider, isOn: model.selected.contains(provider.id)) {
                    model.toggle(provider.id)
                }
            }
        }
    }

    private func missingBanner(_ provider: Provider) -> some View {
        HStack(spacing: 12) {
            Text("\(Text(provider.name).foregroundStyle(Theme.textPrimary)) isn't installed yet. SWE will list it anyway, and you can install it any time.")
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 12)
            Button("Install…") { installing = provider }
                .secondaryButton()
                .help("Show the install commands for \(provider.name)")
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, 12)
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.warning.opacity(0.6), lineWidth: 1))
    }
}

/// One agent: its logo, its name, a tick when picked.
private struct AgentTile: View {
    var provider: Provider
    var isOn: Bool
    var toggle: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: toggle) {
            HStack(spacing: 10) {
                HarnessIcon(harness: provider.icon, size: 18)
                    .frame(width: 30, height: 30)
                    .background(Theme.panel, in: .rect(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.border, lineWidth: 1))
                Text(provider.name)
                    .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1)
                Spacer(minLength: 4)
                if isOn {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(Theme.textPrimary)
                        .accessibilityHidden(true)
                }
            }
            .padding(.horizontal, 12)
            .frame(height: 52)
            .background(isOn ? Theme.cardHover : Theme.card, in: .rect(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12)
                .strokeBorder(isOn ? Theme.textSecondary : (hovering ? Theme.borderHover : Theme.border),
                              lineWidth: 1))
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .accessibilityAddTraits(isOn ? .isSelected : [])
    }
}

// MARK: - Step 2: AGENTS.md, skills and MCP servers

private struct LinksStep: View {
    @Bindable var model: FirstRunSetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            switch model.preview {
            case .idle, .loading:
                SkeletonRows(rows: 4, shape: .feature,
                             label: "Checking what each agent already has…")
            case .failed(let message):
                ErrorCard(message: message)
                Button("Try again") { Task { await model.load() } }
                    .secondaryButton()
            case .loaded(let preview):
                if let error = model.applyError { ErrorCard(message: error) }
                if let outcome = model.outcome { done(outcome) }
                if model.offersHub || model.cloning {
                    hubCard(demo: preview.hub?.demoUrl)
                } else if let cloned = model.cloned {
                    clonedCard(cloned)
                }
                VStack(spacing: 0) {
                    instructionsRow(preview.instructions)
                    Divider().overlay(Theme.border)
                    row(icon: "sparkles", title: "Skills",
                        detail: "~/.swe/skills becomes every agent's skills folder, so a skill you add shows up in all of them.")
                    Divider().overlay(Theme.border)
                    row(icon: "powerplug", title: "MCP servers", detail: mcpDetail(preview.mcp))
                }
                .padding(.horizontal, Theme.cardPadding)
                .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
                Text("Anything replaced is backed up to ~/.swe/backups first. A real file that would be lost stays where it is.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
    }

    /// `swe setup --hub`: shown while `~/.swe` has no hub yet.
    private func hubCard(demo: String?) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Start from your hub repo").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            Text("Optional. Clone your own hub repo into ~/.swe, so its AGENTS.md, skills, plugins and MCP servers come with it. Leave it empty to start an empty hub.")
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Text(FirstRunSetupModel.hubForkNote)
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            if let demo, let url = URL(string: demo), ["http", "https"].contains(url.scheme ?? "") {
                Link("Demo hub: \(demo)", destination: url).font(Theme.caption)
            }
            HStack(spacing: 8) {
                TextField("https://github.com/you/quiver-hub.git", text: $model.hubURL)
                    .sweField(monospaced: true)
                    .onSubmit { Task { await model.cloneHub() } }
                    .disabled(model.cloning || model.applying)
                Button("Clone") { Task { await model.cloneHub() } }
                    .secondaryButton()
                    .disabled(model.cloning || model.applying
                              || model.hubURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            if model.cloning {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("Cloning… This can take a few minutes.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            if let error = model.hubError {
                ErrorCard(message: error, framed: false)
            }
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
    }

    private func clonedCard(_ cloned: SetupPreview.Hub.Cloned) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Cloned \(cloned.url) into ~/.swe.", systemImage: "checkmark.circle")
                .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                .textSelection(.enabled)
            Text("Its AGENTS.md, skills, plugins and MCP servers are on this Mac now. Set up links them into each agent.")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(cloned.marketplaces.filter { $0.error != nil }, id: \.self) { row in
                Text("\(row.marketplace) isn't registered in \(HarnessMark.accountName(row.harness)): \(row.error ?? "")")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
    }

    private func mcpDetail(_ servers: [SetupPreview.Server]) -> String {
        guard !servers.isEmpty else {
            return "None found in your agents yet. Add servers later on the MCP servers page."
        }
        let names = servers.map(\.name).joined(separator: ", ")
        return "Copies \(servers.count) server\(servers.count == 1 ? "" : "s") your agents already use into ~/.swe/mcp.json: \(names)."
    }

    @ViewBuilder
    private func instructionsRow(_ instructions: SetupPreview.Instructions?) -> some View {
        row(icon: "doc.text", title: "Shared instructions",
            detail: "~/.swe/AGENTS.md, linked into each agent as its own file (CLAUDE.md, AGENTS.md, GEMINI.md…).") {
            if let instructions {
                if instructions.agentsFileCustomised {
                    Text("Your AGENTS.md is already there and stays as it is.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                } else if !instructions.adoptCandidates.isEmpty {
                    HStack(spacing: 8) {
                        Text("Start from").font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        SelectMenu(options: instructions.adoptCandidates.map {
                                       (FirstRunSetupModel.Start.file($0.path),
                                        "Your \(displayPath($0.path))")
                                   } + [(.seed, "SWE's starter file")],
                                   selection: $model.start, width: nil)
                            .disabled(model.outcome != nil)
                    }
                }
                ForEach(model.untouchedCandidates, id: \.self) { candidate in
                    Text("\(displayPath(candidate.path)) has other text, so it stays as it is. Link it later on the AGENTS.md page.")
                        .font(Theme.caption).foregroundStyle(Theme.warningText)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private func done(_ outcome: SetupOutcome) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Set up. A few things to know:", systemImage: "checkmark.circle")
                .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            if !outcome.unstarred.isEmpty {
                Text("Not starred, since swe has no entry for them yet: \(outcome.unstarred.map { HarnessMark.accountName($0) }.joined(separator: ", ")). Settings › Providers still shows their usage.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !outcome.leftAlone.isEmpty {
                Text("These stay as they are:").font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            ForEach(outcome.leftAlone, id: \.path) { row in
                VStack(alignment: .leading, spacing: 2) {
                    Text(displayPath(row.path)).font(Theme.font(size: 12, design: .monospaced))
                        .foregroundStyle(Theme.textPrimary).textSelection(.enabled)
                    if !row.detail.isEmpty {
                        Text(row.detail).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                }
            }
            Text("Continue when you've read this. The Harnesses and AGENTS.md pages show them again.")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
        .opacity(outcome.needsReading ? 1 : 0)
    }

    private func row(icon: String, title: String, detail: String,
                     @ViewBuilder extra: () -> some View = { EmptyView() }) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon).font(Theme.font(size: 15)).foregroundStyle(Theme.textSecondary)
                .frame(width: 20).padding(.top, 2)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                Text(detail).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                extra()
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 14)
    }
}

// MARK: - Step 3: look

private struct LookStep: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        HStack(spacing: 14) {
            ForEach(AppearanceMode.allCases, id: \.self) { mode in
                card(mode)
            }
        }
    }

    private func card(_ mode: AppearanceMode) -> some View {
        let isOn = app.appearance == mode
        return Button { app.appearance = mode } label: {
            VStack(alignment: .leading, spacing: 12) {
                WindowSketch(mode: mode)
                    .frame(height: 110)
                    .overlay(alignment: .topTrailing) {
                        if isOn {
                            Image(systemName: "checkmark.circle.fill")
                                .font(Theme.font(size: 16))
                                .foregroundStyle(Theme.textPrimary, Theme.card)
                                .padding(8)
                        }
                    }
                HStack {
                    Label(mode.title, systemImage: mode.symbol)
                        .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                    Spacer()
                    Text(mode.blurb).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            .padding(12)
            .background(isOn ? Theme.cardHover : Theme.card, in: .rect(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14)
                .strokeBorder(isOn ? Theme.textSecondary : Theme.border, lineWidth: 1))
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isOn ? .isSelected : [])
    }
}

private extension AppearanceMode {
    var symbol: String {
        switch self {
        case .system: "desktopcomputer"
        case .light: "sun.max"
        case .dark: "moon"
        }
    }

    var blurb: String {
        switch self {
        case .system: "Matches macOS"
        case .light: "Bright and clear"
        case .dark: "Easy on the eyes"
        }
    }
}

/// A tiny SWE window in light, dark, or both halves for System. Fixed
/// colours on purpose: it shows the mode, whatever mode is on now.
private struct WindowSketch: View {
    var mode: AppearanceMode

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                window(dark: mode != .light)
                if mode == .system {
                    window(dark: false)
                        .mask(alignment: .trailing) {
                            Rectangle().frame(width: geo.size.width * 0.55)
                        }
                }
            }
        }
        .clipShape(.rect(cornerRadius: 8))
        .accessibilityHidden(true)
    }

    private func window(dark: Bool) -> some View {
        let wash = dark ? Color(white: 0.09) : Color(white: 0.96)
        let panel = dark ? Color(white: 0.13) : Color.white
        let line = dark ? Color(white: 0.26) : Color(white: 0.86)
        return HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(0..<4, id: \.self) { i in
                    Capsule().fill(i == 0 ? Theme.accentFill : line)
                        .frame(width: i == 0 ? 30 : 38, height: 4)
                }
                Spacer()
            }
            .padding(10)
            .frame(width: 64)
            .frame(maxHeight: .infinity, alignment: .top)
            .background(wash)
            VStack(alignment: .leading, spacing: 7) {
                Capsule().fill(line).frame(width: 50, height: 6)
                Capsule().fill(line).frame(height: 4)
                Capsule().fill(line).frame(width: 80, height: 4)
                Spacer()
                RoundedRectangle(cornerRadius: 4).fill(line).frame(height: 16)
            }
            .padding(10)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(panel)
        }
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(line, lineWidth: 1))
    }
}

// MARK: - Step 4: notifications

private struct NotificationsStep: View {
    @Environment(AppModel.self) private var app
    @State private var sentTest = false

    var body: some View {
        @Bindable var limits = app.limits
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: "bell.badge").font(Theme.font(size: 16))
                    .foregroundStyle(Theme.textSecondary).padding(.top, 2)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Allow notifications for SWE").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                    Text(permissionDetail).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 12)
                permissionButton
            }
            .padding(Theme.cardPadding)
            .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))

            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Limits").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                    Text("When a usage limit passes 90%, and when it resets. Once per crossing.")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                }
                Spacer(minLength: 12)
                SettingSwitch(title: "Notify about limits", isOn: $limits.notifyEnabled)
            }
            .padding(Theme.cardPadding)
            .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))

            HStack(spacing: 10) {
                Button(sentTest ? "Sent" : "Send test notification", systemImage: "bell") {
                    Task {
                        await app.limits.sendTestNotification()
                        sentTest = app.limits.notificationStatus == .authorized
                    }
                }
                .secondaryButton()
                Text("More in Settings › Notifications.").font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
        .task { await app.limits.checkNotificationPermission() }
    }

    private var permissionDetail: String {
        if let failure = app.limits.notificationRequestError,
           app.limits.notificationStatus == .notDetermined {
            return "macOS could not ask for permission (\(failure)). Try Allow again, or turn them on in System Settings › Notifications."
        }
        switch app.limits.notificationStatus {
        case .authorized, .provisional, .ephemeral: return "Allowed."
        case .denied: return "Turned off for SWE. In System Settings › Notifications, find SWE in the list and turn on Allow notifications."
        default: return "Click Allow, then Allow again in the macOS dialog."
        }
    }

    @ViewBuilder private var permissionButton: some View {
        switch app.limits.notificationStatus {
        case .authorized, .provisional, .ephemeral:
            Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.success)
                .accessibilityLabel("Allowed")
        case .denied:
            Button("Open System Settings", systemImage: "gear") {
                if let url = URL(string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension") {
                    NSWorkspace.shared.open(url)
                }
            }
            .secondaryButton()
        default:
            Button("Allow") { Task { await app.limits.requestNotifications() } }
                .secondaryButton()
        }
    }
}

// MARK: - Step 5: phone access

/// What setting up phone access involves. Nothing runs here: "Set up now"
/// opens the checklist in Settings › Integrations › Remote once this
/// window closes, and "Later" just finishes.
private struct PhoneStep: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            row(icon: "network", title: "Tailscale",
                detail: "Your phone reaches this Mac over Tailscale, a private network between your own devices. SWE checks it's installed, signed in, and has HTTPS on.")
            Divider().overlay(Theme.border)
            row(icon: "server.rack", title: "The hub",
                detail: "SWE runs a small server in the background that your phone talks to, and puts it behind tailscale serve.")
            Divider().overlay(Theme.border)
            row(icon: "iphone", title: "Your phone",
                detail: "Scan a code with the SWE app, or open a link in the phone's browser.")
        }
        .padding(.horizontal, Theme.cardPadding)
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
    }

    private func row(icon: String, title: String, detail: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon).font(Theme.font(size: 15)).foregroundStyle(Theme.textSecondary)
                .frame(width: 20).padding(.top, 2)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                Text(detail).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 14)
    }
}
