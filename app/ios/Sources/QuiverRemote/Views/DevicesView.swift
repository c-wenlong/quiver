import SwiftUI

/// P8: approve another device's pair request by typing its code.
/// (Listing and removing paired devices needs a hub route; see
/// app/ios/ROADMAP.md, P8.)
struct DevicesView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @State private var requests: [PendingPairRequest] = []
    @State private var loaded = false
    @State private var error: String?

    var body: some View {
        PageScroll {
            Text("Type the code shown on the new device.")
                .font(Theme.body).foregroundStyle(Theme.textSecondary)
                .padding(.horizontal, 4)
            if !loaded {
                LoadingRow(text: "Loading requests")
            } else if let error {
                StateCard(icon: "exclamationmark.triangle", title: "Couldn't load requests", message: error, tone: .error,
                          actionTitle: "Try again") { Task { await load() } }
            } else if requests.isEmpty {
                StateCard(icon: "iphone.and.arrow.forward", title: "No pending requests",
                          message: "Start pairing on the new device; its request shows up here.")
            } else {
                GroupLabel(text: "Pending requests")
                ForEach(requests) { r in
                    PairRequestCard(request: r) { await load() }
                }
            }
            GroupLabel(text: "This device")
            CardList(data: [ThisDevice(app: app)]) { d in
                HStack(spacing: 12) {
                    Image(systemName: "iphone").font(.system(size: 16)).foregroundStyle(Theme.textSecondary).frame(width: 20)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(d.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                        Text(d.detail).font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                    }
                    Spacer()
                }
                .padding(.vertical, Theme.rowVertical).padding(.horizontal, Theme.rowHorizontal)
            }
        }
        .navigationTitle("Pair requests")
        .navigationBarTitleDisplayMode(.large)
        .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        .refreshable { await load() }
        .task {
            while !Task.isCancelled {
                await load()
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    private func load() async {
        guard let client = app.client else { return }
        do {
            requests = try await client.pairRequests()
            error = nil
        } catch {
            self.error = (error as? HubError)?.message ?? error.localizedDescription
        }
        loaded = true
    }
}

private struct ThisDevice: Identifiable {
    let id = "this"
    let name: String
    let detail: String
    @MainActor init(app: AppModel) {
        name = app.hub?.credential.name ?? "This iPhone"
        detail = "Paired with \(app.hub?.baseURL.host ?? "the hub")"
    }
}

private struct PairRequestCard: View {
    @Environment(AppModel.self) private var app
    let request: PendingPairRequest
    let reload: () async -> Void
    @State private var code = ""
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 12) {
                    Image(systemName: "iphone.gen3").font(.system(size: 18)).foregroundStyle(Theme.textSecondary).frame(width: 22)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(request.name ?? "New device").font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                        Text([request.nodeName, request.login, request.createdAt.map { "\(RelativeTime.short($0)) ago" }]
                            .compactMap { $0 }.joined(separator: " · "))
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                    }
                    Spacer()
                }
                TextField("000 000", text: $code)
                    .keyboardType(.numberPad)
                    .multilineTextAlignment(.center)
                    .font(.system(size: 20, weight: .medium, design: .monospaced))
                    .tracking(3)
                    .quiverField()
                    .onChange(of: code) { _, new in code = String(new.filter(\.isNumber).prefix(6)) }
                if let error { Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText) }
                HStack(spacing: 8) {
                    Button("Approve") { Task { await decide(true) } }.primaryButton()
                        .disabled(code.count != 6 || busy)
                    Button("Deny") { Task { await decide(false) } }.secondaryButton(destructive: true)
                        .disabled(busy)
                }
            }
        }
    }

    private func decide(_ approve: Bool) async {
        guard let client = app.client else { return }
        busy = true
        defer { busy = false }
        do {
            try await client.decidePairRequest(id: request.requestId, code: code, approve: approve)
            await reload()
        } catch {
            self.error = (error as? HubError)?.message ?? error.localizedDescription
        }
    }
}
