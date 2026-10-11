import AppKit
import QuiverCore
import SwiftUI

struct AccountsPage: View {
    @Environment(AppModel.self) private var app
    @State private var adding = false
    @State private var actionError: String?
    @State private var others: OtherLimitsModel?

    var body: some View {
        let model = app.accounts
        Page(title: "Accounts",
             subtitle: "Each account keeps its own sign-in. Open it in its app or a terminal without logging out.") {
            if let error = model.error ?? actionError {
                Card { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(Theme.danger) }
            }
            ForEach(Vendor.allCases, id: \.self) { vendor in
                let cards = model.cards.filter { $0.profile.vendor == vendor }
                if !cards.isEmpty {
                    Text(vendor.title).font(.title3.weight(.semibold)).padding(.top, 4)
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 340), spacing: 14)], spacing: 14) {
                        ForEach(cards) { card in
                            AccountCard(card: card, perform: perform)
                        }
                    }
                }
            }
            if let others, !others.rows.isEmpty || others.note != nil {
                Text("Other harnesses").font(.title3.weight(.semibold)).padding(.top, 4)
                Card {
                    if let note = others.note {
                        Text(note).font(.caption).foregroundStyle(.secondary)
                    }
                    ForEach(others.rows) { row in
                        OtherLimitRow(row: row)
                        if row != others.rows.last { Divider() }
                    }
                    Text("From swe, which polls the harnesses you starred.")
                        .font(.caption).foregroundStyle(.tertiary)
                }
            }
        }
        .toolbar {
            Button("Add account", systemImage: "plus") { adding = true }
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await model.load(force: true) }
            }
            .disabled(model.isLoading)
        }
        .sheet(isPresented: $adding) {
            AddAccountSheet { vendor, name, color in
                Task {
                    do {
                        let profile = try await model.add(vendor: vendor, name: name, color: color)
                        perform { try model.signIn(profile) }
                    } catch {
                        actionError = error.localizedDescription
                    }
                }
            }
        }
        .task { await model.load(force: false) }
        .task(id: app.swePath) {
            let model = OtherLimitsModel(swe: app.swe)
            others = model
            await model.load()
        }
    }

    private func perform(_ action: () throws -> Void) {
        do {
            try action()
            actionError = nil
        } catch {
            actionError = error.localizedDescription
        }
    }
}

extension Vendor {
    var title: String {
        switch self {
        case .claude: "Claude"
        case .codex: "Codex"
        }
    }
}

private struct AccountCard: View {
    @Environment(AppModel.self) private var app
    var card: AccountsModel.Card
    var perform: (() throws -> Void) -> Void
    @State private var renaming = false
    @State private var newName = ""
    @State private var confirmingRemove = false

    var body: some View {
        let profile = card.profile
        let model = app.accounts
        Card {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Circle().fill(Color(hex: profile.color)).frame(width: 9, height: 9)
                Text(profile.name).font(.headline)
                // The default account is already named Default; a badge would repeat it.
                if profile.isDefault && profile.name != "Default" { Badge("Default") }
                if let plan = card.usage?.plan { Badge(planTitle(plan)) }
                Spacer()
                Menu {
                    Button("Sign in…") { perform { try model.signIn(profile) } }
                    Button("Show folder in Finder") {
                        NSWorkspace.shared.activateFileViewerSelecting([model.folder(of: profile)])
                    }
                    if !profile.isDefault {
                        Divider()
                        Button("Rename…") { newName = profile.name; renaming = true }
                        Button("Remove…", role: .destructive) { confirmingRemove = true }
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
            }
            if let email = card.usage?.email {
                Text(email).font(.caption).foregroundStyle(.secondary)
            }
            UsageBars(usage: card.usage)
            HStack {
                StatusLine(usage: card.usage)
                Spacer()
                Button("Open app") { perform { try model.openApp(profile) } }
                Button("Terminal") { perform { try model.openTerminal(profile) } }
            }
            .controlSize(.small)
        }
        .alert("Rename account", isPresented: $renaming) {
            TextField("Name", text: $newName)
            Button("Rename") { Task { try? await model.rename(profile, to: newName) } }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog("Remove \(profile.name)?", isPresented: $confirmingRemove) {
            Button("Remove account and its folder", role: .destructive) {
                Task { try? await model.remove(profile) }
            }
        } message: {
            Text("Deletes this account's sign-in, settings and history kept by Quiver. The vendor account itself is untouched.")
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
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(window.label).font(.caption.weight(.medium))
                Spacer()
                if let reset = window.resetsAt, reset > .now {
                    Text("Resets \(reset, format: .relative(presentation: .named))")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text("\(percent)%").font(.caption.monospacedDigit()).frame(width: 40, alignment: .trailing)
            }
            ProgressView(value: min(max(window.usedPercent, 0), 100), total: 100)
                .tint(Theme.usage(percent))
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
        .font(.caption)
        .foregroundStyle(.secondary)
    }
}

struct Badge: View {
    var text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text)
            .font(.caption2.weight(.medium))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(.quaternary, in: .capsule)
    }
}

private struct AddAccountSheet: View {
    var onAdd: (Vendor, String, String?) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var vendor: Vendor = .claude
    @State private var name = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add an account").font(.title3.weight(.semibold))
            Picker("Vendor", selection: $vendor) {
                ForEach(Vendor.allCases, id: \.self) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            TextField("Name, like Work or Personal", text: $name)
                .textFieldStyle(.roundedBorder)
            Text("Quiver gives the account its own folders, then opens the CLI's sign-in in your terminal. Open the desktop app once afterwards to sign in there too.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                Button("Add and sign in") {
                    onAdd(vendor, name.trimmingCharacters(in: .whitespaces), nil)
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 420)
    }
}

extension Color {
    /// `#rrggbb`; anything else falls back to the accent.
    init(hex: String) {
        let digits = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        guard digits.count == 6, let value = UInt32(digits, radix: 16) else {
            self = Theme.accent
            return
        }
        self.init(red: Double((value >> 16) & 0xff) / 255,
                  green: Double((value >> 8) & 0xff) / 255,
                  blue: Double(value & 0xff) / 255)
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
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(row.name.capitalized).font(.subheadline.weight(.medium))
                if let plan = limit.planType { Badge(planTitle(plan)) }
                Spacer()
                switch limit.status {
                case .ok:
                    if let reset = limit.resetAt, reset > .now {
                        Text("Resets \(reset, format: .relative(presentation: .named))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Text("\(limit.usedPercent)%").font(.caption.monospacedDigit())
                case .authRequired:
                    Text("Sign in again in \(row.name.capitalized)").font(.caption).foregroundStyle(.secondary)
                case .noSubscription:
                    Text("No subscription").font(.caption).foregroundStyle(.secondary)
                case .timeout:
                    Text("Didn't answer in time").font(.caption).foregroundStyle(.secondary)
                }
            }
            if limit.status == .ok {
                ProgressView(value: Double(min(max(limit.usedPercent, 0), 100)), total: 100)
                    .tint(Theme.usage(limit.usedPercent))
                if let window = limit.window {
                    Text(window).font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
    }
}
