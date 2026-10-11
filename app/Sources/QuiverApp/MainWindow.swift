import QuiverCore
import SwiftUI

struct MainWindow: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        @Bindable var model = model
        NavigationSplitView {
            List(selection: $model.selection) {
                ForEach(SidebarGroup.allCases) { group in
                    Section(group.title) {
                        ForEach(group.sections) { section in
                            Label(section.title, systemImage: section.symbol)
                                .tag(section)
                        }
                    }
                }
            }
            .navigationSplitViewColumnWidth(min: 190, ideal: 210)
        } detail: {
            PageView(section: model.selection ?? .overview)
        }
        .onAppear {
            model.openMainWindow = { [openWindow] in openWindow(id: "main") }
        }
    }
}

struct PageView: View {
    var section: AppSection

    var body: some View {
        switch section {
        case .overview:
            OverviewPage()
        case .accounts:
            AccountsPage()
        case .doctor:
            DoctorPage()
        case .settings:
            SettingsPage()
        default:
            Placeholder(section: section)
        }
    }
}

/// A page not built yet, saying which milestone brings it.
struct Placeholder: View {
    var section: AppSection

    var milestone: String {
        switch section {
        case .overview: "M3"
        case .sessions: "M4"
        case .accounts: "M2"
        case .skills, .agentsMd, .mcpServers: "M5"
        case .doctor, .settings: "M1"
        }
    }

    var body: some View {
        ContentUnavailableView(
            section.title,
            systemImage: section.symbol,
            description: Text("Coming in \(milestone).")
        )
    }
}
