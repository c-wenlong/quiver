import AppKit
import QuiverCore
import SwiftUI

struct AccountsPage: View {
    @Environment(AppModel.self) private var app
    @State private var adding = false
    @State private var actionError: String?

    var body: some View {
        let model = app.accounts
        Page(title: "Accounts",
             subtitle: "Each account keeps its own sign-in. Open it in its app or a terminal without logging out.") {
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await reloadAll(force: true) }
            }
            .iconButton()
            .disabled(model.isLoading)
            .keyboardShortcut("r", modifiers: .command)
            .help("Refresh (⌘R)")
            Button("Add account", systemImage: "plus") { adding = true }
                .primaryButton()
        } content: {
            if let error = model.error ?? actionError {
                ErrorCard(message: error)
            }
            // One grid for every account: Quiver's own profiles first, vendors
            // in a fixed order, then the starred harnesses in swe's order.
            // Each card carries its logo, so there are no per-vendor headings.
            let cards = Vendor.allCases.flatMap { vendor in app.starredAccountCards.filter { $0.profile.vendor == vendor } }
            let harnesses = app.others.cards
            if !cards.isEmpty || !harnesses.isEmpty {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 340), spacing: Theme.gutter, alignment: .top)], spacing: Theme.gutter) {
                    ForEach(cards) { card in
                        AccountCard(card: card, perform: perform, reloadAll: reloadAll)
                    }
                    ForEach(harnesses) { row in
                        HarnessAccountCard(row: row, perform: perform)
                    }
                }
            }
            if let note = app.others.note {
                Text(note).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            } else if !cards.isEmpty || !harnesses.isEmpty {
                Text("Only starred harnesses show here. Star one on Harnesses to add it, or unstar it to hide it.")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            } else {
                Text("Nothing starred. Star a harness on Harnesses to see it here.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
        .sheet(isPresented: $adding) {
            AddAccountSheet { vendor, name, color in
                Task {
                    do {
                        let profile = try await model.add(vendor: vendor, name: name, color: color)
                        perform { try await model.signIn(profile) }
                    } catch {
                        actionError = error.localizedDescription
                    }
                }
            }
        }
        .task { await app.others.load() }
    }

    private func reloadAll(force: Bool) async {
        await app.accounts.load(force: force)
        await app.others.load()
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

private struct AccountCard: View {
    @Environment(AppModel.self) private var app
    var card: AccountsModel.Card
    var perform: (@escaping () async throws -> Void) -> Void
    var reloadAll: (Bool) async -> Void
    @State private var renaming = false
    @State private var newName = ""
    @State private var confirmingRemove = false

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
                Menu {
                    Button("Sign in…") { perform { try await model.signIn(profile) } }
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
                } label: {
                    Image(systemName: "ellipsis")
                        .foregroundStyle(Theme.textSecondary)
                        .frame(width: 24, height: 20)
                        .contentShape(.rect)
                }
                .menuStyle(.button)
                .plainButton(cornerRadius: 6)
                .menuIndicator(.hidden)
                .fixedSize()
                .help("More")
            }
            if let email = card.usage?.email {
                Text(email).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            UsageBars(usage: card.usage)
            HStack {
                StatusLine(usage: card.usage)
                Spacer()
                LaunchButtons(
                    appPath: app.apps.installedApp(for: profile.vendor.rawValue),
                    // swe's answer when its registry lists the CLI, else a direct PATH check.
                    cliInstalled: app.others.cliInstalled[profile.vendor.rawValue] ?? model.cliOnPath[profile.vendor],
                    openApp: { path in perform { try await model.openApp(profile, at: path) } },
                    openTerminal: { perform { try await model.openTerminal(profile) } })
            }
        }
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
        if let windows = usage?.windows, !windows.isEmpty {
            VStack(spacing: 8) {
                ForEach(windows, id: \.label) { window in
                    UsageBar(window: window)
                }
            }
            .opacity(usage?.isStale == true ? 0.55 : 1)
        }
    }
}

struct UsageBar: View {
    var window: UsageWindow

    var body: some View {
        let percent = Int(window.usedPercent.rounded())
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(window.label).font(Theme.control).foregroundStyle(Theme.textPrimary)
                Spacer()
                if let reset = window.resetsAt, reset > .now {
                    Text("Resets \(reset, format: .relative(presentation: .named))")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                Text("\(percent)%").font(Theme.control.monospacedDigit()).foregroundStyle(Theme.textPrimary)
                    .frame(width: 40, alignment: .trailing)
            }
            UsageTrack(fraction: window.usedPercent / 100, color: Theme.usage(percent))
        }
    }
}

/// What the reading is and how old it is, in one quiet line.
struct StatusLine: View {
    var usage: AccountUsage?

    var body: some View {
        Group {
            switch usage?.status {
            case nil:
                Text("Checking…")
            case .ok:
                if let fetched = usage?.fetchedAt {
                    Text("Fetched \(fetched, format: .relative(presentation: .named))\(usage?.isStale == true ? ", couldn't refresh" : "")")
                } else {
                    Text("No reading yet")
                }
            case .signedOut:
                Text("Signed out. Use Sign in from the menu.")
            case .expired:
                Text("Sign-in expired. Open the CLI once to refresh it.")
            case .rateLimited(let until):
                Text("Usage API busy until \(until, format: .dateTime.hour().minute())")
            case .error(let message):
                Text(message).lineLimit(1)
            }
        }
        .font(Theme.caption)
        .foregroundStyle(Theme.textSecondary)
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

/// A heading between groups of cards: 20/28 semibold.
struct SectionTitle: View {
    var text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text).font(Theme.sectionTitle).tracking(-0.5).foregroundStyle(Theme.textPrimary).padding(.top, 8)
    }
}

private struct AddAccountSheet: View {
    var onAdd: (Vendor, String, String?) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var vendor: Vendor = .claude
    @State private var name = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add an account").font(Theme.sectionTitle).tracking(-0.5)
            Tabs(options: Vendor.allCases.map { ($0, $0.title, $0.rawValue) }, selection: $vendor)
            TextField("Name, like Work or Personal", text: $name)
                .quiverField()
            Text("Quiver gives the account its own folders, then opens the CLI's sign-in in your terminal. Open the desktop app once afterwards to sign in there too.")
                .font(.caption).foregroundStyle(.secondary)
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
    var appPath: String?
    var cliInstalled: Bool?
    var openApp: (String) -> Void
    var openTerminal: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            if let appPath {
                Button("Open app") { openApp(appPath) }
                    .secondaryButton()
                    .help(appPath)
            }
            if cliInstalled != false {
                Button("Terminal") { openTerminal() }
                    .secondaryButton()
            }
            if appPath == nil && cliInstalled == false {
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
    var perform: (@escaping () async throws -> Void) -> Void
    @State private var renaming = false
    @State private var newName = ""
    /// A rename in flight; the menu waits for it.
    @State private var saving = false

    var body: some View {
        let limit = row.limit
        let ownName = UsageFormat.harness(row.name)
        let name = row.title
        let model = app.accounts
        Card {
            HStack(spacing: 8) {
                HarnessIcon(harness: row.name, size: 16)
                    .help(ownName)
                Text(name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .accessibilityLabel(name == ownName ? name : "\(name), \(ownName)")
                if let plan = limit?.planType { Badge(planTitle(plan)) }
                Spacer()
                Menu {
                    if HarnessAccess.loginCommand(for: row.name) != nil {
                        Button("Sign in…") { perform { try await model.signIn(harness: row.name) } }
                        Divider()
                    }
                    Button("Rename…") { newName = name; renaming = true }
                        .disabled(saving)
                    if name != ownName {
                        Button("Use the name \(ownName)") { rename(to: "") }
                            .disabled(saving)
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .foregroundStyle(Theme.textSecondary)
                        .frame(width: 24, height: 20)
                        .contentShape(.rect)
                }
                .menuStyle(.button)
                .plainButton(cornerRadius: 6)
                .menuIndicator(.hidden)
                .fixedSize()
                .help("More")
            }
            if let limit, limit.status == .ok {
                // The same bars as an account card, one per window the
                // harness reports: window, reset, percent.
                ForEach(limit.allWindows, id: \.window) { window in
                    UsageBar(window: UsageWindow(label: window.window.isEmpty ? "Usage" : UsageFormat.window(window.window),
                                                 usedPercent: Double(min(max(window.usedPercent, 0), 100)),
                                                 resetsAt: window.resetAt))
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
            HStack {
                Group {
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
                    } else {
                        Text(noLimitReason)
                    }
                }
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                Spacer()
                LaunchButtons(
                    appPath: app.apps.installedApp(for: row.name),
                    cliInstalled: row.cliInstalled,
                    openApp: { path in perform { try await model.openApp(at: path) } },
                    openTerminal: { perform { try await model.openTerminal(command: row.command) } })
            }
        }
        .alert("Rename \(ownName)", isPresented: $renaming) {
            TextField("Name", text: $newName)
            Button("Rename") { rename(to: newName) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Sets its name in harness.json, so the Harnesses page shows it too. Leave it empty to go back to \(ownName).")
        }
    }

    /// Why a card has no usage bars: swe reads limits only for some
    /// harnesses; a starred one it can read just has no reading yet.
    private var noLimitReason: String {
        if !HarnessAccess.hasLimits(row.name) { return "No usage limits to read" }
        return "No reading yet"
    }

    /// The harness's display name lives in harness.json, so a rename goes
    /// through `swe edit` and the cards reload from swe.
    private func rename(to name: String) {
        guard !saving else { return }
        saving = true
        let swe = app.swe
        let others = app.others
        let harness = row.name
        perform {
            defer { saving = false }
            try await HarnessesModel.rename(harness, to: name, using: swe)
            await others.load()
        }
    }
}
