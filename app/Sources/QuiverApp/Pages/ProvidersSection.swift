import QuiverCore
import SwiftUI

/// Settings › Providers: two tabs. Harnesses are the CLIs that run
/// sessions; Inference is what they run on, the model plans and the API
/// keys.
struct ProvidersSection: View {
    enum Tab: String { case harnesses, inference }

    @Environment(AppModel.self) private var app
    @AppStorage("providersTab") private var saved = Tab.harnesses.rawValue

    private var tab: Binding<Tab> {
        Binding(get: { Tab(rawValue: saved) ?? .harnesses }, set: { saved = $0.rawValue })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Tabs(options: [(Tab.harnesses, "Harnesses"), (.inference, "Inference")], selection: tab)
                .fixedSize()
            switch tab.wrappedValue {
            case .harnesses:
                ProviderVisibilityCard(model: app.providers)
            case .inference:
                InferencePlansCard()
                APIKeysSection()
            }
        }
    }
}

/// The model plans Quiver knows, and whether each one is set up here.
private struct InferencePlansCard: View {
    @State private var detected: Set<String>?

    var body: some View {
        SettingsCard {
            SettingRow(title: "Inference plans",
                       detail: "Model subscriptions you use through a harness. Quiver doesn't read their usage yet.") {
                EmptyView()
            }
            SettingsDivider()
            VStack(spacing: 0) {
                ForEach(InferenceCatalog.all) { plan in
                    InferencePlanRow(plan: plan, found: detected.map { $0.contains(plan.id) })
                }
            }
            .padding(.vertical, 8)
        }
        .task {
            let path = await LoginShell.cachedEnvironment()["PATH"] ?? ""
            detected = Set(InferenceCatalog.detected { ProviderDetection.commandExists($0, path: path) })
        }
    }
}

private struct InferencePlanRow: View {
    var plan: InferencePlan
    /// Nil until the PATH check finishes.
    var found: Bool?

    var body: some View {
        HStack(spacing: 14) {
            HarnessIcon(harness: plan.icon, size: 20)
            VStack(alignment: .leading, spacing: 1) {
                Text(plan.name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                Text(plan.vendor).font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            Spacer()
            Text(status).font(Theme.caption).foregroundStyle(Theme.textSecondary)
        }
        .opacity(found == false ? 0.4 : 1)
        .padding(.horizontal, 8)
        .frame(height: 44)
        .accessibilityElement(children: .combine)
    }

    private var status: String {
        switch found {
        case nil: "Checking…"
        case true?: "\(plan.commands.joined(separator: ", ")) found"
        case false?: "\(plan.commands.joined(separator: ", ")) not found"
        }
    }
}
