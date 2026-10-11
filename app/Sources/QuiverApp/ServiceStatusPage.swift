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
                .buttonStyle(IconButtonStyle())
                .disabled(model.isLoading)
                .help("Check every status page now")
        } content: {
            ForEach(model.groups, id: \.group) { entry in
                VStack(alignment: .leading, spacing: 12) {
                    Text(entry.group.title).font(Theme.cardTitle).foregroundStyle(Theme.textSecondary)
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 360), spacing: Theme.gutter, alignment: .top)],
                              alignment: .leading, spacing: Theme.gutter) {
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

/// TokenTracker's `status-row`: 20pt padding, 16pt radius, a hairline that
/// strengthens on hover, the name at 16/500 and the state under it.
private struct ServiceCard: View {
    var status: ServiceStatus
    var isLoading: Bool
    @State private var hovering = false
    @Environment(\.openURL) private var openURL

    private static let nameFont = Font.system(size: 16, weight: .medium)
    private static let componentLimit = 5
    private static let incidentLimit = 3

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Text(status.service.name).font(Self.nameFont).foregroundStyle(Theme.textPrimary)
                Spacer(minLength: 8)
                Button("Open status page", systemImage: "arrow.up.right.square") { openURL(status.service.pageURL) }
                    .labelStyle(.iconOnly)
                    .buttonStyle(RingedPlainStyle(cornerRadius: 4))
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
                        .buttonStyle(.link)
                        .font(Theme.caption)
                    }
                }
                .padding(.top, 2)
            }

            CheckedLine(status: status)
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(hovering ? Theme.rowHover : Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius)
            .strokeBorder(hovering ? Theme.borderStrong : Theme.border, lineWidth: 1))
        .onHover { hovering = $0 }
        .animation(.easeOut(duration: 0.15), value: hovering)
    }
}

/// The overall state: always an icon and a label with the colour.
struct HealthLabel: View {
    var health: ServiceHealth?
    var isLoading = false
    var font: Font = .system(size: 16, weight: .medium)

    var body: some View {
        Label {
            Text(title)
        } icon: {
            Image(systemName: symbol)
        }
        .font(font)
        .foregroundStyle(color)
    }

    private var title: String {
        if let health { return health.title }
        return isLoading ? "Checking…" : "Unknown"
    }

    private var symbol: String {
        switch health {
        case .operational: "checkmark.circle.fill"
        case .maintenance: "wrench.and.screwdriver.fill"
        case .minor: "exclamationmark.triangle.fill"
        case .major, .critical: "xmark.octagon.fill"
        case nil: "questionmark.circle"
        }
    }

    private var color: Color {
        switch health {
        case .operational: Theme.success
        case .maintenance: Theme.textSecondary
        case .minor: Theme.warning
        case .major, .critical: Theme.danger
        case nil: Theme.textMuted
        }
    }
}

private struct ComponentRow: View {
    var component: AffectedComponent

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: symbol).foregroundStyle(color).font(.system(size: 11))
            Text(component.name).foregroundStyle(Theme.textPrimary).lineLimit(1)
            Spacer(minLength: 8)
            Text(component.state.title).foregroundStyle(color)
        }
        .font(Theme.caption)
    }

    private var symbol: String {
        switch component.state {
        case .maintenance: "wrench.and.screwdriver.fill"
        case .degraded: "exclamationmark.triangle.fill"
        case .partialOutage, .majorOutage: "xmark.octagon.fill"
        }
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
                        .buttonStyle(RingedPlainStyle(cornerRadius: 4))
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

private struct CheckedLine: View {
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
        } else if let checked = status.checkedAt {
            Text("Checked \(checked, format: .relative(presentation: .named))")
                .font(Theme.caption)
                .foregroundStyle(Theme.textMuted)
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
                    HealthLabel(health: app.serviceStatus.worst, font: Theme.caption.weight(.medium))
                        .labelStyle(.iconOnly)
                    Text(line).font(Theme.caption).foregroundStyle(Theme.textPrimary).lineLimit(1)
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right").font(.system(size: 10)).foregroundStyle(Theme.textMuted)
                }
                .contentShape(.rect)
            }
            .buttonStyle(RingedPlainStyle(cornerRadius: 4))
            .help("Open Service status")
            .accessibilityLabel("Service status: \(line)")
        }
    }
}
