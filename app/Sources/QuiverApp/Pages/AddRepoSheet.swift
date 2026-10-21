import QuiverCore
import SwiftUI

/// Pin a repo on the dashboard. Type `owner/repo`, paste a GitHub URL,
/// or search GitHub and pin one of the hits. The pin itself is verified
/// by `swe gh repo add` before it lands.
struct AddRepoSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss

    @State private var query = ""
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
                    .onSubmit { Task { await model.search(query, swe: app.swe) } }
            }
            if let error = model.pinError ?? model.searchError {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            if model.searching {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Searching GitHub…").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            } else if !model.searchResults.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(model.searchResults.prefix(8).enumerated()), id: \.element.id) { index, hit in
                        HitRow(hit: hit, pinned: model.pins.contains { $0.name == hit.name },
                               working: model.mutating.contains(hit.name)) {
                            pin(hit.name)
                        }
                        if index < min(model.searchResults.count, 8) - 1 {
                            Divider().overlay(Theme.rowDivider)
                        }
                    }
                }
                .padding(.vertical, 4)
                .padding(.horizontal, 6)
                .background(Theme.field, in: .rect(cornerRadius: Theme.controlRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
                    .strokeBorder(Theme.border, lineWidth: 1))
            }
            HStack {
                Button("Search") { Task { await model.search(query, swe: app.swe) } }
                    .secondaryButton()
                    .disabled(model.searching || query.trimmingCharacters(in: .whitespaces).isEmpty)
                Spacer()
                Button("Cancel") { dismiss() }.secondaryButton()
                if let direct = directInput {
                    Button(working ? "Pinning…" : "Pin \(direct)") {
                        pin(direct)
                    }
                    .primaryButton()
                    .disabled(working)
                    .help("Verify the repo exists, then pin it")
                }
            }
        }
        .sheetScaffold(width: 520)
        .onAppear { Task { await model.search("", swe: app.swe) } }
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
