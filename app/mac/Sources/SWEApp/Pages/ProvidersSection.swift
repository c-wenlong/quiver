import SWECore
import SwiftUI

/// Settings › Providers: two tabs. Harnesses are the CLIs that run
/// sessions; Inference is what they run on, the coding plans and the API
/// keys, in one list.
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
                InferenceSection()
            }
        }
    }
}
