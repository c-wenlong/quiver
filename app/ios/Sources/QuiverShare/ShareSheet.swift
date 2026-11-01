import SwiftUI

/// The compact sheet: hub (when there's more than one), session, message,
/// what's being shared, Send. The rules live in `ShareModel`.
struct ShareSheet: View {
    @Bindable var model: ShareModel
    let close: (_ sent: Bool) -> Void

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("Send to Quiver")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { close(false) }
                            .disabled(isSending)
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        if model.phase == .ready || isSending {
                            Button("Send") { Task { await sendThenClose() } }
                                .disabled(!model.canSend)
                        }
                    }
                }
        }
    }

    private var isSending: Bool {
        if case .sending = model.phase { return true }
        return false
    }

    @ViewBuilder private var content: some View {
        switch model.phase {
        case .loading:
            ProgressView("Loading sessions")
        case .notPaired(let why):
            VStack {
                hubMenu
                ContentUnavailableView("Not paired", systemImage: "link.badge.plus", description: Text(why))
            }
        case .unreachable(let why):
            VStack {
                hubMenu
                ContentUnavailableView {
                    Label("Can't reach the hub", systemImage: "wifi.exclamationmark")
                } description: {
                    Text(why)
                } actions: {
                    Button("Try again") { Task { await model.refresh() } }
                }
            }
        case .sent:
            ContentUnavailableView("Sent to the hub", systemImage: "checkmark.circle", description: Text(model.sentNote))
        case .ready, .sending:
            form
        }
    }

    private var hubPicker: some View {
        Picker("Hub", selection: Binding(
            get: { model.hub?.id ?? "" },
            set: { id in Task { await model.selectHub(id) } })) {
            ForEach(model.hubs.hubs) { Text(model.hubs.name(of: $0)).tag($0.id) }
        }
    }

    /// On an error, another paired hub is still one tap away.
    @ViewBuilder private var hubMenu: some View {
        if model.hubs.hubs.count > 1 {
            hubPicker.pickerStyle(.menu).padding(.top)
        }
    }

    private var form: some View {
        Form {
            if model.hubs.hubs.count > 1 {
                Section("Hub") { hubPicker }
            }
            Section("Message") {
                TextField("Add a message (optional)", text: $model.message, axis: .vertical)
                    .lineLimit(2...5)
            }
            if !model.payloads.isEmpty || !model.notices.isEmpty {
                Section("Sharing") {
                    ForEach(Array(model.payloads.enumerated()), id: \.offset) { _, payload in
                        SharedRow(payload: payload)
                    }
                    ForEach(model.notices, id: \.self) { Text($0).foregroundStyle(.orange).font(.footnote) }
                }
            }
            Section {
                if model.sessions.isEmpty {
                    Text(ShareModel.noSessionsNote).foregroundStyle(.secondary)
                }
                ForEach(model.sessions) { row in
                    Button { model.selectedKey = row.key } label: { SessionPick(row: row, picked: row.key == model.selectedKey) }
                        .buttonStyle(.plain)
                }
            } header: {
                Text("Session")
            } footer: {
                footer
            }
        }
        .disabled(isSending)
    }

    @ViewBuilder private var footer: some View {
        if case .sending(let what) = model.phase {
            HStack(spacing: 8) { ProgressView(); Text(what) }
        } else if let error = model.sendError {
            Text(error).foregroundStyle(.red)
        } else if let why = model.blocker, !model.sessions.isEmpty {
            Text(why)
        }
    }

    private func sendThenClose() async {
        guard await model.send() else { return }
        try? await Task.sleep(for: .milliseconds(600))
        close(true)
    }
}

private struct SessionPick: View {
    let row: SessionRow
    let picked: Bool

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(row.displayTitle).lineLimit(1)
                Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            if picked { Image(systemName: "checkmark").foregroundStyle(.tint) }
        }
        .contentShape(Rectangle())
    }

    private var detail: String {
        var parts = [row.harnessName]
        if let cwd = row.cwd, !cwd.isEmpty { parts.append((cwd as NSString).lastPathComponent) }
        if let at = row.lastActiveAt {
            parts.append(Date(timeIntervalSince1970: at).formatted(.relative(presentation: .named)))
        }
        return parts.joined(separator: " · ")
    }
}

private struct SharedRow: View {
    let payload: SharedPayload

    var body: some View {
        switch payload {
        case .text(let s):
            Label(s, systemImage: "text.quote").lineLimit(2)
        case .link(let url):
            Label(url.absoluteString, systemImage: "link").lineLimit(1)
        case .image(let name, _), .imageData(let name, _):
            Label(name, systemImage: "photo").lineLimit(1)
        case .file(let name, _, let size):
            Label("\(name) (\(AttachmentRules.size(size)))", systemImage: "doc").lineLimit(1)
        }
    }
}
