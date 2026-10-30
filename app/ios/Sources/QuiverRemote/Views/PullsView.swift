import SwiftUI

/// P11: every open pull request across the hub's pinned repos, grouped
/// by repo — the same answer `GET /v1/pulls` gives, so the phone and
/// the web app's Repositories page agree.
struct PullsView: View {
    @Environment(AppModel.self) private var app
    /// Owned by the tab bar, so switching the GitHub tab between Issues
    /// and Pull requests keeps what each already loaded.
    let model: PullsModel
    /// Kept on the model, so it survives the GitHub tab's switch to
    /// Issues and back.
    private var filter: PullFilter {
        get { model.filter }
        nonmutating set { model.filter = newValue }
    }

    var body: some View {
        let sections = PullGrouping.sections(of: model.repos, filter: filter)
        PageScroll {
            if model.loaded, model.envBlocked == nil, !model.repos.isEmpty {
                filterBar
            }
            // A failed refresh keeps the rows it already had — the
            // notice says why they're stale rather than replacing them.
            if let error = model.error, !model.repos.isEmpty {
                Label { Text(inlineCode: "Couldn't refresh: \(error)") } icon: { Image(systemName: "exclamationmark.triangle") }
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .lineLimit(2)
            }
            if !model.loaded {
                LoadingRow(text: "Loading pull requests")
            } else if let error = model.error, model.repos.isEmpty {
                StateCard.unreachable(error) { Task { await model.load(client: app.client, refresh: true) } }
            } else if let blocked = model.envBlocked {
                StateCard(icon: "exclamationmark.lock", title: "Can't reach GitHub",
                          message: "On \(model.machine ?? app.hubName): \(blocked)")
            } else if model.repos.isEmpty {
                StateCard(icon: "arrow.triangle.pull", title: "No pinned repos",
                          message: "Pin repositories on the hub with `swe gh repo add` and their open pull requests show up here.")
            } else if sections.isEmpty {
                StateCard(icon: "arrow.triangle.pull", title: filter == .all ? "No open pull requests" : "Nothing here",
                          message: filter == .all ? "Open a pull request on GitHub and it shows up here."
                                                : "No open pull request matches this filter right now.")
            } else {
                ForEach(sections) { section in
                    if let rows = section.rows {
                        GroupLabel(text: section.name, trailing: "\(rows.count)")
                        CardList(data: rows) { pr in
                            NavigationLink(value: Route.pull(repo: section.name, number: pr.number)) {
                                PullRowView(pr: pr)
                            }
                            .buttonStyle(RowButtonStyle())
                            .contextMenu { PullRowMenu(pr: pr) }
                        }
                    } else {
                        // `gh` failed on this repo: its slot stays,
                        // dimmed, with why.
                        GroupLabel(text: section.name)
                        StateCard(icon: "exclamationmark.triangle", title: "Can't list this repo's pull requests",
                                  message: section.error, tone: .error)
                    }
                }
            }
        }
        .refreshable { await model.load(client: app.client, refresh: true) }
        .navigationTitle("Pull requests")
        .task(id: app.hub?.id) { await model.load(client: app.client) }
    }

    private var filterBar: some View {
        HStack(spacing: 6) {
            ForEach(PullFilter.allCases) { f in
                FilterChip(title: f.title, count: PullGrouping.count(model.repos, filter: f), selected: filter == f) {
                    filter = f
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.top, 4)
    }
}

/// A PR row's context menu: on GitHub or copied, since a row tap opens
/// the detail page rather than launching anything blind.
struct PullRowMenu: View {
    @Environment(\.openURL) private var openURL
    let pr: PullRow

    var body: some View {
        if let url = pr.url.flatMap(URL.init) {
            Button { openURL(url) } label: {
                Label("Open on GitHub", systemImage: "safari")
            }
            Button { UIPasteboard.general.string = pr.url ?? "" } label: {
                Label("Copy link", systemImage: "doc.on.doc")
            }
        }
    }
}

/// The state/draft mark at the row's left, as `swe gh` draws it:
/// open green, draft an open circle, closed red, merged purple-ish.
struct PullGlyph: View {
    let state: String?
    let draft: Bool

    var body: some View {
        Image(systemName: symbol)
            .font(Theme.rowIcon)
            .foregroundStyle(color)
            .iconColumn(20)
            .accessibilityLabel(accessibility)
    }

    private var symbol: String {
        switch state {
        case "merged": "arrow.triangle.merge"
        case "closed": "arrow.triangle.pull"
        default: draft ? "circle.dashed" : "arrow.triangle.pull"
        }
    }

    private var color: Color {
        switch state {
        case "merged": Theme.textLabel
        case "closed": Theme.danger
        default: draft ? Theme.textMuted : Theme.accentFill
        }
    }

    private var accessibility: String {
        switch state {
        case "merged": "Merged"
        case "closed": "Closed"
        default: draft ? "Draft" : "Open"
        }
    }
}

/// The checks rollup's one glyph, coloured as `swe gh` colours ✓/✗/◐/?/−.
struct ChecksGlyph: View {
    let state: String?

    var body: some View {
        Image(systemName: symbol)
            .font(Theme.font(size: 13))
            .foregroundStyle(color)
            .accessibilityLabel("Checks \(state ?? "none")")
    }

    private var symbol: String {
        switch state {
        case "passing": "checkmark.circle.fill"
        case "failing": "xmark.circle.fill"
        case "running": "circle.lefthalf.filled"
        case "unknown": "questionmark.circle"
        default: "minus.circle"
        }
    }

    private var color: Color {
        switch state {
        case "passing": Theme.accentFill
        case "failing": Theme.danger
        case "running", "unknown": Theme.warning
        default: Theme.textMuted
        }
    }
}

extension PullRow {
    /// `updated_at` parsed for `RelativeTime`.
    var updatedDate: Date? { GitHubTime.date(updatedAt) }

    /// The review column `swe gh` draws: a merge conflict outranks
    /// every verdict.
    var reviewMark: (String, Color)? {
        if mergeable == "conflicting" { return ("conflict", Theme.danger) }
        switch reviewDecision {
        case "approved": return ("approved", Theme.accentFill)
        case "changes_requested": return ("changes", Theme.danger)
        case "review_required": return ("review", Theme.textMuted)
        default: return nil
        }
    }
}

struct PullRowView: View {
    let pr: PullRow

    var body: some View {
        HStack(spacing: 12) {
            PullGlyph(state: pr.state, draft: pr.draft == true)
            VStack(alignment: .leading, spacing: 3) {
                Text(pr.title ?? "Untitled")
                    .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1).truncationMode(.tail)
                HStack(spacing: 6) {
                    Text("#\(pr.number)")
                    ChecksGlyph(state: pr.checks)
                    if let mark = pr.reviewMark {
                        Text(mark.0).foregroundStyle(mark.1)
                    }
                    if let author = pr.author, !author.isEmpty, author != "-" {
                        Text("· \(author)").lineLimit(1)
                    }
                    let when = RelativeTime.short(pr.updatedDate?.timeIntervalSince1970)
                    if !when.isEmpty { Text("· \(when)") }
                }
                .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                if let badges = badges, !badges.isEmpty {
                    HStack(spacing: 4) {
                        ForEach(Array(badges.enumerated()), id: \.offset) { Badge($0.element) }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Image(systemName: "chevron.right").font(Theme.chevron).foregroundStyle(Theme.textMuted)
        }
        .padding(.vertical, Theme.rowVertical)
        .padding(.horizontal, Theme.rowHorizontal)
        .contentShape(.rect)
    }

    /// "Mine"/"review requested" flags first, then up to three labels —
    /// the rest stay on the detail page.
    private var badges: [String]? {
        var out: [String] = []
        if pr.mine == true { out.append("mine") }
        if pr.reviewRequested == true { out.append("review requested") }
        out.append(contentsOf: (pr.labels ?? []).prefix(3))
        return out.isEmpty ? nil : out
    }
}
