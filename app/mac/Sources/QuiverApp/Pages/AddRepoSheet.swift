import QuiverCore
import SwiftUI

/// Pin a repo on the dashboard. Type `owner/repo`, paste a GitHub URL,
/// or search your own repos and orgs and pin one of the hits. The pin
/// itself is verified by `swe gh repo add` before it lands.
struct AddRepoSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss

    @State private var query = ""
    /// The text Return searched, so the debounce doesn't search it again.
    @State private var submitted: String?
    @State private var working = false

    private var model: GitHubModel { app.github }

    /// Whether the text looks like something `repo add` can take
    /// directly: `owner/repo` or a github.com URL — then a "Pin" button
    /// skips the search step.
    private var directInput: String? {
        let text = query.trimmingCharacters(in: .whitespaces)
        if text.contains("github.com") { return text }
        let parts = text.split(separator: "/")
        if parts.count == 2, parts.allSatisfy({ !$0.isEmpty }) { return text }
        return nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Pin a repository").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            Text("The pinned repos drive the Repositories page and act as the fallback when a command runs outside a checkout.")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            FormField("Repository") {
                TextField("owner/repo, GitHub URL, or search terms", text: $query)
                    .quiverField(monospaced: true)
                    .onSubmit {
                        submitted = query
                        Task { await model.search(query, swe: app.swe) }
                    }
            }
            if let error = model.pinError ?? model.searchError {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            // The droplist: five rows in view, the rest a scroll away.
            // Results stay on screen while a re-search runs, so typing
            // never flashes the list away and back.
            if model.searching || !model.searchResults.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    if model.searching {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            Text("Searching your repos…")
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        }
                        .padding(.vertical, 8).padding(.horizontal, 12)
                        Divider().overlay(Theme.rowDivider)
                    }
                    ScrollView {
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(Array(model.searchResults.enumerated()), id: \.element.id) { index, hit in
                                HitRow(hit: hit, pinned: model.pins.contains { $0.name == hit.name },
                                       working: model.mutating.contains(hit.name)) {
                                    pin(hit.name)
                                }
                                if index < model.searchResults.count - 1 {
                                    Divider().overlay(Theme.rowDivider)
                                }
                            }
                        }
                    }
                    .frame(maxHeight: 280)
                }
                .padding(.vertical, 4)
                .background(Theme.field, in: .rect(cornerRadius: Theme.controlRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
                    .strokeBorder(Theme.border, lineWidth: 1))
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.secondaryButton()
                if let direct = directInput {
                    Button(working ? "Pinning…" : "Pin \(direct)") {
                        pin(direct)
                    }
                    .primaryButton()
                    .disabled(working)
                    .hoverTip("Verify the repo exists, then pin it", replacesLabel: false)
                }
            }
        }
        .sheetScaffold(width: 520)
        // Typing is the search: each edit re-arms a 350ms debounce, and
        // only a pause actually hits GitHub. An emptied field clears the
        // list right away — `search("")` does that itself.
        .task(id: query) {
            // New text ends the marker: clearing the field emptied the
            // results, so retyping the same words must search again.
            submitted = nil
            if query.trimmingCharacters(in: .whitespaces).isEmpty {
                await model.search("", swe: app.swe)
                return
            }
            try? await Task.sleep(for: .milliseconds(350))
            // Return already searched this text: one gh call, not two.
            if !Task.isCancelled, query != submitted {
                await model.search(query, swe: app.swe)
            }
        }
    }

    private func pin(_ name: String) {
        working = true
        Task {
            let added = await model.addRepo(name, swe: app.swe)
            working = false
            if let added {
                model.selected = added
                dismiss()
            }
        }
    }
}

/// One search hit: name, stars and blurb, with a Pin button on the right.
private struct HitRow: View {
    var hit: RepoHit
    var pinned: Bool
    var working: Bool
    var pin: () -> Void
    @State private var hovering = false

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(hit.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    Label("\(hit.stars)", systemImage: "star")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
                if let description = hit.description, !description.isEmpty {
                    Text(description).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(2)
                }
            }
            Spacer(minLength: 8)
            if pinned {
                Badge("Pinned")
            } else if working {
                ProgressView().controlSize(.small).frame(height: 24)
            } else {
                Button("Pin", action: pin).secondaryButton()
            }
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 6)
        .background(hovering ? Theme.rowHover : .clear, in: .rect(cornerRadius: Theme.controlRadius))
        .onHover { hovering = $0 }
    }
}
