import QuiverCore
import SwiftUI

/// Add an MCP server: a stdio command line or an http URL, with env vars
/// or headers that can each be marked secret (the value goes to the
/// secrets file, the config gets `${NAME}`). The hub is always written;
/// "Add to" picks which harnesses get it too.
struct McpAddServerSheet: View {
    var harnesses: [String]
    /// The harnesses the grid compares: the "Add to" defaults.
    var active: [String]
    var reload: () async -> Void

    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss

    enum Kind: String, CaseIterable { case command = "Command", url = "URL" }

    struct Variable: Identifiable {
        var id = UUID()
        var key = ""
        var value = ""
        var secret = false
    }

    @State private var name = ""
    @State private var kind = Kind.command
    @State private var commandLine = ""
    @State private var url = ""
    @State private var sse = false
    @State private var variables: [Variable] = [Variable()]
    @State private var targets: Set<String>
    @State private var force = false
    @State private var working = false
    @State private var error: String?

    init(harnesses: [String], active: [String], reload: @escaping () async -> Void) {
        self.harnesses = harnesses
        self.active = active
        self.reload = reload
        _targets = State(initialValue: Set(active))
    }

    private static let namePattern = /^[A-Za-z0-9][A-Za-z0-9_.@-]*$/

    private var words: [String] { mcpShellWords(commandLine) }

    private var saveDisabled: Bool {
        working || name.wholeMatch(of: Self.namePattern) == nil
            || (kind == .command ? words.isEmpty : url.trimmingCharacters(in: .whitespaces).isEmpty)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Add a server").font(Theme.sectionTitle).tracking(-0.5)
            FormField("Name") {
                TextField("github", text: $name).quiverField()
            }
            FormField("Kind") {
                Picker("", selection: $kind) {
                    ForEach(Kind.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented).labelsHidden()
            }
            if kind == .command {
                FormField("Command", hint: words.isEmpty ? nil :
                            "command \(words[0])" + (words.count > 1 ? " · args \(words.dropFirst().joined(separator: " "))" : "")) {
                    TextField("npx -y @modelcontextprotocol/server-github", text: $commandLine)
                        .quiverField(monospaced: true)
                }
            } else {
                FormField("URL") {
                    HStack(spacing: 8) {
                        TextField("https://mcp.example.com/mcp", text: $url)
                            .quiverField(monospaced: true)
                        Picker("", selection: $sse) {
                            Text("HTTP").tag(false)
                            Text("SSE").tag(true)
                        }
                        .pickerStyle(.segmented).labelsHidden().frame(width: 130)
                    }
                }
            }
            FormField(kind == .command ? "Env vars" : "Headers") {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach($variables) { $variable in
                        HStack(spacing: 8) {
                            TextField(kind == .command ? "NAME" : "Header",
                                      text: $variable.key)
                                .quiverField(monospaced: true).frame(width: 190)
                            Group {
                                if variable.secret {
                                    SecureField("value", text: $variable.value)
                                } else {
                                    TextField("value or ${NAME}", text: $variable.value)
                                }
                            }
                            .quiverField(monospaced: true)
                            Toggle("Secret", isOn: $variable.secret)
                                .toggleStyle(.switch).controlSize(.mini)
                                .onChange(of: variable.key) { _, key in
                                    variable.secret = mcpIsSecretName(key)
                                }
                            Button("Remove", systemImage: "minus.circle") {
                                variables.removeAll { $0.id == variable.id }
                            }
                            .labelStyle(.iconOnly).iconButton()
                        }
                    }
                    Button(kind == .command ? "Add variable" : "Add header",
                           systemImage: "plus") {
                        variables.append(Variable())
                    }
                    .secondaryButton()
                    Text("Secret values go to ~/.quiver/secrets/mcp/.api_keys; "
                         + "the configs get ${NAME} references.")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            FormField("Add to", hint: "Always saved to the Quiver hub (~/.quiver/mcp.json).") {
                FlowLayout(spacing: 6, lineSpacing: 6) {
                    ForEach(harnesses, id: \.self) { harness in
                        Toggle(HarnessMark(harness).name, isOn: Binding(
                            get: { targets.contains(harness) },
                            set: { on in
                                if on { targets.insert(harness) } else { targets.remove(harness) }
                            }))
                        .toggleStyle(.switch).controlSize(.small)
                    }
                }
            }
            Toggle("Replace if it already exists", isOn: $force)
                .toggleStyle(.switch).controlSize(.small)
            if let error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction).secondaryButton()
                Button(working ? "Adding…" : "Add server") { Task { await save() } }
                    .keyboardShortcut(.defaultAction).primaryButton()
                    .disabled(saveDisabled)
            }
        }
        .sheetScaffold(width: 560)
        .background(Theme.panel)
    }

    private func save() async {
        working = true
        error = nil
        defer { working = false }
        do {
            var pairs: [(String, String)] = []
            for variable in variables where !variable.key.isEmpty {
                var value = variable.value
                if variable.secret && !value.hasPrefix("${") {
                    let secretName = kind == .command
                        ? variable.key
                        : mcpHeaderSecretName(server: name, header: variable.key)
                    try await app.swe.mcpSecretSet(name: secretName, value: value)
                    value = "${\(secretName)}"
                }
                pairs.append((variable.key, value))
            }
            let spec: McpServerSpec
            if kind == .command {
                spec = McpServerSpec(name: name, kind: .command(
                    command: words[0], args: Array(words.dropFirst()), env: pairs))
            } else {
                spec = McpServerSpec(name: name, kind: .url(
                    url: url.trimmingCharacters(in: .whitespaces),
                    transport: sse ? "sse" : "http", headers: pairs))
            }
            _ = try await app.swe.mcpAdd(spec, to: targets.sorted(), force: force)
            await reload()
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
