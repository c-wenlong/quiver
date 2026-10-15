import QuiverCore
import SwiftUI

/// The "Add harness" sheet: swe's installer catalog minus the harnesses
/// already registered. Quiver never picks a package manager; it shows each
/// one's command to copy, or to run in the terminal (casks and scripts can
/// prompt for a password, so the install stays where sudo works).
struct AddHarnessSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Bindable var model: InstallCatalogModel
    /// Called after a Register succeeds so the page list picks it up.
    var onRegister: () async -> Void

    @State private var expanded: Set<String> = []
    @State private var copied: String?

    private var sweExecutable: String { SweLocation.executable(override: app.swePath) }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Add harness").font(Theme.sectionTitle).tracking(-0.5)
                    .foregroundStyle(Theme.textPrimary)
                Text("Install commands for each package manager. Nothing runs until you pick one.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            SearchField(prompt: "Filter harnesses", text: $model.query)
            if let error = model.actionError { ErrorCard(message: error) }
            content
            HStack {
                Spacer()
                Button("Done") { dismiss() }
                    .secondaryButton()
                    .keyboardShortcut(.cancelAction)
            }
        }
        .padding(20)
        .frame(width: 560)
        .background(Theme.panel)
        .task { await model.load() }
    }

    @ViewBuilder private var content: some View {
        switch model.state {
        case .idle, .loading:
            LoadingCard(text: "Asking swe what it can install…")
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded:
            let groups = model.groups
            if groups.installed.isEmpty && groups.missing.isEmpty {
                Card {
                    Text(model.everythingRegistered
                            ? "Every harness swe knows is already registered."
                            : "No harnesses match.")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                }
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        if !groups.installed.isEmpty {
                            section("On your Mac, not registered",
                                    rows: groups.installed, row: installedRow)
                        }
                        if !groups.missing.isEmpty {
                            section("Not installed", rows: groups.missing, row: missingRow)
                        }
                    }
                    .padding(1)
                }
                .frame(minHeight: 160, maxHeight: 480)
            }
        }
    }

    private func section(_ title: String, rows: [InstallableHarness],
                         row: @escaping (InstallableHarness) -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(Theme.cardTitle).foregroundStyle(Theme.textSecondary)
            VStack(spacing: 0) {
                ForEach(rows) { harness in
                    row(harness)
                    if harness.id != rows.last?.id { Divider().overlay(Theme.border) }
                }
            }
            .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
        }
    }

    /// Icon + brand-style title + description, laid out like a HarnessRow.
    private func rowHeader(_ harness: InstallableHarness) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                HarnessIcon(harness: harness.name, size: 16)
                    .alignmentGuide(.firstTextBaseline) { $0[.bottom] - 3 }
                Text(UsageFormat.harness(harness.name))
                    .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                Text(harness.command)
                    .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.textMuted)
                    .lineLimit(1).truncationMode(.middle)
            }
            if !harness.description.isEmpty {
                Text(harness.description)
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// Already on PATH but unknown to swe: `swe add` registers it as it is.
    private func installedRow(_ harness: InstallableHarness) -> some View {
        HStack(alignment: .top, spacing: 12) {
            rowHeader(harness)
            Spacer(minLength: 12)
            Button(model.registering.contains(harness.name) ? "Registering…" : "Register") {
                Task { if await model.register(harness) == nil { await onRegister() } }
            }
            .secondaryButton()
            .disabled(!model.registering.isEmpty)
            .help("Register it with swe add, no reinstall")
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, 12)
    }

    /// Not on PATH: start collapsed; expanding lists each install method.
    private func missingRow(_ harness: InstallableHarness) -> some View {
        let open = expanded.contains(harness.name)
        return VStack(alignment: .leading, spacing: 6) {
            Button {
                withAnimation(.easeOut(duration: Theme.fadeDuration)) {
                    if open { expanded.remove(harness.name) } else { expanded.insert(harness.name) }
                }
            } label: {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Theme.textMuted)
                        .rotationEffect(.degrees(open ? 90 : 0))
                        .frame(width: 12)
                        .padding(.top, 2)
                    rowHeader(harness)
                    Spacer(minLength: 12)
                    Text(harness.methods.count == 1 ? "1 way" : "\(harness.methods.count) ways")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted).monospacedDigit()
                        .padding(.top, 2)
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(UsageFormat.harness(harness.name)), \(open ? "collapse" : "expand") install methods")
            if open && !harness.methods.isEmpty {
                VStack(spacing: 0) {
                    ForEach(harness.methods, id: \.via) { method in
                        methodRow(method, of: harness)
                        if method.via != harness.methods.last?.via { Divider().overlay(Theme.border) }
                    }
                }
                .padding(.leading, 22)
            }
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, 12)
    }

    /// One install method: manager badge, the copyable command, Copy and Run.
    private func methodRow(_ method: InstallMethod, of harness: InstallableHarness) -> some View {
        let key = "\(harness.name):\(method.via)"
        return HStack(spacing: 8) {
            Badge(method.managerTitle)
            Text(method.command)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(Theme.textSecondary)
                .lineLimit(1).truncationMode(.middle)
                .textSelection(.enabled)
                .help(method.command)
            Spacer(minLength: 8)
            Button("Copy", systemImage: copied == key ? "checkmark" : "doc.on.doc") {
                copyToPasteboard(method.command)
                copied = key
                Task {
                    try? await Task.sleep(for: .seconds(1.5))
                    if copied == key { copied = nil }
                }
            }
            .iconButton()
            .help(copied == key ? "Copied" : "Copy")
            .accessibilityLabel("Copy \(method.managerTitle) install command")
            Button("Run in Terminal", systemImage: "terminal") {
                Task {
                    model.clearError()
                    do {
                        try await app.accounts.runInTerminal(line:
                            InstallCatalogModel.installLine(harness.name, via: method.via,
                                                            swe: sweExecutable))
                    } catch {
                        model.report(error)
                    }
                }
            }
            .iconButton()
            .disabled(!method.available)
            .help(method.available ? "Run in Terminal" : "\(method.managerTitle) isn't on your PATH")
            .accessibilityLabel("Run \(method.managerTitle) install in Terminal")
        }
        .padding(.vertical, 6)
    }
}
