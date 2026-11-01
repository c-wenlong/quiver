import AppKit
import SWECore
import SwiftUI

/// Registers a plugin marketplace source through
/// `swe plugins marketplace add`. The harness toggles offer the harnesses
/// the catalog knows act on plugins; on a fresh Mac that is both.
struct AddMarketplaceSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss

    @State private var source = ""
    @State private var harnesses: [String] = ["claude", "codex"]
    @State private var chosen: Set<String> = ["claude", "codex"]
    @State private var working = false
    /// The running add, so Cancel (or any other close) stops the clone
    /// rather than letting it finish and add the marketplace anyway.
    @State private var adding: Task<Void, Never>?
    /// The installed-harness lookup is still running; the toggles wait for
    /// it so its answer can't undo a choice made meanwhile.
    @State private var lookingUp = true
    @State private var error: String?
    /// One line per harness after a run, in swe's words.
    @State private var results: [MarketplaceAddReply.Result]?

    private static let actionable: Set<String> = ["claude", "codex"]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add marketplace").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            Text("A GitHub owner/repo, a Git URL, or a folder with marketplace.json.")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            FormField("Source") {
                HStack(spacing: 8) {
                    TextField("owner/repo, a Git URL, or a folder", text: $source)
                        .sweField(monospaced: true)
                    Button("Choose folder…") {
                        let panel = NSOpenPanel()
                        panel.canChooseFiles = false
                        panel.canChooseDirectories = true
                        if panel.runModal() == .OK, let url = panel.url {
                            source = url.path
                        }
                    }
                    .secondaryButton()
                }
            }
            FormField("Harnesses") {
                HStack(spacing: 14) {
                    ForEach(harnesses, id: \.self) { harness in
                        Toggle(HarnessMark(harness).name,
                               isOn: Binding(get: { chosen.contains(harness) },
                                             set: { on in
                                                 if on { chosen.insert(harness) }
                                                 else { chosen.remove(harness) }
                                             }))
                            .toggleStyle(.checkbox)
                            .disabled(lookingUp)
                    }
                }
            }
            if working {
                Text("Cloning can take a minute…")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            if let results {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(results, id: \.harness) { result in
                        Label("\(HarnessMark(result.harness).name): \(result.detail ?? "added")",
                              systemImage: result.ok ? "checkmark.circle" : "xmark.circle")
                            .font(Theme.caption)
                            .foregroundStyle(result.ok ? Theme.textSecondary : Theme.dangerText)
                    }
                }
            }
            if let error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.secondaryButton()
                if results?.contains(where: \.ok) == true {
                    Button("Done") { dismiss() }.primaryButton()
                } else {
                    Button("Add") {
                        working = true
                        error = nil
                        adding = Task {
                            do {
                                let reply = try await app.swe.addMarketplace(
                                    source: source, harnesses: harnesses.filter { chosen.contains($0) })
                                // Added: a close from here on leaves the
                                // reloads below to finish.
                                adding = nil
                                results = reply.results
                                if reply.results.contains(where: \.ok) {
                                    // Health's "not added" findings change too.
                                    async let health: Void = app.plugins.load(swe: app.swe, for: app.swePath)
                                    await app.pluginCatalog.load(swe: app.swe, for: app.swePath)
                                    await health
                                }
                            } catch {
                                self.error = error.localizedDescription
                            }
                            working = false
                        }
                    }
                    .primaryButton()
                    .disabled(working || lookingUp || source.isEmpty || chosen.isEmpty)
                }
            }
        }
        .sheetScaffold(width: 480)
        // Cancelling ends the swe run, clone and all.
        .onDisappear { adding?.cancel() }
        .task {
            defer { lookingUp = false }
            // Only harnesses that can register a marketplace get a toggle.
            if let listed = try? await app.swe.harnesses(withUsage: false) {
                let installed = listed.filter {
                    Self.actionable.contains($0.name) && $0.installed != false
                }.map(\.name)
                if !installed.isEmpty {
                    harnesses = installed
                    chosen = Set(installed)
                }
            }
        }
    }
}
