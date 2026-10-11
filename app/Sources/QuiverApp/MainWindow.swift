import QuiverCore
import SwiftUI

/// The shell, after TokenTracker: a borderless sidebar on the window wash
/// and the page in a rounded panel inset 8pt from the top and 12pt from the
/// right and bottom.
struct MainWindow: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        HStack(spacing: 0) {
            Sidebar()
                .frame(width: Theme.sidebarWidth)
            PageView(section: model.selection ?? .overview)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.panel)
                .clipShape(.rect(cornerRadius: Theme.panelRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.panelRadius).strokeBorder(Theme.border, lineWidth: 1))
                .shadow(color: .black.opacity(0.05), radius: 1, y: 1)
                .padding(.top, 8)
                .padding([.trailing, .bottom], 12)
        }
        .background(Theme.wash)
        .ignoresSafeArea(.container, edges: .top)
        .onAppear {
            model.openMainWindow = { [openWindow] in openWindow(id: "main") }
        }
    }
}

private struct Sidebar: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                ForEach(SidebarGroup.allCases) { group in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(group.title)
                            .font(Theme.caption)
                            .foregroundStyle(Theme.textMuted)
                            .padding(.horizontal, 10)
                            .padding(.bottom, 4)
                        ForEach(group.sections) { section in
                            NavItem(section: section, isSelected: model.selection == section) {
                                model.selection = section
                            }
                        }
                    }
                }
            }
            .padding(.horizontal, 8)
            // Clear of the traffic lights.
            .padding(.top, 52)
        }
        .scrollIndicators(.never)
    }
}

/// 32pt row, 13pt label, 15pt regular symbol; the current page on a grey
/// pill at medium weight. Hover changes the fill only.
private struct NavItem: View {
    var section: AppSection
    var isSelected: Bool
    var action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: section.symbol)
                    .font(.system(size: 15, weight: .regular))
                    .frame(width: 18)
                Text(section.title)
                    .font(Theme.nav.weight(isSelected ? .medium : .regular))
                Spacer(minLength: 0)
            }
            .foregroundStyle(isSelected || hovering ? Theme.textPrimary : Theme.textNav)
            .padding(.horizontal, 10)
            .frame(height: Theme.navRowHeight)
            .background(isSelected ? Theme.navActive : hovering ? Theme.hover : .clear,
                        in: .rect(cornerRadius: Theme.controlRadius))
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))
        .onHover { hovering = $0 }
        .accessibilityAddTraits(isSelected ? .isSelected : [])
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
        case .apiKeys:
            APIKeysPage()
        case .serviceStatus:
            ServiceStatusPage()
        case .harnesses:
            HarnessesPage()
        case .skills:
            SkillsPage()
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
        case .accounts, .apiKeys: "M2"
        case .harnesses, .skills, .agentsMd, .mcpServers: "M5"
        case .doctor, .settings: "M1"
        case .serviceStatus: "M7"
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
