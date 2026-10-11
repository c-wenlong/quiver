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
                // A page never widens the window: it gets the panel's width.
                .frame(minWidth: 0, maxWidth: .infinity, maxHeight: .infinity)
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
    @FocusState private var focused: AppSection?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                ForEach(SidebarGroup.allCases) { group in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(group.title)
                            .font(.system(size: 11))
                            .foregroundStyle(Theme.textMuted)
                            .padding(.horizontal, 10)
                            .padding(.bottom, 4)
                        ForEach(group.sections) { section in
                            NavItem(section: section, isSelected: model.selection == section,
                                    isFocused: focused == section) {
                                model.selection = section
                            }
                            .focused($focused, equals: section)
                        }
                    }
                }
            }
            .padding(.horizontal, 8)
            // Clear of the traffic lights.
            .padding(.top, 52)
            // Like a source list: up and down move the selection and focus.
            .onKeyPress(.upArrow) { move(-1) }
            .onKeyPress(.downArrow) { move(1) }
        }
        .scrollIndicators(.never)
    }

    private func move(_ offset: Int) -> KeyPress.Result {
        let next = (focused ?? model.selection ?? .overview).step(offset)
        model.selection = next
        if focused != nil { focused = next }
        return .handled
    }
}

/// 32pt row, 13pt label, 15pt regular symbol; the current page on a grey
/// pill at medium weight. Hover changes the fill only.
private struct NavItem: View {
    var section: AppSection
    var isSelected: Bool
    var isFocused: Bool
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
            .padding(.horizontal, 8)
            .frame(height: Theme.navRowHeight)
            .background(isSelected ? Theme.navActive : hovering ? Theme.hover : .clear,
                        in: .rect(cornerRadius: Theme.controlRadius))
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))
        .focusRing(isFocused, cornerRadius: Theme.controlRadius)
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
        case .sessions:
            SessionsPage()
        case .accounts:
            AccountsPage()
        case .agentsMd:
            AgentsMdPage()
        case .mcpServers:
            McpServersPage()
        case .plugins:
            PluginsPage()
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
        }
    }
}
