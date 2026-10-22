import CoreImage.CIFilterBuiltins
import QuiverCore
import SwiftUI

/// Settings › Remote: Clockwork-style pairing card. Shows whether the hub
/// is reachable, lists paired devices, and mints a one-time pair token it
/// renders as a QR code — the phone scans it to reach the PWA or the iOS
/// wrapper over Tailscale.
struct RemoteSection: View {
    @Environment(AppModel.self) private var app
    @State private var model: RemoteModel?
    /// Which payload the QR encodes while the ticket sheet is up.
    fileprivate enum PairTarget: String, CaseIterable, Identifiable {
        case web, app
        var id: Self { self }
        var title: String { self == .web ? "Browser" : "Quiver app" }
    }
    @State private var target: PairTarget = .web

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            hubCard
            devicesCard
        }
        .task(id: app.swePath) {
            let remote = RemoteModel(swe: app.swe)
            model = remote
            await remote.load()
        }
        .sheet(isPresented: showTicket, onDismiss: { model?.dismissTicket() }) {
            if let model { PairSheet(model: model, target: $target) }
        }
    }

    private var showTicket: Binding<Bool> {
        Binding(get: { model?.ticket != nil },
                set: { if !$0 { model?.dismissTicket() } })
    }

    private var hubCard: some View {
        Card(title: "Hub") {
            switch model?.status ?? .idle {
            case .idle, .loading:
                SettingRow(title: "Checking hub…", detail: "swe remote status") {
                    ProgressView().controlSize(.small)
                }
            case .failed(let message):
                SettingRow(title: "Status failed", detail: message) {
                    Button("Try again", action: reload).secondaryButton()
                }
            case .loaded(let status):
                if status.hub.ok {
                    SettingRow(title: "Hub running",
                               detail: hubDetail(status)) {
                        Badge("Online")
                    }
                } else {
                    SettingRow(title: "No hub reachable",
                               detail: "Run `swe remote serve` on the machine that should host your phone sessions, then refresh.") {
                        Button("Refresh", action: reload).secondaryButton()
                    }
                }
            }
        }
    }

    private func hubDetail(_ status: RemoteStatus) -> String {
        var bits: [String] = []
        if let name = status.hub.machine?.name, !name.isEmpty { bits.append(name) }
        if let addr = status.hubAddr, !addr.isEmpty { bits.append(addr) }
        if let uptime = status.hub.uptimeS {
            bits.append("up \(Self.uptime(uptime))")
        }
        return bits.isEmpty ? "Connected" : bits.joined(separator: " · ")
    }

    static func uptime(_ seconds: Double) -> String {
        let s = Int(seconds)
        if s < 60 { return "\(s)s" }
        if s < 3600 { return "\(s / 60)m" }
        if s < 86400 { return "\(s / 3600)h \((s % 3600) / 60)m" }
        return "\(s / 86400)d \((s % 86400) / 3600)h"
    }

    private var devicesCard: some View {
        Card(title: "Paired devices") {
            switch model?.status ?? .idle {
            case .idle, .loading:
                SettingRow(title: "Loading devices…", detail: "") {
                    ProgressView().controlSize(.small)
                }
            case .failed:
                EmptyView()
            case .loaded(let status):
                let live = status.devices.filter { !$0.revoked }
                if live.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("No devices paired yet.")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        pairButton
                    }
                } else {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(live.enumerated()), id: \.element.id) { index, device in
                            if index > 0 { Divider().overlay(Theme.border) }
                            DeviceRow(device: device, model: model)
                        }
                        Divider().overlay(Theme.border)
                        pairButton.padding(.vertical, 10)
                    }
                }
            }
        }
    }

    private var pairButton: some View {
        HStack(spacing: 10) {
            Button {
                Task { await model?.mintTicket() }
            } label: {
                if model?.pairing == true {
                    ProgressView().controlSize(.small)
                } else {
                    Label("Pair a device", systemImage: "qrcode")
                }
            }
            .primaryButton()
            .disabled(model?.pairing == true || hubDown)
            if let error = model?.pairError {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
        }
    }

    private var hubDown: Bool {
        if case .loaded(let status) = model?.status { return !status.hub.ok }
        return false
    }

    private func reload() {
        Task { await model?.load() }
    }
}

private struct DeviceRow: View {
    var device: RemoteStatus.Device
    var model: RemoteModel?

    var body: some View {
        SettingRow(title: device.name?.isEmpty == false ? device.name! : "Device",
                   detail: detail,
                   warning: model?.revokeErrors[device.id],
                   danger: true) {
            if model?.revoking.contains(device.id) == true {
                ProgressView().controlSize(.small)
            } else {
                Button("Revoke") {
                    Task { await model?.revoke(device) }
                }
                .secondaryButton()
                .hoverTip("Sign this device out of the hub")
            }
        }
    }

    private var detail: String {
        var bits = [device.id]
        if let last = device.lastSeenAt {
            bits.append("seen \(last.formatted(.relative(presentation: .named)))")
        }
        return bits.joined(separator: " · ")
    }
}

/// The pair sheet: QR plus a copyable link and the token's countdown.
private struct PairSheet: View {
    @Bindable var model: RemoteModel
    @Binding var target: RemoteSection.PairTarget
    /// Ticks the countdown once a second.
    @State private var now = Date()
    private let tick = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("Pair a device").font(Theme.cardTitle).foregroundStyle(Theme.textStrong)
                Spacer()
                Button("Done") { model.dismissTicket() }.secondaryButton()
            }
            if let ticket = model.ticket {
                HStack(alignment: .top, spacing: 18) {
                    QRCode(payload: payload(ticket))
                        .frame(width: 172, height: 172)
                        .padding(8)
                        .background(.white, in: .rect(cornerRadius: Theme.cardRadius))
                    VStack(alignment: .leading, spacing: 10) {
                        Picker("", selection: $target) {
                            ForEach(RemoteSection.PairTarget.allCases) { t in
                                Text(t.title).tag(t)
                            }
                        }
                        .pickerStyle(.segmented).labelsHidden().frame(width: 220)
                        Text(instructions).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(payload(ticket))
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(Theme.textPrimary)
                            .textSelection(.enabled)
                            .lineLimit(3)
                        HStack(spacing: 8) {
                            CopyButton(title: "Copy link", help: "Copy the pairing link",
                                       iconSize: 12) { copyToPasteboard(payload(ticket)) }
                                .secondaryButton()
                            countdown(ticket)
                        }
                    }
                }
            }
        }
        .padding(20)
        .frame(width: 480)
        .onReceive(tick) { now = $0 }
    }

    private func payload(_ ticket: PairTicket) -> String {
        target == .web ? ticket.url : ticket.deepLink
    }

    private var instructions: String {
        switch target {
        case .web:
            "On your phone, scan the code to open the remote in a browser, then Add to Home Screen to keep it."
        case .app:
            "On your phone, scan the code to open the pairing link in the Quiver app."
        }
    }

    @ViewBuilder
    private func countdown(_ ticket: PairTicket) -> some View {
        if let left = model.ticketSecondsLeft(at: now) {
            if left > 0 {
                Label("Expires in \(left / 60):\(String(format: "%02d", left % 60))",
                      systemImage: "clock")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            } else {
                Button("Expired — mint a new code") {
                    Task { await model.mintTicket() }
                }
                .secondaryButton()
            }
        }
    }
}

/// A `CIQRCodeGenerator` rendering, crisp at any size.
private struct QRCode: View {
    var payload: String
    /// Point size the bitmap is rendered for before scaling.
    private static let extent: CGFloat = 512

    var body: some View {
        if let image = render() {
            Image(nsImage: image)
                .resizable()
                .interpolation(.none)
                .aspectRatio(1, contentMode: .fit)
                .accessibilityLabel("Pairing QR code")
        } else {
            Label("QR failed", systemImage: "exclamationmark.triangle")
                .foregroundStyle(Theme.textSecondary)
        }
    }

    private func render() -> NSImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(payload.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage else { return nil }
        let scale = Self.extent / output.extent.width
        let scaled = output.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        let rep = NSCIImageRep(ciImage: scaled)
        let image = NSImage(size: rep.size)
        image.addRepresentation(rep)
        return image
    }
}
