import AppKit
import QuiverCore
import SwiftUI

/// The GitHub dashboard: a picker of the pinned repos across the top,
/// then the selected repo's pull requests, issues, workflow runs,
/// Pages site, releases, tags and branches, each in its own tab.
/// Rows carry their state (draft, conflicts, review, CI rollup) as
/// icons and colour, and link straight to the GitHub page. The visible
/// tab refetches on a minute timer while the page is open.
struct RepositoriesPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    @State private var adding = false

    private var model: GitHubModel { app.github }

    var body: some View {
        @Bindable var model = model
        Page(title: "Repositories",
             subtitle: "Your pinned repos' pull requests, issues and CI, from swe gh.") {
            if model.envBlocked == nil {
                Button("Add repository", systemImage: "plus") { adding = true }
                    .primaryButton()
                Button("Refresh", systemImage: "arrow.clockwise") {
                    Task { await model.refresh(swe: app.swe) }
                }
                .iconButton()
                .keyboardShortcut("r", modifiers: .command)
                .help("Fetch the visible tab again (⌘R)")
            }
        } content: {
            if let blocked = model.envBlocked {
                ErrorCard(message: blocked, actionTitle: "Refresh",
                          actionShortcut: KeyboardShortcut("r", modifiers: .command)) {
                    Task { await model.refresh(swe: app.swe) }
                }
            } else if !model.probed {
                LoadingCard(text: "Checking gh and the pinned repos…")
            } else if model.pins.isEmpty {
                EmptyCard(text: "Pin a repository to see its pull requests, issues and CI here.",
                          icon: "shippingbox", actionTitle: "Add repository") { adding = true }
            } else {
                repoPicker
                Tabs(options: RepoTab.allCases.map { ($0, $0.title) },
                     selection: $model.tab, style: .underline)
                if let error = model.pinError {
                    ErrorCard(message: error)
                }
                if let repo = model.selected {
                    let board = model.board(for: repo)
                    switch model.tab {
                    case .pulls: pulls(repo: repo, board: board)
                    case .issues: issues(repo: repo, board: board)
                    case .actions: actions(repo: repo, board: board)
                    case .pages: pages(repo: repo, board: board)
                    case .releases: releases(repo: repo, board: board)
                    case .tags: tags(repo: repo, board: board)
                    case .branches: branches(repo: repo, board: board)
                    }
                }
            }
        }
        .task(id: Visible(repo: model.selected, tab: model.tab, swe: app.swePath)) {
            await model.loadIfStale(swe: app.swe)
            // Poll while the page is open so merged PRs and finished CI
            // land without a manual refresh.
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(60))
                await model.refresh(swe: app.swe)
            }
        }
        .sheet(isPresented: $adding) {
            AddRepoSheet()
        }
    }

    /// Identity for the visible surface: switching repo, tab or swe
    /// re-runs the load task and its poll loop.
    private struct Visible: Hashable {
        var repo: String?
        var tab: RepoTab
        var swe: String
    }

    /// The pinned repos as chips, plus the add button at the end.
    @ViewBuilder private var repoPicker: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 8) {
                ForEach(model.pins) { pin in
                    RepoChip(name: pin.name, isSelected: pin.name == model.selected) {
                        model.selected = pin.name
                    } remove: {
                        Task { await model.removeRepo(pin.name, swe: app.swe) }
                    }
                }
                Button { adding = true } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 12, weight: .medium))
                        .frame(height: 28)
                        .padding(.horizontal, 10)
                        .background(Theme.field, in: .capsule)
                        .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
                        .foregroundStyle(Theme.textSecondary)
                }
                .plainButton(cornerRadius: 14)
                .help("Pin another repository")
            }
            .padding(.vertical, 2)
        }
        .scrollIndicators(.never)
    }

    // MARK: - Surfaces

    @ViewBuilder
    private func pulls(repo: String, board: RepoBoard) -> some View {
        SurfaceList(board.pulls, empty: "No open pull requests.",
                    loading: model.loading.contains(.init(repo: repo, tab: .pulls))) { pr in
            OpenRow(url: pr.url, open: openURL.callAsFunction) {
                PrIcon(state: pr.state, draft: pr.draft)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 8) {
                        Text(pr.title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                            .lineLimit(1).truncationMode(.tail)
                        ForEach(pr.labels.prefix(3), id: \.self) { Badge($0) }
                    }
                    Text("#\(pr.number) · \(pr.author) · \(pr.branch) → \(pr.base)"
                         + (pr.updatedAt.date.map { " · \(UsageFormat.ago($0, now: .now))" } ?? ""))
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 12)
                if pr.mergeable == .conflicting {
                    Label("Conflicts", systemImage: "exclamationmark.triangle")
                        .font(Theme.caption).foregroundStyle(Theme.warningText)
                }
                ReviewMark(decision: pr.reviewDecision)
                ChecksMark(checks: pr.checks)
            }
        }
    }

    @ViewBuilder
    private func issues(repo: String, board: RepoBoard) -> some View {
        SurfaceList(board.issues, empty: "No open issues.",
                    loading: model.loading.contains(.init(repo: repo, tab: .issues))) { issue in
            OpenRow(url: issue.url, open: openURL.callAsFunction) {
                Image(systemName: issue.state == .open ? "exclamationmark.circle" : "checkmark.circle")
                    .font(.system(size: 15))
                    .foregroundStyle(issue.state == .open ? Theme.success : Theme.textMuted)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 8) {
                        Text(issue.title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                            .lineLimit(1).truncationMode(.tail)
                        ForEach(issue.labels.prefix(3), id: \.self) { Badge($0) }
                    }
                    Text("#\(issue.number) · \(issue.author)"
                         + (issue.comments > 0 ? " · \(issue.comments) comments" : "")
                         + (issue.updatedAt.date.map { " · \(UsageFormat.ago($0, now: .now))" } ?? ""))
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 12)
                ForEach(issue.assignees.prefix(2), id: \.self) {
                    Badge("@\($0)")
                }
            }
        }
    }

    @ViewBuilder
    private func actions(repo: String, board: RepoBoard) -> some View {
        SurfaceList(board.runs, empty: "No workflow runs.",
                    loading: model.loading.contains(.init(repo: repo, tab: .actions))) { run in
            OpenRow(url: run.url, open: openURL.callAsFunction) {
                RunIcon(status: run.status, conclusion: run.conclusion)
                VStack(alignment: .leading, spacing: 3) {
                    Text(run.title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                        .lineLimit(1).truncationMode(.tail)
                    Text("\(run.workflow) · \(run.branch) · \(run.event)"
                         + (run.createdAt.date.map { " · \(UsageFormat.ago($0, now: .now))" } ?? ""))
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 12)
                if let conclusion = run.conclusion {
                    Badge(conclusion == "success" ? "Passed" : conclusion.capitalized)
                }
            }
        }
    }

    @ViewBuilder
    private func pages(repo: String, board: RepoBoard) -> some View {
        switch board.pages {
        case .idle, .loading:
            LoadingCard(text: "Reading the Pages settings…")
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded(let pages):
            if let pages {
                Card(title: "GitHub Pages",
                     subtitle: pages.cname ?? pages.url ?? repo) {
                    VStack(alignment: .leading, spacing: 10) {
                        if let url = pages.url, let link = URL(string: url) {
                            Button { openURL(link) } label: {
                                Label(url, systemImage: "arrow.up.right.square")
                                    .font(Theme.bodySmall).foregroundStyle(Theme.accent)
                            }
                            .plainButton(cornerRadius: Theme.controlRadius)
                        }
                        if let branch = pages.sourceBranch {
                            Label("Built from \(branch)", systemImage: "arrow.triangle.branch")
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        }
                        if let build = pages.latestBuild {
                            HStack(spacing: 8) {
                                BuildMark(status: build.status)
                                Text("Last build \(build.status)")
                                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                if let commit = build.commit {
                                    Text(commit.prefix(7))
                                        .font(.system(size: 12, design: .monospaced))
                                        .foregroundStyle(Theme.textMuted)
                                }
                                if let at = build.updatedAt?.date {
                                    Text(UsageFormat.ago(at, now: .now))
                                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                                }
                            }
                            if let error = build.error {
                                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                                    .lineLimit(3)
                            }
                        }
                    }
                }
            } else {
                EmptyCard(text: "This repo has no GitHub Pages site.", icon: "globe")
            }
        }
    }

    @ViewBuilder
    private func releases(repo: String, board: RepoBoard) -> some View {
        SurfaceList(board.releases, empty: "No releases.",
                    loading: model.loading.contains(.init(repo: repo, tab: .releases))) { release in
            OpenRow(url: release.url, open: openURL.callAsFunction) {
                Image(systemName: "tag").font(.system(size: 14))
                    .foregroundStyle(Theme.accent)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 8) {
                        Text(release.name ?? release.tag)
                            .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                            .lineLimit(1).truncationMode(.tail)
                        if release.latest { Badge("Latest") }
                        if release.draft { Badge("Draft") }
                        if release.prerelease { Badge("Pre-release") }
                    }
                    Text(release.tag
                         + (release.publishedAt?.date.map { " · \(UsageFormat.ago($0, now: .now))" } ?? ""))
                        .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                }
            }
        }
    }

    @ViewBuilder
    private func tags(repo: String, board: RepoBoard) -> some View {
        SurfaceList(board.tags, empty: "No tags.",
                    loading: model.loading.contains(.init(repo: repo, tab: .tags))) { tag in
            OpenRow(url: tag.url, open: openURL.callAsFunction) {
                Image(systemName: "tag").font(.system(size: 14))
                    .foregroundStyle(Theme.textSecondary)
                Text(tag.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                Spacer(minLength: 12)
                Text(tag.sha).font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.textMuted)
            }
        }
    }

    @ViewBuilder
    private func branches(repo: String, board: RepoBoard) -> some View {
        SurfaceList(board.branches, empty: "No branches.",
                    loading: model.loading.contains(.init(repo: repo, tab: .branches))) { branch in
            OpenRow(url: branch.url, open: openURL.callAsFunction) {
                Image(systemName: "arrow.triangle.branch").font(.system(size: 14))
                    .foregroundStyle(branch.default ? Theme.accent : Theme.textSecondary)
                Text(branch.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1).truncationMode(.middle)
                if branch.default { Badge("Default") }
                if branch.protected { Badge("Protected") }
                Spacer(minLength: 12)
                Text(branch.sha).font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.textMuted)
            }
        }
    }
}

/// A surface's rows inside one card: the spinner on first load, the
/// error card on failure, the empty line on `.loaded([])`, else a row
/// per entry separated by hairlines.
private struct SurfaceList<Row: Identifiable & Equatable & Sendable, Content: View>: View {
    var state: Loadable<[Row]>
    var empty: String
    var loading: Bool
    @ViewBuilder var row: (Row) -> Content

    init(_ state: Loadable<[Row]>, empty: String, loading: Bool,
         @ViewBuilder row: @escaping (Row) -> Content) {
        self.state = state
        self.empty = empty
        self.loading = loading
        self.row = row
    }

    var body: some View {
        switch state {
        case .idle, .loading:
            LoadingCard(text: "Fetching…")
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded(let rows) where rows.isEmpty:
            EmptyCard(text: empty, icon: "checkmark.circle")
        case .loaded(let rows):
            Card {
                VStack(spacing: 0) {
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, item in
                        row(item)
                        if index < rows.count - 1 { Divider().overlay(Theme.rowDivider) }
                    }
                }
                if loading {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text("Refreshing…").font(Theme.caption).foregroundStyle(Theme.textMuted)
                    }
                }
            }
        }
    }
}

/// A row that opens its GitHub URL: hover fill, link pointer, plain style.
private struct OpenRow<Content: View>: View {
    var url: String
    var open: (URL) -> Void
    @ViewBuilder var content: Content
    @State private var hovering = false

    var body: some View {
        Button {
            if let link = URL(string: url) { open(link) }
        } label: {
            HStack(alignment: .top, spacing: 12) { content }
                .padding(.vertical, 10)
                .padding(.horizontal, 6)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(hovering ? Theme.rowHover : .clear,
                            in: .rect(cornerRadius: Theme.controlRadius))
                .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))
        .onHover { hovering = $0 }
    }
}

/// A pinned repo in the picker: capsule with the repo's short name,
/// selected on `bg.selected`, unpin on the context menu.
private struct RepoChip: View {
    var name: String
    var isSelected: Bool
    var select: () -> Void
    var remove: () -> Void
    @State private var hovering = false

    private var short: String { name.split(separator: "/").last.map(String.init) ?? name }

    var body: some View {
        Button(action: select) {
            Text(short)
                .font(Theme.control)
                .foregroundStyle(isSelected || hovering ? Theme.textPrimary : Theme.textSecondary)
                .padding(.horizontal, 12)
                .frame(height: 28)
                .background(isSelected ? Theme.selected : hovering ? Theme.hover : Theme.field,
                            in: .capsule)
                .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
        }
        .plainButton(cornerRadius: 14)
        .onHover { hovering = $0 }
        .help(name)
        .contextMenu {
            Button("Open on GitHub") {
                if let url = URL(string: "https://github.com/\(name)") { NSWorkspace.shared.open(url) }
            }
            Divider()
            Button("Unpin \(name)", role: .destructive, action: remove)
        }
    }
}

// MARK: - State marks

/// GitHub's purple for merged/closed-done states.
private let ghPurple = Color(light: "#8250df", dark: "#a371f7")

private struct PrIcon: View {
    var state: GhPr.State
    var draft: Bool
    var body: some View {
        Image(systemName: state == .merged ? "arrow.triangle.merge" : "arrow.triangle.pull")
            .font(.system(size: 15))
            .foregroundStyle(color)
            .padding(.top, 1)
    }
    private var color: Color {
        if draft { return Theme.textMuted }
        return switch state {
        case .open: Theme.success
        case .merged: ghPurple
        case .closed: Theme.danger
        }
    }
}

/// The folded statusCheckRollup as one coloured mark.
private struct ChecksMark: View {
    var checks: GhPr.Checks?
    var body: some View {
        Group {
            switch checks {
            case .passing:
                Label("Passing", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(Theme.success)
            case .failing:
                Label("Failing", systemImage: "xmark.circle.fill")
                    .foregroundStyle(Theme.danger)
            case .running:
                Label("Running", systemImage: "arrow.triangle.2.circlepath")
                    .foregroundStyle(Theme.warning)
            case .unknown:
                Label("CI unknown", systemImage: "questionmark.circle")
                    .foregroundStyle(Theme.textMuted)
            case .none?, nil:
                EmptyView()
            }
        }
        .font(Theme.caption)
    }
}

/// The review decision, shown only when GitHub tracks one.
private struct ReviewMark: View {
    var decision: GhPr.Review?
    var body: some View {
        Group {
            switch decision {
            case .approved:
                Label("Approved", systemImage: "checkmark.seal.fill").foregroundStyle(Theme.success)
            case .changesRequested:
                Label("Changes", systemImage: "pencil.circle.fill").foregroundStyle(Theme.danger)
            case .reviewRequired:
                Label("Review needed", systemImage: "eye.circle.fill").foregroundStyle(Theme.warning)
            case .none?, nil:
                EmptyView()
            }
        }
        .font(Theme.caption)
    }
}

/// A workflow run's status: running marks spin yellow, finished runs
/// take their conclusion's colour.
private struct RunIcon: View {
    var status: String?
    var conclusion: String?
    var body: some View {
        Image(systemName: symbol).font(.system(size: 15)).foregroundStyle(color)
    }
    private var symbol: String {
        if status == "in_progress" || status == "queued" { return "arrow.triangle.2.circlepath" }
        switch conclusion {
        case "success": return "checkmark.circle.fill"
        case "failure": return "xmark.circle.fill"
        case "cancelled": return "slash.circle"
        case "skipped": return "minus.circle"
        default: return "questionmark.circle"
        }
    }
    private var color: Color {
        if status == "in_progress" || status == "queued" { return Theme.warning }
        return switch conclusion {
        case "success": Theme.success
        case "failure": Theme.danger
        case "cancelled", "skipped": Theme.textMuted
        default: Theme.textMuted
        }
    }
}

/// A Pages build's status mark.
private struct BuildMark: View {
    var status: String
    var body: some View {
        Image(systemName: symbol).font(.system(size: 14)).foregroundStyle(color)
    }
    private var symbol: String {
        switch status {
        case "built": "checkmark.circle.fill"
        case "building": "arrow.triangle.2.circlepath"
        case "errored": "xmark.circle.fill"
        default: "questionmark.circle"
        }
    }
    private var color: Color {
        switch status {
        case "built": return Theme.success
        case "building": return Theme.warning
        case "errored": return Theme.danger
        default: return Theme.textMuted
        }
    }
}
