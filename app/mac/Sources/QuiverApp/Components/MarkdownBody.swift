import QuiverCore
import SwiftUI

/// The PR body, segmented by `GitHubMarkdown`: prose between `<details>`
/// folds renders as markdown; each fold collapses to its `<summary>`
/// line — the way GitHub renders them.
struct MarkdownBody: View {
    let segments: [MarkdownSegment]

    init(body: String) { segments = GitHubMarkdown.segments(body) }
    init(segments: [MarkdownSegment]) { self.segments = segments }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(segments.enumerated()), id: \.offset) { _, seg in
                switch seg {
                case .markdown(let md):
                    MarkdownBlocks(blocks: GitHubMarkdown.blocks(md))
                case .details(let title, let inner):
                    DetailsSection(title: title, segments: inner)
                }
            }
        }
    }

    /// One line of inline markdown → styled runs. Apple's parser does
    /// spans fine; what it can't do is the pre-pass here: `![alt](src)`
    /// and `[![alt](badge)](href)` become plain links — nested
    /// link-image brackets parse as garbage, and an image badge is a
    /// label anyway. Lines parse one at a time so a soft break stays a
    /// rendered newline, matching how GitHub wraps bodies.
    static func inline(_ md: String) -> AttributedString {
        var md = md
        md = GitHubMarkdown.flatLinks(md)
        var out = AttributedString()
        for (i, line) in md.split(separator: "\n",
                                  omittingEmptySubsequences: false).enumerated() {
            if i > 0 { out += AttributedString("\n") }
            out += (try? AttributedString(
                markdown: String(line),
                options: .init(interpretedSyntax: .full,
                               failurePolicy: .returnPartiallyParsedIfPossible)))
                ?? AttributedString(String(line))
        }
        return webLinksOnly(out)
    }

    /// PR, issue and transcript text is someone else's: a link it holds
    /// opens through the default handler on click, so a `file:` or
    /// custom-scheme link would launch whatever it names. Only web links
    /// stay links, as every other open in the app checks `isWebURL`.
    static func webLinksOnly(_ text: AttributedString) -> AttributedString {
        var text = text
        for run in text.runs {
            if let url = run.link, !url.isWebURL {
                text[run.range].link = nil
            }
        }
        return text
    }
}

/// The body's blocks as real views: sized headings, monospaced fences,
/// a quote rail, and list markers that draw — the structures Apple's
/// fused-text render could only hint at with characters.
struct MarkdownBlocks: View {
    let blocks: [MarkdownBlock]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                switch block {
                case .heading(let level, let text):
                    Text(MarkdownBody.inline(text))
                        .font(Theme.font(size: headingSize(level), weight: .semibold))
                        .foregroundStyle(Theme.textPrimary)
                case .paragraph(let text):
                    Text(MarkdownBody.inline(text))
                case .code(let code, let lang):
                    VStack(alignment: .leading, spacing: 4) {
                        if let lang {
                            Text(lang).font(Theme.caption)
                                .foregroundStyle(Theme.textMuted)
                        }
                        Text(AttributedString(highlighting: code, language: lang))
                            .font(Theme.font(size: 11, design: .monospaced))
                            .foregroundStyle(Theme.textPrimary)
                            .textSelection(.enabled)
                    }
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.code)
                    .clipShape(.rect(cornerRadius: 6))
                case .quote(let inner):
                    HStack(alignment: .top, spacing: 0) {
                        Capsule().fill(Theme.border).frame(width: 3)
                        MarkdownBlocks(blocks: inner)
                            .padding(.leading, 8)
                    }
                    .foregroundStyle(Theme.textSecondary)
                case .list(let items):
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                            HStack(alignment: .firstTextBaseline, spacing: 6) {
                                marker(item)
                                Text(MarkdownBody.inline(item.text))
                                    .foregroundStyle(Theme.textPrimary)
                            }
                            .padding(.leading, CGFloat(item.depth) * 14)
                        }
                    }
                case .rule:
                    Divider().overlay(Theme.border)
                }
            }
        }
    }

    private func headingSize(_ level: Int) -> CGFloat {
        [17, 15, 14, 13, 13, 13][min(max(level, 1), 6) - 1]
    }

    @ViewBuilder
    private func marker(_ item: MarkdownListItem) -> some View {
        switch item.kind {
        case .bullet:
            Text("•").foregroundStyle(Theme.textSecondary)
        case .ordered(let n):
            Text("\(n).").foregroundStyle(Theme.textSecondary)
                .monospacedDigit()
        case .task(let done):
            Image(systemName: done ? "checkmark.square.fill" : "square")
                .foregroundStyle(done ? Theme.success : Theme.textMuted)
                .font(Theme.font(size: 11))
        }
    }
}

/// A `<details>` block: the `<summary>` line behind the same fold
/// affordance as Checks, its segments rendered inside when opened.
private struct DetailsSection: View {
    let title: String
    let segments: [MarkdownSegment]
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { open.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "chevron.right")
                        .font(Theme.font(size: 9, weight: .semibold))
                        .foregroundStyle(Theme.textMuted)
                        .rotationEffect(open ? .degrees(90) : .zero)
                    Text(MarkdownBody.inline(title))
                        .font(Theme.bodySmall)
                        .foregroundStyle(Theme.textPrimary)
                        .lineLimit(1)
                }
            }
            .plainButton()
            if open {
                MarkdownBody(segments: segments)
                    .padding(.leading, 12)
            }
        }
    }
}
