import QuiverCore
import SwiftUI

/// One session read live from this Mac's hub, swapped in over the
/// Sessions list the way the web app opens its chat: the transcript so
/// far, then each message as the agent writes it.
///
/// Back is the header's Sessions button, ⌘[, or picking the Sessions
/// sidebar item again.
struct SessionChatPage: View {
    @Environment(AppModel.self) private var app
    /// The Sessions row it opened from: the header until the hub's row
    /// loads, and what Details shows.
    var row: SessionsModel.Row
    var onBack: () -> Void
    var onDetails: () -> Void
    @State private var model: SessionChatModel?

    private var key: String { HubSessionRow.key(harness: row.session.harness, id: row.session.id) }

    var body: some View {
        Page(title: row.session.title ?? model?.row?.title ?? row.session.id,
             subtitle: subtitle, maxWidth: 960, scrolls: false) {
            Button { onBack() } label: {
                Label("Sessions", systemImage: "chevron.left")
            }
            .secondaryButton()
            .keyboardShortcut("[", modifiers: .command)
            .help("Back to all sessions (⌘[)")
            if let model { ChatBadges(model: model) }
            Button("Details", action: onDetails)
                .secondaryButton()
                .help("Folder, usage and the resume command")
        } content: {
            if let model {
                ChatTranscript(model: model)
            } else {
                Spacer()
            }
        }
        .task(id: key) {
            model?.stop()
            let chat = SessionChatModel(key: key, hub: app.hub)
            model = chat
            await chat.start()
        }
        .onDisappear { model?.stop() }
    }

    /// harness · folder, as the web app's chat bar has it.
    private var subtitle: String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let folder = (model?.row?.cwd ?? row.session.cwd).map {
            $0.hasPrefix(home + "/") ? "~" + $0.dropFirst(home.count) : $0
        }
        return [HarnessMark(row.session.harness).name, folder].compactMap { $0 }.joined(separator: " · ")
    }
}

/// Status, ownership and the stream's state, beside the header's buttons.
private struct ChatBadges: View {
    var model: SessionChatModel

    var body: some View {
        if let row = model.row {
            if let status = Self.status(row.status) { Badge(status) }
            Badge(row.isAttached ? "Attached" : "Observed")
                .hoverTip(row.isAttached ? "Runs under Quiver: you can send to it."
                          : "Owned by its terminal: read-only here.")
        }
        HStack(spacing: 6) {
            Circle()
                .fill(model.connection == .live ? Theme.success
                      : model.connection == .reconnecting ? Theme.warning : Theme.textMuted)
                .frame(width: Theme.statusDot, height: Theme.statusDot)
            Text(model.connection == .live ? "Live"
                 : model.connection == .reconnecting ? "Reconnecting…" : "Connecting…")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
        }
        .padding(.horizontal, 4)
    }

    /// The web app's chat labels for the hub's statuses.
    static func status(_ raw: String?) -> String? {
        switch raw {
        case "active": "Active"
        case "done": "Done"
        case "followup": "Waiting"
        case "error": "Error"
        case "interrupted": "Interrupted"
        case "approval": "Approval needed"
        default: nil
        }
    }
}

private struct ChatTranscript: View {
    var model: SessionChatModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let error = model.loadError {
                ErrorCard(message: error, actionTitle: "Try again") {
                    Task { await model.start() }
                }
            }
            if let row = model.row, !row.isAttached {
                ObservedBanner(row: row)
            }
            if model.loading {
                LoadingCard(text: "Loading the transcript…")
                Spacer()
            } else if model.loadError == nil {
                log
            } else {
                Spacer()
            }
        }
        .frame(maxHeight: .infinity, alignment: .top)
    }

    private var log: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 14) {
                if model.hasEarlier {
                    Button(model.loadingEarlier ? "Loading…" : "Show earlier messages") {
                        Task { await model.loadEarlier() }
                    }
                    .secondaryButton()
                    .disabled(model.loadingEarlier)
                    .frame(maxWidth: .infinity)
                }
                if model.items.isEmpty {
                    Text("No messages yet.")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                }
                ForEach(model.items) { item in
                    ChatEntry(item: item)
                }
                if model.working {
                    WorkingDots().accessibilityLabel("Agent is working")
                }
            }
            .padding(.vertical, 4)
            .padding(.trailing, 12)
        }
        .scrollContentBackground(.hidden)
        // Opens on the newest message, and stays there as more arrive.
        .defaultScrollAnchor(.bottom)
        .defaultScrollAnchor(.bottom, for: .sizeChanges)
    }
}

/// An observed session is read-only here; the web app says why above
/// its transcript.
private struct ObservedBanner: View {
    var row: HubSessionRow

    var body: some View {
        Card {
            Label(text, systemImage: "eye")
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
        }
    }

    private var text: String {
        if row.attachable != true { return "Observed session: read-only (this harness can't be attached)." }
        if row.status == "active" { return "Observed session: mid-turn. Attach once it is idle." }
        return "Observed session: read-only until attached."
    }
}

private struct ChatEntry: View {
    var item: HubChatItem

    var body: some View {
        switch item.kind {
        case .user(let text):
            HStack {
                Spacer(minLength: 120)
                MarkdownBlocks(blocks: GitHubMarkdown.blocks(text))
                    .font(Theme.bodySmall)
                    .foregroundStyle(Theme.textPrimary)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(Theme.subtle, in: .rect(cornerRadius: Theme.blockRadius))
                    .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
            }
        case .assistant(let text):
            MarkdownBlocks(blocks: GitHubMarkdown.blocks(text))
                .font(Theme.bodySmall)
                .foregroundStyle(Theme.textPrimary)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .thinking(let text):
            Fold(title: Text("Thought").foregroundStyle(Theme.textSecondary)) {
                MarkdownBlocks(blocks: GitHubMarkdown.blocks(text))
                    .font(Theme.bodySmall)
                    .foregroundStyle(Theme.textSecondary)
            }
        case .tool(let call, let result):
            ToolCard(call: call, result: result)
        case .approval(_, let prompt, let decision, let resolved):
            ApprovalCard(prompt: prompt, decision: decision, resolved: resolved)
        case .compaction:
            Text("Context compacted")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .frame(maxWidth: .infinity)
        case .system(let text):
            Text(text)
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .error(let text):
            VStack(alignment: .leading, spacing: 4) {
                Text("Error").font(Theme.caption).foregroundStyle(Theme.dangerText)
                MarkdownBlocks(blocks: GitHubMarkdown.blocks(text))
                    .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            }
        }
    }
}

/// A chevron and a title that open what's under them, closed at first.
private struct Fold<Title: View, Content: View>: View {
    var title: Title
    @ViewBuilder var content: Content
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: Theme.fadeDuration)) { open.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "chevron.right")
                        .font(Theme.font(size: 9, weight: .semibold))
                        .foregroundStyle(Theme.textMuted)
                        .rotationEffect(open ? .degrees(90) : .zero)
                    title.font(Theme.caption)
                }
                .contentShape(.rect)
            }
            .plainButton()
            if open {
                content.padding(.leading, 15)
            }
        }
    }
}

/// A tool call: its name and argument summary, opening to the full
/// arguments and the output once it lands.
private struct ToolCard: View {
    var call: HubToolCall
    var result: String?

    var body: some View {
        Fold(title: HStack(spacing: 8) {
            Text(call.name).font(Theme.font(size: 12, weight: .medium)).foregroundStyle(Theme.textPrimary)
            if let summary = Self.summary(call), !summary.isEmpty {
                Text(summary)
                    .font(Theme.font(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.textSecondary)
                    .lineLimit(1).truncationMode(.middle)
            }
        }) {
            VStack(alignment: .leading, spacing: 8) {
                if let full = call.argsFull, !full.isEmpty { CodeBlock(text: String(full.prefix(8000))) }
                if let result {
                    Text("Output").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    CodeBlock(text: result.isEmpty ? "(no output)" : result)
                }
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))
    }
}

extension ToolCard {
    /// The hub's summary leads with the tool's name ("Bash · make test"),
    /// which the card already shows.
    static func summary(_ call: HubToolCall) -> String? {
        guard let summary = call.argsSummary else { return nil }
        let lead = call.name + " · "
        return summary.hasPrefix(lead) ? String(summary.dropFirst(lead.count)) : summary
    }
}

private struct CodeBlock: View {
    var text: String

    var body: some View {
        Text(text)
            .font(Theme.font(size: 11, design: .monospaced))
            .foregroundStyle(Theme.textPrimary)
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.code, in: .rect(cornerRadius: 6))
    }
}

/// An approval the agent asked for, and how it was answered.
private struct ApprovalCard: View {
    var prompt: String
    var decision: String?
    var resolved: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Approval needed").font(Theme.caption).foregroundStyle(Theme.warningText)
            MarkdownBlocks(blocks: GitHubMarkdown.blocks(prompt))
                .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            Text(outcome).font(Theme.caption).foregroundStyle(Theme.textSecondary)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius)
            .strokeBorder(resolved ? Theme.border : Theme.warning, lineWidth: 1))
    }

    private var outcome: String {
        guard resolved else { return "Waiting for an answer" }
        switch decision {
        case "allow": return "Allowed"
        case "always": return "Always allowed"
        case "deny": return "Denied"
        default: return "Answered"
        }
    }
}

/// Three dots that pulse while the agent works.
private struct WorkingDots: View {
    var body: some View {
        TimelineView(.periodic(from: .now, by: 0.35)) { context in
            let step = Int(context.date.timeIntervalSinceReferenceDate / 0.35) % 3
            HStack(spacing: 5) {
                ForEach(0..<3, id: \.self) { i in
                    Circle()
                        .fill(Theme.textMuted)
                        .frame(width: 6, height: 6)
                        .opacity(i == step ? 1 : 0.35)
                }
            }
        }
        .padding(.vertical, 4)
    }
}
