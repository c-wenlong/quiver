import SwiftUI

/// P1 to P3: welcome, pair this device, and the approve-code wait.
struct WelcomeView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    /// Opened from the hub menu to pair one more hub: it can be cancelled,
    /// and it closes once the new hub is on screen.
    var addingHub = false
    @State private var path: [PairStep] = []
    @State private var scanning = false
    /// The hub on screen when the sheet opened.
    @State private var startedOn: PairedHub?

    enum PairStep: Hashable { case pair, token }

    var body: some View {
        NavigationStack(path: $path) {
            VStack(spacing: 0) {
                Spacer()
                VStack(spacing: 16) {
                    Image("AppMark")
                        .resizable()
                        .frame(width: 84, height: 84)
                        .clipShape(.rect(cornerRadius: 20))
                        .overlay(RoundedRectangle(cornerRadius: 20).strokeBorder(Theme.border, lineWidth: 1))
                    Text(addingHub ? "Pair another hub" : "SWE Remote").font(Theme.pageTitle).tracking(-0.6).foregroundStyle(Theme.textPrimary)
                    Text(addingHub ? "Add another machine running a SWE hub. Switch between hubs from the menu at the top right."
                                   : "Watch and steer your agent sessions from your phone, over Tailscale.")
                        .font(Theme.body).foregroundStyle(Theme.textSecondary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, 32)
                Spacer()
                VStack(spacing: 10) {
                    Button("Pair with Tailscale") { path.append(.pair) }.primaryButton()
                    Button { scanning = true } label: { Label("Scan pair QR", systemImage: "qrcode.viewfinder") }
                        .secondaryButton()
                    if let error = app.pairing.error {
                        Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText).multilineTextAlignment(.center)
                    }
                    // Where the code comes from: the Mac app or the hub's web
                    // app (both have Settings › Remote), else the CLI on a
                    // server with neither.
                    VStack(spacing: 4) {
                        Text("On the hub machine, open SWE › Settings › Remote › Pair a device and scan its code.")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        (Text("No SWE app there? Run ") + Text("swe remote setup").font(Theme.mono)
                            + Text(", then ") + Text("swe remote pair --app").font(Theme.mono) + Text("."))
                            .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    }
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 6)
                }
                .padding(.horizontal, Theme.gutter)
                .padding(.bottom, 24)
            }
            .frame(maxWidth: .infinity)
            .background(Theme.wash)
            .navigationDestination(for: PairStep.self) { step in
                switch step {
                case .pair: PairView(path: $path)
                case .token: TokenPairView()
                }
            }
            .sheet(isPresented: $scanning) {
                // Scanning is the person picking this hub, so a scanned
                // code pairs without the confirm a link from outside gets.
                QRScannerSheet { url in
                    scanning = false
                    guard let link = PairLink(url) else { return }
                    Task { await app.pair(with: link) }
                }
            }
            .overlay { if app.pairing.busy && app.pairing.pending == nil { BusyOverlay() } }
            .toolbar {
                if addingHub {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") {
                            app.cancelPairing()
                            dismiss()
                        }
                    }
                }
            }
        }
        .tint(Theme.textPrimary)
        // Open over the hub menu, this sheet is where a pair link's
        // confirm can show; under it, RootView's can't.
        .pairOfferSheet(enabled: addingHub)
        .onAppear { startedOn = app.hub }
        // The new hub is on screen (paired by any route: Tailscale, token,
        // scanned code or a link): the sheet's job is done.
        // Pairing the same hub again counts too: it gets a new credential.
        .onChange(of: app.hub) { _, hub in
            if addingHub, hub != startedOn { dismiss() }
        }
    }
}

struct PairView: View {
    @Environment(AppModel.self) private var app
    @Binding var path: [WelcomeView.PairStep]
    @FocusState private var focus: Field?

    enum Field { case host, name }

    var body: some View {
        @Bindable var app = app
        Group {
            if let pending = app.pairing.pending {
                PairCodeView(pending: pending)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("The hub knows you by your Tailscale device, so there's no password.")
                            .font(Theme.body).foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.bottom, 12)
                        fieldLabel("Hub")
                        TextField("my-mac or 100.64.0.1", text: $app.pairing.host)
                            .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                            .focused($focus, equals: .host).submitLabel(.next)
                            .onSubmit { focus = .name }
                            .quiverField()
                        Text("Its Tailscale name or address. Port 8477 unless you changed it.")
                            .font(Theme.caption).foregroundStyle(Theme.textMuted).padding(.bottom, 8)
                        fieldLabel("Device name")
                        TextField("iPhone", text: $app.pairing.deviceName)
                            .focused($focus, equals: .name).submitLabel(.go)
                            .onSubmit { Task { await app.startTailscalePairing() } }
                            .quiverField()
                        if let error = app.pairing.error {
                            Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText).padding(.top, 4)
                        }
                    }
                    .padding(Theme.gutter)
                }
                .safeAreaInset(edge: .bottom) {
                    VStack(spacing: 4) {
                        Button {
                            Task { await app.startTailscalePairing() }
                        } label: {
                            if app.pairing.busy { ProgressView().tint(Theme.primaryForeground) } else { Text("Pair with Tailscale") }
                        }
                        .primaryButton()
                        .disabled(app.pairing.busy)
                        Button("Use a pair token instead") { path.append(.token) }
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .scaledMinHeight(36)
                    }
                    .padding(.horizontal, Theme.gutter)
                    .padding(.bottom, 8)
                    .background(Theme.wash)
                }
            }
        }
        .background(Theme.wash)
        .navigationTitle(app.pairing.pending == nil ? "Pair this device" : "")
        .navigationBarTitleDisplayMode(.large)
        .onAppear { if app.pairing.host.isEmpty { focus = .host } }
    }

    private func fieldLabel(_ text: String) -> some View {
        Text(text).font(Theme.groupLabel).foregroundStyle(Theme.textSecondary).padding(.horizontal, 2)
    }
}

/// P3: the code another device types to approve this one.
struct PairCodeView: View {
    @Environment(AppModel.self) private var app
    var pending: PendingPairing
    @State private var copied = false

    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            VStack(spacing: 14) {
                Text("Your pair code").font(Theme.groupLabel).foregroundStyle(Theme.textSecondary)
                Text(pending.spacedCode)
                    .font(Theme.code6).tracking(4).monospacedDigit()
                    .foregroundStyle(pending.outcome == nil ? Theme.textPrimary : Theme.textMuted)
                Text("Approve it from a paired device, or on the hub run")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                Button {
                    UIPasteboard.general.string = "swe remote devices --approve=\(pending.started.code)"
                    copied = true
                } label: {
                    HStack(spacing: 6) {
                        Text("swe remote devices --approve=\(pending.started.code)").font(Theme.mono).lineLimit(1).minimumScaleFactor(0.8)
                        Image(systemName: copied ? "checkmark" : "doc.on.doc").font(Theme.font(size: 12))
                    }
                    .foregroundStyle(Theme.textPrimary)
                    .padding(.horizontal, 10).scaledMinHeight(32)
                    .background(Theme.code, in: .rect(cornerRadius: Theme.controlRadius))
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, Theme.gutter)
            Spacer()
            Card {
                switch pending.outcome {
                case nil:
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        let left = max(0, Int(pending.expiresAt.timeIntervalSince(context.date)))
                        HStack(spacing: 12) {
                            ProgressView().controlSize(.small)
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Waiting for approval").font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                                Text("Expires in \(left / 60):\(String(format: "%02d", left % 60))")
                                    .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
                            }
                            Spacer()
                        }
                    }
                case .denied?:
                    outcome("This request was denied", "Try again, or ask whoever approved it.")
                case .expired?:
                    outcome("This request expired", "Codes last five minutes. Start a new one.")
                }
            }
            .padding(.horizontal, Theme.gutter)
            VStack(spacing: 4) {
                if pending.outcome != nil {
                    Button("Try again") { Task { app.cancelPairing(); await app.startTailscalePairing() } }.primaryButton()
                }
                Button("Cancel") { app.cancelPairing() }
                    .font(Theme.body).foregroundStyle(Theme.textSecondary).scaledMinHeight(44)
            }
            .padding(.horizontal, Theme.gutter)
            .padding(.top, 12)
            .padding(.bottom, 8)
        }
        .frame(maxWidth: .infinity)
        .background(Theme.wash)
    }

    private func outcome(_ title: String, _ message: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "xmark.circle").foregroundStyle(Theme.dangerText)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                Text(message).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            Spacer()
        }
    }
}

/// Token fallback for devices the hub can't identify on the tailnet.
struct TokenPairView: View {
    @Environment(AppModel.self) private var app
    @State private var token = ""

    var body: some View {
        @Bindable var app = app
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                (Text("On the hub, run ") + Text("swe remote pair").font(Theme.mono) + Text(" and paste the token it prints."))
                    .font(Theme.body).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.bottom, 12)
                Text("Hub").font(Theme.groupLabel).foregroundStyle(Theme.textSecondary)
                TextField("my-mac or 100.64.0.1", text: $app.pairing.host)
                    .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    .quiverField()
                Text("Pair token").font(Theme.groupLabel).foregroundStyle(Theme.textSecondary).padding(.top, 8)
                TextField("Token", text: $token)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .quiverField(monospaced: true)
                if let error = app.pairing.error {
                    Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText).padding(.top, 4)
                }
                Button("Pair with token") { Task { await app.pair(token: token) } }
                    .primaryButton()
                    .disabled(token.trimmingCharacters(in: .whitespaces).isEmpty || app.pairing.busy)
                    .padding(.top, 16)
            }
            .padding(Theme.gutter)
        }
        .background(Theme.wash)
        .navigationTitle("Pair token")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct BusyOverlay: View {
    var body: some View {
        ZStack {
            Color.black.opacity(0.25).ignoresSafeArea()
            ProgressView().padding(24).background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        }
    }
}

/// Asks before pairing with the hub a `swe://pair` (or legacy `quiver://pair`) link from outside
/// the app names. Pairing hands that hub every prompt and approval this
/// phone sends, so a link someone else crafted must not do it unseen.
struct PairOfferSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dynamicTypeSize) private var size
    let link: PairLink

    var body: some View {
        // Scrolls only when the text size makes it taller than the sheet,
        // which can then be pulled up to full height too.
        ViewThatFits(in: .vertical) {
            offer
            ScrollView { offer }
        }
        .background(Theme.wash)
        .presentationDetents(size > .large ? [.medium, .large] : [.medium])
        .interactiveDismissDisabled(app.pairing.busy)
    }

    private var offer: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Pair with this hub?").font(Theme.sectionTitle).foregroundStyle(Theme.textPrimary)
            Card(padding: 0) {
                HStack(spacing: 12) {
                    Image(systemName: "desktopcomputer").font(Theme.rowIcon).foregroundStyle(Theme.textSecondary).iconColumn(20)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(link.address).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                            .lineLimit(2).truncationMode(.middle)
                        Text(link.baseURL.scheme == "https" ? "HTTPS" : "Plain HTTP, not encrypted")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.vertical, Theme.rowVertical).padding(.horizontal, Theme.rowHorizontal)
            }
            Text("A pair link opened this. Pair only with a hub you run: it sees your sessions, prompts and approvals.")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            if let error = app.pairing.error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            VStack(spacing: 4) {
                // A used-up code can't pair again; only a new link can.
                if !app.pairing.codeSpent {
                    Button {
                        Task { await app.acceptPairOffer() }
                    } label: {
                        if app.pairing.busy { ProgressView().tint(Theme.primaryForeground) } else { Text("Pair") }
                    }
                    .primaryButton()
                    .disabled(app.pairing.busy)
                }
                Button(app.pairing.codeSpent ? "Close" : "Cancel") { app.declinePairOffer() }
                    .font(Theme.body).foregroundStyle(Theme.textSecondary).scaledMinHeight(44)
            }
        }
        .padding(Theme.gutter)
        .padding(.top, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

extension View {
    /// Shows `PairOfferSheet` while a pair link waits. Only one view on
    /// screen may present it: RootView, or the pair-another-hub sheet
    /// while that is open over it.
    func pairOfferSheet(enabled: Bool = true) -> some View {
        modifier(PairOfferPresenter(enabled: enabled))
    }
}

private struct PairOfferPresenter: ViewModifier {
    @Environment(AppModel.self) private var app
    let enabled: Bool

    func body(content: Content) -> some View {
        content.sheet(item: Binding(
            get: { enabled ? app.pairOffer : nil },
            // Swiped away: the same as Cancel. A pair that just landed
            // has cleared the offer already.
            set: { if $0 == nil, enabled, app.pairOffer != nil { app.declinePairOffer() } }
        )) { link in
            PairOfferSheet(link: link)
        }
    }
}
