import SWECore
import SwiftUI

/// Settings › Files › Hub sync: `swe sync status` for `~/.swe`, and a Sync
/// button that runs `swe sync`. The web Settings page draws the same card
/// for the hub machine, with the same lines (`HubSyncStatus.lines`).
struct HubSyncSection: View {
    @Environment(AppModel.self) private var app
    @State private var status: HubSyncStatus?
    @State private var loadError: String?
    @State private var loading = false
    @State private var syncing = false
    @State private var result: HubSyncResult?
    @State private var syncError: String?

    var body: some View {
        Group {
            if let status {
                card(status)
            } else if let loadError {
                SettingsCard {
                    SettingRow(title: "Hub repo", detail: "", warning: loadError, danger: true) {
                        refreshButton
                    }
                }
            } else {
                SkeletonRows(rows: 3, shape: .check, label: "Reading ~/.swe…")
            }
        }
        // Every visit reads the repo again: a commit made in a terminal counts.
        .task(id: app.swePath) { await load(fetch: false) }
    }

    private func card(_ status: HubSyncStatus) -> some View {
        let lines = status.lines
        let remote = status.remote.map { " Remote: \($0)." } ?? ""
        // A later read that failed shows over the card it couldn't refresh.
        let repoWarning = loadError.map { "Couldn't check again: \($0)" }
            ?? (status.ready ? nil : lines.warning)
        return SettingsCard {
            SettingRow(title: "Hub repo", detail: lines.repo + remote,
                       warning: repoWarning, danger: true) {
                refreshButton
            }
            if status.ready {
                SettingsDivider()
                SettingRow(title: "Changes", detail: lines.changes, warning: lines.warning) {
                    EmptyView()
                }
                SettingsDivider()
                let outcome = result?.outcome
                SettingRow(title: "Sync",
                           detail: outcome?.text
                               ?? "Commits these changes, merges the remote in, pushes, then re-links every harness. Never secrets/, cache/ or the stores swe keeps out of git.",
                           warning: syncError ?? (outcome?.failed == true ? outcome?.text : nil),
                           danger: true) {
                    Button(syncing ? "Syncing…" : "Sync") { Task { await sync() } }
                        .primaryButton()
                        .disabled(syncing)
                        .hoverTip("Commit, merge the remote in and push")
                }
            }
        }
    }

    private var refreshButton: some View {
        Button("Fetch and check again", systemImage: "arrow.clockwise") {
            Task { await load(fetch: true) }
        }
        .labelStyle(.iconOnly)
        .iconButton()
        .disabled(loading || syncing)
        .hoverTip("Fetch the remote and check again")
    }

    private func load(fetch: Bool) async {
        loading = true
        defer { loading = false }
        do {
            status = try await app.swe.hubSyncStatus(fetch: fetch)
            loadError = nil
        } catch is CancellationError {
        } catch {
            loadError = error.localizedDescription
        }
    }

    private func sync() async {
        guard !syncing else { return }
        syncing = true
        syncError = nil
        result = nil
        do {
            result = try await app.swe.hubSync()
        } catch is CancellationError {
        } catch {
            syncError = error.localizedDescription
        }
        syncing = false
        await load(fetch: false)
    }
}
