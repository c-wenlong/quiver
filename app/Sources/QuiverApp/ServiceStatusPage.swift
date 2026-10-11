import AppKit
import QuiverCore
import SwiftUI

/// Live status of the services the harnesses depend on, after TokenTracker's
/// Service Status page: one `status-row` card per service in two columns,
/// with affected components and open incidents underneath.
struct ServiceStatusPage: View {
    @Environment(AppModel.self) private var app

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
                .help("Check every status page now")
        } content: {
            ForEach(model.groups, id: \.group) { entry in
                VStack(alignment: .leading, spacing: 12) {
                    Text(entry.group.title).font(Theme.cardTitle).foregroundStyle(Theme.textSecondary)
                    EqualRowGrid(minimumColumnWidth: 360, spacing: Theme.gutter) {
                        ForEach(entry.statuses) { status in
                            ServiceCard(status: status, isLoading: model.isLoading)
                        }
                    }
                }
            }
            Text("Read from each provider's public status page, at most every five minutes. Gemini comes from Google Cloud's status feed, which covers the Gemini API.")
                .font(Theme.caption)
                .foregroundStyle(Theme.textMuted)
        }
        .task { await model.load(force: false) }
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

/// TokenTracker's `status-row`: 20pt padding, 16pt radius, a hairline that
/// strengthens on hover, the 16pt logo and the name at 16/500, and the
/// state under it as an 8pt dot and a label.
private struct ServiceCard: View {
    var status: ServiceStatus
    var isLoading: Bool
    @State private var hovering = false
    @Environment(\.openURL) private var openURL

    private static let nameFont = Font.system(size: 16, weight: .medium)
    /// Status pages whose logo is filed under the harness's own id.
    private static let logoIDs = ["openai": "codex", "github": "copilot"]
    private static let componentLimit = 5
    private static let incidentLimit = 3

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                HarnessIcon(harness: Self.logoIDs[status.service.id] ?? status.service.id, size: 16)
                Text(status.service.name).font(Self.nameFont).foregroundStyle(Theme.textPrimary)
                Spacer(minLength: 8)
                Button("Open status page", systemImage: "arrow.up.right.square") { openURL(status.service.pageURL) }
                    .labelStyle(.iconOnly)
                    .plainButton(cornerRadius: 4)
                    .foregroundStyle(Theme.textSecondary)
                    .help(status.service.pageURL.absoluteString)
            }

            VStack(alignment: .leading, spacing: 4) {
                HealthLabel(health: status.report?.health, isLoading: isLoading && status.checkedAt == nil)
                if let summary = status.report?.summary, status.report?.health != .operational {
                    Text(summary).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                if let note = status.service.note {
                    Text(note).font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }

            if let report = status.report, !report.components.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(report.components.prefix(Self.componentLimit), id: \.self) { component in
                        ComponentRow(component: component)
                    }
                    if report.components.count > Self.componentLimit {
                        Text("And \(report.components.count - Self.componentLimit) more")
                            .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    }
                }
            }

            if let report = status.report, !report.incidents.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(report.incidents.prefix(Self.incidentLimit)) { incident in
                        IncidentView(incident: incident)
                    }
                    if report.incidents.count > Self.incidentLimit {
                        Button("\(report.incidents.count - Self.incidentLimit) more on the status page") {
                            openURL(status.service.pageURL)
                        }
                        .plainButton(cornerRadius: 4)
                        .font(Theme.caption)
                        .foregroundStyle(Theme.textSecondary)
                        .underline()
                    }
                }
                .padding(.top, 2)
            }

            ProblemLine(status: status)
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(hovering ? Theme.cardHover : Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius)
            .strokeBorder(hovering ? Theme.borderStrong : Theme.border, lineWidth: 1))
        .onHover { hovering = $0 }
        .animation(.easeOut(duration: 0.15), value: hovering)
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
                        .plainButton(cornerRadius: 4)
                        .font(.system(size: 11))
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
                    Text(line).font(Theme.caption).foregroundStyle(Theme.textPrimary).lineLimit(1)
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right").font(.system(size: 10)).foregroundStyle(Theme.textMuted)
                }
                .contentShape(.rect)
            }
            .plainButton(cornerRadius: 4)
            .help("Open service status")
            .accessibilityLabel("Service status: \(line)")
        }
    }
}
