import AppKit
import QuiverCore
import SwiftUI

/// Where the plugins in `~/.quiver/plugins` have not reached Claude Code or
/// Codex, each with the fix swe would run.
struct PluginsPage: View {
    @Environment(AppModel.self) private var app
    @State private var model = PluginsModel()
    @State private var confirming: PluginDrift?
    @State private var confirmingAll = false

    var body: some View {
        Page(title: "Plugins",
             subtitle: "Where the copies Claude Code and Codex run no longer match ~/.quiver/plugins.") {
            if model.fixAllCount > 0 || model.fixAllProgress != nil {
                Button(fixAllTitle) { confirmingAll = true }
                    .primaryButton()
                    .disabled(model.fixing != nil)
                    .help("Run every fix swe can run, one at a time")
            }
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.load(swe: app.swe) } }
                .iconButton()
                .disabled(model.fixing != nil)
                .help("Check again")
        } content: {
            if let error = model.fixError {
                ErrorCard(message: error)
            } else if let fixed = model.lastFixed {
                Card {
                    Label("Ran \(fixed)", systemImage: "checkmark.circle")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                }
            }
            switch model.state {
            case .idle, .loading:
                ProgressView().frame(maxWidth: .infinity)
            case .failed(let message):
                ErrorCard(message: message)
            case .loaded(let report) where report.sources == 0:
                Card { Label("No plugins in ~/.quiver/plugins, so there is nothing to keep in step.", systemImage: "puzzlepiece.extension").foregroundStyle(Theme.textSecondary) }
            case .loaded(let report) where report.drift.isEmpty:
                Card { Label("Claude Code and Codex run what ~/.quiver/plugins holds.", systemImage: "checkmark.seal").foregroundStyle(Theme.textPrimary) }
            case .loaded(let report):
                let harnesses = report.drift.map(\.harness).reduce(into: [String]()) { if !$0.contains($1) { $0.append($1) } }
                ForEach(harnesses, id: \.self) { harness in
                    let rows = report.drift.filter { $0.harness == harness }
                    Card {
                        HarnessTitle(harness: harness, findings: rows.count)
                        ForEach(rows) { finding in
                            DriftRow(finding: finding, running: model.fixing == finding.id,
                                     busy: model.fixing != nil) { confirming = finding }
                            if finding != rows.last { Divider().overlay(Theme.border) }
                        }
                    }
                }
            }
        }
        .task(id: app.swePath) { await model.load(swe: app.swe) }
        .confirmationDialog("Run this fix?", isPresented: .init(
            get: { confirming != nil }, set: { if !$0 { confirming = nil } }), presenting: confirming) { finding in
            Button("Run") { Task { await model.fix(finding, swe: app.swe) } }
        } message: { finding in
            Text("swe will check the finding still applies, then run:\n\(finding.fix)")
        }
        .confirmationDialog(model.fixAllCount == 1 ? "Run 1 fix?" : "Run \(model.fixAllCount) fixes?",
                            isPresented: $confirmingAll) {
            Button("Run all") { Task { await model.fixAll(swe: app.swe) } }
        } message: {
            Text("One at a time, stopping at the first that fails. swe checks each still applies before it runs:\n\n"
                 + runnableFixes.joined(separator: "\n"))
        }
    }

    private var fixAllTitle: String {
        guard let progress = model.fixAllProgress else { return "Fix all" }
        return "Fixing \(min(progress.done + 1, progress.total)) of \(progress.total)…"
    }

    private var runnableFixes: [String] {
        guard case .loaded(let report) = model.state else { return [] }
        return report.drift.filter(\.runnable).map(\.fix)
    }
}

/// The card's heading: the harness logo, its name and how many findings.
private struct HarnessTitle: View {
    var harness: String
    var findings: Int

    var body: some View {
        HStack(spacing: 8) {
            HarnessIcon(harness: harness, size: 16)
            Text(UsageFormat.harness(harness)).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            Text(findings == 1 ? "1 finding" : "\(findings) findings")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
        }
    }
}

private struct DriftRow: View {
    var finding: PluginDrift
    var running: Bool
    var busy: Bool
    var fix: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Circle().fill(finding.isSevere ? Theme.danger : Theme.warning)
                .frame(width: 8, height: 8).padding(.top, 6)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(finding.ref).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    Badge(finding.kindTitle)
                }
                Text(finding.detail).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                if !finding.paths.isEmpty {
                    Text(pathsLine).font(Theme.caption).foregroundStyle(Theme.textMuted)
                        .lineLimit(2).truncationMode(.middle)
                }
                HStack(alignment: .top, spacing: 6) {
                    // A code block: bg.code, 6/8 padding, radius 4, mono 12/16.
                    Text(finding.fix)
                        .font(.system(size: 12, design: .monospaced))
                        .lineSpacing(2)
                        .foregroundStyle(Theme.textSecondary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.vertical, 6)
                        .padding(.horizontal, 8)
                        .background(Theme.code, in: .rect(cornerRadius: 4))
                        // bg.code is the card's own colour in dark; the hairline keeps the block visible.
                        .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(Theme.border, lineWidth: 1))
                    Button("Copy", systemImage: "doc.on.doc") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(finding.fix, forType: .string)
                    }
                    .labelStyle(.iconOnly)
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 24, height: 28)
                    .contentShape(.rect)
                    .plainButton(cornerRadius: 4)
                    .help("Copy")
                }
                if !finding.runnable {
                    Text("No command does this, so make the edit by hand.")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            Spacer(minLength: 8)
            if finding.runnable {
                if running {
                    ProgressView().controlSize(.small).frame(height: Theme.controlHeight + 4)
                } else {
                    Button("Fix", action: fix)
                        .secondaryButton()
                        .disabled(busy)
                }
            }
        }
    }

    private var pathsLine: String {
        let shown = finding.paths.prefix(3).joined(separator: ", ")
        let more = finding.paths.count - 3
        return more > 0 ? "\(shown) and \(more) more" : shown
    }
}
