import QuiverCore
import SwiftUI

/// The token and spend columns, shared between the header and each row.
private let tokensColumnWidth: CGFloat = 80
private let spendColumnWidth: CGFloat = 84

/// Every recent session across harnesses, with what it cost. Modelled on
/// TokenTracker's Sessions page: search and filters, a summary strip, then
/// the sessions grouped by the day they were last active.
struct SessionsPage: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        if let model = app.sessions {
            SessionsContent(model: model)
        } else {
            Page(title: "Sessions", subtitle: "Local and cloud sessions, usage and estimated cost.") {
                ErrorCard(message: app.overviewError ?? "The usage ledger is unavailable.")
            }
        }
    }
}

private struct SessionsContent: View {
    @Environment(AppModel.self) private var app
    @Bindable var model: SessionsModel
    @State private var detail: SessionsModel.Row?
    @State private var actionError: String?
    /// Rows a day's group shows before folding behind a Show more row.
    private static let foldLimit = 10
    /// Days the user has unfolded.
    @State private var openedDays: Set<Date> = []

    var body: some View {
        Page(title: "Sessions", subtitle: "Local and cloud sessions, usage and estimated cost.") {
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task {
                    await model.load(for: app.swePath)
                    // A scan reloads the figures through completedScans below.
                    await app.overview?.load(scan: true)
                }
            }
                .iconButton()
                .disabled(model.isLoading)
                .keyboardShortcut("r", modifiers: .command)
                .help("List sessions again (⌘R)")
        } content: {
            FilterBar(model: model)
            SummaryStrip(summary: model.summary)
            if let error = model.error ?? actionError {
                Card {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(Theme.bodySmall).foregroundStyle(Theme.warningText)
                }
            }
            if !model.failures.isEmpty {
                Card {
                    Label("Couldn't read: \(model.failures.joined(separator: "; "))", systemImage: "exclamationmark.circle")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            if let cloudError = model.cloudError {
                Card {
                    Label("Devin cloud sessions didn't update: \(cloudError)", systemImage: "exclamationmark.circle")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            if model.isCapped {
                Card {
                    Label("Showing the most recent \(model.rows.count) sessions; older ones are left out.",
                          systemImage: "info.circle")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            list
        }
        // Opening the page again within a minute shows the same list.
        .task(id: app.swePath) { await model.loadIfStale(for: app.swePath) }
        // New usage from a scan, without listing the sessions again.
        .task(id: app.overview?.completedScans) {
            await model.refreshUsage(ledgerScanned: (app.overview?.completedScans ?? 0) > 0)
        }
        .sheet(item: $detail) { row in
            SessionDetail(row: row, resume: { resume(row) }, close: { detail = nil })
        }
    }

    @ViewBuilder private var list: some View {
        let groups = model.groupedByDay()
        if model.rows.isEmpty && model.isLoading {
            LoadingCard(text: "Listing sessions…")
        } else if groups.isEmpty {
            // Every matching row can be trivial: the footer still shows
            // what was hidden and offers "Show" to reveal them.
            EmptyCard(text: model.rows.isEmpty ? "No sessions listed." : "No sessions match these filters.",
                      icon: "clock")
            hiddenFooter
        } else {
            VStack(spacing: 0) {
                HStack(spacing: 16) {
                    Text("Session / project").frame(maxWidth: .infinity, alignment: .leading)
                    Text("Tokens").frame(width: tokensColumnWidth, alignment: .trailing)
                    Text("Spend").frame(width: spendColumnWidth, alignment: .trailing)
                    Color.clear.frame(width: Theme.iconButton)
                }
                .font(Theme.tableHeader).tracking(0.12).foregroundStyle(Theme.tableHeaderText)
                // The rows' side padding, so each heading sits over its column.
                .padding(.horizontal, 8)
                .padding(.bottom, 10)
                Divider().overlay(Theme.border)

                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(groups) { group in
                        Text(group.day, format: .dateTime.month(.abbreviated).day().year())
                            .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                            .padding(.horizontal, 8)
                            .padding(.top, 18).padding(.bottom, 10)
                        Divider().overlay(Theme.border)
                        let shown = RowFold.visible(group.rows, limit: Self.foldLimit,
                                                    expanded: openedDays.contains(group.day))
                        ForEach(shown) { row in
                            SessionRow(row: row, project: model.project(for: row.session),
                                       open: { detail = row }, resume: { resume(row) })
                            Divider().overlay(Theme.border)
                        }
                        if RowFold.hiddenCount(total: group.rows.count, limit: Self.foldLimit) > 0 {
                            ExpandRow(isExpanded: openedDays.contains(group.day),
                                      hidden: RowFold.hiddenCount(total: group.rows.count, limit: Self.foldLimit),
                                      help: (expand: "Show every session from this day",
                                             collapse: "Show only the first \(Self.foldLimit)"),
                                      edge: .inset) {
                                withAnimation(.easeOut(duration: Theme.fadeDuration)) {
                                    if openedDays.contains(group.day) { openedDays.remove(group.day) }
                                    else { openedDays.insert(group.day) }
                                }
                            }
                        }
                    }
                }
                hiddenFooter
            }
        }
    }

    /// "N short or empty sessions hidden" plus the Show/Hide toggle,
    /// under the list and under the empty card alike.
    @ViewBuilder private var hiddenFooter: some View {
        if model.hideTrivial, model.hiddenTrivialCount > 0 {
            HStack(spacing: 8) {
                Text(model.showTrivial
                     ? "Showing \(model.hiddenTrivialCount) short or empty sessions"
                     : "\(model.hiddenTrivialCount) short or empty sessions hidden")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                Spacer()
                Button(model.showTrivial ? "Hide" : "Show") {
                    model.showTrivial.toggle()
                }
                .font(Theme.caption)
                .buttonStyle(.plain)
                .foregroundStyle(Theme.textSecondary)
            }
            .padding(.horizontal, 8)
            .padding(.top, 10)
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

// MARK: - Filters and summary

private struct FilterBar: View {
    @Bindable var model: SessionsModel

    var body: some View {
        // One row when it fits, else the search on its own line.
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) {
                search.frame(minWidth: 180)
                selects
            }
            // Narrow windows: the selects wrap rather than overflow.
            VStack(alignment: .leading, spacing: 10) {
                search
                FlowLayout(spacing: 10, lineSpacing: 10) { selects }
            }
        }
    }

    private var search: some View {
        SearchField(prompt: "Search sessions", text: $model.filter.search)
            .frame(maxWidth: .infinity)
    }

    @ViewBuilder private var selects: some View {
        SelectMenu(options: [(nil, "All tools", nil)] + model.harnesses.map { (Optional($0), UsageFormat.harness($0), $0) },
                   selection: $model.filter.harness,
                   title: model.filter.harness.map(UsageFormat.harness) ?? "All tools")
        SelectMenu(symbol: "calendar", options: SessionsModel.Window.allCases.map { ($0, $0.title) },
                   selection: $model.filter.window)
        CountedMenu(symbol: "folder", allTitle: "All projects",
                    title: selectedProject ?? "All projects",
                    rows: model.projects.map { ($0.key, $0.name, "\($0.count)") },
                    selection: $model.filter.project)
        CountedMenu(allTitle: "All models",
                    title: model.filter.model ?? "All models",
                    rows: model.models.map { ($0.model, $0.model, UsageFormat.tokens($0.tokens)) },
                    selection: $model.filter.model,
                    limit: 30,
                    logo: { BrandLogo(model: $0, size: 14) })
    }

    /// The selected project's display name for its filter key.
    private var selectedProject: String? {
        model.filter.project.flatMap { key in model.projects.first { $0.key == key }?.name }
    }
}

/// A select whose options carry a count and can number in the hundreds,
/// so it opens a popover with a search field and, when `limit` is set, a
/// "Show all" row that expands past a top slice. Closed it looks like the
/// `SelectMenu` beside it.
private struct CountedMenu: View {
    var symbol: String? = nil
    /// The "no filter" row's text.
    var allTitle: String
    /// The closed control's text: the selection or `allTitle`.
    var title: String
    /// The rows, already in display order: key, label, detail.
    var rows: [(key: String, title: String, detail: String)]
    @Binding var selection: String?
    /// Show only this many rows behind "Show all"; nil shows them all.
    var limit: Int? = nil
    /// A search field appears once there are more rows than this.
    var searchThreshold = 15
    /// A logo before each row's title, by key; the model filter passes
    /// the brand logo, the project filter none.
    var logo: ((String) -> BrandLogo)? = nil
    @State private var open = false
    @State private var query = ""
    @State private var showAll = false
    @State private var hovering = false

    private var needle: String { query.trimmingCharacters(in: .whitespaces).lowercased() }

    /// The rows to list: the matches while searching, else the top slice.
    private var shown: [(key: String, title: String, detail: String)] {
        if !needle.isEmpty {
            return rows.filter { $0.title.lowercased().contains(needle) }
        }
        if let limit, !showAll, rows.count > limit { return Array(rows.prefix(limit)) }
        return rows
    }

    var body: some View {
        Button { open.toggle() } label: {
            HStack(spacing: 6) {
                if let symbol { Image(systemName: symbol).foregroundStyle(Theme.textSecondary) }
                Text(title).foregroundStyle(Theme.textPrimary).lineLimit(1).truncationMode(.tail)
                Spacer(minLength: 4)
                Image(systemName: "chevron.down").font(.system(size: 12))
                    .foregroundStyle(Theme.textSecondary)
            }
            .font(Theme.bodySmall)
            .padding(.horizontal, 12)
            .frame(width: 168, height: Theme.fieldHeight)
            .background(Theme.field, in: .rect(cornerRadius: Theme.selectRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.selectRadius)
                .strokeBorder(hovering || open ? Theme.borderHover : Theme.border, lineWidth: 1))
            .contentShape(.rect(cornerRadius: Theme.selectRadius))
        }
        .plainButton(cornerRadius: Theme.selectRadius)
        .onHover { hovering = $0 }
        .popover(isPresented: $open, arrowEdge: .bottom) { popover }
        .onChange(of: open) {
            if !open { query = ""; showAll = false }
        }
    }

    private var popover: some View {
        VStack(alignment: .leading, spacing: 0) {
            if rows.count > searchThreshold {
                SearchField(prompt: "Search", text: $query)
                    .padding(8)
                Divider().overlay(Theme.border)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    option(key: nil, title: allTitle, detail: nil)
                    Divider().overlay(Theme.border)
                    ForEach(shown, id: \.key) { row in
                        option(key: row.key, title: row.title, detail: row.detail)
                    }
                    if let limit, rows.count > limit, !showAll, needle.isEmpty {
                        Divider().overlay(Theme.border)
                        Button {
                            showAll = true
                        } label: {
                            Text("Show all (\(rows.count))")
                                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                                .padding(.horizontal, 8).padding(.vertical, 6)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .contentShape(.rect)
                        }
                        .plainButton(cornerRadius: Theme.controlRadius)
                    }
                }
                .padding(6)
            }
            .frame(maxHeight: 360)
        }
        .frame(width: 300)
    }

    private func option(key: String?, title: String, detail: String?) -> some View {
        Button {
            selection = key
            open = false
        } label: {
            HStack(spacing: 8) {
                if let key, let logo { logo(key) }
                Text(title)
                    .font(Theme.bodySmall)
                    .foregroundStyle(Theme.textPrimary)
                    .lineLimit(1)
                Spacer(minLength: 8)
                if let detail {
                    Text(detail).font(Theme.caption).foregroundStyle(Theme.textMuted).monospacedDigit()
                }
                Image(systemName: "checkmark")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Theme.accent)
                    .opacity(selection == key ? 1 : 0)
            }
            .padding(.horizontal, 8).padding(.vertical, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
        }
        .plainButton(cornerRadius: Theme.controlRadius)
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
        .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
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
    /// The session's project resolved to its main repository.
    var project: String?
    var open: () -> Void
    var resume: () -> Void
    @State private var hovering = false
    @FocusState private var resumeFocused: Bool

    var body: some View {
        let s = row.session
        HStack(alignment: .top, spacing: 16) {
            // The logo centres on the row (title and details together), not
            // on the title line.
            HStack(alignment: .center, spacing: 12) {
                HarnessIcon(harness: HarnessMark(s.harness).icon ?? s.harness, size: 16)
                    .help(HarnessMark(s.harness).name)
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
                .frame(width: tokensColumnWidth, alignment: .trailing)
            Text(row.usage.map { UsageFormat.dollars($0.totals.costUSD) } ?? "–")
                .font(Theme.bodySmall).monospacedDigit()
                .foregroundStyle(row.usage == nil ? Theme.textSecondary : Theme.accent)
                .frame(width: spendColumnWidth, alignment: .trailing)
            Button(s.opensInBrowser ? "Open in Devin" : "Resume",
                   systemImage: s.opensInBrowser ? "arrow.up.right.square" : "play", action: resume)
                .buttonStyle(IconButtonStyle())
                .disabled(s.resume == nil && !s.opensInBrowser)
                // Shown on row hover, or while it has keyboard focus so its
                // ring is visible; otherwise hidden but still reachable.
                .opacity(hovering || resumeFocused ? 1 : 0)
                .focused($resumeFocused)
                .focusRing(resumeFocused, cornerRadius: Theme.iconButtonRadius)
                .help(s.resume != nil ? "Resume in your terminal"
                      : s.opensInBrowser ? "Open in Devin"
                      : row.location == .cloud ? "A cloud session can't be resumed here"
                      : "This harness has no resume command")
        }
        .padding(.vertical, 12)
        .padding(.horizontal, 8)
        .background(hovering ? Theme.panelHover : .clear, in: .rect(cornerRadius: Theme.controlRadius))
        .contentShape(.rect)
        .onHover { hovering = $0 }
        // A double-click opens the session; a selection would take it.
        .textSelection(.disabled)
        .onTapGesture(count: 2, perform: open)
        .contextMenu {
            Button("Details…", action: open)
            if s.opensInBrowser {
                Button("Open in Devin", action: resume)
            } else {
                Button("Resume in terminal", action: resume).disabled(s.resume == nil)
            }
            Divider()
            Button("Copy session ID") { copyToPasteboard(s.id) }
            if let cwd = s.cwd {
                Button("Show folder in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: cwd)])
                }
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityAction(named: "Details", open)
        .accessibilityAction(named: "Resume", resume)
    }

    /// project · agent · location · models · time · duration
    var details: String {
        let s = row.session
        var parts: [String] = []
        if let project { parts.append(project) }
        parts.append(HarnessMark(s.harness).name)
        parts.append(row.location.title)
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

/// A session's state as the shared badge; nothing for a finished one.
private struct StatusBadge: View {
    var status: String?

    var body: some View {
        if let title = Self.title(status) { Badge(title) }
    }

    static func title(_ status: String?) -> String? {
        switch status {
        case "active": "Active"
        case "error": "Error"
        case "interrupted": "Interrupted"
        case "followup": "Waiting"
        default: nil
        }
    }
}

// MARK: - Detail

struct SessionDetail: View {
    /// The widest a value can be: the 640 pt sheet less its padding and
    /// the label column.
    static let valueWidth: CGFloat = 460
    var row: SessionsModel.Row
    var resume: () -> Void
    var close: () -> Void

    var body: some View {
        let s = row.session
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 8) {
                HarnessIcon(harness: HarnessMark(s.harness).icon ?? s.harness, size: 20)
                Text(s.title ?? s.id).font(Theme.sectionTitle).tracking(-0.5).lineLimit(2)
                StatusBadge(status: s.status)
            }
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                field("Harness", HarnessMark(s.harness).name)
                field("Location", row.location.title)
                if !s.opensInBrowser { field("Folder", s.cwd ?? "Unknown") }
                field("Last active", s.lastActiveAt.formatted(date: .abbreviated, time: .shortened))
                field("Session id", s.id)
                if let usage = row.usage {
                    field("Account", usage.accountHome)
                    GridRow(alignment: .top) {
                        Text("Models").font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        FlowLayout(spacing: 10, lineSpacing: 4) {
                            ForEach(usage.models, id: \.self) { name in
                                HStack(spacing: 5) {
                                    BrandLogo(model: name, size: 14)
                                    Text(name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                                        .lineLimit(1).truncationMode(.middle)
                                }
                                // A long provider-prefixed or custom name
                                // truncates inside the sheet's value column.
                                .frame(maxWidth: SessionDetail.valueWidth, alignment: .leading)
                            }
                        }
                    }
                }
                if let duration = row.duration, duration >= 60 {
                    field("Ran for", SessionRow.duration(duration))
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
                .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
                if usage.totals.unpricedTokens > 0 {
                    Text("\(UsageFormat.tokens(usage.totals.unpricedTokens)) tokens from models with no list price.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            } else {
                Text(s.opensInBrowser ? "Devin's API reports no token or ACU usage for this session."
                     : "No token usage recorded for this session in the ledger.")
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
                Button("Copy session ID") { copyToPasteboard(s.id) }.secondaryButton()
                Spacer()
                Button("Close", action: close).keyboardShortcut(.cancelAction).secondaryButton()
                Button(s.opensInBrowser ? "Open in Devin" : "Resume in terminal") { resume(); close() }
                    .keyboardShortcut(.defaultAction)
                    .primaryButton()
                    .disabled(s.resume == nil && !s.opensInBrowser)
                    .help(s.resume != nil ? "Resume in terminal"
                          : s.opensInBrowser ? "Open the session in Devin's web app"
                          : row.location == .cloud ? "A cloud session can't be resumed here"
                          : "This session is no longer in \(HarnessMark(s.harness).name)'s history")
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
