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
        Page(title: "Settings", subtitle: "How Quiver starts, what it shows, and where it finds things.",
             maxWidth: 1024) {
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
        .onAppear { drafts = PathDrafts(swe: app.swePath, apiKeys: app.apiKeysPath) }
    }
}

struct PathDrafts: Equatable {
    var swe = ""
    var apiKeys = ""
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
            .animation(.easeOut(duration: 0.15), value: hovering)
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
                Rectangle().fill(Theme.border).frame(height: 1)
            }
            switch section {
            case .general:
                SettingsCard {
                    LoginItemRow(failure: $loginFailure)
                    SettingsDivider()
                    @Bindable var app = app
                    SettingRow(title: "Terminal", detail: "Used by Terminal, Sign in and Resume.") {
                        Tabs(options: TerminalApp.allCases.map { ($0, $0.title) }, selection: $app.terminal)
                    }
                    SettingsDivider()
                    SettingRow(title: "App icon", detail: "The Dock shows this one. Finder keeps the light icon.") {
                        Tabs(options: AppIconStyle.allCases.map { ($0, $0.title) }, selection: $app.appIcon)
                    }
                }
            case .notifications:
                SettingsCard {
                    @Bindable var limits = app.limits
                    SettingRow(title: "Notify about limits",
                               detail: "When a limit reaches 90%, and again when it resets after that. Once per window.",
                               warning: app.limits.notificationsBlocked && app.limits.notifyEnabled
                                   ? "Notifications for Quiver are off in System Settings > Notifications." : nil) {
                        SettingSwitch(title: "Notify about limits", isOn: $limits.notifyEnabled)
                    }
                }
            case .providers:
                ProviderVisibilityCard(model: app.providers)
            case .paths:
                SettingsCard {
                    PathRow(title: "swe executable", placeholder: "/opt/homebrew/bin/swe",
                            stored: app.swePath, draft: $drafts.swe, running: SweLocation.executable(override: app.swePath),
                            hint: "Empty uses the swe your login shell finds.") { app.swePath = $0 }
                    SettingsDivider()
                    PathRow(title: "API keys file", placeholder: "~/.api_keys",
                            stored: app.apiKeysPath, draft: $drafts.apiKeys, running: app.apiKeys.path,
                            hint: "Empty uses ~/.api_keys.") { app.apiKeysPath = $0 }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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
    var body: some View { Rectangle().fill(Theme.border).frame(height: 1) }
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
        panel.canChooseDirectories = false
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
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "dev"
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
                .animation(.easeOut(duration: 0.15), value: hovering)
        }
        .plainButton(cornerRadius: 4)
        .onHover { hovering = $0 }
    }
}
