import AppKit
import QuiverCore
import SwiftUI

/// The remote-control setup checklist: one row per step of
/// `swe remote setup --json`, each with its state and the one thing to do
/// about it. The sheet polls while it is open, so a step finished in a
/// browser or in Tailscale's menu ticks on its own; Set up runs `--apply`
/// for every step Quiver can do itself.
struct RemoteSetupSheet: View {
    @Environment(\.dismissSheet) private var dismiss
    @Bindable var setup: RemoteSetupModel
    /// Pairing reuses the Hub card's model, so a phone paired here shows
    /// in Paired devices at once.
    @Bindable var remote: RemoteModel
    @State private var target: PairTarget = .web

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            header
            content
            footer
        }
        .padding(Theme.sheetPadding)
        .frame(width: 600)
        .background(Theme.panel)
        // Ends when the sheet closes, and the poll with it.
        .task { await setup.watch() }
        .quiverSheet(isPresented: showTicket, onDismiss: { remote.dismissTicket() }) {
            PairSheet(model: remote, target: $target)
        }
    }

    private var showTicket: Binding<Bool> {
        Binding(get: { remote.ticket != nil },
                set: { if !$0 { remote.dismissTicket() } })
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Set up remote control")
                .font(Theme.sectionTitle).tracking(-0.5)
                .foregroundStyle(Theme.textStrong)
            Text("Watch and steer sessions from your phone, over Tailscale. Steps tick on their own as they finish.")
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var content: some View {
        if let report = setup.report {
            if let error = setup.applyError { ErrorCard(message: error) }
            // The list below is the last check that worked; say why the
            // newest one didn't, so steps that stopped updating aren't a mystery.
            if let error = setup.loadError {
                ErrorCard(message: "Couldn't check again: \(error)", actionTitle: "Try again") {
                    Task { await setup.load() }
                }
            }
            if report.ready { readyCard(report) }
            stepList(report)
            if let pairError = remote.pairError {
                Text(pairError).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
        } else if let error = setup.loadError {
            ErrorCard(message: error, actionTitle: "Try again") {
                Task { await setup.load() }
            }
        } else {
            SkeletonDetail()
        }
    }

    private func stepList(_ report: RemoteSetupReport) -> some View {
        let current = report.current?.id
        return VStack(spacing: 0) {
            ForEach(Array(report.steps.enumerated()), id: \.element.id) { index, step in
                if index > 0 { Divider().overlay(Theme.border) }
                SetupStepRow(step: step, isCurrent: step.id == current,
                             applying: setup.applying, pairing: remote.pairing,
                             pair: pair)
            }
        }
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .clipShape(.rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
    }

    private func readyCard(_ report: RemoteSetupReport) -> some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: "checkmark.seal.fill")
                .font(Theme.font(size: 18)).foregroundStyle(Theme.success)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text("Ready. Phones reach this Mac at")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                Text(report.url)
                    .font(Theme.font(size: 13, design: .monospaced))
                    .foregroundStyle(Theme.textPrimary)
                    .textSelection(.enabled)
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: 8)
            CopyButton(title: "Copy address", help: "Copy the hub's https address", iconSize: 12) {
                copyToPasteboard(report.url)
            }
            .secondaryButton()
            Button(action: pair) {
                if remote.pairing {
                    ProgressView().controlSize(.small)
                } else {
                    Label("Pair a phone", systemImage: "qrcode")
                }
            }
            .primaryButton()
            .disabled(remote.pairing)
        }
        .padding(Theme.cardPadding)
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.success.opacity(0.5), lineWidth: 1))
    }

    private var footer: some View {
        HStack(spacing: 10) {
            if setup.applying {
                ProgressView().controlSize(.small)
                Text("Setting up. Fetching a certificate can take a minute.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            } else if setup.loading && setup.report == nil {
                EmptyView()
            } else if let report = setup.report, !report.ready, !report.canApply, setup.loadError == nil {
                Text("Waiting for you. This list checks again every few seconds.")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            Spacer()
            Button("Done") { dismiss() }
                .secondaryButton()
                .keyboardShortcut(.cancelAction)
            if let report = setup.report, report.canApply || setup.applying {
                Button {
                    Task { await setup.apply() }
                } label: {
                    Text(setup.applying ? "Setting up…" : "Set up")
                }
                .primaryButton()
                .keyboardShortcut(.defaultAction)
                .disabled(setup.applying)
                .hoverTip(applyTip(report))
            }
        }
    }

    private func applyTip(_ report: RemoteSetupReport) -> String {
        let labels = report.applyLabels
        guard !labels.isEmpty else { return "Runs swe remote setup --apply" }
        return "Quiver will: " + labels.joined(separator: ", ").lowercased() + "."
    }

    private func pair() {
        Task { await remote.mintTicket() }
    }
}

/// One step: its state, title, detail, and the button its action asks for.
private struct SetupStepRow: View {
    var step: RemoteSetupReport.Step
    var isCurrent: Bool
    var applying: Bool
    var pairing: Bool
    var pair: () -> Void
    @State private var copied = false
    @State private var copyGeneration = 0

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            icon
                .frame(width: 18, height: 18)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 3) {
                Text(step.title)
                    .font(Theme.bodySmall)
                    .fontWeight(isCurrent ? .medium : .regular)
                    .foregroundStyle(step.state == .blocked ? Theme.textSecondary : Theme.textPrimary)
                if !step.detail.isEmpty {
                    Text(step.detail)
                        .font(Theme.caption)
                        .foregroundStyle(step.state == .failed ? Theme.dangerText : Theme.textSecondary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let command = step.action?.command, step.action?.kind == .command {
                    Text(command)
                        .font(Theme.font(size: 12, design: .monospaced))
                        .foregroundStyle(Theme.textPrimary)
                        .textSelection(.enabled)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            control
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, 12)
        .background(isCurrent ? Theme.cardHover : .clear)
        .overlay(alignment: .leading) {
            if isCurrent {
                Rectangle().fill(highlight).frame(width: 2)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(step.title), \(stateName)")
    }

    private var highlight: Color {
        switch step.state {
        case .failed: Theme.danger
        case .needsYou: Theme.warning
        default: Theme.textSecondary
        }
    }

    private var stateName: String {
        switch step.state {
        case .ok: "done"
        case .todo: "Quiver can do this"
        case .needsYou: "needs you"
        case .failed: "failed"
        case .blocked: "waiting"
        }
    }

    @ViewBuilder private var icon: some View {
        switch step.state {
        case .ok:
            Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.success)
        case .needsYou:
            Image(systemName: "exclamationmark.circle.fill").foregroundStyle(Theme.warning)
        case .failed:
            Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.danger)
        case .todo:
            if applying && isCurrent {
                ProgressView().controlSize(.small)
            } else {
                Image(systemName: "circle.dashed").foregroundStyle(Theme.textSecondary)
            }
        case .blocked:
            Image(systemName: "circle.dashed").foregroundStyle(Theme.textMuted)
        }
    }

    /// A step that's done or waiting has nothing to press. `apply` steps
    /// share the sheet's one Set up button.
    @ViewBuilder private var control: some View {
        if let action = step.action, step.state != .ok, step.state != .blocked {
            switch action.kind {
            case .open:
                if let url = action.url.flatMap(URL.init(string:)), url.isWebURL {
                    Button(action.label) { NSWorkspace.shared.open(url) }
                        .secondaryButton()
                        .hoverTip(url.absoluteString)
                }
            case .launch:
                if let app = action.app {
                    Button(action.label) { AppLauncher.open(named: app) }
                        .secondaryButton()
                }
            case .command:
                if let command = action.command {
                    Button {
                        copyToPasteboard(command)
                        copyGeneration += 1
                        let mine = copyGeneration
                        copied = true
                        Task {
                            try? await Task.sleep(for: .seconds(2))
                            if mine == copyGeneration { copied = false }
                        }
                    } label: {
                        Label(copied ? "Copied" : action.label,
                              systemImage: copied ? "checkmark" : "doc.on.doc")
                    }
                    .secondaryButton()
                    .hoverTip("Copy it, then paste it in Terminal")
                }
            case .pair:
                Button(action: pair) {
                    if pairing {
                        ProgressView().controlSize(.small)
                    } else {
                        Label(action.label, systemImage: "qrcode")
                    }
                }
                .primaryButton()
                .disabled(pairing)
            case .apply:
                // The sheet's one Set up button does it; the row names
                // what that will be, in swe's words ("Move hub").
                Label(action.label, systemImage: "sparkles")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .padding(.top, 1)
                    .hoverTip("Set up, at the bottom, does this")
            case .unknown:
                EmptyView()
            }
        }
    }
}

/// Opens a Mac app by its name, the way `open -a` does.
enum AppLauncher {
    static func open(named name: String) {
        let folders = [URL(fileURLWithPath: "/Applications"),
                       FileManager.default.homeDirectoryForCurrentUser.appending(path: "Applications")]
        if let url = folders.map({ $0.appending(path: "\(name).app") })
            .first(where: { FileManager.default.fileExists(atPath: $0.path) }) {
            NSWorkspace.shared.openApplication(at: url, configuration: NSWorkspace.OpenConfiguration())
            return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        process.arguments = ["-a", name]
        try? process.run()
    }
}
