import SwiftUI

/// P5 and P6: a session's transcript, live, with the composer when
/// attached and the attach card when only watching.
struct ChatView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.scenePhase) private var phase
    let key: String
    @State private var model: ChatModel?

    var body: some View {
        Group {
            if let model { ChatBody(model: model) } else { Theme.wash }
        }
        .background(Theme.wash)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .tabBar)
        .toolbar {
            ToolbarItem(placement: .principal) {
                let row = app.session(key)
                VStack(spacing: 1) {
                    Text(row?.displayTitle ?? "Session").font(.system(size: 15, weight: .semibold)).lineLimit(1)
                    HStack(spacing: 4) {
                        HarnessIcon(harness: row?.harness, name: row?.harnessName ?? "", size: 11)
                        Text(SessionGrouping.shortDir(row?.cwd))
                    }
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary).lineLimit(1)
                }
                .frame(maxWidth: 240)
            }
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button { UIPasteboard.general.string = key } label: { Label("Copy session id", systemImage: "doc.on.doc") }
                    if let model, app.session(key)?.isAttached == true {
                        Button(role: .destructive) { Task { await model.interrupt() } } label: { Label("Interrupt", systemImage: "stop.circle") }
                    }
                } label: { Image(systemName: "ellipsis.circle") }
            }
        }
        .task(id: key) {
            let m = app.chat(key)
            model = m
            await m.start()
        }
        .onDisappear { model?.stop() }
        .onChange(of: phase) { _, new in
            guard let model else { return }
            if new == .active { Task { await model.start() } } else if new == .background { model.stop() }
        }
    }
}

private struct ChatBody: View {
    @Environment(AppModel.self) private var app
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Bindable var model: ChatModel

    /// Scrolling within this many entries of the oldest one loaded asks
    /// for the next page, so it's usually in before you reach the top.
    static let earlierThreshold = 15

    var body: some View {
        let entries = ChatEntry.fold(model.items)
        ScrollView(.vertical) {
            LazyVStack(alignment: .leading, spacing: 10) {
                if model.loading {
                    LoadingRow(text: "Loading transcript")
                } else if let error = model.loadError, model.items.isEmpty {
                    StateCard(icon: "exclamationmark.triangle", title: "Couldn't load this session", message: error, tone: .error)
                } else if model.items.isEmpty {
                    StateCard(icon: "text.bubble", title: "No messages yet", message: "New messages appear here as the agent works.")
                } else if model.hasEarlier {
                    EarlierRow(model: model).onAppear { model.wantsEarlier = true }
                }
                ForEach(Array(entries.enumerated()), id: \.element.id) { index, entry in
                    Group {
                        switch entry {
                        case .item(let item): ChatItemView(item: item, model: model)
                        case .toolRun(_, let items): ToolRunCard(items: items, model: model)
                        }
                    }
                    .onAppear { if index < Self.earlierThreshold, model.hasEarlier { model.wantsEarlier = true } }
                    .transition(.asymmetric(insertion: arrival(12), removal: .opacity))
                }
                if model.working, !model.loading {
                    WorkingIndicator(harness: model.row?.harness, name: model.row?.harnessName ?? "agent")
                        .transition(arrival(8))
                }
            }
            .animation(.easeOut(duration: 0.2), value: model.working)
            .scrollTargetLayout()
            .padding(.horizontal, Theme.gutter)
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .defaultScrollAnchor(.bottom)
        .modifier(LoadsEarlierAtRest(model: model, oldest: entries.first?.id))
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom) { bottomBar }
    }

    /// New entries fade in, rising `rise` points unless Reduce Motion is on.
    private func arrival(_ rise: CGFloat) -> AnyTransition {
        reduceMotion ? .opacity : .opacity.combined(with: .offset(y: rise))
    }

    @ViewBuilder private var bottomBar: some View {
        let row = model.row
        VStack(spacing: 0) {
            Rectangle().fill(Theme.border).frame(height: 1)
            if row?.isAttached == true {
                Composer(model: model)
            } else {
                AttachCard(model: model, row: row)
            }
        }
        .background(Theme.wash)
    }
}

/// Top of the transcript while older pages remain: a spinner where they
/// load on their own (iOS 18), a button on iOS 17.
private struct EarlierRow: View {
    let model: ChatModel

    var body: some View {
        if #available(iOS 18, *) {
            LoadingRow(text: "Loading earlier messages")
        } else if model.loadingEarlier {
            LoadingRow(text: "Loading earlier messages")
        } else {
            Button("Load earlier messages") { Task { await model.loadEarlier() } }
                .secondaryButton(compact: true)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
        }
    }
}

/// Loads the page `model.wantsEarlier` asked for once scrolling is at
/// rest, then puts the entry that was at the top of the screen back
/// there, so what you were reading stays put (iOS 18; iOS 17 gets the
/// button in `EarlierRow`).
///
/// Loading while a flick still moves, or keeping your place by offset,
/// both failed in the simulator: a page landing above keeps the offset
/// from the top, the new first rows show and ask for the next page, and
/// a decelerating scroll overrides any move made to compensate, so it
/// raced to the start of the session. At rest, restoring by entry holds,
/// and the new rows land off screen so nothing asks again until you
/// scroll up to them.
private struct LoadsEarlierAtRest: ViewModifier {
    let model: ChatModel
    /// The oldest entry's id. Never the one to restore: when it's a run
    /// of tool calls and the older page ends in more of them, they join
    /// the run and its id (taken from its first call) changes.
    let oldest: String?

    func body(content: Content) -> some View {
        if #available(iOS 18, *) {
            content.modifier(Tracking(model: model, oldest: oldest))
        } else {
            content
        }
    }

    @available(iOS 18, *)
    private struct Tracking: ViewModifier {
        let model: ChatModel
        let oldest: String?
        @State private var position = ScrollPosition(idType: String.self)
        @State private var idle = true
        /// The entries on screen, top first.
        @State private var visible: [String] = []
        /// Bumped each time you start a scroll, so a page that lands after
        /// you moved on doesn't pull you back.
        @State private var drags = 0

        func body(content: Content) -> some View {
            content
                .scrollPosition($position, anchor: .top)
                .onScrollTargetVisibilityChange(idType: String.self) { visible = $0 }
                .onScrollPhaseChange { _, phase in
                    idle = phase == .idle
                    if phase == .interacting { drags += 1 }
                    load()
                }
                .onChange(of: model.wantsEarlier) { load() }
        }

        private func load() {
            guard idle, model.wantsEarlier, model.hasEarlier, !model.loadingEarlier else { return }
            model.wantsEarlier = false
            let keep = visible.first { $0 != oldest }
            let dragsAtStart = drags
            Task {
                if await model.loadEarlier(), let keep, drags == dragsAtStart {
                    position.scrollTo(id: keep, anchor: .top)
                }
            }
        }
    }
}

// MARK: - Items

/// Three or more tool calls in a row fold into one "Used N tools" card,
/// so a long agent turn doesn't bury what it said.
enum ChatEntry: Identifiable {
    case item(ChatItem)
    case toolRun(String, [ChatItem])

    var id: String {
        switch self {
        case .item(let i): i.id
        case .toolRun(let id, _): id
        }
    }

    static func fold(_ items: [ChatItem], minRun: Int = 3) -> [ChatEntry] {
        var out: [ChatEntry] = []
        var run: [ChatItem] = []
        func flush() {
            if run.count >= minRun { out.append(.toolRun("run-\(run[0].id)", run)) } else { out += run.map(ChatEntry.item) }
            run = []
        }
        for item in items {
            if case .tool = item.kind { run.append(item); continue }
            flush()
            out.append(.item(item))
        }
        flush()
        return out
    }
}

private struct ToolRunCard: View {
    let items: [ChatItem]
    let model: ChatModel
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { withAnimation(.easeOut(duration: 0.15)) { open.toggle() } } label: {
                HStack(spacing: 8) {
                    Image(systemName: "wrench.and.screwdriver").font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                        .frame(width: 16)
                    Text("Used \(items.count) tools").font(Theme.control).foregroundStyle(Theme.textPrimary)
                    Text(names).font(Theme.caption).foregroundStyle(Theme.textMuted).lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: open ? "chevron.up" : "chevron.down").font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(Theme.textMuted)
                }
                .padding(.horizontal, 12).frame(minHeight: 40)
                .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            if open {
                ForEach(items) { ChatItemView(item: $0, model: model) }
                    .padding(.leading, 12)
            }
        }
    }

    /// "Bash ×4, Edit, Read": what ran, most used first.
    private var names: String {
        var counts: [String: Int] = [:]
        var order: [String] = []
        for item in items {
            guard case .tool(let call, _) = item.kind else { continue }
            let name = ToolArgs.displayName(call.name)
            if counts[name] == nil { order.append(name) }
            counts[name, default: 0] += 1
        }
        return order.sorted { counts[$0]! > counts[$1]! }
            .map { counts[$0]! > 1 ? "\($0) ×\(counts[$0]!)" : $0 }
            .joined(separator: ", ")
    }
}

private struct ChatItemView: View {
    let item: ChatItem
    let model: ChatModel

    var body: some View {
        switch item.kind {
        case .user(let text, let send):
            UserBubble(text: text, send: send) { Task { await model.retry(item) } }
        case .assistant(let text):
            AssistantReply(text: text)
        case .thinking(let text):
            ThinkingCard(text: text)
        case .tool(let call, let result):
            ToolCard(call: call, result: result)
        case .approval(let rid, let prompt, let state):
            ApprovalCard(prompt: prompt, state: state) { decision in Task { await model.decide(rid, decision: decision) } }
        case .compaction:
            HStack(spacing: 8) {
                Rectangle().fill(Theme.border).frame(height: 1)
                Text("Context compacted").font(Theme.caption).foregroundStyle(Theme.textMuted).fixedSize()
                Rectangle().fill(Theme.border).frame(height: 1)
            }
            .padding(.vertical, 4)
        case .system(let text):
            Text(text).font(Theme.caption).foregroundStyle(Theme.textMuted).frame(maxWidth: .infinity, alignment: .center)
                .multilineTextAlignment(.center)
        case .error(let text):
            Label(text, systemImage: "exclamationmark.triangle").font(Theme.caption).foregroundStyle(Theme.dangerText)
        }
    }
}

/// The harness's logo with three pulsing dots beside it, under the last
/// message while the agent works; new messages land above it.
struct WorkingIndicator: View {
    let harness: String?
    let name: String
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 8) {
            HarnessIcon(harness: harness, name: name, size: 16)
            dots
        }
        .font(Theme.caption)
        .padding(.leading, 9).padding(.trailing, 12).frame(height: 30)
        .background(Theme.card, in: .capsule)
        .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement()
        .accessibilityLabel("\(name) is working")
    }

    private var dots: some View {
        TimelineView(.animation(paused: reduceMotion)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            HStack(spacing: 4) {
                ForEach(0..<3, id: \.self) { i in
                    // A wave left to right, one pass every 1.2 s.
                    let lit = reduceMotion ? 0.5 : max(0, sin((t / 1.2 - Double(i) * 0.15) * 2 * .pi))
                    Circle().fill(Theme.textSecondary).frame(width: 6, height: 6)
                        .opacity(0.3 + 0.7 * lit)
                        .offset(y: -2 * lit)
                }
            }
        }
    }
}

private struct UserBubble: View {
    let text: String
    let send: SendState?
    let retry: () -> Void

    var body: some View {
        VStack(alignment: .trailing, spacing: 4) {
            Text(text)
                .font(Theme.body).foregroundStyle(Theme.textPrimary)
                .textSelection(.enabled)
                .padding(.horizontal, 12).padding(.vertical, 9)
                .background(Theme.selected, in: .rect(cornerRadius: 16))
                .opacity(send == .sending ? 0.6 : 1)
            switch send {
            case .sending?: Text("Sending").font(.system(size: 11)).foregroundStyle(Theme.textMuted)
            case .queued(let note)?: Text(note).font(.system(size: 11)).foregroundStyle(Theme.textMuted)
            case .delivered(let note?)?: Text(note).font(.system(size: 11)).foregroundStyle(Theme.textMuted)
            case .delivered(nil)?: EmptyView()
            case .failed(let note)?:
                Button(action: retry) {
                    Label("\(note). Tap to retry", systemImage: "arrow.clockwise").font(.system(size: 11))
                }
                .foregroundStyle(Theme.dangerText)
            case nil: EmptyView()
            }
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
        .padding(.leading, 40)
    }
}

/// Agent text as Markdown: headings, lists, quotes, rules, code blocks
/// and tables, with inline styling inside each. Prose wraps to the screen;
/// a table too wide for it scrolls sideways inside its own box, so the
/// chat itself never does.
struct RichText: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(Markdown.parse(text).enumerated()), id: \.offset) { _, block in
                BlockView(block: block)
            }
        }
    }

    static func inline(_ s: String) -> AttributedString {
        let source = Markdown.inlineImagesAsText(s)
        var out = (try? AttributedString(markdown: source, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(source)
        // A link to the agent's own files (`/Users/…`, `src/x.py`) opens
        // nothing on the phone: show its text as a file name instead.
        for run in out.runs {
            guard let url = run.link, !Self.opensOnPhone(url) else { continue }
            out[run.range].link = nil
            out[run.range].inlinePresentationIntent = .code
        }
        return out
    }

    static func opensOnPhone(_ url: URL) -> Bool {
        ["http", "https", "mailto", "tel"].contains(url.scheme?.lowercased() ?? "")
    }
}

private struct BlockView: View {
    let block: Markdown.Block

    var body: some View {
        switch block {
        case .heading(let level, let text):
            Text(RichText.inline(text))
                .font(.system(size: level == 1 ? 20 : level == 2 ? 17 : 15, weight: .semibold))
                .foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, level <= 2 ? 6 : 2)
        case .paragraph(let text):
            prose(text)
        case .listItem(let indent, let marker, let text):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if let (box, rest) = Markdown.taskBox(text) {
                    Image(systemName: box == .done ? "checkmark.square.fill" : "square")
                        .font(.system(size: 14))
                        .foregroundStyle(box == .done ? Theme.accent : Theme.textSecondary)
                        .frame(minWidth: 14, alignment: .trailing)
                        .accessibilityLabel(box == .done ? "Done" : "To do")
                    prose(rest)
                } else {
                    Text(marker)
                        .font(marker == "•" ? Theme.body : Theme.body.monospacedDigit())
                        .foregroundStyle(Theme.textSecondary)
                        .frame(minWidth: 14, alignment: .trailing)
                    prose(text)
                }
            }
            .padding(.leading, CGFloat(min(indent, 4)) * 18)
        case .quote(let text):
            HStack(alignment: .top, spacing: 10) {
                RoundedRectangle(cornerRadius: 1).fill(Theme.borderStrong).frame(width: 3)
                Text(RichText.inline(text)).font(Theme.body).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        case .code(_, let text):
            Text(text).font(Theme.mono).foregroundStyle(Theme.textLabel)
                .textSelection(.enabled)
                .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.code, in: .rect(cornerRadius: Theme.controlRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))
        case .table(let table):
            TableBlock(table: table)
        case .rule:
            Rectangle().fill(Theme.border).frame(height: 1).padding(.vertical, 4)
        case .image(let alt, let source):
            NoteChip(icon: "photo", title: alt.isEmpty ? "Image" : alt,
                     detail: (source as NSString).lastPathComponent + " · on the machine")
        case .rendered:
            NoteChip(icon: "rectangle.3.group", title: "Rendered view",
                     detail: "Open the session on the machine to see it")
        }
    }

    private func prose(_ text: String) -> some View {
        Text(RichText.inline(text)).font(Theme.body).foregroundStyle(Theme.textPrimary)
            .textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A Markdown table in a hairline box. Columns size to their content up
/// to a cap and wrap past it; when the table is wider than the screen it
/// scrolls sideways inside the box, the way Codex shows it.
struct TableBlock: View {
    let table: Markdown.Table
    static let maxColumnWidth: CGFloat = 240
    /// The grid is built eagerly, so a malformed or huge table is cut to
    /// this many columns and rows, with a note saying what was left out.
    static let maxColumns = 24
    static let maxRows = 200

    var body: some View {
        let columns = min(table.columnCount, Self.maxColumns)
        let rows = table.rows.prefix(Self.maxRows)
        let hiddenRows = table.rows.count - rows.count
        let hiddenColumns = table.columnCount - columns
        VStack(alignment: .leading, spacing: 4) {
            grid(columns: columns, rows: Array(rows))
            if hiddenRows > 0 || hiddenColumns > 0 {
                Text([hiddenRows > 0 ? "\(hiddenRows) more rows" : nil,
                      hiddenColumns > 0 ? "\(hiddenColumns) more columns" : nil]
                    .compactMap { $0 }.joined(separator: " and ") + " not shown")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
        }
    }

    private func grid(columns: Int, rows: [[String]]) -> some View {
        ScrollView(.horizontal, showsIndicators: true) {
            Grid(alignment: .topLeading, horizontalSpacing: 0, verticalSpacing: 0) {
                GridRow {
                    ForEach(0..<columns, id: \.self) { c in
                        cell(c < table.header.count ? table.header[c] : "", column: c, header: true)
                    }
                }
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    Rectangle().fill(Theme.border).frame(height: 1).gridCellUnsizedAxes(.horizontal)
                    GridRow {
                        ForEach(0..<columns, id: \.self) { c in
                            cell(c < row.count ? row[c] : "", column: c, header: false)
                        }
                    }
                }
            }
        }
        .scrollBounceBehavior(.basedOnSize, axes: .horizontal)
        // The chat sets .defaultScrollAnchor(.bottom) so it opens at the
        // newest message; scroll views inside inherit it, and for a sideways
        // one "bottom" is the horizontal middle. Tables start at their left.
        .defaultScrollAnchor(.leading)
        .background(Theme.card, in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))
        .clipShape(.rect(cornerRadius: Theme.controlRadius))
    }

    private func cell(_ text: String, column: Int, header: Bool) -> some View {
        let align = column < table.align.count ? table.align[column] : .leading
        return CappedWidth(max: Self.maxColumnWidth) {
            Text(RichText.inline(text))
                .font(header ? Theme.control : Theme.caption)
                .foregroundStyle(header ? Theme.textPrimary : Theme.textLabel)
                .multilineTextAlignment(align == .trailing ? .trailing : align == .center ? .center : .leading)
        }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            // Fill the column so the header shading spans the whole row.
            .frame(maxWidth: .infinity, alignment: align == .trailing ? .trailing : align == .center ? .center : .leading)
            .background(header ? Theme.subtle : .clear)
            .gridColumnAlignment(align == .trailing ? .trailing : align == .center ? .center : .leading)
    }
}

/// A quiet one-line card for content the phone can't show itself.
private struct NoteChip: View {
    let icon: String
    let title: String
    let detail: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon).font(.system(size: 14)).foregroundStyle(Theme.textSecondary).frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(Theme.control).foregroundStyle(Theme.textPrimary).lineLimit(2)
                Text(detail).font(Theme.caption).foregroundStyle(Theme.textMuted).lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12).padding(.vertical, 9)
        .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
    }
}

/// Its content at its natural width, up to `max`, wrapping past it. A
/// horizontal ScrollView offers unlimited width, and a plain
/// `.frame(maxWidth:)` would let a long cell run on one line forever.
struct CappedWidth: Layout {
    var max: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        guard let child = subviews.first else { return .zero }
        let ideal = child.sizeThatFits(.unspecified)
        let width = min(ideal.width, max)
        let size = child.sizeThatFits(ProposedViewSize(width: width, height: nil))
        return CGSize(width: width, height: size.height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        subviews.first?.place(at: bounds.origin, proposal: ProposedViewSize(width: bounds.width, height: bounds.height))
    }
}

private struct ThinkingCard: View {
    let text: String
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { withAnimation(.easeOut(duration: 0.15)) { open.toggle() } } label: {
                HStack(spacing: 6) {
                    Image(systemName: "brain").font(.system(size: 12))
                    Text("Thought").font(Theme.control)
                    Image(systemName: open ? "chevron.up" : "chevron.down").font(.system(size: 10, weight: .semibold))
                }
                .foregroundStyle(Theme.textSecondary)
            }
            .buttonStyle(.plain)
            if open {
                Text(RichText.inline(text)).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

private struct ToolCard: View {
    let call: ToolCall
    let result: String?
    @State private var open = false

    var body: some View {
        let diff = ToolDiff.from(call)
        VStack(alignment: .leading, spacing: 0) {
            Button { withAnimation(.easeOut(duration: 0.15)) { open.toggle() } } label: {
                HStack(spacing: 8) {
                    Image(systemName: Self.icon(call.name)).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                        .frame(width: 16)
                    Text(ToolArgs.summary(call))
                        .font(Theme.mono).foregroundStyle(Theme.textPrimary)
                        .lineLimit(1).truncationMode(.middle)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if let diff {
                        Text("+\(diff.added)").font(Theme.mono).foregroundStyle(Theme.success)
                        Text("−\(diff.removed)").font(Theme.mono).foregroundStyle(Theme.dangerText)
                    }
                    Image(systemName: open ? "chevron.up" : "chevron.down").font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(Theme.textMuted)
                }
                .padding(.horizontal, 12).frame(minHeight: 40)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            if open {
                Rectangle().fill(Theme.border).frame(height: 1)
                VStack(alignment: .leading, spacing: 0) {
                    if let diff {
                        DiffView(lines: Array(diff.lines.prefix(200)))
                    } else if let args = ToolArgs.readable(call), !args.isEmpty {
                        codeBlock(String(args.prefix(4000)))
                    }
                    if let result, !result.isEmpty {
                        Rectangle().fill(Theme.border).frame(height: 1)
                        codeBlock(String(result.prefix(4000)))
                    }
                }
            }
        }
        .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
        .clipShape(.rect(cornerRadius: Theme.blockRadius))
    }

    private func codeBlock(_ s: String) -> some View {
        Text(s).font(Theme.mono).foregroundStyle(Theme.textLabel).textSelection(.enabled)
            .padding(10).frame(maxWidth: .infinity, alignment: .leading)
    }

    static func icon(_ name: String) -> String {
        switch name.lowercased() {
        case "bash", "shell", "exec", "exec_command", "run_shell_command": "terminal"
        case "read", "view", "read_file": "doc.text"
        case "edit", "multiedit", "write", "apply_patch", "patch", "file_edit", "notebookedit": "pencil"
        case "grep", "glob", "search", "find": "magnifyingglass"
        case "webfetch", "websearch", "fetch": "globe"
        case "task", "agent": "person.2"
        default: "wrench.and.screwdriver"
        }
    }
}

struct DiffView: View {
    let lines: [DiffLine]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                HStack(alignment: .top, spacing: 6) {
                    Text(sign(line.kind)).foregroundStyle(color(line.kind)).frame(width: 8)
                    Text(line.text.isEmpty ? " " : line.text)
                        .foregroundStyle(line.kind == .meta ? Theme.textMuted : Theme.textLabel)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .font(Theme.mono)
                .padding(.horizontal, 10).padding(.vertical, 1)
                .background(background(line.kind))
            }
        }
        .padding(.vertical, 6)
    }

    private func sign(_ k: DiffLine.Kind) -> String {
        switch k { case .added: "+"; case .removed: "−"; default: " " }
    }
    private func color(_ k: DiffLine.Kind) -> Color {
        switch k { case .added: Theme.success; case .removed: Theme.dangerText; default: Theme.textMuted }
    }
    private func background(_ k: DiffLine.Kind) -> Color {
        switch k { case .added: Theme.diffAdded; case .removed: Theme.diffRemoved; default: .clear }
    }
}

private struct ApprovalCard: View {
    let prompt: String
    let state: ApprovalState
    let decide: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Circle().fill(Theme.warning).frame(width: 7, height: 7)
                Text(title).font(Theme.control).foregroundStyle(isOpen ? Theme.warningText : Theme.textSecondary)
            }
            Text(RichText.inline(prompt)).font(Theme.body).foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            if isOpen {
                HStack(spacing: 8) {
                    Button("Allow") { decide("allow") }.primaryButton(compact: true).frame(maxWidth: .infinity)
                    Button("Always") { decide("always") }.secondaryButton(compact: true).frame(maxWidth: .infinity)
                    Button("Deny") { decide("deny") }.secondaryButton(compact: true, destructive: true).frame(maxWidth: .infinity)
                }
                .disabled(state == .submitting)
            }
            if case .failed(let note) = state {
                Text(note).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius)
            .strokeBorder(isOpen ? Theme.warning.opacity(0.5) : Theme.border, lineWidth: 1))
    }

    private var isOpen: Bool {
        switch state { case .pending, .submitting, .failed: true; default: false }
    }

    private var title: String {
        switch state {
        case .pending, .failed: "Approval needed"
        case .submitting: "Sending"
        case .queued: "Queued, waiting for the machine"
        case .resolved(let d?): d == "deny" ? "Denied" : d == "always" ? "Always allowed" : "Allowed"
        case .resolved(nil): "Answered"
        }
    }
}

// MARK: - Bottom bar

private struct AttachCard: View {
    let model: ChatModel
    let row: SessionRow?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 12) {
                Image(systemName: "eye").font(.system(size: 16)).foregroundStyle(Theme.textSecondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Watching only").font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    Text(subtitle).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if model.attachQueued {
                    ProgressView().controlSize(.small)
                } else if row?.canAttach == true {
                    Button {
                        Task { await model.attach() }
                    } label: {
                        if model.attaching { ProgressView().controlSize(.small).tint(Theme.primaryForeground) } else { Text("Attach") }
                    }
                    .primaryButton(compact: true)
                    .disabled(model.attaching)
                }
            }
            if let error = model.attachError {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
        }
        .padding(.horizontal, Theme.gutter)
        .padding(.vertical, 12)
    }

    private var subtitle: String {
        if model.attachQueued { return "Attach sent. Waiting for the machine to confirm." }
        guard let row else { return "Read-only." }
        if row.canAttach { return "Attach to send messages." }
        if row.sessionStatus == .active && row.attachable == true { return "Mid-turn. Attach once it's idle." }
        return "This harness can't be attached from the phone."
    }
}

private struct Composer: View {
    @Bindable var model: ChatModel
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 8) {
            HStack(spacing: 8) {
                Picker("Mode", selection: $model.mode) {
                    Text("Queue").tag(ChatModel.SendMode.queue)
                    Text("Steer").tag(ChatModel.SendMode.steer)
                }
                .pickerStyle(.segmented)
                .frame(width: 150)
                Spacer()
                Button { Task { await model.interrupt() } } label: { Label("Stop", systemImage: "stop.fill") }
                    .secondaryButton(compact: true)
            }
            HStack(alignment: .bottom, spacing: 8) {
                TextField("Message the session", text: $model.draft, axis: .vertical)
                    .font(Theme.body)
                    .lineLimit(1...5)
                    .focused($focused)
                    .padding(.horizontal, 14).padding(.vertical, 11)
                    .background(Theme.field, in: .rect(cornerRadius: 22))
                    .overlay(RoundedRectangle(cornerRadius: 22).strokeBorder(Theme.border, lineWidth: 1))
                Button {
                    Task { await model.send() }
                } label: {
                    Image(systemName: "arrow.up").font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(Theme.primaryForeground)
                        .frame(width: 40, height: 40)
                        .background(Theme.primaryBackground, in: .circle)
                }
                .buttonStyle(.plain)
                .disabled(model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .opacity(model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? 0.4 : 1)
                .accessibilityLabel("Send")
            }
        }
        .padding(.horizontal, Theme.gutter)
        .padding(.top, 10)
        .padding(.bottom, 8)
    }
}
