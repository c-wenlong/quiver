import QuiverCore
import ServiceManagement
import SwiftUI

struct SettingsPage: View {
    @Environment(AppModel.self) private var app
    @State private var draft = ""

    var body: some View {
        Page(title: "Settings", subtitle: "Where the app finds swe, which terminal it opens, and when it runs.", maxWidth: 960) {
            Card(title: "swe") {
                Text("Leave empty to use the swe your login shell finds. Point it at a specific install if that one is older than the app.")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                HStack {
                    TextField("/opt/homebrew/bin/swe", text: $draft)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(.body, design: .monospaced))
                        .onSubmit(apply)
                    Button("Choose…", action: choose).buttonStyle(SecondaryButtonStyle())
                    Button("Use", action: apply).keyboardShortcut(.defaultAction).buttonStyle(PrimaryButtonStyle())
                }
                Text("Running: \(SweLocation.executable(override: app.swePath))")
                    .font(Theme.caption)
                    .foregroundStyle(Theme.textSecondary)
            }
            Card(title: "Terminal") {
                @Bindable var app = app
                Tabs(options: TerminalApp.allCases.map { ($0, $0.title) }, selection: $app.terminal)
                Text("Used by Terminal, Sign in and Resume.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            Card(title: "Startup and notifications") {
                LoginItemRow()
                Divider().overlay(Theme.border)
                @Bindable var limits = app.limits
                SettingRow(title: "Notify about limits",
                           detail: "When a limit reaches 90%, and again when it resets after that. Once per window.",
                           isOn: $limits.notifyEnabled)
                if app.limits.notificationsBlocked && app.limits.notifyEnabled {
                    Text("Notifications for Quiver are off in System Settings > Notifications.")
                        .font(Theme.caption).foregroundStyle(Theme.warning)
                }
            }
        }
        .onAppear { draft = app.swePath }
    }

    private func apply() {
        app.swePath = draft.trimmingCharacters(in: .whitespaces)
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true
        if panel.runModal() == .OK, let url = panel.url {
            draft = url.path
            apply()
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

/// A titled switch with a one-line explanation, after TokenTracker's
/// settings rows.
struct SettingRow: View {
    var title: String
    var detail: String
    @Binding var isOn: Bool

    var body: some View {
        Toggle(isOn: $isOn) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                Text(detail).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .toggleStyle(.switch)
        .tint(Theme.accentFill)
    }
}

/// Open at login through `SMAppService.mainApp`. macOS owns the setting, so
/// the switch shows what it reports rather than a saved preference.
private struct LoginItemRow: View {
    @State private var status = SMAppService.mainApp.status
    @State private var failure: String?

    var body: some View {
        SettingRow(title: "Open at login",
                   detail: "Start Quiver in the menu bar when you log in.",
                   isOn: Binding(get: { status == .enabled || status == .requiresApproval },
                                 set: { on in update(on) }))
        if status == .requiresApproval {
            HStack {
                Text("macOS needs you to allow Quiver in Login Items.")
                    .font(Theme.caption).foregroundStyle(Theme.warning)
                Spacer()
                Button("Open Login Items") { SMAppService.openSystemSettingsLoginItems() }
                    .buttonStyle(SecondaryButtonStyle())
            }
        }
        if let failure {
            Text(failure).font(Theme.caption).foregroundStyle(Theme.danger)
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
