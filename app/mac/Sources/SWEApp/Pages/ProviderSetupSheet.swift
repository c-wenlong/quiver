import SWECore
import SwiftUI

/// What opens from a Settings › Providers row: how to uninstall a provider
/// that is installed, or how to install one that isn't. The commands come
/// from swe's installer catalog. SWE never picks a package manager; it
/// shows each command to copy, or to run in the terminal (casks and scripts
/// can prompt for a password, so the command runs where sudo works).
struct ProviderSetupSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    @Environment(\.openURL) private var openURL
    var provider: Provider
    /// What Settings › Providers found; the catalog's own answer wins once
    /// it loads, since it checked PATH just now.
    var isInstalled: Bool
    @State private var model: InstallCatalogModel?

    private var sweExecutable: String { SweLocation.executable(override: app.swePath) }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            if let model {
                if let error = model.actionError { ErrorCard(message: error) }
                content(model)
            } else {
                SkeletonDetail(label: "Asking swe how it installs \(provider.name)…")
            }
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
        .task {
            let catalog = InstallCatalogModel(swe: app.swe)
            model = catalog
            await catalog.load()
        }
    }

    private var installed: Bool { model?.harness(provider.id)?.installed ?? isInstalled }

    private var header: some View {
        HStack(spacing: 10) {
            HarnessIcon(harness: provider.icon, size: 20)
            Text(installed ? "Uninstall \(provider.name)" : "Install \(provider.name)")
                .font(Theme.sectionTitle).tracking(-0.5)
                .foregroundStyle(Theme.textPrimary)
        }
    }

    @ViewBuilder private func content(_ model: InstallCatalogModel) -> some View {
        switch model.state {
        case .idle, .loading:
            SkeletonDetail(label: "Asking swe how it installs \(provider.name)…")
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded:
            let entry = model.harness(provider.id)
            if installed {
                uninstallContent(entry, model: model)
            } else if let entry, !entry.methods.isEmpty {
                note("Install commands for each package manager. Nothing runs until you pick one.")
                if let appPath = entry.app {
                    note("The \(provider.name) app is already on this Mac at \(appPath); these commands install its command line tool.")
                }
                methodList(entry.methods) { method in
                    InstallCatalogModel.installLine(entry.name, via: method.via, swe: sweExecutable)
                }
                websiteLink(entry.website)
            } else if let site = entry?.website, let url = URL(string: site), url.isWebURL {
                note("swe doesn't know an installer for \(provider.name). Get it from its own website.")
                downloadCard(url: url, site: site)
            } else {
                note("swe doesn't know an installer for \(provider.name). Get it from its own website.")
            }
        }
    }

    @ViewBuilder private func uninstallContent(_ entry: InstallableHarness?, model: InstallCatalogModel) -> some View {
        if let entry, !entry.registered {
            registerRow(entry, model: model)
        }
        if let method = entry?.uninstall {
            note("Installed with \(method.managerTitle)\(entry?.path.map { ", at \($0)" } ?? "").")
            methodList([method]) { InstallCatalogModel.uninstallLine($0) }
            note("This removes the program only. Its settings and session history stay on disk.")
        } else if let path = entry?.path {
            note("swe can't tell how \(provider.name) was installed at \(path), so there's no command to show. Its install script's docs usually cover removing it.")
        } else {
            note("swe has no installer on file for \(provider.name). Remove it the way you installed it, or with its own uninstaller.")
        }
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
    }

    /// The vendor download page as a quiet link under the method list —
    /// where the desktop app lives when the commands above only install
    /// a CLI (qoder, antigravity), or a second way in next to a cask.
    @ViewBuilder private func websiteLink(_ website: String?) -> some View {
        if let website, let url = URL(string: website), url.isWebURL {
            Button {
                openURL(url)
            } label: {
                Label("Get \(provider.name) from \(url.host() ?? website)",
                      systemImage: "arrow.up.right.square")
                    .font(Theme.caption)
                    .foregroundStyle(Theme.accent)
            }
            .buttonStyle(.plain)
            .hoverTip("Opens \(website)")
            .accessibilityLabel("Open \(website)")
        }
    }

    /// The vendor download page as the only action, for a provider with
    /// no install command swe can offer.
    private func downloadCard(url: URL, site: String) -> some View {
        HStack(spacing: 12) {
            Text(url.host() ?? site)
                .font(Theme.font(size: 12, design: .monospaced))
                .foregroundStyle(Theme.textSecondary)
                .lineLimit(1).truncationMode(.middle)
                .textSelection(.enabled)
            Spacer(minLength: 12)
            Button("Download") { openURL(url) }
                .secondaryButton()
                .hoverTip("Opens \(site)")
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, 12)
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
    }

    /// On PATH but unknown to swe: `swe add` registers it as it is.
    private func registerRow(_ entry: InstallableHarness, model: InstallCatalogModel) -> some View {
        HStack(spacing: 12) {
            Text("On your Mac but not registered with swe, so the Harnesses page doesn't list it.")
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 12)
            Button(model.registering.contains(entry.name) ? "Registering…" : "Register") {
                Task { await model.register(entry) }
            }
            .secondaryButton()
            .disabled(!model.registering.isEmpty)
            .hoverTip("Register it with swe add, no reinstall", replacesLabel: false)
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, 12)
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
    }

    private func methodList(_ methods: [InstallMethod], line: @escaping (InstallMethod) -> String?) -> some View {
        VStack(spacing: 0) {
            ForEach(methods, id: \.via) { method in
                methodRow(method, line: line(method))
                if method.via != methods.last?.via { Divider().overlay(Theme.border) }
            }
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, 6)
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
    }

    /// One method: the manager's logo, the copyable command, Copy and Run.
    private func methodRow(_ method: InstallMethod, line: String?) -> some View {
        HStack(spacing: 8) {
            Group {
                if let logo = method.logo {
                    HarnessIcon(harness: logo, size: 16)
                } else {
                    Image(systemName: "terminal").font(Theme.font(size: 13)).foregroundStyle(Theme.textPrimary)
                }
            }
            .frame(width: 20, height: 20)
            .help(method.managerTitle)
            .accessibilityLabel(method.managerTitle)
            Text(method.command)
                .font(Theme.font(size: 12, design: .monospaced))
                .foregroundStyle(Theme.textSecondary)
                .lineLimit(1).truncationMode(.middle)
                .textSelection(.enabled)
                .help(method.command)
                .frame(maxWidth: .infinity, alignment: .leading)
                .layoutPriority(1)
            CopyButton(title: "Copy \(method.managerTitle) command",
                       help: "Copy the \(method.managerTitle) command") { copyToPasteboard(method.command) }
                .iconButton()
            Button("Run in Terminal", systemImage: "terminal") {
                guard let line else { return }
                Task {
                    model?.clearError()
                    do {
                        try await app.accounts.runInTerminal(line: line)
                    } catch {
                        model?.report(error)
                    }
                }
            }
            .iconButton()
            .disabled(!method.available || line == nil)
            .hoverTip(method.available ? "Run in Terminal" : "\(method.managerTitle) isn't on your PATH")
            .accessibilityLabel("Run \(method.managerTitle) command in Terminal")
        }
        .padding(.vertical, 6)
    }
}
