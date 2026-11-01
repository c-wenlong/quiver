import SafariServices
import SwiftUI

/// Which web links in an agent reply get a preview card.
enum LinkCards {
    /// At most this many cards under one reply.
    static let limit = 3

    /// The distinct http and https links in a reply, in the order they
    /// first appear. Links inside code blocks and inline code are skipped,
    /// and so is every other scheme (files, mailto, tel). Both Markdown
    /// links and bare URLs count.
    static func urls(in text: String) -> [URL] {
        var seen = Set<String>()
        var out: [URL] = []
        func add(_ url: URL) {
            guard isWeb(url), seen.insert(url.absoluteString).inserted else { return }
            out.append(url)
        }
        for block in Markdown.parse(text) {
            for source in sources(of: block) {
                let inline = RichText.inline(source)
                for run in inline.runs {
                    if let link = run.link { add(link); continue }
                    if run.inlinePresentationIntent?.contains(.code) == true { continue }
                    for url in bareURLs(in: String(inline[run.range].characters)) { add(url) }
                }
            }
        }
        return out
    }

    static func isWeb(_ url: URL) -> Bool {
        ["http", "https"].contains(url.scheme?.lowercased() ?? "") && (url.host?.isEmpty == false)
    }

    /// "claude.ai" for `https://www.claude.ai/code/x`.
    static func host(_ url: URL) -> String {
        let host = url.host ?? url.absoluteString
        return host.lowercased().hasPrefix("www.") ? String(host.dropFirst(4)) : host
    }

    /// "/code/artifact/abc?x=1", or nil for a bare host.
    static func path(_ url: URL) -> String? {
        var s = url.path
        if let q = url.query, !q.isEmpty { s += "?" + q }
        return s.isEmpty || s == "/" ? nil : s
    }

    private static func sources(of block: Markdown.Block) -> [String] {
        switch block {
        case .heading(_, let text), .paragraph(let text), .quote(let text): [text]
        case .listItem(_, _, let text): [text]
        case .table(let table): table.header + table.rows.flatMap { $0 }
        default: []
        }
    }

    private static let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue)

    private static func bareURLs(in s: String) -> [URL] {
        guard s.localizedCaseInsensitiveContains("http") else { return [] }
        let range = NSRange(s.startIndex..., in: s)
        return (detector?.matches(in: s, range: range) ?? []).compactMap(\.url)
    }
}

/// A reply's text followed by link cards. Web links in the text open in
/// the in-app browser too, so closing it lands back in the chat.
struct AssistantReply: View {
    let text: String
    @State private var browsing: BrowserTarget?

    var body: some View {
        let links = Array(LinkCards.urls(in: text).prefix(LinkCards.limit))
        VStack(alignment: .leading, spacing: 8) {
            RichText(text: text).frame(maxWidth: .infinity, alignment: .leading)
            ForEach(links, id: \.absoluteString) { url in
                LinkCard(url: url) { browsing = BrowserTarget(url: url) }
            }
        }
        .environment(\.openURL, OpenURLAction { url in
            guard LinkCards.isWeb(url) else { return .systemAction }
            browsing = BrowserTarget(url: url)
            return .handled
        })
        .fullScreenCover(item: $browsing) { target in
            InAppBrowser(url: target.url).ignoresSafeArea()
        }
    }
}

struct BrowserTarget: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}

struct LinkCard: View {
    let url: URL
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            HStack(spacing: 10) {
                Image(systemName: "link").font(Theme.font(size: 12)).foregroundStyle(Theme.textSecondary)
                    .iconColumn(16)
                VStack(alignment: .leading, spacing: 1) {
                    Text(LinkCards.host(url)).font(Theme.control).foregroundStyle(Theme.textPrimary)
                        .lineLimit(1)
                    if let path = LinkCards.path(url) {
                        Text(path).font(Theme.caption).foregroundStyle(Theme.textMuted)
                            .lineLimit(1).truncationMode(.middle)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Text("Open").font(Theme.control).foregroundStyle(Theme.textSecondary)
                Image(systemName: "arrow.up.right").font(Theme.font(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.textMuted)
            }
            .padding(.horizontal, 12).padding(.vertical, 8).frame(minHeight: 44)
            .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Open \(LinkCards.host(url))")
    }
}

/// Safari inside the app: a Done button returns to the chat.
struct InAppBrowser: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}
