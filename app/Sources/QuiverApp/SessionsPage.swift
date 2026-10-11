import QuiverCore
import SwiftUI

/// Every recent session across harnesses, with what it cost.
struct SessionsPage: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        if let model = app.sessions {
            SessionsContent(model: model)
        } else {
            Page(title: "Sessions", subtitle: "Recent sessions across every harness.") {
                Card { Label(app.overviewError ?? "The usage ledger is unavailable.", systemImage: "exclamationmark.triangle") }
            }
        }
    }
}

private struct SessionsContent: View {
    @Environment(AppModel.self) private var app
    @Bindable var model: SessionsModel
    @State private var selection: SessionsModel.Row.ID?
    @State private var detail: SessionsModel.Row?
    @State private var actionError: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Sessions").font(.largeTitle.weight(.semibold))
                Text(model.rows.isEmpty
                     ? "Recent sessions across every harness, with tokens and spend from the ledger."
                     : "Showing \(model.visible.count) of \(model.rows.count) sessions from the last \(SessionsModel.days) days.")
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            FilterBar(model: model)
            if let error = model.error ?? actionError {
                Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(Theme.warning)
            }
            if !model.failures.isEmpty {
                Label("Couldn't read: \(model.failures.joined(separator: "; "))", systemImage: "exclamationmark.circle")
                    .font(.caption).foregroundStyle(.secondary)
            }
            table
        }
        .padding(28)
        .task(id: app.swePath) { await model.load() }
        .sheet(item: $detail) { row in
            SessionDetail(row: row, resume: { resume(row) }, close: { detail = nil })
        }
    }

    private var table: some View {
        Table(model.visible, selection: $selection) {
            TableColumn("Session") { row in
                HStack(spacing: 8) {
                    RoundedRectangle(cornerRadius: 3).fill(Theme.harness(row.session.harness)).frame(width: 8, height: 8)
                    Text(row.session.title ?? row.session.id).lineLimit(1)
                    StatusBadge(status: row.session.status)
                }
            }
            .width(min: 220, ideal: 330)
            TableColumn("Project") { row in
                Text(row.session.project ?? "None").foregroundStyle(.secondary).lineLimit(1)
            }
            .width(min: 80, ideal: 120)
            TableColumn("Harness") { row in Text(row.session.agent).lineLimit(1) }
                .width(min: 70, ideal: 95)
            TableColumn("Last active") { row in
                Text(row.session.lastActiveAt, format: .relative(presentation: .named)).foregroundStyle(.secondary)
            }
            .width(min: 80, ideal: 105)
            TableColumn("Tokens") { row in
                Text(row.usage.map { UsageFormat.tokens($0.totals.totalTokens) } ?? "–")
                    .monospacedDigit().frame(maxWidth: .infinity, alignment: .trailing)
            }
            .width(min: 60, ideal: 70)
            TableColumn("Spend") { row in
                Text(row.usage.map { UsageFormat.dollars($0.totals.costUSD) } ?? "–")
                    .monospacedDigit().frame(maxWidth: .infinity, alignment: .trailing)
            }
            .width(min: 60, ideal: 80)
        }
        .contextMenu(forSelectionType: SessionsModel.Row.ID.self) { ids in
            if let row = row(ids.first) {
                Button("Details…") { detail = row }
                Button("Resume in Terminal") { resume(row) }.disabled(row.session.resume == nil)
                Divider()
                Button("Copy Session ID") { copy(row.session.id) }
                if let cwd = row.session.cwd {
                    Button("Show Folder in Finder") {
                        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: cwd)])
                    }
                }
            }
        } primaryAction: { ids in
            detail = row(ids.first)
        }
        .overlay {
            if model.rows.isEmpty && model.isLoading {
                ProgressView("Listing sessions…")
            } else if model.visible.isEmpty && !model.isLoading {
                ContentUnavailableView("No sessions", systemImage: "clock",
                                       description: Text("Nothing matches these filters."))
            }
        }
    }

    private func row(_ id: SessionsModel.Row.ID?) -> SessionsModel.Row? {
        model.rows.first { $0.id == id }
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

private struct FilterBar: View {
    @Bindable var model: SessionsModel

    var body: some View {
        HStack(spacing: 10) {
            TextField("Search", text: $model.filter.search)
                .textFieldStyle(.roundedBorder)
                .frame(minWidth: 120, maxWidth: 200)
            Picker("Window", selection: $model.filter.window) {
                ForEach(SessionsModel.Window.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            Picker("Harness", selection: $model.filter.harness) {
                Text("All harnesses").tag(String?.none)
                ForEach(model.harnesses, id: \.self) { Text(UsageFormat.harness($0)).tag(String?.some($0)) }
            }
            .frame(width: 132)
            Picker("Project", selection: $model.filter.project) {
                Text("All projects").tag(String?.none)
                ForEach(model.projects, id: \.self) { Text($0).tag(String?.some($0)) }
            }
            .frame(width: 132)
            Picker("Model", selection: $model.filter.model) {
                Text("All models").tag(String?.none)
                ForEach(model.models, id: \.self) { Text($0).tag(String?.some($0)) }
            }
            .frame(width: 132)
            Spacer(minLength: 0)
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.load() } }
                .labelStyle(.iconOnly)
                .disabled(model.isLoading)
        }
        .labelsHidden()
    }
}

private struct StatusBadge: View {
    var status: String?

    var body: some View {
        switch status {
        case "active": tag("Active", Theme.accent, "circle.fill")
        case "error": tag("Error", Theme.danger, "exclamationmark.triangle.fill")
        case "interrupted": tag("Interrupted", Theme.warning, "pause.circle.fill")
        case "followup": tag("Waiting", .secondary, "bubble.left.fill")
        default: EmptyView()
        }
    }

    private func tag(_ text: String, _ color: some ShapeStyle, _ symbol: String) -> some View {
        Label(text, systemImage: symbol)
            .font(.caption2.weight(.medium))
            .foregroundStyle(color)
            .labelStyle(.titleAndIcon)
    }
}

private struct SessionDetail: View {
    var row: SessionsModel.Row
    var resume: () -> Void
    var close: () -> Void

    var body: some View {
        let s = row.session
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 3).fill(Theme.harness(s.harness)).frame(width: 10, height: 10)
                Text(s.title ?? s.id).font(.title2.weight(.semibold)).lineLimit(2)
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
                    field("Ran", "\(usage.firstTimestamp.formatted(date: .abbreviated, time: .shortened)) to \(usage.lastTimestamp.formatted(date: .omitted, time: .shortened))")
                }
            }
            if let usage = row.usage {
                Card(title: "Usage") {
                    Grid(alignment: .trailing, horizontalSpacing: 18, verticalSpacing: 6) {
                        GridRow {
                            Text("Input"); Text("Cache read"); Text("Cache write"); Text("Output"); Text("Spend")
                        }
                        .font(.caption).foregroundStyle(.secondary)
                        GridRow {
                            Text(UsageFormat.tokens(usage.totals.inputTokens))
                            Text(UsageFormat.tokens(usage.totals.cacheReadTokens))
                            Text(UsageFormat.tokens(usage.totals.cacheWriteTokens))
                            Text(UsageFormat.tokens(usage.totals.outputTokens))
                            Text(UsageFormat.dollars(usage.totals.costUSD)).fontWeight(.semibold)
                        }
                        .monospacedDigit()
                    }
                    if usage.totals.unpricedTokens > 0 {
                        Text("\(UsageFormat.tokens(usage.totals.unpricedTokens)) tokens from models with no list price.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            } else {
                Text("No token usage recorded for this session in the ledger.").foregroundStyle(.secondary)
            }
            if let resumeCommand = s.resume {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Resume command").font(.caption).foregroundStyle(.secondary)
                    Text(resumeCommand.argv.joined(separator: " "))
                        .font(.system(.callout, design: .monospaced))
                        .textSelection(.enabled)
                }
            }
            HStack {
                Button("Copy Session ID") { copy(s.id) }
                Spacer()
                Button("Close", action: close).keyboardShortcut(.cancelAction)
                Button("Resume in Terminal") { resume(); close() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(s.resume == nil)
            }
        }
        .padding(24)
        .frame(width: 620)
    }

    @ViewBuilder private func field(_ name: String, _ value: String) -> some View {
        GridRow {
            Text(name).foregroundStyle(.secondary)
            Text(value).textSelection(.enabled).lineLimit(2).truncationMode(.middle)
        }
    }
}
