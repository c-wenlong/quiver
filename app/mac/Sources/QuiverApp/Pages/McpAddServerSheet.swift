import QuiverCore
import SwiftUI

/// Add an MCP server: a stdio command line or an http URL, with env vars
/// or headers that can each be marked secret (the value goes to the
/// secrets file, the config gets `${NAME}`). The hub is always written;
/// "Add to" picks which harnesses get it too.
struct McpAddServerSheet: View {
    /// The harnesses the page's chips leave out of the grid.
    var hidden: Set<String> = []
    var reload: () async -> Void

    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss

    enum Kind: String, CaseIterable { case command = "Command", url = "URL" }

    /// How a remote server signs in: a bearer token stored as a secret and
    /// sent as `Authorization: Bearer ${SERVER_TOKEN}`, or OAuth, which
    /// writes nothing: each harness signs in when it first connects.
    enum SignIn: String, CaseIterable { case none = "None", bearer = "Bearer token", oauth = "OAuth" }

    struct Variable: Identifiable {
        var id = UUID()
        var key = ""
        var value = ""
        var secret = false
        /// Once the user flips the switch, key edits stop re-deriving it.
        var secretTouched = false
    }

    @State private var name = ""
    @State private var kind = Kind.command
    @State private var commandLine = ""
    @State private var url = ""
    @State private var sse = false
    @State private var signIn = SignIn.none
    @State private var token = ""
    @State private var showToken = false
    @State private var variables: [Variable] = [Variable()]
    @State private var targets: Set<String> = []
    /// "Add to" took its defaults: once, when an inventory is first there.
    @State private var seeded = false
    /// The last inventory the model loaded, kept through a refresh that
    /// fails: the Replace gate must not lose the existing servers.
    @State private var known: McpInventory?
    @State private var force = false
    @State private var working = false
    @State private var error: String?

    /// Read live from the MCP model, not handed in when the sheet opens: a
    /// card's body doesn't follow its presenter's later state, so a sheet
    /// opened while the page was still loading kept "0 of 0 harnesses".
    private var live: McpInventory? {
        if case .loaded(let inventory) = app.mcp.state { return inventory }
        return nil
    }
    private var inventory: McpInventory? { live ?? known }
    private var harnesses: [String] { inventory?.harnesses ?? [] }
    /// The harnesses the grid compares: the "Add to" defaults.
    private var active: [String] {
        inventory?.activeHarnesses.filter { !hidden.contains($0) } ?? []
    }
    /// The servers already listed, for the Replace gate.
    private var existing: [McpServer] { inventory?.servers ?? [] }

    private static let namePattern = /^[A-Za-z0-9][A-Za-z0-9_.@-]*$/

    /// nil means an unclosed quote, which the field hint reports.
    private var words: [String]? { mcpCommandWords(commandLine) }

    private var commandHint: String? {
        guard let words, !words.isEmpty else { return words == nil ? "Unclosed quote" : nil }
        return "command \(words[0])"
            + (words.count > 1 ? " · args \(words.dropFirst().joined(separator: " "))" : "")
    }

    private var saveDisabled: Bool {
        working || name.wholeMatch(of: Self.namePattern) == nil
            || (kind == .command
                ? (words?.isEmpty ?? true)
                : url.trimmingCharacters(in: .whitespaces).isEmpty
                    || (signIn == .bearer && token.trimmingCharacters(in: .whitespaces).isEmpty))
    }

    /// The harnesses a remote server can go to: a local-only config
    /// (Claude Desktop) can't hold one.
    private var offered: [String] {
        kind == .url ? harnesses.filter { !McpServer.localOnly.contains($0) } : harnesses
    }

    private var leftOut: [String] {
        kind == .url ? harnesses.filter { McpServer.localOnly.contains($0) } : []
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .center) {
                Text("Add a server").font(Theme.sectionTitle).tracking(-0.5)
                Spacer(minLength: 16)
                Button("Close", systemImage: "xmark") { dismiss() }
                    .labelStyle(.iconOnly)
                    .iconButton()
            }
            serverSection
            variablesSection
            whereSection
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
        .sheetScaffold(width: 640)
        .background(Theme.panel)
        .onChange(of: live, initial: true) { _, live in
            guard let live else { return }
            known = live
            // Defaults once, from the first inventory there, even one with
            // nothing active; never over a choice already made.
            guard !seeded else { return }
            seeded = true
            if targets.isEmpty { targets = Set(active) }
        }
    }

    /// Local or Remote in the grid's words, the name, then what runs it.
    private var serverSection: some View {
        Card(title: "Server") {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 8) {
                    KindCard(title: mcpLocalTitle, subtitle: "A command its harnesses launch",
                             selected: kind == .command) { kind = .command }
                    KindCard(title: "Remote", subtitle: "A URL, reached from any machine",
                             selected: kind == .url) { kind = .url }
                }
                FormField("Name") {
                    TextField(kind == .command ? "github" : "linear", text: $name).quiverField()
                }
                if kind == .command {
                    FormField("Command", hint: commandHint, problem: words == nil) {
                        TextField("npx -y @modelcontextprotocol/server-github", text: $commandLine)
                            .quiverField(monospaced: true)
                    }
                } else {
                    FormField("URL") {
                        HStack(spacing: 8) {
                            TextField("https://mcp.example.com/mcp", text: $url)
                                .quiverField(monospaced: true)
                            Tabs(options: [(value: false, title: "HTTP"), (value: true, title: "SSE")],
                                 selection: $sse)
                                .frame(height: Theme.fieldHeight)
                        }
                    }
                    signInField
                }
            }
        }
    }

    private var signInField: some View {
        FormField("Sign-in") {
            Tabs(options: SignIn.allCases.map { (value: $0, title: $0.rawValue) }, selection: $signIn)
                .frame(height: Theme.fieldHeight)
                .fixedSize()
            switch signIn {
            case .bearer:
                HStack(spacing: 8) {
                    Group {
                        if showToken { TextField("token", text: $token) } else { SecureField("token", text: $token) }
                    }
                    .quiverField(monospaced: true)
                    Button(showToken ? "Hide" : "Show", systemImage: showToken ? "eye.slash" : "eye") { showToken.toggle() }
                        .labelStyle(.iconOnly)
                        .iconButton()
                        .help(showToken ? "Hide the token" : "Show the token")
                }
                Text("Sent as Authorization: Bearer ${\(mcpSecretName(server: name.isEmpty ? "SERVER" : name, key: "TOKEN"))}")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            case .oauth:
                Text("Nothing is saved: each harness signs in the first time it connects, if it supports OAuth.")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            case .none:
                EmptyView()
            }
        }
    }

    /// Env vars for a command, headers for a URL; each can be a secret.
    private var variablesSection: some View {
        Card(title: kind == .command ? "Env vars" : "Other headers") {
            VStack(alignment: .leading, spacing: 10) {
                ForEach($variables) { $variable in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 8) {
                            TextField(kind == .command ? "NAME" : "Header", text: $variable.key)
                                .quiverField(monospaced: true)
                                .frame(width: 170)
                            Group {
                                if variable.secret {
                                    SecureField("value", text: $variable.value)
                                } else {
                                    TextField("value or ${NAME}", text: $variable.value)
                                }
                            }
                            .quiverField(monospaced: true)
                            HStack(spacing: 6) {
                                Text("Secret").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                SettingSwitch(title: "Secret", isOn: Binding(
                                    get: { variable.secret },
                                    set: { variable.secret = $0; variable.secretTouched = true }))
                            }
                            .fixedSize()
                            Button("Remove", systemImage: "minus") {
                                variables.removeAll { $0.id == variable.id }
                            }
                            .labelStyle(.iconOnly)
                            .iconButton()
                        }
                        .onChange(of: variable.key) { _, key in
                            if !variable.secretTouched {
                                variable.secret = mcpIsSecretName(key)
                            }
                        }
                        if variable.secret && !variable.key.isEmpty {
                            Text("saved as ${\(mcpSecretName(server: name.isEmpty ? "SERVER" : name, key: variable.key))}")
                                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                        }
                    }
                }
                Button(kind == .command ? "Add variable" : "Add header", systemImage: "plus") {
                    variables.append(Variable())
                }
                .secondaryButton()
                Text("Secret values go to ~/.quiver/secrets/mcp/.api_keys; "
                     + "the configs get ${NAME} references.")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
        }
    }

    /// Which harnesses get the server, and whether it may overwrite one.
    private var whereSection: some View {
        Card(title: "Where") {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .center, spacing: 16) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Add to").font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
                        Text("Always saved to the Quiver hub too")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                    Spacer(minLength: 8)
                    HarnessTargetsMenu(harnesses: offered, active: active.filter(offered.contains),
                                       targets: $targets)
                }
                .padding(.bottom, leftOut.isEmpty ? 14 : 6)
                if !leftOut.isEmpty {
                    Text("\(leftOut.map { HarnessMark($0).name }.joined(separator: ", ")) takes local servers only in its config; add remote ones in the app itself.")
                        .font(Theme.caption).foregroundStyle(Theme.warningText)
                        .padding(.bottom, 14)
                }
                Rectangle().fill(Theme.border).frame(height: Theme.hairline)
                HStack(alignment: .center, spacing: 16) {
                    Text("Replace if it already exists")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
                    Spacer(minLength: 8)
                    SettingSwitch(title: "Replace if it already exists", isOn: $force)
                }
                .padding(.top, 14)
            }
        }
    }

    private func save() async {
        working = true
        error = nil
        defer { working = false }
        do {
            // No secrets may land in the store for an add swe would
            // refuse: check the name before mcpSecretSet runs.
            // Only the hub and the ticked harnesses get written, so only
            // a copy there is a conflict, as swe judges it.
            let picked = targets.intersection(offered)
            let clash = existing.first { $0.name == name }
            if let clash, clash.inHub || !picked.isDisjoint(with: clash.harnesses), !force {
                error = "A server named \(name) already exists. Turn on Replace to overwrite it."
                return
            }
            // Every refusal comes before the first secret is stored, so a
            // rejected add leaves the store as it was.
            let filled = variables.filter { !$0.key.isEmpty }
            let stored = filled.filter { $0.secret && !$0.value.hasPrefix("${") }
            var secretNames = stored.map { mcpSecretName(server: name, key: $0.key) }
            let bearer = kind == .url && signIn == .bearer
            let tokenRef = mcpSecretName(server: name, key: "TOKEN")
            if bearer {
                if filled.contains(where: { $0.key.lowercased() == "authorization" }) {
                    error = "Give the token once: as the bearer token or an Authorization header."
                    return
                }
                secretNames.append(tokenRef)
            }
            if let twice = Dictionary(grouping: secretNames, by: { $0 }).first(where: { $0.value.count > 1 })?.key {
                error = "Two values would be saved as the secret \(twice). Rename one of them."
                return
            }
            // The whole spec is built before any secret is stored, so
            // nothing past this point can refuse with secrets left behind.
            let pairs: [(String, String)] = filled.map { variable in
                (variable.key, variable.secret && !variable.value.hasPrefix("${")
                    ? "${\(mcpSecretName(server: name, key: variable.key))}" : variable.value)
            } + (bearer ? [("Authorization", "Bearer ${\(tokenRef)}")] : [])
            let spec: McpServerSpec
            if kind == .command {
                guard let words, !words.isEmpty else { return }
                spec = McpServerSpec(name: name, kind: .command(
                    command: words[0], args: Array(words.dropFirst()), env: pairs))
            } else {
                spec = McpServerSpec(name: name, kind: .url(
                    url: url.trimmingCharacters(in: .whitespaces),
                    transport: sse ? "sse" : "http", headers: pairs))
            }
            // The secrets go in before the add: swe refuses an add whose
            // ${NAME} has no value yet, and writes each harness's config
            // with the values filled in.
            var values = stored.map { (mcpSecretName(server: name, key: $0.key), $0.value) }
            if bearer { values.append((tokenRef, token.trimmingCharacters(in: .whitespaces))) }
            // Names this add stored for the first time, so a failed add
            // removes them again instead of leaving them in the store.
            var created: [String] = []
            do {
                for (secret, value) in values {
                    let result = try await app.swe.mcpSecretSet(name: secret, value: value)
                    if result.replaced == false { created.append(secret) }
                }
                _ = try await app.swe.mcpAdd(spec, to: picked.sorted(), force: force)
            } catch {
                // swe saves the hub before the harness files, so once the
                // hub holds the server its ${NAME}s must keep their values.
                // A list that can't be read keeps them too.
                let hub = try? await app.swe.mcpServers(health: false)
                if let hub, !hub.servers.contains(where: { $0.name == name && $0.inHub }) {
                    for secret in created { _ = try? await app.swe.mcpSecretRemove(name: secret) }
                }
                throw error
            }
            // Kept until the add lands, so a refused one can be retried
            // without typing the token again.
            token = ""
            await reload()
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// "Add to" as one select: the face says how many of the harnesses are
/// ticked, and the popover is a checklist that stays open so several can
/// change in one go, with "All active" and "None" at the top. A harness
/// shows as its logo, with a word only where the logo is ambiguous.
private struct HarnessTargetsMenu: View {
    var harnesses: [String]
    var active: [String]
    @Binding var targets: Set<String>
    @State private var open = false
    @State private var hovering = false

    var body: some View {
        Button { open.toggle() } label: {
            HStack(spacing: 6) {
                Text(mcpTargetsSummary(targets, of: harnesses))
                    .foregroundStyle(Theme.textPrimary).lineLimit(1)
                Spacer(minLength: 4)
                Image(systemName: "chevron.down").font(Theme.font(size: 12))
                    .foregroundStyle(Theme.textSecondary)
            }
            .font(Theme.bodySmall)
            .padding(.horizontal, 12)
            .frame(height: Theme.fieldHeight)
            .fixedSize()
            .background(Theme.field, in: .rect(cornerRadius: Theme.selectRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.selectRadius)
                .strokeBorder(hovering ? Theme.borderHover : Theme.border, lineWidth: 1))
            .contentShape(.rect(cornerRadius: Theme.selectRadius))
        }
        .plainButton(cornerRadius: Theme.selectRadius)
        .onHover { hovering = $0 }
        .fixedSize()
        .accessibilityLabel("Add to")
        .accessibilityValue(mcpTargetsSummary(targets, of: harnesses))
        .popover(isPresented: $open, arrowEdge: .bottom) { checklist }
    }

    private var checklist: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 4) {
                ChecklistShortcut(title: "All active") { targets = Set(active) }
                ChecklistShortcut(title: "None") { targets = [] }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 6)
            .padding(.bottom, 6)
            Rectangle().fill(Theme.border).frame(height: Theme.hairline)
                .padding(.bottom, 6)
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(harnesses, id: \.self) { harness in
                        ChecklistRow(harness: harness, isOn: targets.contains(harness)) {
                            if targets.contains(harness) { targets.remove(harness) } else { targets.insert(harness) }
                        }
                    }
                }
            }
            .scrollBounceBehavior(.basedOnSize)
            .frame(maxHeight: 360)
        }
        .padding(.vertical, 8)
        .frame(minWidth: 200)
        .fixedSize(horizontal: true, vertical: true)
    }
}

/// A quiet text button for the checklist's shortcuts.
private struct ChecklistShortcut: View {
    var title: String
    var action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(Theme.control)
                .foregroundStyle(hovering ? Theme.textPrimary : Theme.textSecondary)
                .padding(.horizontal, 8)
                .frame(height: 24)
                .background(hovering ? Theme.rowHover : .clear, in: .rect(cornerRadius: Theme.smallRadius))
                .contentShape(.rect(cornerRadius: Theme.smallRadius))
        }
        .plainButton(cornerRadius: Theme.smallRadius)
        .onHover { hovering = $0 }
    }
}

/// One harness in the checklist: a check when it's in, then its mark.
private struct ChecklistRow: View {
    var harness: String
    var isOn: Bool
    var toggle: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: toggle) {
            HStack(spacing: 10) {
                Image(systemName: "checkmark")
                    .font(Theme.font(size: 11, weight: .semibold))
                    .foregroundStyle(Theme.interactive)
                    .frame(width: 14)
                    .opacity(isOn ? 1 : 0)
                HarnessMarkView(harness: harness, size: 16)
                    .font(Theme.bodySmall)
                    .foregroundStyle(Theme.textPrimary)
                Spacer(minLength: 12)
            }
            .padding(.horizontal, 12)
            .frame(height: 32)
            .background(hovering ? Theme.rowHover : .clear)
            .contentShape(.rect)
        }
        .plainButton(cornerRadius: 0)
        .onHover { hovering = $0 }
        .accessibilityLabel(HarnessMark(harness).name)
        .accessibilityAddTraits(isOn ? .isSelected : [])
    }
}

/// Local or Remote: the grid's section words, with a line under each.
private struct KindCard: View {
    var title: String
    var subtitle: String
    var selected: Bool
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(Theme.bodySmall.weight(.medium)).foregroundStyle(Theme.textPrimary)
                    .lineLimit(2).multilineTextAlignment(.leading)
                Text(subtitle).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background(selected ? Theme.selected : .clear, in: .rect(cornerRadius: Theme.controlRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
                .strokeBorder(selected ? Theme.borderStrong : Theme.border, lineWidth: 1))
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        .plainButton(cornerRadius: Theme.controlRadius)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}
