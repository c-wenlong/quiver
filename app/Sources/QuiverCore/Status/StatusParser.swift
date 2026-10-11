import Foundation

/// Reads status feeds into a `StatusReport`. Lenient about missing fields,
/// since incident.io and Google serve their own variants; nil only when the
/// body is not the expected feed at all.
enum StatusParser {
    // MARK: Statuspage

    /// Atlassian Statuspage `summary.json`.
    static func statuspage(_ data: Data, now: Date) -> StatusReport? {
        guard let root = UsageJSON.object(data),
              let status = root["status"] as? [String: Any],
              let indicator = UsageJSON.string(status["indicator"]),
              let health = ServiceHealth(indicator: indicator)
        else { return nil }

        let incidents = (root["incidents"] as? [[String: Any]] ?? [])
            .filter { !["resolved", "postmortem", "completed"].contains(UsageJSON.string($0["status"]) ?? "") }
            .compactMap { incident($0, maintenance: false) }
        // Only maintenance that has started; the feed also lists ones weeks out.
        let maintenances = (root["scheduled_maintenances"] as? [[String: Any]] ?? [])
            .filter { ["in_progress", "verifying"].contains(UsageJSON.string($0["status"]) ?? "") }
            .compactMap { incident($0, maintenance: true) }

        return StatusReport(
            health: health,
            summary: UsageJSON.string(status["description"]),
            components: components(root["components"] as? [[String: Any]] ?? []),
            incidents: worstFirst(incidents + maintenances))
    }

    /// Non-operational components, worst first and otherwise in page order.
    /// A group's state only echoes its members, so groups are listed only
    /// when no member is.
    private static func components(_ raw: [[String: Any]]) -> [AffectedComponent] {
        var leaves: [AffectedComponent] = []
        var groups: [AffectedComponent] = []
        for component in raw {
            guard let name = UsageJSON.string(component["name"]),
                  let state = UsageJSON.string(component["status"]).flatMap(ComponentState.init(statuspage:))
            else { continue }
            if component["group"] as? Bool == true {
                groups.append(AffectedComponent(name: name, state: state))
            } else {
                leaves.append(AffectedComponent(name: name, state: state))
            }
        }
        let listed = leaves.isEmpty ? groups : leaves
        return listed.enumerated()
            .sorted { ($0.element.state, -$0.offset) > ($1.element.state, -$1.offset) }
            .map(\.element)
    }

    private static func incident(_ raw: [String: Any], maintenance: Bool) -> ServiceIncident? {
        guard let id = UsageJSON.string(raw["id"]), let name = UsageJSON.string(raw["name"]) else { return nil }
        let updates = (raw["incident_updates"] as? [[String: Any]] ?? [])
            .map { update in (update, UsageJSON.date(update["display_at"]) ?? UsageJSON.date(update["created_at"])) }
        let latest = updates.max { ($0.1 ?? .distantPast) < ($1.1 ?? .distantPast) }
        let impact = UsageJSON.string(raw["impact"]).flatMap(ServiceHealth.init(indicator:))
        return ServiceIncident(
            id: id,
            name: name,
            impact: maintenance ? .maintenance : max(impact ?? .minor, .minor),
            stage: UsageJSON.string(raw["status"]).map(stageTitle),
            // A maintenance's `started_at` is when it was announced; it
            // begins at `scheduled_for`.
            startedAt: (maintenance
                ? UsageJSON.date(raw["scheduled_for"]) ?? UsageJSON.date(raw["started_at"])
                : UsageJSON.date(raw["started_at"]) ?? UsageJSON.date(raw["scheduled_for"]))
                ?? UsageJSON.date(raw["created_at"]),
            latestUpdate: latest.flatMap { UsageJSON.string($0.0["body"]) },
            updatedAt: latest?.1 ?? UsageJSON.date(raw["updated_at"]),
            url: UsageJSON.string(raw["shortlink"]).flatMap(URL.init(string:)),
            isMaintenance: maintenance)
    }

    /// "in_progress" → "In progress".
    private static func stageTitle(_ raw: String) -> String {
        let words = raw.replacingOccurrences(of: "_", with: " ").lowercased()
        return words.prefix(1).uppercased() + words.dropFirst()
    }

    // MARK: Google Cloud

    /// Google Cloud's `incidents.json`: every incident of the last year, open
    /// ones with no `end` (or an end still ahead), narrowed to `products`.
    static func googleCloud(_ data: Data, products: Set<String>, now: Date) -> StatusReport? {
        guard let all = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]] else { return nil }
        var incidents: [ServiceIncident] = []
        var components: [AffectedComponent] = []
        for raw in all {
            if let end = UsageJSON.date(raw["end"]), end <= now { continue }
            let matched = (raw["affected_products"] as? [[String: Any]] ?? []).filter { product in
                ["id", "title", "current_title"].contains { UsageJSON.string(product[$0]).map(products.contains) == true }
            }
            guard !matched.isEmpty, let id = UsageJSON.string(raw["id"]) else { continue }
            let severity = UsageJSON.string(raw["severity"])
            let impact: ServiceHealth = switch severity {
            case "high": .critical
            case "medium": .major
            default: .minor
            }
            let state: ComponentState = switch severity {
            case "high": .majorOutage
            case "medium": .partialOutage
            default: .degraded
            }
            // Two open incidents can hit the same product; keep the worse state.
            for product in matched {
                guard let name = UsageJSON.string(product["current_title"]) ?? UsageJSON.string(product["title"])
                else { continue }
                if let index = components.firstIndex(where: { $0.name == name }) {
                    components[index].state = max(components[index].state, state)
                } else {
                    components.append(AffectedComponent(name: name, state: state))
                }
            }
            let update = raw["most_recent_update"] as? [String: Any]
            incidents.append(ServiceIncident(
                id: id,
                name: UsageJSON.string(raw["external_desc"]) ?? "Incident",
                impact: impact,
                stage: UsageJSON.string(update?["status"]).map(stageTitle),
                startedAt: UsageJSON.date(raw["begin"]),
                latestUpdate: UsageJSON.string(update?["text"]).map(plainText),
                updatedAt: UsageJSON.date(update?["when"]) ?? UsageJSON.date(raw["modified"]),
                url: UsageJSON.string(raw["uri"]).flatMap { URL(string: "https://status.cloud.google.com/\($0)") },
                isMaintenance: false))
        }
        let ranked = worstFirst(incidents)
        return StatusReport(
            health: ranked.first?.impact ?? .operational,
            summary: nil,
            components: components.sorted { $0.state > $1.state },
            incidents: ranked)
    }

    /// Google writes updates in Markdown. Headings go, emphasis markers go,
    /// and the rest is kept as lines.
    private static func plainText(_ markdown: String) -> String {
        markdown.split(separator: "\n", omittingEmptySubsequences: false)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.hasPrefix("#") }
            .map { $0.replacingOccurrences(of: "**", with: "").replacingOccurrences(of: "__", with: "") }
            .joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func worstFirst(_ incidents: [ServiceIncident]) -> [ServiceIncident] {
        incidents.sorted {
            if $0.impact != $1.impact { return $0.impact > $1.impact }
            return ($0.startedAt ?? .distantPast) > ($1.startedAt ?? .distantPast)
        }
    }
}
