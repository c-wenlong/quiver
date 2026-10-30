import QuiverCore
import SwiftUI

/// Settings › Experimental: the features that may still change or go away,
/// and, on dev builds only, the controls for where the build's updates
/// come from.
struct ExperimentalSection: View {
    @Environment(AppModel.self) private var app
    /// The Source checkout draft, kept with the other path drafts in
    /// SettingsPage so switching sections doesn't drop it.
    @Binding var sourceCheckout: String

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            SettingsCardGroup(title: "Session index",
                              detail: "How Quiver finds what a session said, not only its titles and paths.") {
                SettingsCard {
                    SessionSearchRow(model: app.search)
                }
            }
            // Prod builds ship to users: no channel picker, no checkout,
            // no Rebuild, whatever a stored dev override says. The gate is
            // the stamped channel, so a release never shows these.
            if app.updates.showsDevControls {
                SettingsCardGroup(title: "Dev build",
                                  detail: "Where this build's updates come from, and the source checkout Rebuild compiles.") {
                    devUpdatesCard
                }
            }
        }
    }

    private var devUpdatesCard: some View {
        SettingsCard {
            SettingRow(title: "Updates from",
                       detail: "This build defaults to \(app.updates.build.channel.title).") {
                Tabs(options: UpdateChannel.allCases.map { ($0, $0.title) },
                     selection: Binding(get: { app.updates.channel },
                                        set: { app.updates.setChannel($0) }))
            }
            SettingsDivider()
            PathRow(title: "Source checkout",
                    placeholder: "~/code/quiver",
                    stored: app.updates.checkoutOverride,
                    draft: $sourceCheckout,
                    running: app.updates.checkout ?? "",
                    hint: app.updates.stampedCheckout.map {
                        "Empty uses the folder this build came from: \($0)"
                    } ?? "Empty: no source folder recorded in this build.",
                    folders: true) { app.updates.setCheckout($0) }
            SettingsDivider()
            localCodeRow
        }
        .task { await app.updates.refresh(force: false) }
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
            }(),
            danger: { if case .failed = updates.rebuildState { return true }
                      return false }()) {
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
}

/// "Session search": one row carrying the switch and description, then the
/// index's own line — "Last synced …" with its counts, a resync button and
/// Clear — while the feature runs. Laid out by hand like `SettingRow`,
/// because the status line holds controls a `detail` string can't.
private struct SessionSearchRow: View {
    @Bindable var model: SearchIndexModel

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 16) {
                Text("Session search")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
                Spacer(minLength: 8)
                Badge("Experimental")
                // An unsupported build can't turn it on; a persisted "on"
                // still turns off, so the setting can never stick.
                SettingSwitch(title: "Session search", isOn: $model.enabled)
                    .disabled(!model.supported && !model.enabled)
            }
            if model.supported {
                Text("Index the text of every session transcript, so search can match what was said, not only titles and paths.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            } else {
                Label {
                    Text("Not supported on this build: its SQLite has no FTS5.")
                        .foregroundStyle(Theme.textSecondary)
                } icon: {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(Theme.warningText)
                }
                .font(Theme.caption)
            }
            if model.enabled {
                HStack(spacing: 8) {
                    Text(model.statusText)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(2)
                    Button {
                        Task { await model.resync() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(Theme.font(size: 11, weight: .medium))
                            .foregroundStyle(Theme.textSecondary)
                    }
                    .plainButton()
                    .disabled(model.isScanning)
                    .help(model.isScanning ? "Indexing now" : "Read every transcript again now")
                    .accessibilityLabel("Read every transcript again")
                    Spacer(minLength: 4)
                    Button("Clear search data") { Task { await model.clearData() } }
                        .secondaryButton()
                }
                .padding(.top, 6)
            }
        }
        .padding(.vertical, 14)
        .onAppear { model.statusVisible = true }
        .onDisappear { model.statusVisible = false }
    }
}
