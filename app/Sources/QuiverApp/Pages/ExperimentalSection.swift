import QuiverCore
import SwiftUI

/// Settings › Experimental: the features that may still change or go away.
/// Today that's one card — the session-search index.
struct ExperimentalSection: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        SettingsCard {
            SessionSearchRow(model: app.search)
        }
    }
}

/// "Session search": the switch with its one-line description, and while it
/// runs an "Index status" row carrying the live counts and Clear.
private struct SessionSearchRow: View {
    @Bindable var model: SearchIndexModel

    var body: some View {
        SettingRow(title: "Session search",
                   detail: "Index the text of every session transcript, so search can match what was said, not only titles and paths.",
                   warning: model.supported
                       ? nil
                       : "Not supported on this build: its SQLite has no FTS5.") {
            HStack(spacing: 10) {
                Badge("Experimental")
                // An unsupported build can't turn it on; a persisted "on"
                // still turns off, so the setting can never stick.
                SettingSwitch(title: "Session search", isOn: $model.enabled)
                    .disabled(!model.supported && !model.enabled)
            }
        }
        if model.enabled {
            SettingsDivider()
            SettingRow(title: "Index status", detail: model.statusText) {
                Button("Clear search data") { Task { await model.clearData() } }
                    .secondaryButton()
            }
        }
    }
}
