import AppKit
import QuiverCore
import SwiftUI

/// `swe harness discover` as a tick-list: the new matches, high-confidence
/// ones pre-ticked; Register calls `discover --apply` for exactly those.
struct DetectSheet: View {
    var model: HarnessesModel
    var reload: () async -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss

    @State private var discovery: HarnessDiscovery?
    @State private var checked: Set<String> = []
    @State private var registered: HarnessDiscovery.Applied?
    @State private var error: String?
    @State private var working = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Detect harnesses").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            if let error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            if let registered {
                // Everything picked can be skipped, which left "Registered ."
                Text(registered.added.isEmpty
                     ? "Nothing was registered."
                     : "Registered \(registered.added.map { HarnessMark($0).name }.joined(separator: ", ")).")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                ForEach(registered.skipped) { skip in
                    Text("Skipped \(skip.name): \(skip.reason)")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            } else if let discovery {
                let (rows, _) = HarnessDiscovery.selection(discovery.findings)
                if rows.isEmpty {
                    Text("Every harness on this Mac is already registered.")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                } else {
                    // A Mac with many matches scrolls here; the buttons stay put.
                    ScrollView {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(rows) { finding in
                            Toggle(isOn: Binding(
                                get: { checked.contains(finding.name) },
                                set: { on in
                                    if on { checked.insert(finding.name) } else { checked.remove(finding.name) }
                                })) {
                                HStack(spacing: 8) {
                                    Text(finding.name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                                    Text(finding.command).font(Theme.caption.monospacedDigit()).foregroundStyle(Theme.textSecondary)
                                    Badge(finding.confidence)
                                    if let path = finding.path {
                                        Text(path.replacingOccurrences(
                                            of: FileManager.default.homeDirectoryForCurrentUser.path,
                                            with: "~"))
                                            .font(Theme.caption).foregroundStyle(Theme.textMuted)
                                    }
                                }
                            }
                            .toggleStyle(.checkbox)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(maxHeight: 360)
                }
            } else {
                ProgressView().controlSize(.small)
                Text("Scanning PATH and ~/ for coding CLIs…")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            HStack {
                Spacer()
                Button(registered == nil ? "Cancel" : "Done") { dismiss() }
                    .secondaryButton()
                if registered == nil, discovery != nil {
                    let rows = HarnessDiscovery.selection(discovery!.findings).rows
                    if !rows.isEmpty {
                        Button("Register (\(checked.count))") {
                            working = true
                            error = nil
                            Task {
                                do {
                                    registered = try await model.register(
                                        rows.filter { checked.contains($0.name) }.map(\.name))
                                    await reload()
                                } catch { self.error = error.localizedDescription }
                                working = false
                            }
                        }
                        .primaryButton()
                        .disabled(working || checked.isEmpty)
                    }
                }
            }
        }
        .sheetScaffold(width: 460)
        .task {
            do {
                let reply = try await app.swe.discoverHarnesses()
                discovery = reply
                checked = HarnessDiscovery.selection(reply.findings).checked
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// `swe add <name> <command>` as a form. --no-replace keeps swe from
/// overwriting an existing entry; the field warns before swe sees it.
struct AddHarnessSheet: View {
    var model: HarnessesModel
    /// Names already in the registry, so a duplicate can't be submitted.
    var known: Set<String>
    var reload: () async -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss

    @State private var name = ""
    @State private var command = ""
    @State private var description = ""
    @State private var aliases = ""
    @State private var tags = ""
    @State private var working = false
    @State private var error: String?

    static let nameRule = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

    var nameProblem: String? {
        if name.isEmpty { return "Letters, digits, then . _ -" }
        if known.contains(name) { return "\(name) is already registered" }
        return name.wholeMatch(of: Self.nameRule) == nil
            ? "Start with a letter or digit; then . _ -" : nil
    }

    private func csv(_ text: String) -> [String] {
        text.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add harness").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            FormField("Name") {
                TextField("mycli", text: $name).quiverField(monospaced: true)
                if let nameProblem {
                    Text(nameProblem).font(Theme.caption).foregroundStyle(Theme.dangerText)
                }
            }
            FormField("Command") {
                TextField("binary name or path", text: $command).quiverField(monospaced: true)
                if !command.isEmpty, !command.hasPrefix("/"), !Self.onPath(command) {
                    Text("Not on PATH yet").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            FormField("Description") {
                TextField("What it does", text: $description).quiverField()
            }
            FormField("Aliases") {
                TextField("comma-separated", text: $aliases).quiverField(monospaced: true)
            }
            FormField("Tags") {
                TextField("agentic, coding", text: $tags).quiverField(monospaced: true)
            }
            if let error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.secondaryButton()
                Button("Add") {
                    working = true
                    error = nil
                    Task {
                        do {
                            try await model.add(
                                name: name, command: command,
                                description: description,
                                aliases: csv(aliases), tags: csv(tags))
                            await reload()
                            dismiss()
                        } catch { self.error = error.localizedDescription }
                        working = false
                    }
                }
                .primaryButton()
                .disabled(working || nameProblem != nil || command.isEmpty)
            }
        }
        .sheetScaffold(width: 420)
    }

    /// Whether `command` resolves on PATH, a quick which(1) on the app's own
    /// PATH plus the usual Homebrew spot.
    static func onPath(_ command: String) -> Bool {
        let paths = (ProcessInfo.processInfo.environment["PATH"] ?? "")
            .split(separator: ":").map(String.init) + ["/opt/homebrew/bin", "/usr/local/bin"]
        return paths.contains { FileManager.default.isExecutableFile(atPath: "\($0)/\(command)") }
    }
}
