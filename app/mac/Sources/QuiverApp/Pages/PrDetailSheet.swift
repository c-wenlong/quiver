import QuiverCore
import SwiftUI

/// The PR a row click opens: repo + number is all `swe gh pr view`
/// needs — everything else arrives with the reply.
struct PrSelection: Identifiable, Hashable {
    var repo: String
    var number: Int
    var id: String { "\(repo)#\(number)" }
}

/// The PR workbench: what a board row bounces to instead of github.com.
/// Everything `swe gh pr view` returns — title, state, branches, the
/// per-check rows, reviews, the merge-readiness fields and the body —
/// plus the write actions the control plane owns: merge with a
/// strategy, arm auto-merge, or fall through to the GitHub page.
struct PrDetailSheet: View {
    var selection: PrSelection

    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    @Environment(\.openURL) private var openURL

    @State private var method = "squash"
    /// A merge's returned error — kept on the sheet so a failed merge
    /// stays readable after the busy state clears.
    @State private var mergeError: String?
    /// The check list starts folded — the pass count on the header is
    /// enough when everything is green; it opens for the failed rows.
    @State private var checksOpen = false

    private var model: GitHubModel { app.github }
    private var state: Loadable<GhPrDetail> {
        model.prDetails[selection.id] ?? .idle
    }
    private var merging: Bool {
        model.actionBusy.contains("merge:\(selection.repo)#\(selection.number)")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            switch state {
            case .idle, .loading:
                SkeletonDetail()
                doneRow
            case .failed(let message):
                ErrorCard(message: message, actionTitle: "Refresh") {
                    Task { await load() }
                }
                doneRow
            case .loaded(let pr):
                content(pr)
            }
        }
        .sheetScaffold(width: 680)
        .task { await load() }
    }

    /// The way out while nothing has loaded: the loaded header carries
    /// its own Done, and a slow or failed fetch must not trap the sheet.
    private var doneRow: some View {
        HStack {
            Spacer()
            Button("Done") { dismiss() }
                .secondaryButton()
                .keyboardShortcut(.cancelAction)
        }
    }

    private func load() async {
        await model.loadPrDetail(repo: selection.repo,
                                 number: selection.number, swe: app.swe)
    }

    @ViewBuilder
    private func content(_ pr: GhPrDetail) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header(pr)
                if !pr.checkRuns.isEmpty { checks(pr.checkRuns) }
                if !pr.reviews.isEmpty || !pr.reviewRequests.isEmpty {
                    reviews(pr)
                }
                let body = pr.body.trimmingCharacters(in: .whitespacesAndNewlines)
                if !body.isEmpty {
                    section("Description") {
                        MarkdownBody(body: body)
                            .font(Theme.bodySmall)
                            .foregroundStyle(Theme.textSecondary)
                            .lineSpacing(3)
                            .textSelection(.enabled)
                            .padding(.vertical, 8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxHeight: 460)

        if let mergeError {
            Text(mergeError).font(Theme.caption).foregroundStyle(Theme.dangerText)
                .lineLimit(3)
                .textSelection(.enabled)
                .help(mergeError)
        }

        HStack(spacing: 10) {
            if pr.state == .open {
                SelectMenu(options: [
                    (value: "squash", title: "Squash and merge"),
                    (value: "rebase", title: "Rebase and merge"),
                    (value: "merge", title: "Create a merge commit"),
                ], selection: $method, width: 172)
                Button {
                    Task { await merge(auto: false) }
                } label: {
                    if merging {
                        ProgressView().controlSize(.small)
                            .tint(Theme.successForeground)
                    } else {
                        Text("Merge")
                    }
                }
                .successButton()
                .fixedSize()
                .disabled(merging || pr.draft || pr.mergeable == .conflicting)
                .hoverTip(pr.draft ? "Draft PRs can't merge"
                          : pr.mergeable == .conflicting ? "Resolve the conflicts first"
                          : "Merge now with the selected strategy",
                          replacesLabel: false)
                Button("Auto-merge") { Task { await merge(auto: true) } }
                    .secondaryButton()
                    .fixedSize()
                    .disabled(merging || pr.draft || pr.autoMerge != nil)
                    .hoverTip(pr.autoMerge != nil
                              ? "Auto-merge is already armed"
                              : "Arm auto-merge — merges when checks pass",
                              replacesLabel: false)
            }
            Spacer()
            Button {
                if let url = URL(string: pr.url), url.isWebURL { openURL(url) }
            } label: {
                Image(systemName: "arrow.up.right.square")
            }
            .iconButton()
            .help("Open on GitHub")
            Button { Task { await load() } } label: {
                Image(systemName: "arrow.clockwise")
            }
            .iconButton()
            .help("Fetch again")
            Button("Done") { dismiss() }
                .secondaryButton()
                .fixedSize()
                .keyboardShortcut(.cancelAction)
        }
    }

    /// A titled card of content — the sheet's repeating section shape.
    @ViewBuilder
    private func section<Content: View>(_ title: String,
                                      @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(Theme.caption).foregroundStyle(Theme.textMuted)
            SettingsCard { content() }
        }
    }

    @ViewBuilder
    private func header(_ pr: GhPrDetail) -> some View {
        HStack(alignment: .top, spacing: 10) {
            PrIcon(state: pr.state, draft: pr.draft)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 4) {
                Text(pr.title).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(2)
                Text("#\(pr.number) · \(pr.repo) · \(pr.branch) → \(pr.base)"
                     + (pr.updatedAt.date.map { " · \(UsageFormat.ago($0, now: .now))" } ?? ""))
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .lineLimit(1).truncationMode(.middle)
                HStack(spacing: 8) {
                    Text("by \(pr.author)").font(Theme.caption)
                    if let additions = pr.additions {
                        Text("+\(additions)/−\(pr.deletions ?? 0) in \(pr.changedFiles ?? 0) files")
                            .font(Theme.caption)
                    }
                    if let mergeable = pr.mergeable {
                        Text(mergeable.rawValue).font(Theme.caption)
                    }
                    if pr.mergeState != "unknown" {
                        Text(pr.mergeState).font(Theme.caption)
                    }
                }
                .foregroundStyle(Theme.textSecondary)
                HStack(spacing: 6) {
                    ChecksMark(checks: pr.checks)
                    ReviewMark(decision: pr.reviewDecision)
                    if let auto = pr.autoMerge {
                        Badge("Auto-merge · \(auto.method)")
                    }
                    ForEach(pr.labels.prefix(4), id: \.self) { Badge($0) }
                }
            }
        }
    }

    /// The check list, folded until asked — the header carries the pass
    /// count so a green PR reads "Checks 9/9" without the nine rows.
    @ViewBuilder
    private func checks(_ rows: [GhCheck]) -> some View {
        let passed = rows.filter { $0.state == "passing" }.count
        let failing = rows.contains { $0.state == "failing" }
        VStack(alignment: .leading, spacing: 6) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { checksOpen.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "chevron.right")
                        .font(Theme.font(size: 9, weight: .semibold))
                        .foregroundStyle(Theme.textMuted)
                        .rotationEffect(checksOpen ? .degrees(90) : .zero)
                    Text("Checks").font(Theme.caption).foregroundStyle(Theme.textMuted)
                    Text("\(passed)/\(rows.count)")
                        .font(Theme.caption)
                        .foregroundStyle(failing ? Theme.danger
                                         : passed == rows.count ? Theme.success
                                         : Theme.warning)
                }
            }
            .plainButton()
            .help(checksOpen ? "Hide the check list" : "Show every check")
            if checksOpen {
                SettingsCard { checkRows(rows) }
            }
        }
    }

    @ViewBuilder
    private func checkRows(_ rows: [GhCheck]) -> some View {
        ForEach(Array(rows.enumerated()), id: \.offset) { index, check in
            HStack(spacing: 8) {
                CheckGlyph(state: check.state)
                Text(check.name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1)
                if !check.workflow.isEmpty {
                    Text(check.workflow).font(Theme.caption).foregroundStyle(Theme.textMuted)
                        .lineLimit(1)
                }
                Spacer(minLength: 8)
                if let conclusion = check.conclusion {
                    Text(conclusion).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                } else {
                    Text(check.status).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                if let url = URL(string: check.url), url.isWebURL {
                    Button { openURL(url) } label: {
                        Image(systemName: "arrow.up.right.square")
                            .font(Theme.font(size: 11)).foregroundStyle(Theme.textMuted)
                    }
                    .plainButton()
                    .help("Open the check")
                }
            }
            .padding(.vertical, 7)
            if index < rows.count - 1 { SettingsDivider() }
        }
    }

    @ViewBuilder
    private func reviews(_ pr: GhPrDetail) -> some View {
        section("Reviews") {
            ForEach(Array(pr.reviews.enumerated()), id: \.offset) { index, review in
                HStack(spacing: 8) {
                    Text(review.author).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                    Text(review.state.replacing("_", with: " "))
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    Spacer(minLength: 8)
                    if let at = review.submittedAt?.date {
                        Text(UsageFormat.ago(at, now: .now))
                            .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    }
                }
                .padding(.vertical, 7)
                if index < pr.reviews.count - 1 || !pr.reviewRequests.isEmpty {
                    SettingsDivider()
                }
            }
            if !pr.reviewRequests.isEmpty {
                Text("requested: \(pr.reviewRequests.joined(separator: ", "))")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .padding(.vertical, 7)
            }
        }
    }

    private func merge(auto: Bool) async {
        mergeError = nil
        let error = await model.mergePr(repo: selection.repo,
                                        ref: "\(selection.number)",
                                        method: method, auto: auto, swe: app.swe)
        if let error {
            mergeError = error
        } else {
            // The board row dropped out; the detail refetches so the
            // sheet shows merged / auto-merge-armed state, not stale.
            await load()
        }
    }
}

/// One check row's state mark — swe folds CheckRun and StatusContext
/// into the same `passing|running|failing|none` vocabulary.
private struct CheckGlyph: View {
    var state: String
    var body: some View {
        Image(systemName: symbol).font(Theme.font(size: 14)).foregroundStyle(color)
    }
    private var symbol: String {
        switch state {
        case "passing": "checkmark.circle.fill"
        case "failing": "xmark.circle.fill"
        case "running": "arrow.triangle.2.circlepath"
        default: "minus.circle"
        }
    }
    private var color: Color {
        switch state {
        case "passing": Theme.success
        case "failing": Theme.danger
        case "running": Theme.warning
        default: Theme.textMuted
        }
    }
}
