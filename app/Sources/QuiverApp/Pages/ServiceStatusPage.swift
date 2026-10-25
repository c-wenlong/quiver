import AppKit
import QuiverCore
import SwiftUI

/// Live status of the services the harnesses depend on, after TokenTracker's
/// Service Status page: one `status-row` card per service in two columns,
/// with affected components and open incidents underneath.
struct ServiceStatusPage: View {
    @Environment(AppModel.self) private var app
    @State private var adding = false

    private var model: ServiceStatusModel { app.serviceStatus }

    var body: some View {
        Page(title: "Service status",
             subtitle: "Live status from the official status pages of the services your harnesses use.") {
            if let checked = model.lastChecked {
                Text("Checked \(checked, format: .relative(presentation: .named))")
                    .font(Theme.caption)
                    .foregroundStyle(Theme.textMuted)
            }
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.load(force: true) } }
                .iconButton()
                .disabled(model.isLoading)
                .keyboardShortcut("r", modifiers: .command)
                .help("Check every status page now (⌘R)")
            Button("Add service", systemImage: "plus") { adding = true }
                .primaryButton()
        } content: {
            ForEach(model.groups, id: \.group) { entry in
                VStack(alignment: .leading, spacing: 12) {
                    Text(entry.group.title).font(Theme.cardTitle).foregroundStyle(Theme.textSecondary)
                    EqualRowGrid(minimumColumnWidth: 360, spacing: Theme.gutter) {
                        ForEach(entry.statuses) { status in
                            ServiceCard(status: status, isLoading: model.isLoading,
                                        remove: status.service.isCustom
                                            ? { Task { await model.remove(id: status.id) } } : nil)
                        }
                    }
                }
            }
            Text("Read from each provider's public status page, at most every five minutes. Gemini comes from Google Cloud's status feed, which covers the Gemini API. Pages you add are checked the same way.")
                .font(Theme.caption)
                .foregroundStyle(Theme.textMuted)
        }
        .task { await model.load(force: false) }
        .quiverSheet(isPresented: $adding) {
            AddServiceSheet(
                check: { await model.probe($0) },
                add: { name, pageURL, group in
                    Task { await model.add(name: name, pageURL: pageURL, group: group) }
                })
        }
    }
}

/// The "Track a status page" sheet: name, address and group, with a Check
/// button that reads the page's summary.json before Add is allowed.
private struct AddServiceSheet: View {
    var check: (String) async -> ServiceStatusService.ProbeResult
    var add: (String, URL, StatusService.Group) -> Void
    @Environment(\.dismissSheet) private var dismiss
    @State private var name = ""
    @State private var address = ""
    @State private var group = StatusService.Group.providers
    @State private var checking = false
    /// The last Check's answer, cleared when the address is edited.
    @State private var checked: ServiceStatusService.ProbeResult?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Track a status page").font(Theme.sectionTitle).tracking(-0.5)
            TextField("Name, like OpenRouter", text: $name)
                .quiverField()
            TextField("https://status.example.com", text: $address)
                .quiverField()
                .onChange(of: address) { checked = nil }
            HStack {
                Button("Check") { runCheck() }
                    .secondaryButton()
                    .disabled(checking || address.trimmingCharacters(in: .whitespaces).isEmpty)
                if checking {
                    ProgressView().controlSize(.small)
                }
            }
            switch checked {
            case .ok(let pageURL, let report):
                HStack(spacing: 8) {
                    HealthDot(health: report.health)
                    Text("\(report.health.title) · Reads \(pageURL.host() ?? pageURL.absoluteString)")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            case .failure(let problem):
                Text(problem).font(Theme.caption).foregroundStyle(Theme.danger)
            case nil:
                EmptyView()
            }
            Tabs(options: [(.providers, "AI providers"), (.infrastructure, "Infrastructure")],
                 selection: $group)
            Text("AI providers also show in the menu bar when they have an incident.")
                .font(Theme.font(size: 10)).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .secondaryButton()
                Button("Add") {
                    if case .ok(let pageURL, _) = checked {
                        add(name.trimmingCharacters(in: .whitespaces), pageURL, group)
                    }
                    dismiss()
                }
                .primaryButton()
                .keyboardShortcut(.defaultAction)
                .disabled(!canAdd)
            }
        }
        .sheetScaffold(width: Theme.formSheetWidth, padding: Theme.formSheetPadding)
    }

    private var canAdd: Bool {
        if case .ok = checked { return !name.trimmingCharacters(in: .whitespaces).isEmpty }
        return false
    }

    private func runCheck() {
        checking = true
        let text = address
        Task {
            let result = await check(text)
            // The user may have kept typing while the check ran.
            if address == text { checked = result }
            checking = false
        }
    }
}

/// Columns of at least `minimumColumnWidth`, as many as fit, with every
/// card in a row as tall as the tallest, so a row's bottoms line up.
private struct EqualRowGrid: Layout {
    var minimumColumnWidth: CGFloat
    var spacing: CGFloat

    private func columns(for width: CGFloat) -> Int {
        max(1, Int((width + spacing) / (minimumColumnWidth + spacing)))
    }

    private func rowHeights(_ subviews: Subviews, columnWidth: CGFloat, columns: Int) -> [CGFloat] {
        stride(from: 0, to: subviews.count, by: columns).map { start in
            subviews[start..<min(start + columns, subviews.count)]
                .map { $0.sizeThatFits(ProposedViewSize(width: columnWidth, height: nil)).height }
                .max() ?? 0
        }
    }

    private func columnWidth(_ width: CGFloat, _ columns: Int) -> CGFloat {
        (width - spacing * CGFloat(columns - 1)) / CGFloat(columns)
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? minimumColumnWidth
        let count = columns(for: width)
        let heights = rowHeights(subviews, columnWidth: columnWidth(width, count), columns: count)
        return CGSize(width: width, height: heights.reduce(0, +) + spacing * CGFloat(max(heights.count - 1, 0)))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let count = columns(for: bounds.width)
        let cell = columnWidth(bounds.width, count)
        var y = bounds.minY
        for (row, height) in rowHeights(subviews, columnWidth: cell, columns: count).enumerated() {
            for column in 0..<count {
                let index = row * count + column
                guard index < subviews.count else { break }
                subviews[index].place(at: CGPoint(x: bounds.minX + CGFloat(column) * (cell + spacing), y: y),
                                      proposal: ProposedViewSize(width: cell, height: height))
            }
            y += height + spacing
        }
    }
}

private extension ServiceStatus {
    /// What a card click can open. The summary only counts when the card
    /// would show it (not on an all-clear reading).
    var hasDetails: Bool {
        if let summary = report?.summary, report?.health != .operational,
           !summary.isEmpty { return true }
        guard let report else { return false }
        return !report.components.isEmpty || !report.incidents.isEmpty
    }

    /// The counts line, like "3 incidents · 5 components".
    var detailNote: String? {
        var parts: [String] = []
        if let report {
            if !report.incidents.isEmpty {
                parts.append(report.incidents.count == 1 ? "1 incident" : "\(report.incidents.count) incidents")
            }
            if !report.components.isEmpty {
                parts.append(report.components.count == 1 ? "1 component" : "\(report.components.count) components")
            }
        }
        if let summary = report?.summary, report?.health != .operational,
           !summary.isEmpty {
            parts.append("summary")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

/// TokenTracker's `status-row`: 20pt padding, 16pt radius, a hairline that
/// strengthens on hover, the 16pt logo and the name at 16/500, and the
/// state under it as an 8pt dot and a label. A card that `hasDetails`
/// opens them as a sheet on a click the row's buttons don't take, so one
/// busy page can no longer stretch its grid row.
private struct ServiceCard: View {
    var status: ServiceStatus
    var isLoading: Bool
    /// Set for a page the user added; built-ins cannot be removed.
    var remove: (() -> Void)? = nil
    @State private var hovering = false
    @State private var confirmingRemove = false
    @State private var showingDetails = false
    @Environment(\.openURL) private var openURL

    private static var nameFont: Font { Theme.font(size: 16, weight: .medium) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            VStack(alignment: .leading, spacing: 4) {
                HealthLabel(health: status.report?.health, isLoading: isLoading && status.checkedAt == nil)
                if let note = status.service.note {
                    Text(note).font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            ProblemLine(status: status)
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(hovering ? Theme.cardHover : Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius)
            .strokeBorder(hovering ? Theme.borderStrong : Theme.border, lineWidth: 1))
        .contentShape(.rect)
        .onHover { hovering = $0 }
        // A click opens the details sheet; a selection would take it, and
        // the header's buttons hit-test ahead of the gesture.
        .textSelection(.disabled)
        .onTapGesture { if status.hasDetails { showingDetails = true } }
        .animation(.easeOut(duration: Theme.fadeDuration), value: hovering)
        .quiverSheet(isPresented: $showingDetails) { ServiceDetailsSheet(status: status) }
    }

    private var header: some View {
        HStack(spacing: 10) {
            if let logo = status.service.logoID { HarnessIcon(harness: logo, size: 16) }
            Text(status.service.name).font(Self.nameFont).foregroundStyle(Theme.textPrimary)
            Spacer(minLength: 8)
            Button("Open status page", systemImage: "arrow.up.right.square") { openURL(status.service.pageURL) }
                .labelStyle(.iconOnly)
                .plainButton(cornerRadius: Theme.smallRadius)
                .foregroundStyle(Theme.textSecondary)
                .help(status.service.pageURL.absoluteString)
            if let remove {
                EllipsisMenu {
                    Button("Remove…") { confirmingRemove = true }
                }
                .confirmationDialog("Stop tracking \(status.service.name)?", isPresented: $confirmingRemove) {
                    Button("Stop tracking", role: .destructive, action: remove)
                }
            }
        }
    }
}

/// What a card click opens when it `hasDetails`: the provider's summary,
/// the affected components and the open incidents. They list in full,
/// since the feed only reports what is not operational, under a capped
/// scroll.
private struct ServiceDetailsSheet: View {
    var status: ServiceStatus
    @Environment(\.dismissSheet) private var dismiss
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                if let logo = status.service.logoID { HarnessIcon(harness: logo, size: 20) }
                Text(status.service.name).font(Theme.sectionTitle).tracking(-0.5)
                Spacer(minLength: 8)
                HealthLabel(health: status.report?.health)
            }
            if let note = status.detailNote {
                Text(note).font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            if let note = status.service.note {
                Text(note).font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            ProblemLine(status: status)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) { details }
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 420)
            HStack {
                Button("Open status page", systemImage: "arrow.up.right.square") {
                    openURL(status.service.pageURL)
                }
                .secondaryButton()
                Spacer()
                Button("Done") { dismiss() }
                    .secondaryButton()
                    .keyboardShortcut(.cancelAction)
            }
        }
        .sheetScaffold(width: Theme.sheetWidth)
    }

    @ViewBuilder private var details: some View {
        if let summary = status.report?.summary, status.report?.health != .operational {
            Text(summary).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
        }

        if let report = status.report, !report.components.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("Affected components").font(Theme.caption).foregroundStyle(Theme.textMuted)
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(report.components, id: \.self) { component in
                        ComponentRow(component: component)
                    }
                }
            }
        }

        if let report = status.report, !report.incidents.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("Incidents").font(Theme.caption).foregroundStyle(Theme.textMuted)
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(report.incidents) { incident in
                        IncidentView(incident: incident)
                    }
                }
            }
        }
    }
}

/// The overall state as TokenTracker shows it: an 8pt dot in the state's
/// colour and the label beside it. The label stays neutral, since amber
/// and green text at this size fall well short of 4.5:1 on white.
struct HealthLabel: View {
    var health: ServiceHealth?
    var isLoading = false
    var font: Font = Theme.bodySmall

    var body: some View {
        HStack(spacing: 8) {
            HealthDot(health: health)
            Text(title).font(font).foregroundStyle(health == nil ? Theme.textMuted : Theme.textPrimary)
        }
        .accessibilityElement(children: .combine)
    }

    private var title: String {
        if let health { return health.title }
        return isLoading ? "Checking…" : "Unknown"
    }
}

/// The state's colour on its own, for the card label and the menu bar line.
struct HealthDot: View {
    var health: ServiceHealth?
    var size: CGFloat = 8

    var body: some View {
        Circle().fill(color).frame(width: size, height: size).accessibilityHidden(true)
    }

    private var color: Color {
        switch health {
        case .operational: Theme.success
        case .maintenance: Theme.textSecondary
        case .minor: Theme.warning
        case .major, .critical: Theme.danger
        case nil: Theme.borderStrong
        }
    }
}

private struct ComponentRow: View {
    var component: AffectedComponent

    var body: some View {
        HStack(spacing: 8) {
            Circle().fill(color).frame(width: 6, height: 6).accessibilityHidden(true)
            Text(component.name).foregroundStyle(Theme.textPrimary).lineLimit(1)
            Spacer(minLength: 8)
            Text(component.state.title).foregroundStyle(Theme.textSecondary)
        }
        .font(Theme.caption)
    }

    private var color: Color {
        switch component.state {
        case .maintenance: Theme.textSecondary
        case .degraded: Theme.warning
        case .partialOutage, .majorOutage: Theme.danger
        }
    }
}

private struct IncidentView: View {
    var incident: ServiceIncident
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(incident.name).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                if let url = incident.url {
                    Button("Open incident", systemImage: "arrow.up.right") { openURL(url) }
                        .labelStyle(.iconOnly)
                        .plainButton(cornerRadius: Theme.smallRadius)
                        .font(Theme.font(size: 11))
                        .foregroundStyle(Theme.textSecondary)
                        .help(url.absoluteString)
                }
            }
            Text(meta).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            if let update = incident.latestUpdate {
                Text(update)
                    .font(Theme.caption)
                    .foregroundStyle(Theme.textSecondary)
                    .lineLimit(4)
                    .textSelection(.enabled)
            }
        }
        .padding(.leading, 10)
        .overlay(alignment: .leading) {
            Rectangle().fill(Theme.border).frame(width: 2)
        }
    }

    private var meta: String {
        var parts: [String] = []
        if let stage = incident.stage { parts.append(stage) }
        if let started = incident.startedAt {
            parts.append("\(incident.isMaintenance ? "began" : "started") \(Self.when(started))")
        }
        if let updated = incident.updatedAt {
            parts.append("updated \(updated.formatted(.relative(presentation: .named)))")
        }
        return parts.joined(separator: " · ")
    }

    private static func when(_ date: Date) -> String {
        date.formatted(.dateTime.day().month(.abbreviated).hour().minute())
    }
}

/// Why the reading is missing or old. The time of a good reading is in
/// the page header, once.
private struct ProblemLine: View {
    var status: ServiceStatus

    var body: some View {
        if let problem = status.problem {
            Label {
                if status.isStale, let checked = status.checkedAt {
                    Text("\(problem). Showing the reading from \(checked, format: .relative(presentation: .named)).")
                } else {
                    Text(problem)
                }
            } icon: {
                Image(systemName: "exclamationmark.triangle")
            }
            .font(Theme.caption)
            .foregroundStyle(Theme.textSecondary)
        }
    }
}

/// One line in the menu bar panel when a provider has an incident; nothing
/// otherwise. Clicking it opens the Service status page.
struct ServiceStatusMenuLine: View {
    @Environment(AppModel.self) private var app
    /// Closes the panel and brings up the main window.
    var openMain: () -> Void

    var body: some View {
        if let line = app.serviceStatus.menuLine {
            Button {
                app.selection = .serviceStatus
                openMain()
            } label: {
                HStack(spacing: 6) {
                    HealthDot(health: app.serviceStatus.worst)
                    // The logos of the services the line names: both of two,
                    // else the first ("Claude: …, and 2 more").
                    let affected = app.serviceStatus.affected
                    ForEach(affected.prefix(affected.count == 2 ? 2 : 1).compactMap(\.logoID), id: \.self) {
                        HarnessIcon(harness: $0, size: 12)
                    }
                    Text(line).font(Theme.caption).foregroundStyle(Theme.textPrimary).lineLimit(1)
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right").font(Theme.font(size: 10)).foregroundStyle(Theme.textMuted)
                }
                .contentShape(.rect)
            }
            .plainButton(cornerRadius: Theme.smallRadius)
            .help("Open service status")
            .accessibilityLabel("Service status: \(line)")
        }
    }
}
