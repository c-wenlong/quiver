import SWECore
import SwiftUI

/// The issue a row click (or its Resolve button) opens: repo + number is
/// all `swe gh issue view` needs. `mode` picks the sheet, so swapping
/// from the detail to the resolve sheet replaces one card with the other.
struct IssueSelection: Identifiable, Hashable {
    enum Mode: Hashable { case detail, resolve }
    var repo: String
    var number: Int
    var mode: Mode
    var id: String { "\(repo)#\(number):\(mode)" }

    func with(_ mode: Mode) -> IssueSelection {
        IssueSelection(repo: repo, number: number, mode: mode)
    }
}

/// One card for both issue sheets; `open` swaps the page's selection,
/// which is how the detail sheet's Resolve button reaches the other.
struct IssueSheet: View {
    var selection: IssueSelection
    var open: (IssueSelection) -> Void

    var body: some View {
        switch selection.mode {
        case .detail:
            IssueDetailSheet(selection: selection) { open(selection.with(.resolve)) }
        case .resolve:
            ResolveIssueSheet(selection: selection)
        }
    }
}

/// The issue workbench a row opens instead of github.com: what
/// `swe gh issue view` returns (title, labels, body, every comment), a
/// composer that posts a comment through `gh` on this Mac, and the way
/// into the resolve sheet.
struct IssueDetailSheet: View {
    var selection: IssueSelection
    var resolve: () -> Void

    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    @Environment(\.openURL) private var openURL

    @State private var draft = ""
    @State private var draftSelection: TextSelection?
    /// The last post's failure, kept on the sheet until the next try.
    @State private var commentError: String?

    private var model: GitHubModel { app.github }
    private var key: String { "\(selection.repo)#\(selection.number)" }
    private var state: Loadable<GhIssueDetail> { model.issueDetails[key] ?? .idle }
    private var posting: Bool { model.commenting(repo: selection.repo, number: selection.number) }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            switch state {
            case .idle, .loading:
                SkeletonDetail(label: "Fetching the issue…")
                footer(nil)
            case .failed(let message):
                ErrorCard(message: message, actionTitle: "Refresh") {
                    Task { await load() }
                }
                footer(nil)
            case .loaded(let issue):
                content(issue)
            }
        }
        .sheetScaffold(width: 680)
        .task { await load() }
    }

    private func load() async {
        await model.loadIssueDetail(repo: selection.repo, number: selection.number, swe: app.swe)
    }

    @ViewBuilder
    private func content(_ issue: GhIssueDetail) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header(issue)
                let body = issue.body.trimmingCharacters(in: .whitespacesAndNewlines)
                section("Description") {
                    if body.isEmpty {
                        Text("No description.")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textMuted)
                            .padding(.vertical, 8)
                    } else {
                        markdown(body)
                    }
                }
                comments(issue.comments)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxHeight: 400)
        composer
        footer(issue)
    }

    @ViewBuilder
    private func header(_ issue: GhIssueDetail) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: issue.state == .open ? "exclamationmark.circle" : "checkmark.circle")
                .font(Theme.font(size: 16))
                .foregroundStyle(issue.state == .open ? Theme.success : Theme.textMuted)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 4) {
                Text(issue.title).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(2)
                Text(["#\(issue.number)", selection.repo, "by \(issue.author)",
                      (issue.createdAt?.date ?? issue.updatedAt?.date).map { "opened \(UsageFormat.ago($0, now: .now))" }]
                        .compactMap { $0 }.joined(separator: " · "))
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .lineLimit(1).truncationMode(.middle)
                if !issue.labels.isEmpty || !issue.assignees.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(issue.labels.prefix(5), id: \.self) { Badge($0) }
                        ForEach(issue.assignees.prefix(3), id: \.self) { Badge("@\($0)") }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func comments(_ rows: [GhIssueComment]) -> some View {
        section(rows.isEmpty ? "Comments" : "Comments · \(rows.count)") {
            if rows.isEmpty {
                Text("No comments yet.")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textMuted)
                    .padding(.vertical, 8)
            }
            ForEach(Array(rows.enumerated()), id: \.offset) { index, comment in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text(comment.author).font(Theme.rowTitle)
                            .foregroundStyle(Theme.textPrimary)
                        if let at = comment.createdAt?.date {
                            Text(UsageFormat.ago(at, now: .now))
                                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                        }
                        Spacer(minLength: 8)
                        if let link = comment.url.flatMap(URL.init(string:)), link.isWebURL {
                            Button { openURL(link) } label: {
                                Image(systemName: "arrow.up.right.square")
                                    .font(Theme.font(size: 11)).foregroundStyle(Theme.textMuted)
                            }
                            .plainButton()
                            .hoverTip("Open this comment on GitHub")
                        }
                    }
                    markdown(comment.body)
                }
                .padding(.top, 8)
                if index < rows.count - 1 { SettingsDivider() }
            }
        }
    }

    private func markdown(_ text: String) -> some View {
        MarkdownBody(body: text)
            .font(Theme.bodySmall)
            .foregroundStyle(Theme.textSecondary)
            .lineSpacing(3)
            .textSelection(.enabled)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// A titled card of content, as `PrDetailSheet` draws its sections.
    @ViewBuilder
    private func section<Content: View>(_ title: String,
                                      @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(Theme.caption).foregroundStyle(Theme.textMuted)
            SettingsCard { content() }
        }
    }

    /// The comment box: Return breaks the line, ⌘↩ posts.
    private var composer: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .bottom, spacing: 8) {
                TextField("Add context for whoever picks this up, you or an agent…",
                          text: $draft, selection: $draftSelection, axis: .vertical)
                    .lineLimit(2...6)
                    .sweField(multiline: true)
                    .disabled(posting)
                    .onKeyPress(.return, phases: .down) { press in
                        if press.modifiers.contains(.command) {
                            post()
                        } else {
                            newline()
                        }
                        return .handled
                    }
                Button(action: post) {
                    if posting {
                        ProgressView().controlSize(.small)
                    } else {
                        Text("Comment")
                    }
                }
                .secondaryButton()
                .fixedSize()
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(posting || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .hoverTip("Post this comment on GitHub (⌘↩)")
            }
            Text("Posts to GitHub as you, through gh on this Mac. Markdown works.")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
            if let commentError {
                Text(commentError).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .lineLimit(3)
                    .textSelection(.enabled)
            }
        }
    }

    @ViewBuilder
    private func footer(_ issue: GhIssueDetail?) -> some View {
        HStack(spacing: 10) {
            Button(action: resolve) {
                Label("Resolve with agent", systemImage: "sparkles")
            }
            .primaryButton()
            .fixedSize()
            .disabled(issue?.state == .closed)
            .hoverTip(issue?.state == .closed
                      ? "This issue is closed."
                      : "Start an agent on this issue in its own worktree")
            Spacer()
            Button {
                let url = issue?.url ?? "https://github.com/\(selection.repo)/issues/\(selection.number)"
                if let link = URL(string: url), link.isWebURL { openURL(link) }
            } label: {
                Image(systemName: "arrow.up.right.square")
            }
            .iconButton()
            .hoverTip("Open on GitHub")
            .accessibilityLabel("Open on GitHub")
            Button { Task { await load() } } label: {
                Image(systemName: "arrow.clockwise")
            }
            .iconButton()
            .hoverTip("Fetch again")
            .accessibilityLabel("Refresh")
            Button("Done") { dismiss() }
                .secondaryButton()
                .fixedSize()
                .keyboardShortcut(.cancelAction)
        }
    }

    /// A line break in place of the selection, or at the end when the
    /// field reports none.
    private func newline() {
        guard case .selection(let range) = draftSelection?.indices, range.upperBound <= draft.endIndex else {
            draft += "\n"
            return
        }
        let at = draft.distance(from: draft.startIndex, to: range.lowerBound)
        draft.replaceSubrange(range, with: "\n")
        draftSelection = TextSelection(insertionPoint: draft.index(draft.startIndex, offsetBy: at + 1))
    }

    private func post() {
        let text = draft
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !posting else { return }
        commentError = nil
        Task {
            if let error = await model.commentOnIssue(repo: selection.repo, number: selection.number,
                                                      body: text, swe: app.swe) {
                commentError = error
            } else if draft == text {
                draft = ""
            }
        }
    }
}
