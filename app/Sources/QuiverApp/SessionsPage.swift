import QuiverCore
import SwiftUI

/// Every recent session across harnesses, with what it cost. Modelled on
/// TokenTracker's Sessions page: search and filters, a summary strip, then
/// the sessions grouped by the day they were last active.
struct SessionsPage: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        if let model = app.sessions {
            SessionsContent(model: model)
        } else {
            Page(title: "Sessions", subtitle: "Local sessions, usage and estimated cost.") {
                Card { Label(app.overviewError ?? "The usage ledger is unavailable.", systemImage: "exclamationmark.triangle") }
            }
        }
    }
}

private struct SessionsContent: View {
    @Environment(AppModel.self) private var app
    @Bindable var model: SessionsModel
    @State private var detail: SessionsModel.Row?
    @State private var actionError: String?

    var body: some View {
        Page(title: "Sessions", subtitle: "Local sessions, usage and estimated cost.") {
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.load() } }
                .buttonStyle(IconButtonStyle())
                .disabled(model.isLoading)
                .help("List sessions again")
        } content: {
            FilterBar(model: model)
            SummaryStrip(summary: model.summary)
            if let error = model.error ?? actionError {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(Theme.bodySmall).foregroundStyle(Theme.warning)
            }
            if !model.failures.isEmpty {
                Label("Couldn't read: \(model.failures.joined(separator: "; "))", systemImage: "exclamationmark.circle")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            list
        }
        .task(id: app.swePath) { await model.load() }
        .sheet(item: $detail) { row in
            SessionDetail(row: row, resume: { resume(row) }, close: { detail = nil })
        }
    }

    @ViewBuilder private var list: some View {
        let groups = model.groupedByDay()
        VStack(spacing: 0) {
            HStack(spacing: 16) {
                Text("Session / project").frame(maxWidth: .infinity, alignment: .leading)
                Text("Tokens").frame(width: 80, alignment: .trailing)
                Text("Spend").frame(width: 84, alignment: .trailing)
                Color.clear.frame(width: Theme.iconButton)
            }
            .font(Theme.tableHeader).foregroundStyle(Theme.textSecondary)
            .padding(.bottom, 10)
            Divider().overlay(Theme.border)

            if model.rows.isEmpty && model.isLoading {
                ProgressView("Listing sessions…").padding(.vertical, 40)
            } else if groups.isEmpty {
                ContentUnavailableView("No sessions", systemImage: "clock",
                                       description: Text("Nothing matches these filters."))
                    .padding(.vertical, 24)
            }
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(groups) { group in
                    Text(group.day, format: .dateTime.month(.abbreviated).day().year())
                        .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                        .padding(.top, 18).padding(.bottom, 10)
                    Divider().overlay(Theme.border)
                    ForEach(group.rows) { row in
                        SessionRow(row: row, open: { detail = row }, resume: { resume(row) })
                        Divider().overlay(Theme.border)
                    }
                }
            }
        }
    }

    private func resume(_ row: SessionsModel.Row) {
        Task {
            do {
                try await app.resume(row)
                actionError = nil
            } catch {
                actionError = "Couldn't resume: \(error.localizedDescription)"
            }
        }
    }
}

private func copy(_ text: String) {
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(text, forType: .string)
}

// MARK: - Filters and summary

private struct FilterBar: View {
    @Bindable var model: SessionsModel

    var body: some View {
        HStack(spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundStyle(Theme.textSecondary)
                TextField("Search title, project, model or id", text: $model.filter.search)
                    .textFieldStyle(.plain)
                    .font(Theme.bodySmall)
            }
            .padding(.horizontal, 12)
            .frame(height: 36)
            .frame(minWidth: 180, maxWidth: .infinity)
            .background(Theme.field, in: .rect(cornerRadius: Theme.controlRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))

            Select(title: model.filter.harness.map(UsageFormat.harness) ?? "All tools", symbol: nil,
                   options: [(nil, "All tools")] + model.harnesses.map { (Optional($0), UsageFormat.harness($0)) },
                   selection: $model.filter.harness)
            Select(title: model.filter.window.title, symbol: "calendar",
                   options: SessionsModel.Window.allCases.map { ($0, $0.title) },
                   selection: $model.filter.window)
            Select(title: model.filter.project ?? "All projects", symbol: "folder",
                   options: [(nil, "All projects")] + model.projects.map { (Optional($0), $0) },
                   selection: $model.filter.project)
            Select(title: model.filter.model ?? "All models", symbol: nil,
                   options: [(nil, "All models")] + model.models.map { (Optional($0), $0) },
                   selection: $model.filter.model)
        }
    }
}

/// A TokenTracker select: a 36pt bordered field with a chevron, opening a menu.
private struct Select<Value: Hashable>: View {
    var title: String
    var symbol: String?
    var options: [(value: Value, title: String)]
    @Binding var selection: Value

    var body: some View {
        Menu {
            ForEach(options, id: \.value) { option in
                Button {
                    selection = option.value
                } label: {
                    if option.value == selection { Label(option.title, systemImage: "checkmark") } else { Text(option.title) }
                }
            }
        } label: {
            HStack(spacing: 6) {
                if let symbol { Image(systemName: symbol).foregroundStyle(Theme.textSecondary) }
                Text(title).lineLimit(1).truncationMode(.tail)
                Spacer(minLength: 4)
                Image(systemName: "chevron.down").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
            }
            .font(Theme.bodySmall)
            .foregroundStyle(Theme.textPrimary)
            .padding(.horizontal, 12)
            .frame(width: 150, height: 36)
            .background(Theme.field, in: .rect(cornerRadius: Theme.controlRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        .menuStyle(.button)
        .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))
        .menuIndicator(.hidden)
        .fixedSize()
    }
}

private struct SummaryStrip: View {
    var summary: SessionsModel.Summary

    var body: some View {
        HStack(spacing: 0) {
            cell("Matching sessions", Text("\(summary.sessions)"))
            Divider().overlay(Theme.border).padding(.vertical, 14)
            cell("Tokens", Text(UsageFormat.tokens(summary.totals.totalTokens)))
            Divider().overlay(Theme.border).padding(.vertical, 14)
            cell("Spend", Text(UsageFormat.dollars(summary.totals.costUSD)).foregroundStyle(Theme.accent),
                 note: summary.totals.unpricedTokens > 0
                    ? "Plus \(UsageFormat.tokens(summary.totals.unpricedTokens)) tokens with no list price" : nil)
        }
        .frame(height: 76)
        .background(Theme.card, in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.border, lineWidth: 1))
    }

    private func cell(_ label: String, _ value: Text, note: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                Text(label).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                if let note { Image(systemName: "info.circle").font(Theme.caption).foregroundStyle(Theme.textSecondary).help(note) }
            }
            value.font(Theme.figure).monospacedDigit().foregroundStyle(Theme.textPrimary)
        }
        .padding(.horizontal, 22)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Rows

private struct SessionRow: View {
    var row: SessionsModel.Row
    var open: () -> Void
    var resume: () -> Void
    @State private var hovering = false

    var body: some View {
        let s = row.session
        HStack(alignment: .top, spacing: 16) {
            HStack(alignment: .top, spacing: 12) {
                RoundedRectangle(cornerRadius: 3).fill(Theme.harness(s.harness))
                    .frame(width: 10, height: 10).padding(.top, 5)
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text(s.title ?? s.id).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                        StatusBadge(status: s.status)
                    }
                    Text(details)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(row.usage.map { UsageFormat.tokens($0.totals.totalTokens) } ?? "–")
                .font(Theme.bodySmall).monospacedDigit().foregroundStyle(Theme.textPrimary)
                .frame(width: 80, alignment: .trailing)
            Text(row.usage.map { UsageFormat.dollars($0.totals.costUSD) } ?? "–")
                .font(Theme.bodySmall).monospacedDigit()
                .foregroundStyle(row.usage == nil ? Theme.textSecondary : Theme.accent)
                .frame(width: 84, alignment: .trailing)
            Button("Resume", systemImage: "play", action: resume)
                .buttonStyle(IconButtonStyle())
                .disabled(s.resume == nil)
                .opacity(hovering ? 1 : 0.0001)
                .help(s.resume == nil ? "This harness has no resume command" : "Resume in your terminal")
        }
        .padding(.vertical, 12)
        .padding(.horizontal, 8)
        .background(hovering ? Theme.rowHover : .clear, in: .rect(cornerRadius: Theme.controlRadius))
        .contentShape(.rect)
        .onHover { hovering = $0 }
        .onTapGesture(count: 2, perform: open)
        .contextMenu {
            Button("Details…", action: open)
            Button("Resume in Terminal", action: resume).disabled(s.resume == nil)
            Divider()
            Button("Copy Session ID") { copy(s.id) }
            if let cwd = s.cwd {
                Button("Show Folder in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: cwd)])
                }
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityAction(named: "Details", open)
        .accessibilityAction(named: "Resume", resume)
    }

    /// project · agent · models · time · duration
    var details: String {
        let s = row.session
        var parts: [String] = []
        if let project = s.project { parts.append(project) }
        parts.append(s.agent)
        if let models = row.usage?.models, !models.isEmpty { parts.append(models.joined(separator: ", ")) }
        parts.append(s.lastActiveAt.formatted(date: .omitted, time: .shortened))
        if let duration = row.duration, duration >= 60 { parts.append(Self.duration(duration)) }
        return parts.joined(separator: " · ")
    }

    static func duration(_ seconds: TimeInterval) -> String {
        let minutes = Int(seconds / 60)
        return minutes >= 60 ? "\(minutes / 60)h \(minutes % 60)m" : "\(minutes)m"
    }
}

private struct StatusBadge: View {
    var status: String?

    var body: some View {
        switch status {
        case "active": tag("Active", Theme.success)
        case "error": tag("Error", Theme.danger)
        case "interrupted": tag("Interrupted", Theme.warning)
        case "followup": tag("Waiting", Theme.textSecondary)
        default: EmptyView()
        }
    }

    private func tag(_ text: String, _ color: Color) -> some View {
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(text)
        }
        .font(.system(size: 11, weight: .medium))
        .foregroundStyle(Theme.textSecondary)
        .padding(.horizontal, 6).padding(.vertical, 2)
        .background(Theme.subtle, in: .capsule)
    }
}

// MARK: - Detail

private struct SessionDetail: View {
    var row: SessionsModel.Row
    var resume: () -> Void
    var close: () -> Void

    var body: some View {
        let s = row.session
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 3).fill(Theme.harness(s.harness)).frame(width: 10, height: 10)
                Text(s.title ?? s.id).font(Theme.sectionTitle).tracking(-0.5).lineLimit(2)
                StatusBadge(status: s.status)
            }
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                field("Harness", s.agent)
                field("Folder", s.cwd ?? "Unknown")
                field("Last active", s.lastActiveAt.formatted(date: .abbreviated, time: .shortened))
                field("Session id", s.id)
                if let usage = row.usage {
                    field("Account", usage.accountHome)
                    field("Models", usage.models.joined(separator: ", "))
                    if let duration = row.duration, duration >= 60 {
                        field("Ran for", SessionRow.duration(duration))
                    }
                }
            }
            if let usage = row.usage {
                HStack(spacing: 0) {
                    figure("Input", UsageFormat.tokens(usage.totals.inputTokens))
                    figure("Cache read", UsageFormat.tokens(usage.totals.cacheReadTokens))
                    figure("Cache write", UsageFormat.tokens(usage.totals.cacheWriteTokens))
                    figure("Output", UsageFormat.tokens(usage.totals.outputTokens))
                    figure("Spend", UsageFormat.dollars(usage.totals.costUSD), money: true)
                }
                .padding(.vertical, 12)
                .background(Theme.card, in: .rect(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.border, lineWidth: 1))
                if usage.totals.unpricedTokens > 0 {
                    Text("\(UsageFormat.tokens(usage.totals.unpricedTokens)) tokens from models with no list price.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            } else {
                Text("No token usage recorded for this session in the ledger.")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            }
            if let command = s.resume {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Resume command").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    Text(command.argv.joined(separator: " "))
                        .font(.system(size: 12, design: .monospaced))
                        .textSelection(.enabled)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Theme.subtle, in: .rect(cornerRadius: Theme.controlRadius))
                }
            }
            HStack {
                Button("Copy session id") { copy(s.id) }.buttonStyle(SecondaryButtonStyle())
                Spacer()
                Button("Close", action: close).keyboardShortcut(.cancelAction).buttonStyle(SecondaryButtonStyle())
                Button("Resume in Terminal") { resume(); close() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(s.resume == nil)
            }
        }
        .padding(24)
        .frame(width: 640)
        .background(Theme.panel)
    }

    @ViewBuilder private func field(_ name: String, _ value: String) -> some View {
        GridRow {
            Text(name).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            Text(value).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                .textSelection(.enabled).lineLimit(2).truncationMode(.middle)
        }
    }

    private func figure(_ label: String, _ value: String, money: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            Text(value).font(Theme.rowTitle).monospacedDigit()
                .foregroundStyle(money ? Theme.accent : Theme.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 14)
    }
}
