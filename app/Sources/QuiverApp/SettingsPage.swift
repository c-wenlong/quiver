import QuiverCore
import SwiftUI

struct SettingsPage: View {
    @Environment(AppModel.self) private var app
    @State private var draft = ""

    var body: some View {
        Page(title: "Settings", subtitle: "Where the app finds swe, and how it looks.") {
            Card(title: "swe") {
                Text("Leave empty to use the swe your login shell finds. Point it at a specific install if that one is older than the app.")
                    .foregroundStyle(.secondary)
                HStack {
                    TextField("/opt/homebrew/bin/swe", text: $draft)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(.body, design: .monospaced))
                        .onSubmit(apply)
                    Button("Choose…", action: choose)
                    Button("Use", action: apply).keyboardShortcut(.defaultAction)
                }
                Text("Running: \(SweLocation.executable(override: app.swePath))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
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
