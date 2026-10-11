import QuiverCore
import SwiftUI

struct SettingsPage: View {
    @Environment(AppModel.self) private var app
    @State private var draft = ""
    @State private var keysDraft = ""

    var body: some View {
        Page(title: "Settings", subtitle: "Where the app finds swe, and which terminal it opens.", maxWidth: 960) {
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
            Card(title: "API keys file") {
                Text("The shell file the API keys page reads and edits. Leave empty for ~/.api_keys.")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                HStack {
                    TextField("~/.api_keys", text: $keysDraft)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(.body, design: .monospaced))
                        .onSubmit { app.apiKeysPath = keysDraft.trimmingCharacters(in: .whitespaces) }
                    Button("Use") { app.apiKeysPath = keysDraft.trimmingCharacters(in: .whitespaces) }
                        .buttonStyle(SecondaryButtonStyle())
                }
            }
            Card(title: "Terminal") {
                @Bindable var app = app
                Tabs(options: TerminalApp.allCases.map { ($0, $0.title) }, selection: $app.terminal)
                Text("Used by Terminal, Sign in and Resume.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
        .onAppear {
            draft = app.swePath
            keysDraft = app.apiKeysPath
        }
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
