import SwiftTerm
import SwiftUI
import UIKit

/// A shell on the hub's machine, for debugging when chat can't help:
/// SSH straight to its sshd over the tailnet, so it works with the hub
/// down. Set up once while the hub answers (`swe remote ssh --on`).
struct ConsoleView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @State private var console = ConsoleModel()

    var body: some View {
        NavigationStack {
            content
                .background(Theme.wash)
                .navigationTitle(console.title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
                    ToolbarItem(placement: .primaryAction) {
                        Button { Task { await restart() } } label: { Image(systemName: "arrow.clockwise") }
                            .accessibilityLabel("Reconnect")
                            .disabled(console.phase == .preparing || console.phase == .connecting)
                    }
                }
        }
        .task { await restart() }
        .onDisappear { console.stop() }
    }

    @ViewBuilder private var content: some View {
        switch console.phase {
        case .preparing:
            LoadingRow(text: "Asking the hub how to log in").frame(maxHeight: .infinity, alignment: .top)
        case .needsSetup(let readiness):
            PageScroll { SetupCard(readiness: readiness) { Task { await restart() } } }
        case .failed(let message):
            PageScroll {
                StateCard(icon: "exclamationmark.triangle", title: "Couldn't open the console", message: message,
                          tone: .error, actionTitle: "Try again") { Task { await restart() } }
            }
        case .connecting, .open, .closed:
            VStack(spacing: 0) {
                if console.offlineTarget, let target = console.target {
                    Text("The hub isn't answering. Connected with the host keys it gave on an earlier visit to \(target.host).")
                        .font(Theme.caption).foregroundStyle(Theme.warningText)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, Theme.gutter).padding(.vertical, 8)
                }
                TerminalSurface(console: console)
                    // Text off the screen's edge, on the terminal's own ground.
                    .padding(.horizontal, 6)
                    .background(Theme.code)
                    .overlay {
                        if console.phase == .connecting {
                            LoadingRow(text: "Connecting to \(console.target?.address ?? "the machine")")
                                .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
                                .padding(Theme.gutter)
                        }
                    }
                    .overlay(alignment: .bottom) {
                        if case .closed(let reason) = console.phase {
                            StateCard(icon: "powerplug", title: "Connection closed", message: reason ?? "The shell ended.",
                                      actionTitle: "Reconnect") { Task { await console.reconnect() } }
                                .padding(Theme.gutter)
                        }
                    }
            }
        }
    }

    private func restart() async {
        guard let hub = app.hub, let client = app.client else {
            dismiss()
            return
        }
        await console.start(hub: hub, client: client)
    }
}

/// The hub said the machine isn't ready, and what to run there.
private struct SetupCard: View {
    let readiness: SSHReadiness
    let retry: () -> Void

    var body: some View {
        switch readiness {
        case .hubOff:
            card(title: "Turn on the console on the hub",
                 message: "This phone's key is saved on the hub. Run this on the hub's machine to let it log in:",
                 command: "swe remote ssh --on")
        case .sshdOff(let port):
            card(title: "Turn on Remote Login",
                 message: "Nothing answers on port \(port). On a Mac: System Settings, General, Sharing, Remote Login. Check it with:",
                 command: "swe remote ssh")
        case .noHostKeys:
            card(title: "No host key to check",
                 message: "sshd has no ed25519 or ecdsa host key, so this phone can't tell it's talking to the right machine. Make one with:",
                 command: "sudo ssh-keygen -A")
        case .ready:
            EmptyView()
        }
    }

    private func card(title: String, message: String, command: String) -> some View {
        Card(padding: 20) {
            VStack(alignment: .leading, spacing: 10) {
                Label(title, systemImage: "terminal").font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                Text(message).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                Button { UIPasteboard.general.string = command } label: {
                    HStack {
                        Text(command).font(Theme.monoBody).foregroundStyle(Theme.textPrimary)
                        Spacer()
                        Image(systemName: "doc.on.doc").foregroundStyle(Theme.textSecondary)
                    }
                    .padding(10)
                    .background(Theme.code, in: .rect(cornerRadius: Theme.controlRadius))
                }
                .accessibilityHint("Copies the command")
                Button("Check again", action: retry).secondaryButton(compact: true)
            }
        }
    }
}

/// SwiftTerm's terminal view, wired to the console: keys out, bytes in,
/// size changes to the pty. Its keyboard bar has Esc, Ctrl, Tab and arrows.
private struct TerminalSurface: UIViewRepresentable {
    let console: ConsoleModel

    func makeCoordinator() -> Coordinator { Coordinator(console: console) }

    func makeUIView(context: Context) -> TerminalView {
        let view = TerminalView(frame: .zero, font: Theme.terminalFont(context.environment.dynamicTypeSize))
        view.terminalDelegate = context.coordinator
        view.nativeBackgroundColor = UIColor(Theme.code)
        view.nativeForegroundColor = UIColor(Theme.textPrimary)
        context.coordinator.view = view
        console.screen = context.coordinator
        // Bring the keyboard up: the console is for typing.
        DispatchQueue.main.async { _ = view.becomeFirstResponder() }
        return view
    }

    func updateUIView(_ view: TerminalView, context: Context) {
        let font = Theme.terminalFont(context.environment.dynamicTypeSize)
        if view.font.pointSize != font.pointSize { view.font = font }
    }

    @MainActor final class Coordinator: NSObject, TerminalViewDelegate, ConsoleScreen {
        let console: ConsoleModel
        weak var view: TerminalView?

        init(console: ConsoleModel) { self.console = console }

        func feed(_ bytes: [UInt8]) { view?.feed(byteArray: bytes[...]) }

        nonisolated func send(source: TerminalView, data: ArraySlice<UInt8>) {
            let bytes = Array(data)
            MainActor.assumeIsolated { console.send(bytes) }
        }

        nonisolated func sizeChanged(source: TerminalView, newCols: Int, newRows: Int) {
            MainActor.assumeIsolated { console.resize(cols: newCols, rows: newRows) }
        }

        nonisolated func setTerminalTitle(source: TerminalView, title: String) {}
        nonisolated func hostCurrentDirectoryUpdate(source: TerminalView, directory: String?) {}
        nonisolated func scrolled(source: TerminalView, position: Double) {}
        nonisolated func requestOpenLink(source: TerminalView, link: String, params: [String: String]) {
            guard let url = URL(string: link), ["http", "https"].contains(url.scheme?.lowercased()) else { return }
            MainActor.assumeIsolated { UIApplication.shared.open(url) }
        }
        nonisolated func bell(source: TerminalView) {}
        nonisolated func clipboardCopy(source: TerminalView, content: Data) {
            guard let text = String(data: content, encoding: .utf8) else { return }
            MainActor.assumeIsolated { UIPasteboard.general.string = text }
        }
        nonisolated func iTermContent(source: TerminalView, content: ArraySlice<UInt8>) {}
        nonisolated func rangeChanged(source: TerminalView, startY: Int, endY: Int) {}
    }
}
