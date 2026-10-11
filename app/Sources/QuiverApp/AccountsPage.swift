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
            .help("Refresh")
            Button("Add account", systemImage: "plus") { adding = true }
                .primaryButton()
        } content: {
            if let error = model.error ?? actionError {
                ErrorCard(message: error)
            }
            // One grid for every account, vendors in a fixed order; each
            // card carries its vendor's logo, so no per-vendor heading.
            let cards = Vendor.allCases.flatMap { vendor in model.cards.filter { $0.profile.vendor == vendor } }
            if !cards.isEmpty {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 340), spacing: Theme.gutter, alignment: .top)], spacing: Theme.gutter) {
                    ForEach(cards) { card in
                        AccountCard(card: card, perform: perform, reloadAll: reloadAll)
                    }
                }
            }
            if !app.others.rows.isEmpty || app.others.note != nil {
                SectionTitle("Other harnesses")
                Card {
                    if let note = app.others.note {
                        Text(note).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                    ForEach(app.others.rows) { row in
                        OtherLimitRow(row: row)
                        if row != app.others.rows.last { Divider().overlay(Theme.border) }
                    }
                    Text("From swe, which polls the harnesses you starred.")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
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
                Button("Open app") { perform { try await model.openApp(profile) } }
                    .secondaryButton()
                Button("Terminal") { perform { try await model.openTerminal(profile) } }
                    .secondaryButton()
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

private struct OtherLimitRow: View {
    var row: OtherLimitsModel.Row

    var body: some View {
        let limit = row.limit
        let name = UsageFormat.harness(row.name)
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                HarnessIcon(harness: row.name, size: 16)
                Text(name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                if let plan = limit.planType { Badge(planTitle(plan)) }
                Spacer()
                Group {
                    switch limit.status {
                    case .ok: EmptyView()
                    case .authRequired: Text("Sign in again in \(name)")
                    case .noSubscription: Text("No subscription")
                    case .timeout: Text("Didn't answer in time")
                    }
                }
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            if limit.status == .ok {
                // The same bar as an account card: window, reset, percent.
                UsageBar(window: UsageWindow(label: limit.window ?? "Usage",
                                             usedPercent: Double(min(max(limit.usedPercent, 0), 100)),
                                             resetsAt: limit.resetAt))
            }
        }
    }
}
