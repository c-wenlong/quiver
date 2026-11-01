import SwiftUI

/// Rename from the Sessions list: the name is kept on the hub, so every
/// paired device and the web app show it.
struct RenameSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let row: SessionRow

    @State private var text: String
    @State private var saving = false
    @State private var error: String?
    @FocusState private var focused: Bool

    init(row: SessionRow) {
        self.row = row
        _text = State(initialValue: RenameRules.clamp(row.title ?? ""))
    }

    var body: some View {
        NavigationStack {
            PageScroll {
                GroupLabel(text: "Name", trailing: "\(RenameRules.length(text))/\(RenameRules.maxLength)")
                TextField(row.harnessTitle.flatMap { $0.isEmpty ? nil : $0 } ?? "Name this session", text: $text)
                    .sweField()
                    .focused($focused)
                    .submitLabel(.done)
                    .onSubmit { if RenameRules.canSave(text, current: row.title) { save(RenameRules.normalized(text)) } }
                    .onChange(of: text) { _, new in
                        let clamped = RenameRules.clamp(new)
                        if clamped != new { text = clamped }
                    }
                    .disabled(saving)
                Text("Every device paired with this hub shows this name.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary).padding(.horizontal, 4)
                if row.hasCustomTitle {
                    Button { save("") } label: { Text("Use the original name") }
                        .secondaryButton()
                        .disabled(saving)
                        .padding(.top, 8)
                    if let original = row.harnessTitle, !original.isEmpty {
                        Text("Original: \(original)")
                            .font(Theme.caption).foregroundStyle(Theme.textMuted).lineLimit(2)
                            .padding(.horizontal, 4)
                    }
                }
                if let error {
                    Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                        .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
                }
            }
            .navigationTitle("Rename")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    if saving {
                        ProgressView().controlSize(.small)
                    } else {
                        Button("Save") { save(RenameRules.normalized(text)) }
                            .disabled(!RenameRules.canSave(text, current: row.title))
                    }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .interactiveDismissDisabled(saving)
        .onAppear { focused = true }
    }

    private func save(_ title: String) {
        saving = true
        error = nil
        Task {
            do {
                try await app.rename(row.key, title: title)
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
            saving = false
        }
    }
}

/// Fork from the Sessions list: a new session that continues from this
/// one's history, with a first message, opened once the hub names it.
struct ForkSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let row: SessionRow
    var onStarted: (String) -> Void

    @State private var prompt = ""
    @State private var flow = LaunchFlow()
    /// The running fork, so Cancel or a swipe-down stops the polling
    /// before it can open a chat behind a sheet that is gone.
    @State private var launch: Task<Void, Never>?

    var body: some View {
        NavigationStack {
            ForkForm(row: row, prompt: $prompt, phase: flow.phase, busy: flow.busy) {
                launch = Task { await start() }
            }
            .navigationTitle("Fork \(row.displayTitle)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        launch?.cancel()
                        dismiss()
                    }
                }
            }
        }
        .interactiveDismissDisabled(flow.busy)
        .onDisappear { launch?.cancel() }
    }

    private func start() async {
        guard let client = app.client else { return }
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        let key = await flow.run(
            post: { try await client.fork(row.key, prompt: text) },
            fetch: { try await client.launch($0) })
        guard let key, !Task.isCancelled else { return }
        await app.refresh()
        guard !Task.isCancelled else { return }
        onStarted(key)
        dismiss()
    }
}

/// The fork sheet's body, apart from its navigation so it renders alone.
struct ForkForm: View {
    let row: SessionRow
    @Binding var prompt: String
    var phase: LaunchFlow.Phase
    var busy: Bool
    var start: () -> Void

    var body: some View {
        PageScroll { ForkFields(row: row, prompt: $prompt, phase: phase, busy: busy, start: start) }
    }
}

struct ForkFields: View {
    let row: SessionRow
    @Binding var prompt: String
    var phase: LaunchFlow.Phase
    var busy: Bool
    var start: () -> Void

    var body: some View {
        let ready = !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        Group {
            GroupLabel(text: "What should the fork do?")
            TextField("Its first message", text: $prompt, axis: .vertical)
                .lineLimit(4...10)
                .font(Theme.body)
                .foregroundStyle(Theme.textPrimary)
                .padding(12)
                .background(Theme.field, in: .rect(cornerRadius: Theme.blockRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
                .disabled(busy)
            Text("The fork starts from this session's history and leaves the original as it is. It needs a first message: the harness writes nothing until it gets one.")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 4)
            Button(action: start) {
                HStack(spacing: 8) {
                    if busy { ProgressView().controlSize(.small).tint(Theme.primaryForeground) }
                    Text(phase == .waiting ? "Waiting for the fork" : busy && phase != .starting ? "Opening the chat" : busy ? "Starting" : "Start")
                }
            }
            .primaryButton()
            .disabled(!ready || busy)
            .padding(.top, 8)
            if case .failed(let message) = phase {
                Text(message).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
            } else if case .unconfirmed(let message) = phase {
                Text(message).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("Runs under tmux in \(SessionGrouping.shortDir(row.cwd)). The chat opens once the fork shows up.")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 4)
        }
    }
}

/// A short note at the foot of the Sessions list, with an optional
/// action such as Undo. It goes away by itself after a few seconds.
struct ListToast: Identifiable, Equatable {
    let id = UUID()
    var message: String
    var actionTitle: String?
    var action: (() -> Void)?
    var duration: Duration = .seconds(4)

    static func == (a: ListToast, b: ListToast) -> Bool { a.id == b.id }
}

struct ToastView: View {
    var toast: ListToast
    var dismiss: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Text(toast.message)
                .font(Theme.caption)
                .foregroundStyle(Theme.primaryForeground)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            if let title = toast.actionTitle, let action = toast.action {
                Button {
                    dismiss()
                    action()
                } label: {
                    Text(title).font(Theme.font(size: 14, weight: .semibold)).foregroundStyle(Theme.primaryForeground)
                        .padding(.horizontal, 6).frame(minHeight: 32).contentShape(.rect)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.leading, 16).padding(.trailing, 10).padding(.vertical, 10)
        .background(Theme.primaryBackground, in: .rect(cornerRadius: Theme.blockRadius))
        .shadow(color: .black.opacity(0.18), radius: 12, y: 4)
        .padding(.horizontal, Theme.gutter)
        .padding(.bottom, 12)
    }
}
