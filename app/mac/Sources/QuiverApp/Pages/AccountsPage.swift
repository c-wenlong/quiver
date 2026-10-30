import AppKit
import QuiverCore
import SwiftUI

struct AccountsPage: View {
    @Environment(AppModel.self) private var app
    @State private var adding = false
    @State private var actionError: String?
    /// Every card's usage history, off by default so the cards stay
    /// the same height; one switch for the whole page.
    @AppStorage("accountsShowHistory") private var showHistory = false
    @AppStorage("accountsHistoryMonthly") private var monthly = false

    var body: some View {
        let model = app.accounts
        Page(title: "Accounts",
             subtitle: "Each account keeps its own sign-in. Open it in its app or a terminal without logging out.") {
            Button("Refresh", systemImage: "arrow.clockwise") {
                // A refresh is a fresh look: the last action's failure goes.
                // Other reloads keep it, such as the one after a failed Remove.
                actionError = nil
                Task { await reloadAll(force: true, renewExpired: true) }
            }
            .iconButton()
            .disabled(model.isLoading)
            .keyboardShortcut("r", modifiers: .command)
            .help("Refresh (⌘R)")
            if showHistory {
                Tabs(options: [(false, "Weekly"), (true, "Monthly")], selection: $monthly)
            }
            Button(showHistory ? "Hide history" : "Show history", systemImage: "chart.bar") {
                showHistory.toggle()
            }
            .secondaryButton()
            .help("Weekly or monthly tokens on every account")
            Button("Add account", systemImage: "plus") { adding = true }
                .primaryButton()
        } content: {
            if let error = model.error ?? actionError {
                ErrorCard(message: error)
            }
            // One grid for every account: Quiver's own profiles first, vendors
            // in a fixed order, then the starred harnesses in swe's order.
            // Each card carries its logo, so there are no per-vendor headings.
            // Quiver's own account cards are Claude and Codex only; extra
            // Devin/Cursor accounts appear as harness cards from `others`.
            let cards = Vendor.allCases.filter(\.hasDefaultProfile).flatMap { vendor in
                app.starredAccountCards.filter { $0.profile.vendor == vendor } }
            let harnesses = app.others.cards
            if !cards.isEmpty || !harnesses.isEmpty {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 340), spacing: Theme.gutter, alignment: .top)], spacing: Theme.gutter) {
                    ForEach(cards) { card in
                        AccountCard(card: card, history: history, perform: perform,
                                    reloadAll: { await reloadAll(force: $0) })
                    }
                    ForEach(harnesses) { row in
                        HarnessAccountCard(row: row, history: history, perform: perform)
                    }
                }
                .task(id: showHistory ? historyKeys(cards: cards, harnesses: harnesses) : []) {
                    if showHistory {
                        await app.accountHistory?.show(historyKeys(cards: cards, harnesses: harnesses))
                    } else {
                        app.accountHistory?.hide()
                    }
                }
                // Off the page, scans stop rebuilding charts nobody sees.
                .onDisappear { app.accountHistory?.hide() }
            }
            if let note = app.others.note {
                Text(note).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            } else if !cards.isEmpty || !harnesses.isEmpty {
                Text("Only starred harnesses show here. Star one on Harnesses to add it, or unstar it to hide it.")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            } else {
                EmptyCard(text: "Nothing starred. Star a harness on Harnesses to see it here.",
                          icon: "star",
                          actionTitle: "Open Harnesses") { app.selection = .harnesses }
            }
        }
        .quiverSheet(isPresented: $adding) {
            AddAccountSheet { vendor, name, color in
                Task {
                    do {
                        let profile = try await model.add(vendor: vendor, name: name, color: color)
                        perform { try await model.signIn(profile) }
                        // Extra Devin/Cursor accounts are harness cards, built by `others`.
                        if !vendor.hasDefaultProfile { await app.others.load() }
                    } catch {
                        actionError = error.localizedDescription
                    }
                }
            }
        }
        .task { await app.others.load() }
    }

    /// What every card's history section needs, or nil while it is hidden.
    private var history: HistoryDisplay? {
        showHistory ? HistoryDisplay(monthly: monthly, profiles: allProfiles) : nil
    }

    /// Every account Quiver keeps a folder for, so a harness's own card
    /// leaves out the homes that have cards of their own.
    private var allProfiles: [Profile] {
        app.accounts.cards.map(\.profile) + app.others.cards.compactMap(\.profile)
    }

    private func historyKeys(cards: [AccountsModel.Card],
                             harnesses: [OtherLimitsModel.Card]) -> [AccountHistory.Key] {
        let profiles = allProfiles
        return cards.map { app.accounts.historyKey(harness: $0.profile.vendor.rawValue,
                                                   profile: $0.profile, profiles: profiles) }
            + harnesses.map { app.accounts.historyKey(harness: $0.name, profile: $0.profile,
                                                      profiles: profiles) }
    }

    private func reloadAll(force: Bool, renewExpired: Bool = false) async {
        // Only the Refresh button renews and asks swe to refetch failures;
        // every other caller just loads.
        if renewExpired {
            await app.accounts.refreshFromButton()
            await app.others.load(refreshFailed: true)
            // The Codex totals are a separate fetch on the same button.
            await app.codexUsage?.refresh(force: true)
        } else {
            await app.accounts.load(force: force)
            await app.others.load()
        }
    }

    /// Runs an account action and shows its failure on the page.
    private func perform(_ action: @escaping () async throws -> Void) {
        Task {
            do {
                try await action()
                actionError = nil
            } catch {
                actionError = error.localizedDescription
            }
        }
    }
}

/// How the cards draw their history while the page shows it.
struct HistoryDisplay {
    var monthly: Bool
    var profiles: [Profile]
}

private struct AccountCard: View {
    @Environment(AppModel.self) private var app
    var card: AccountsModel.Card
    var history: HistoryDisplay?
    var perform: (@escaping () async throws -> Void) -> Void
    var reloadAll: (Bool) async -> Void
    @State private var renaming = false
    @State private var newName = ""
    @State private var confirmingRemove = false
    @State private var installing: Provider?
    /// Sign in, or Install first when its CLI isn't on PATH.
    @ViewBuilder func signInItem(harness: String,
                                 cli: String?, installed: Bool?,
                                 run: @escaping () -> Void) -> some View {
        if case .install(let h) = AccountSignIn.action(cli: cli, harness: harness, installed: installed) {
            Button("Install \(HarnessMark(h).name)…") { installing = ProviderCatalog.provider(h) }
        } else {
            Button("Sign in…", action: run)
        }
    }
    var installSheet: some View {
        // Drawn behind the card, not inside its stack, so it adds no row
        // spacing; the sheet itself is drawn over the window.
        Color.clear.quiverSheet(item: $installing, onDismiss: {
            // A CLI installed from the sheet turns Install back into Sign in.
            Task {
                await app.others.load(force: true)
                await app.accounts.load(force: false)
            }
        }) { p in
            ProviderSetupSheet(provider: p, isInstalled: false)
        }
    }

    var body: some View {
        let profile = card.profile
        let model = app.accounts
        Card {
            HStack(spacing: 8) {
                HarnessIcon(harness: profile.vendor.rawValue, size: 16)
                    .help(profile.vendor.title)
                Text(profile.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    // The logo is hidden from VoiceOver, so the name carries the vendor.
                    .accessibilityLabel("\(profile.name), \(profile.vendor.title)")
                // The default account is already named Default; a badge would repeat it.
                if profile.isDefault && profile.name != "Default" { Badge("Default") }
                if let plan = card.usage?.plan { Badge(planTitle(plan)) }
                Spacer()
                EllipsisMenu {
                    signInItem(harness: profile.vendor.rawValue,
                               cli: model.signInCLI(for: profile),
                               installed: app.others.cliInstalled[LaunchPlanner.cliName(for: profile.vendor)]
                                   ?? model.cliOnPath[profile.vendor]) {
                        perform { try await model.signIn(profile) }
                    }
                    Button("Show folder in Finder") {
                        NSWorkspace.shared.activateFileViewerSelecting([model.folder(of: profile)])
                    }
                    Divider()
                    // Any account can be renamed; a default only can't be removed,
                    // since it is the vendor's own ~/.claude or ~/.codex.
                    Button("Rename…") { newName = profile.name; renaming = true }
                    if !profile.isDefault {
                        Button("Remove…", role: .destructive) { confirmingRemove = true }
                    }
                }
            }
            if let email = card.usage?.email {
                Text(email).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            UsageBars(usage: card.usage)
            HStack {
                StatusLine(usage: card.usage, card: card)
                Spacer()
                LaunchButtons(
                    target: LaunchTarget(kind: .account(profile), model: model,
                        installedApp: { app.apps.installedApp(for: $0) },
                        // swe's answer when its registry lists the CLI, else a direct PATH check.
                        cliInstalled: { app.others.cliInstalled[$0] ?? model.cliOnPath[profile.vendor] }),
                    perform: perform)
            }
            if let history {
                let key = model.historyKey(harness: profile.vendor.rawValue, profile: profile,
                                           profiles: history.profiles)
                AccountHistorySection(
                    history: app.accountHistory?.histories[key],
                    codex: profile.vendor == .codex
                        ? app.codexUsage?.section(for: model.folder(of: profile).path) : nil,
                    harnessName: profile.vendor.title,
                    monthly: history.monthly,
                    signIn: { perform { try await model.signIn(profile) } })
            }
        }
        .background { installSheet }
        .alert("Rename account", isPresented: $renaming) {
            TextField("Name", text: $newName)
            Button("Rename") { perform { try await model.rename(profile, to: newName) } }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog("Remove \(profile.name)?", isPresented: $confirmingRemove) {
            Button("Remove account and its folder", role: .destructive) {
                perform {
                    defer { Task { await reloadAll(false) } }
                    try await model.remove(profile)
                }
            }
        } message: {
            Text("Deletes the settings and history Quiver kept for this account. The vendor account itself is untouched.")
        }
    }
}

struct UsageBars: View {
    var usage: AccountUsage?

    var body: some View {
        if let usage, !usage.windows.isEmpty {
            // A failed fetch keeps the last reading: every row goes red and
            // greyed, rather than merely dimmed, so it never reads as live.
            let failed = usage.isStale && usage.status != .ok
            VStack(spacing: 8) {
                ForEach(usage.windows, id: \.label) { window in
                    UsageBar(window: window, failed: failed, asOf: usage.fetchedAt)
                }
            }
            .opacity(usage.isStale && !failed ? 0.55 : 1)
        }
    }
}

struct UsageBar: View {
    var window: UsageWindow
    /// The reading is a kept older one after a failed fetch: red label,
    /// greyed bar, and the real age in place of the reset time.
    var failed: Bool = false
    var asOf: Date? = nil

    var body: some View {
        let percent = Int(window.usedPercent.rounded())
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(window.label).font(Theme.control)
                    .foregroundStyle(failed ? Theme.dangerText : Theme.textPrimary)
                Spacer()
                if failed {
                    if let asOf {
                        Text("as of \(UsageFormat.ago(asOf, now: .now))")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                } else if let reset = window.resetsAt, reset > .now {
                    Text("Resets \(reset, format: .relative(presentation: .named))")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                Text(failed && asOf == nil ? "—" : "\(percent)%")
                    .font(Theme.control.monospacedDigit()).foregroundStyle(Theme.textPrimary)
                    .opacity(failed ? 0.45 : 1)
                    .frame(width: 40, alignment: .trailing)
            }
            UsageTrack(fraction: window.usedPercent / 100, color: Theme.usage(percent))
                .opacity(failed ? 0.45 : 1)
        }
    }
}

/// What the reading is and how old it is, in one quiet line.
struct StatusLine: View {
    @Environment(AppModel.self) private var app
    var usage: AccountUsage?
    /// Set on the Accounts page and in the menu bar panel, so an expired
    /// Claude sign-in can offer the renewal wording.
    var card: AccountsModel.Card? = nil

    var body: some View {
        Group {
            switch usage?.status {
            case nil:
                Text("Checking…")
            case .ok:
                if let fetched = usage?.fetchedAt {
                    Text("Fetched \(fetched, format: .relative(presentation: .named))\(usage?.isStale == true ? ", couldn't refresh" : "")\(expiry(.upcoming).map { ". \($0)." } ?? "")")
                } else {
                    Text("No reading yet\(expiry(.upcoming).map { ". \($0)." } ?? "")")
                }
            case .signedOut:
                Text("Signed out. Use Sign in from the menu.")
            case .expired:
                // A 401 can come before the stored expiry; only a lapsed
                // time is worth naming.
                let expired = expiry(.lapsed) ?? "Sign-in expired"
                if let card, card.profile.vendor == .claude {
                    if app.accounts.renewing.contains(card.id) {
                        Text("Renewing sign-in…")
                    } else if app.accounts.renewFailed.contains(card.id) {
                        Text("Couldn't renew the sign-in. Open the CLI once to sign in again.")
                    } else if app.accounts.cliOnPath[.claude] == true {
                        Text("\(expired). Press refresh to renew it.")
                    } else {
                        Text("\(expired). Open the CLI once to refresh it.")
                    }
                } else {
                    Text("\(expired). Open the CLI once to refresh it.")
                }
            case .rateLimited(let until):
                Text("Usage API busy until \(until, format: .dateTime.hour().minute())")
            case .error(let message):
                Text(message).lineLimit(1)
            }
        }
        .font(Theme.caption)
        .foregroundStyle(Theme.textSecondary)
    }

    private enum Expiry { case upcoming, lapsed }

    /// "Sign-in expires at 22:41" while the sign-in still works, "Sign-in
    /// expired yesterday at 00:08" once it lapsed; nil when the credential
    /// records no expiry or it sits on the other side of now.
    private func expiry(_ side: Expiry) -> String? {
        guard let at = usage?.signInExpiresAt else { return nil }
        let now = Date.now
        guard (at > now) == (side == .upcoming) else { return nil }
        return UsageFormat.signIn(expiresAt: at, now: now)
    }
}

/// The 6pt usage bar: a quiet track and a rounded fill.
struct UsageTrack: View {
    var fraction: Double
    var color: Color

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.usageTrack)
                Capsule().fill(color).frame(width: max(0, min(1, fraction)) * geo.size.width)
            }
        }
        .frame(height: Theme.usageTrackHeight)
        .accessibilityElement()
        .accessibilityValue("\(Int((fraction * 100).rounded())) percent")
    }
}


private struct AddAccountSheet: View {
    var onAdd: (Vendor, String, String?) -> Void
    @Environment(\.dismissSheet) private var dismiss
    @State private var vendor: Vendor = .claude
    @State private var name = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add an account").font(Theme.sectionTitle).tracking(-0.5)
            Tabs(options: Vendor.allCases.map { ($0, $0.title, $0.rawValue) }, selection: $vendor)
            TextField("Name, like Work or Personal", text: $name)
                .quiverField()
            Text(caption(for: vendor))
                .font(Theme.font(size: 10)).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .secondaryButton()
                Button("Add and sign in") {
                    onAdd(vendor, name.trimmingCharacters(in: .whitespaces), nil)
                    dismiss()
                }
                .primaryButton()
                .keyboardShortcut(.defaultAction)
                .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 420)
    }

    /// What this vendor's sign-in looks like; Cursor's is inside the app
    /// and Devin's folder also holds what its agent runs.
    private func caption(for vendor: Vendor) -> String {
        switch vendor {
        case .cursor:
            "Signs in through the Cursor app. The cursor-agent CLI stays on your default account."
        case .devin:
            "Commands the agent runs in this account keep their data (for example uv and nvim) in its folder."
        case .claude, .codex:
            "Quiver gives the account its own folders, then opens the CLI's sign-in in your terminal. Open the desktop app once afterwards to sign in there too."
        }
    }
}

extension Color {
    /// `#rrggbb` or `#rgb`; anything else falls back to grey.
    init(hex: String) {
        guard let rgb = HexColor.rgb(hex) else {
            self = .gray
            return
        }
        self.init(red: Double(rgb.red) / 255, green: Double(rgb.green) / 255, blue: Double(rgb.blue) / 255)
    }
}

/// "max 20x" or "max_20x" -> "Max 20x": words capitalised, multipliers left alone.
func planTitle(_ plan: String) -> String {
    plan.replacingOccurrences(of: "_", with: " ")
        .split(separator: " ")
        .map { word in
            word.first?.isNumber == true ? String(word) : word.prefix(1).uppercased() + word.dropFirst()
        }
        .joined(separator: " ")
}

/// Open app and Terminal, each only when a static check found it: the app in
/// /Applications or ~/Applications, the CLI on PATH (as swe reports). Before
/// swe has answered, Terminal is offered rather than hidden.
private struct LaunchButtons: View {
    var target: LaunchTarget
    /// Overrides the Terminal button's help when the CLI cannot run as
    /// this account (cursor-agent keeps the default sign-in).
    var terminalHelp: String? = nil
    var perform: (@escaping () async throws -> Void) -> Void

    var body: some View {
        HStack(spacing: 8) {
            if let appPath = target.appPath {
                let appName = URL(fileURLWithPath: appPath)
                    .deletingPathExtension().lastPathComponent
                Button("Open app", systemImage: "arrow.up.forward.app") {
                    perform { try await target.openApp(at: appPath) }
                }
                .iconButton()
                .labelStyle(.iconOnly)
                .help("Open \(appName)")
                .accessibilityLabel("Open \(appName)")
            }
            if target.cliInstalled != false {
                Button("Terminal", systemImage: "terminal") {
                    perform { try await target.openTerminal() }
                }
                .iconButton()
                .labelStyle(.iconOnly)
                .help(terminalHelp ?? "Open \(target.title) in Terminal")
                .accessibilityLabel("Open \(target.title) in Terminal")
            }
            if !target.canLaunch {
                Text("Not installed").font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
        }
    }
}

/// A starred harness with no Quiver profile, laid out like an account card:
/// its limit from swe, a rename, its CLI's own sign-in where it has one, and
/// whichever of its app and CLI are installed.
private struct HarnessAccountCard: View {
    @Environment(AppModel.self) private var app
    var row: OtherLimitsModel.Card
    var history: HistoryDisplay?
    var perform: (@escaping () async throws -> Void) -> Void
    @State private var renaming = false
    @State private var newName = ""
    @State private var confirmingRemove = false
    @State private var installing: Provider?
    @State private var fixing = false
    /// Sign in, or Install first when its CLI isn't on PATH.
    @ViewBuilder func signInItem(harness: String,
                                 cli: String?, installed: Bool?,
                                 run: @escaping () -> Void) -> some View {
        if case .install(let h) = AccountSignIn.action(cli: cli, harness: harness, installed: installed) {
            Button("Install \(HarnessMark(h).name)…") { installing = ProviderCatalog.provider(h) }
        } else {
            Button("Sign in…", action: run)
        }
    }
    var installSheet: some View {
        // Drawn behind the card, not inside its stack, so it adds no row
        // spacing; the sheet itself is drawn over the window.
        Color.clear.quiverSheet(item: $installing, onDismiss: {
            // A CLI installed from the sheet turns Install back into Sign in.
            Task {
                await app.others.load(force: true)
                await app.accounts.load(force: false)
            }
        }) { p in
            ProviderSetupSheet(provider: p, isInstalled: false)
        }
    }

    var body: some View {
        let limit = row.limit
        // The harness's own name, never the account name given to it here.
        let ownName = row.harnessTitle
        let name = row.title
        let model = app.accounts
        Card {
            HStack(spacing: 8) {
                HarnessIcon(harness: row.name, size: 16)
                    .help(ownName)
                Text(name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .accessibilityLabel(name == ownName ? name : "\(name), \(ownName)")
                if let plan = limit?.planType { Badge(planTitle(plan)) }
                if row.issue?.status.needsAction == true {
                    Text("Action needed")
                        .font(Theme.font(size: 12, weight: .medium))
                        .foregroundStyle(Theme.warningText)
                        .lineLimit(1)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 1)
                        .background(Theme.warning.opacity(0.15), in: .capsule)
                        .overlay(Capsule().strokeBorder(Theme.warning.opacity(0.4), lineWidth: 1))
                }
                Spacer()
                if let profile = row.profile {
                    // An extra account: its name and folder are the profile's,
                    // so a rename writes profiles.json and Remove deletes them.
                    EllipsisMenu {
                        signInItem(harness: profile.vendor.rawValue,
                                   cli: model.signInCLI(for: profile),
                                   installed: app.others.cliInstalled[LaunchPlanner.cliName(for: profile.vendor)]
                                       ?? model.cliOnPath[profile.vendor]) {
                            perform { try await model.signIn(profile) }
                        }
                        Button("Show folder in Finder") {
                            NSWorkspace.shared.activateFileViewerSelecting([model.folder(of: profile)])
                        }
                        Divider()
                        Button("Rename…") { newName = profile.name; renaming = true }
                        Button("Remove…", role: .destructive) { confirmingRemove = true }
                    }
                } else {
                    EllipsisMenu {
                        if let login = HarnessAccess.loginCommand(for: row.name) {
                            let cli = AccountSignIn.cli(loginCommand: login)
                            signInItem(harness: row.name, cli: cli,
                                       installed: AccountSignIn.installed(
                                           harness: row.name, cli: cli,
                                           cliInstalled: app.others.cliInstalled,
                                           vendorInstalled: row.cliInstalled)) {
                                perform { try await model.signIn(harness: row.name) }
                            }
                            Divider()
                        }
                        Button("Rename…") { newName = name; renaming = true }
                        if let accountName = row.accountName, !accountName.isEmpty {
                            Button("Use the name \(ownName)") { rename(to: "") }
                        }
                    }
                }
            }
            if let email = limit?.email {
                Text(email).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            if let limit, limit.status == .ok { usageSection(limit) }
            HStack {
                if let line = row.issue?.actionNeededLine {
                    // The remedy's summary is the explanation, in amber;
                    // the Fix button beside it opens the full steps.
                    Text(line)
                        .font(Theme.caption).foregroundStyle(Theme.warningText)
                } else {
                    statusLine(limit: limit, ownName: ownName)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                Spacer()
                if row.issue?.status.needsAction == true {
                    Button("Fix…") { fixing = true }
                        .secondaryButton()
                        .hoverTip("Show how to fix this")
                }
                if let profile = row.profile {
                    LaunchButtons(
                        target: LaunchTarget(kind: .account(profile), model: model,
                            installedApp: { app.apps.installedApp(for: $0) },
                            cliInstalled: { _ in row.cliInstalled ?? model.cliOnPath[profile.vendor] }),
                        // cursor-agent keeps one sign-in per Mac; say so
                        // before the button reads as opening this account.
                        terminalHelp: profile.vendor == .cursor
                            ? "Opens cursor-agent on your default Cursor account. The Cursor CLI keeps one sign-in per Mac."
                            : nil,
                        perform: perform)
                } else {
                    LaunchButtons(
                        target: LaunchTarget(kind: .harness(name: row.name, command: row.command),
                            model: model,
                            installedApp: { app.apps.installedApp(for: $0) },
                            cliInstalled: { _ in row.cliInstalled }),
                        perform: perform)
                }
            }
            if let history {
                AccountHistorySection(
                    history: app.accountHistory?.histories[
                        model.historyKey(harness: row.name, profile: row.profile,
                                         profiles: history.profiles)],
                    codex: nil,
                    harnessName: ownName,
                    monthly: history.monthly,
                    signIn: nil)
            }
        }
        .background { installSheet }
        .background {
            if let issue = row.issue {
                // Drawn like installSheet: over the window, no row space.
                Color.clear.quiverSheet(isPresented: $fixing) {
                    IssueFixSheet(title: name, issue: issue, perform: perform)
                }
            }
        }
        // The sheet goes with its issue; left set, `fixing` would open it
        // again, unasked, the next time this account has one.
        .onChange(of: row.issue == nil) { _, gone in if gone { fixing = false } }
        .alert("Rename \(row.profile?.name ?? ownName)", isPresented: $renaming) {
            TextField("Name", text: $newName)
            if let profile = row.profile {
                Button("Rename") { perform { try await renameProfile(profile, to: newName) } }
            } else {
                Button("Rename") { rename(to: newName) }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            if row.profile == nil {
                Text("Changes its name in Accounts, the menu bar and widgets only. Leave it empty to use \(ownName).")
            }
        }
        .confirmationDialog("Remove \(row.profile?.name ?? "")?", isPresented: $confirmingRemove) {
            if let profile = row.profile {
                Button("Remove account and its folder", role: .destructive) {
                    perform {
                        defer { Task { await app.others.load() } }
                        try await model.remove(profile)
                    }
                }
            }
        } message: {
            Text("Deletes the settings and history Quiver kept for this account. The vendor account itself is untouched.")
        }
    }

    /// The usage bars as an account card shows them: one per window the
    /// harness reports, then on-demand spend and an extra-usage balance.
    @ViewBuilder private func usageSection(_ limit: RateLimit) -> some View {
        ForEach(limit.allWindows, id: \.window) { window in
            UsageBar(window: UsageWindow(label: window.window.isEmpty ? "Usage" : UsageFormat.window(window.window),
                                         usedPercent: Double(min(max(window.usedPercent, 0), 100)),
                                         resetsAt: window.resetAt),
                     failed: window.unavailable, asOf: window.asOf)
        }
        if let onDemand = limit.onDemand {
            HStack {
                Text("On-demand").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                Spacer()
                Text("\(onDemand.value) · \(onDemand.detail)").font(Theme.caption.monospacedDigit())
                    .foregroundStyle(Theme.textPrimary)
            }
        }
        if let balance = limit.extraUsageUSD {
            HStack {
                Text("Extra usage balance").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                Spacer()
                Text(UsageFormat.balance(balance)).font(Theme.caption.monospacedDigit())
                    .foregroundStyle(Theme.textPrimary)
            }
        }
    }

    /// The card's status line when nothing needs the user: the reading's
    /// age, or why there is none. Only an outage mentions Refresh, since
    /// it is the one failure a refresh can fix.
    @ViewBuilder
    private func statusLine(limit: RateLimit?, ownName: String) -> some View {
        if let limit {
            switch limit.status {
            case .ok:
                if let fetched = limit.fetchedAt {
                    Text("Fetched \(fetched, format: .relative(presentation: .named))")
                } else {
                    Text("No reading yet")
                }
            case .authRequired:
                Text(HarnessAccess.loginCommand(for: row.name) != nil
                     ? "Signed out. Use Sign in from the menu." : "Sign in again in \(ownName).")
            case .noSubscription: Text("No subscription")
            case .timeout: Text("Didn't answer in time")
            }
        } else if row.issue?.status == .timeout {
            Text("Didn't answer in time. Refresh to try again.")
        } else {
            switch row.usageNote {
            case .signIn?:
                // Extra accounts always have Sign in in the menu.
                Text(row.profile != nil || HarnessAccess.loginCommand(for: row.name) != nil
                     ? "Not signed in. Use Sign in from the menu." : "Not signed in. Sign in from \(ownName).")
            case .fetchFailed?: Text("Couldn't fetch usage. Refresh to try again.")
            case .updateSwe?: Text("Update swe to read this account's usage")
            case nil: Text(noLimitReason)
            }
        }
    }

    /// Why a card has no usage bars: swe reads limits only for some
    /// harnesses; a starred one it can read just has no reading yet.
    private var noLimitReason: String {
        if !HarnessAccess.hasLimits(row.name) { return "No usage limits to read" }
        return "No reading yet"
    }

    /// The account name lives in the app's own file, so a rename never
    /// touches harness.json and stays a no-swe local write.
    private func rename(to name: String) {
        let others = app.others
        let harness = row.name
        perform {
            try others.rename(harness, to: name)
        }
    }

    /// A profile rename is a ProfilesStore write, then a reload so the
    /// card, menu bar and widgets pick the name up.
    private func renameProfile(_ profile: Profile, to name: String) async throws {
        try await app.accounts.rename(profile, to: name)
        await app.others.load()
    }
}

/// The Fix sheet an actionable issue opens: the remedy swe named, its
/// steps numbered, and its command with copy and Run in Terminal like
/// the Doctor rows offer.
private struct IssueFixSheet: View {
    @Environment(AppModel.self) private var app
    var title: String
    var issue: HarnessIssue
    var perform: (@escaping () async throws -> Void) -> Void

    private static var mono: Font { Theme.font(size: 12, design: .monospaced) }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(issue.remedy?.title ?? "Fix \(title)")
                .font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            Text(issue.remedy?.summary ?? "This account needs attention.")
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            if let steps = issue.remedy?.steps, !steps.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                        HStack(alignment: .top, spacing: 8) {
                            Text("\(index + 1).")
                                .font(Theme.bodySmall)
                                .foregroundStyle(Theme.textMuted)
                                .frame(width: 14, alignment: .trailing)
                            Text(step)
                                .font(Theme.bodySmall)
                                .foregroundStyle(Theme.textPrimary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
            }
            ForEach(Array((issue.remedy?.actions ?? []).enumerated()), id: \.offset) { _, action in
                switch action {
                case .command(let command):
                    HStack(spacing: 8) {
                        Text(command)
                            .font(Self.mono)
                            .lineSpacing(2)
                            .foregroundStyle(Theme.textPrimary)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Button("Run in Terminal", systemImage: "terminal") {
                            perform {
                                try await app.accounts.runInTerminal(line: command)
                            }
                        }
                        .secondaryButton()
                        .hoverTip("Runs \(command) in a new terminal")
                        CopyButton(help: "Copy the command") { copyToPasteboard(command) }
                            .iconButton()
                    }
                    .padding(.leading, 12)
                    .padding(.trailing, 6)
                    .padding(.vertical, 6)
                    .background(Theme.code, in: .rect(cornerRadius: Theme.controlRadius))
                    .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
                        .strokeBorder(Theme.border, lineWidth: 1))
                case .open(let url, let label):
                    // A permission grant happens in System Settings: open
                    // the pane the steps name instead of a command.
                    Button(label, systemImage: "arrow.up.forward.app") {
                        NSWorkspace.shared.open(url)
                    }
                    .secondaryButton()
                }
            }
        }
        .padding(20)
        .frame(width: 420)
    }
}
