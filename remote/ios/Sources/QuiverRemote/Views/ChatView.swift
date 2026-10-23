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
            let m = ChatModel(key: key, app: app)
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
    @Bindable var model: ChatModel

    var body: some View {
        ScrollView(.vertical) {
            LazyVStack(alignment: .leading, spacing: 10) {
                if model.loading {
                    LoadingRow(text: "Loading transcript")
                } else if let error = model.loadError, model.items.isEmpty {
                    StateCard(icon: "exclamationmark.triangle", title: "Couldn't load this session", message: error, tone: .error)
                } else if model.items.isEmpty {
                    StateCard(icon: "text.bubble", title: "No messages yet", message: "New messages appear here as the agent works.")
                }
                ForEach(ChatEntry.fold(model.items)) { entry in
                    switch entry {
                    case .item(let item): ChatItemView(item: item, model: model)
                    case .toolRun(_, let items): ToolRunCard(items: items, model: model)
                    }
                }
            }
            .padding(.horizontal, Theme.gutter)
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .defaultScrollAnchor(.bottom)
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom) { bottomBar }
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
            if counts[call.name] == nil { order.append(call.name) }
            counts[call.name, default: 0] += 1
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
            RichText(text: text).frame(maxWidth: .infinity, alignment: .leading)
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

/// Prose with inline markdown, and fenced code in mono blocks. Every
/// line wraps: nothing in a chat scrolls sideways.
struct RichText: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(MessageBlocks.split(text).enumerated()), id: \.offset) { _, block in
                switch block {
                case .prose(let s):
                    Text(Self.inline(s)).font(Theme.body).foregroundStyle(Theme.textPrimary)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                case .code(let s):
                    Text(s).font(Theme.mono).foregroundStyle(Theme.textLabel)
                        .textSelection(.enabled)
                        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                        .background(Theme.code, in: .rect(cornerRadius: Theme.controlRadius))
                        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))
                }
            }
        }
    }

    static func inline(_ s: String) -> AttributedString {
        (try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(s)
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
                if row?.canAttach == true {
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
