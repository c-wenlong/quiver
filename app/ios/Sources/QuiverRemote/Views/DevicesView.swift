import SwiftUI

/// P8: approve another device's pair request by typing its code, and
/// every device the hub has paired — with a Remove for the ones that
/// shouldn't be in any more (`POST /v1/devices/revoke`).
struct DevicesView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @State private var requests: [PendingPairRequest] = []
    @State private var devices: [PairedDevice] = []
    @State private var requestsLoaded = false
    @State private var devicesLoaded = false
    @State private var requestsError: String?
    @State private var devicesError: String?
    /// A device mid-revoke; its Remove button waits for the post.
    @State private var busyId: String?
    /// The device a Remove confirmation is open for.
    @State private var removing: PairedDevice?
    @State private var actionError: String?

    var body: some View {
        PageScroll {
            Text("Type the code shown on a new device to approve it. Devices paired before are below; remove any you don't recognise.")
                .font(Theme.body).foregroundStyle(Theme.textSecondary)
                .padding(.horizontal, 4)
            if !requestsLoaded {
                LoadingRow(text: "Loading requests")
            } else if let requestsError {
                StateCard(icon: "exclamationmark.triangle", title: "Couldn't load requests", message: requestsError, tone: .error,
                          actionTitle: "Try again") { Task { await loadRequests() } }
            } else if !requests.isEmpty {
                GroupLabel(text: "Pending requests")
                ForEach(requests) { r in
                    PairRequestCard(request: r) { await loadRequests() }
                }
            }
            GroupLabel(text: "Devices")
            if !devicesLoaded {
                LoadingRow(text: "Loading devices")
            } else if let devicesError {
                StateCard(icon: "exclamationmark.triangle", title: "Couldn't load devices", message: devicesError, tone: .error,
                          actionTitle: "Try again") { Task { await loadDevices() } }
            } else {
                CardList(data: devices) { device in row(device) }
            }
            if let actionError {
                Text(actionError).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
            }
        }
        .navigationTitle("Pair requests and devices")
        .navigationBarTitleDisplayMode(.large)
        .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        .refreshable { await load() }
        .confirmationDialog(removing.map { "Remove \($0.label)?" } ?? "Remove device?",
                            isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
                            titleVisibility: .visible, presenting: removing) { device in
            Button("Remove", role: .destructive) { Task { await revoke(device) } }
            Button("Cancel", role: .cancel) {}
        } message: { device in
            Text(device.isSatellite
                 ? "This machine's sessions stop reaching the hub; it would need a new pair token to come back."
                 : "It will need to pair again before it can see this hub.")
        }
        .task {
            while !Task.isCancelled {
                await load()
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    private func load() async {
        await loadRequests()
        await loadDevices()
    }

    private func loadRequests() async {
        guard let client = app.client else { return }
        do {
            requests = try await client.pairRequests()
            requestsError = nil
        } catch is CancellationError {
            // The page closed mid-fetch: nothing failed.
            return
        } catch {
            requestsError = error.localizedDescription
        }
        requestsLoaded = true
    }

    private func loadDevices() async {
        guard let client = app.client else { return }
        do {
            let listed = try await client.devices()
            // Live devices first; a revoked row stays listed so a Remove
            // shows it took, sorted by most recently seen.
            devices = listed.sorted {
                let a = selfDevice($0), b = selfDevice($1)
                if a != b { return a }
                if ($0.revoked == true) != ($1.revoked == true) { return $0.revoked != true }
                return ($0.lastSeenAt ?? 0) > ($1.lastSeenAt ?? 0)
            }
            devicesError = nil
        } catch is CancellationError {
            return
        } catch let error as HubError where error.status == 404 {
            devicesError = "This hub can't list devices yet. Update it, and this page shows everyone paired."
        } catch {
            devicesError = error.localizedDescription
        }
        devicesLoaded = true
    }

    /// Whether the row is the pairing this phone holds on the hub now.
    private func selfDevice(_ device: PairedDevice) -> Bool {
        device.thisDevice == true || device.id == app.hub?.credential.deviceId
    }

    @ViewBuilder private func row(_ device: PairedDevice) -> some View {
        HStack(spacing: 12) {
            Image(systemName: device.symbol).font(Theme.font(size: 16)).foregroundStyle(Theme.textSecondary).iconColumn(20)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(device.label).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                    if selfDevice(device) {
                        // The shared badge: its hairline keeps it visible on
                        // a light card, where the subtle fill alone vanished.
                        Badge("This device")
                    }
                }
                // Two lines: on one, a phone's OS and client used the
                // width and "last seen" was cut to "last seen 1…".
                Text(detail(of: device))
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            if device.revoked == true {
                Text("Removed").font(Theme.caption).foregroundStyle(Theme.textMuted)
            } else if !selfDevice(device) {
                Button {
                    removing = device
                } label: {
                    if busyId == device.id {
                        ProgressView().controlSize(.small)
                    } else {
                        Text("Remove").font(Theme.control).foregroundStyle(Theme.dangerText)
                    }
                }
                .buttonStyle(.plain)
                .disabled(busyId != nil)
            }
        }
        .padding(.vertical, Theme.rowVertical).padding(.horizontal, Theme.rowHorizontal)
    }

    /// The kind/OS/client bits plus when the hub last heard from it.
    private func detail(of device: PairedDevice) -> String {
        var bits = [device.isSatellite ? "machine" : nil, device.os, device.client, device.nodeName]
            .compactMap { $0 }
        let seen = RelativeTime.ago(device.lastSeenAt)
        bits.append(seen.isEmpty ? "never seen" : seen == "just now" ? "seen just now" : "last seen \(seen)")
        return bits.joined(separator: " · ")
    }

    private func revoke(_ device: PairedDevice) async {
        guard let client = app.client else { return }
        busyId = device.id
        actionError = nil
        defer { busyId = nil }
        do {
            try await client.revokeDevice(id: device.id, confirmThisDevice: device.thisDevice ?? false)
            // Mark it at once; the next poll's sort drops it to the foot.
            if let i = devices.firstIndex(where: { $0.id == device.id }) {
                devices[i].revoked = true
            }
        } catch {
            actionError = error.localizedDescription
        }
        await loadDevices()
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
                    Image(systemName: "iphone.gen3").font(Theme.font(size: 18)).foregroundStyle(Theme.textSecondary).iconColumn(22)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(request.name ?? "New device").font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                        Text([request.nodeName, request.login, RelativeTime.ago(request.createdAt)]
                            .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                    }
                    Spacer()
                }
                TextField("000 000", text: $code)
                    .keyboardType(.numberPad)
                    .multilineTextAlignment(.center)
                    .font(Theme.font(size: 20, weight: .medium, design: .monospaced))
                    .tracking(3)
                    .quiverField()
                    .onChange(of: code) { _, new in code = String(new.filter(\.isNumber).prefix(6)) }
                if let error { Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText) }
                HStack(spacing: 8) {
                    Button("Approve") { Task { await decide(true) } }.primaryButton()
                        .disabled(code.count != 6 || busy)
                    // The hub checks the code on a deny too, so a stray
                    // tap can't refuse someone else's request.
                    Button("Deny") { Task { await decide(false) } }.secondaryButton(destructive: true)
                        .disabled(code.count != 6 || busy)
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
            self.error = error.localizedDescription
        }
    }
}
