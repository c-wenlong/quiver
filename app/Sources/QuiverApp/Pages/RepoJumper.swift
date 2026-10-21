import QuiverCore
import SwiftUI

/// ⌘K jumper: a palette over the synced repo catalog. An empty field
/// lists the pins as jump targets; typing searches the catalog, which
/// is local SQLite — a miss costs no GitHub call (`repo search` only
/// falls back to live when the catalog has no hit at all, the same
/// rule the Add-repo droplist uses). Picking an unpinned hit pins it,
/// then scrolls its new section into view — the same route a
/// notification click takes.
struct RepoJumper: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss

    @State private var query = ""
    @State private var working = false

    private var model: GitHubModel { app.github }

    /// The rows the list draws: pins while the field is empty, search
    /// hits once it isn't.
    private var rows: [String] {
        if query.trimmingCharacters(in: .whitespaces).isEmpty {
            return model.pins.map(\.name)
        }
        return model.searchResults.map(\.name)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Jump to a repository").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            Text("Search the synced catalog — picking a repo scrolls its board into view, pinning it first when it isn't.")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            SearchField(prompt: "Repository name…", text: $query)
            if let error = model.pinError ?? model.searchError {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if model.searching {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            Text("Searching the catalog…")
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        }
                        .padding(.vertical, 8).padding(.horizontal, 12)
                        Divider().overlay(Theme.rowDivider)
                    }
                    ForEach(Array(rows.enumerated()), id: \.element) { index, name in
                        JumperRow(name: name,
                                  hit: model.searchResults.first { $0.name == name },
                                  pinned: model.pins.contains { $0.name == name },
                                  working: model.mutating.contains(name)) {
                            jump(name)
                        }
                        if index < rows.count - 1 {
                            Divider().overlay(Theme.rowDivider)
                        }
                    }
                    if !query.trimmingCharacters(in: .whitespaces).isEmpty,
                       !model.searching, rows.isEmpty {
                        Text("Nothing in the catalog matches — Settings → GitHub → Sync refreshes it.")
                            .font(Theme.caption).foregroundStyle(Theme.textMuted)
                            .padding(.vertical, 10).padding(.horizontal, 6)
                    }
                }
            }
            .frame(maxHeight: 300)
            HStack {
                Spacer()
                Button("Done") { dismiss() }.secondaryButton()
                    .keyboardShortcut(.defaultAction)
            }
        }
        .sheetScaffold(width: 480)
        // The Add-repo debounce, same 350ms pause.
        .task(id: query) {
            if query.trimmingCharacters(in: .whitespaces).isEmpty {
                await model.search("", swe: app.swe)
                return
            }
            try? await Task.sleep(for: .milliseconds(350))
            if !Task.isCancelled {
                await model.search(query, swe: app.swe)
            }
        }
    }

    /// Land on the repo's section: pin it first when the hit came from
    /// the catalog outside the pins, then clear the filter and let the
    /// page's scrollTo machinery bring it up.
    private func jump(_ name: String) {
        let land: (String) -> Void = { repo in
            model.filter = ""
            model.selected = repo
            model.scrollTarget = repo
            dismiss()
        }
        if model.pins.contains(where: { $0.name == name }) {
            land(name)
            return
        }
        working = true
        Task {
            let added = await model.addRepo(name, swe: app.swe)
            working = false
            if let added { land(added) }
        }
    }
}

/// One jumper row: the repo name, its blurb when the hit carries one,
/// and a Pinned mark so an already-watched repo reads as a jump, not
/// an add.
private struct JumperRow: View {
    var name: String
    var hit: RepoHit?
    var pinned: Bool
    var working: Bool
    var pick: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: pick) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                        if let hit {
                            Label("\(hit.stars)", systemImage: "star")
                                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                        }
                    }
                    if let description = hit?.description, !description.isEmpty {
                        Text(description).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .lineLimit(2)
                    }
                }
                Spacer(minLength: 8)
                if pinned {
                    Badge("Pinned")
                } else if working {
                    ProgressView().controlSize(.small).frame(height: 24)
                }
            }
            .padding(.vertical, 8)
            .padding(.horizontal, 6)
            .background(hovering ? Theme.rowHover : .clear, in: .rect(cornerRadius: Theme.controlRadius))
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))
        .onHover { hovering = $0 }
    }
}
