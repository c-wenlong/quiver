import QuiverCore
import SwiftUI

/// The shell, after TokenTracker: a borderless sidebar on the window wash
/// and the page in a rounded panel inset 8pt from the top and 12pt from the
/// right and bottom.
struct MainWindow: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var animation: Animation? { reduceMotion ? nil : Theme.sidebarAnimation }

    var body: some View {
        HStack(spacing: 0) {
            // The page reflows only when the pinned state changes; a
            // hover expansion floats over the panel instead.
            Color.clear
                .frame(width: model.sidebarCollapsed
                    ? Theme.sidebarCollapsedWidth : Theme.sidebarWidth)
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
        .overlay(alignment: .leading) { Sidebar() }
        .animation(animation, value: model.sidebarCollapsed)
        .onAppear {
            model.openMainWindow = { [openWindow] in openWindow(id: "main") }
        }
    }
}

private struct Sidebar: View {
    @Environment(AppModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var focused: AppSection?
    /// Pointer state; `collapsed` mirrors the model's persisted choice.
    @State private var expansion = SidebarExpansion()
    /// The delayed hover change; a new pointer event replaces it.
    @State private var pending: Task<Void, Never>?
    /// Where the pointer is right now, ahead of the delayed hover change.
    @State private var pointerInside = false

    private var animation: Animation? { reduceMotion ? nil : Theme.sidebarAnimation }
    private var isExpanded: Bool { expansion.isExpanded }
    private var isFloating: Bool { expansion.isFloating }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 4) {
                toggleRow
                    // Same spacing as between groups.
                    .padding(.bottom, Theme.gutter)
                VStack(alignment: .leading, spacing: 18) {
                    ForEach(SidebarGroup.allCases) { group in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(group.title)
                                .font(.system(size: 11))
                                .foregroundStyle(Theme.textMuted)
                                .padding(.horizontal, 10)
                                .padding(.bottom, 4)
                                // The rail keeps its height so the icons
                                // never move vertically.
                                .opacity(isExpanded ? 1 : 0)
                            ForEach(group.sections) { section in
                                NavItem(section: section, isSelected: model.selection == section,
                                        isFocused: focused == section,
                                        collapsed: !isExpanded) {
                                    // Reselecting the open page asks a detail
                                    // view on it to go back.
                                    if model.selection == section {
                                        model.navReselects += 1
                                    } else {
                                        model.selection = section
                                    }
                                }
                                .focused($focused, equals: section)
                            }
                        }
                    }
                }
            }
            .padding(.leading, Theme.sidebarLeadingPadding)
            .padding(.trailing, Theme.sidebarTrailingPadding)
            // Clear of the traffic lights for the toggle row.
            .padding(.top, 28)
            // Always laid out at full width; the rail clips the right
            // side instead of reflowing, so icons keep their position.
            .frame(width: Theme.sidebarWidth, alignment: .leading)
            // Like a source list: up and down move the selection and focus.
            .onKeyPress(.upArrow) { move(-1) }
            .onKeyPress(.downArrow) { move(1) }
        }
        .scrollIndicators(.never)
        .frame(width: isExpanded ? Theme.sidebarWidth : Theme.sidebarCollapsedWidth,
               alignment: .leading)
        .clipped()
        // A floating rail covers the page: it needs the window surface,
        // a hairline edge and a shadow, fading in with the expansion.
        // The shadow sits on the surface only, so the labels stay crisp.
        .background {
            Theme.wash
                .shadow(color: .black.opacity(0.12), radius: 8)
                .opacity(isFloating ? 1 : 0)
        }
        .overlay(alignment: .trailing) {
            Theme.border.frame(width: 1).opacity(isFloating ? 1 : 0)
        }
        .frame(maxHeight: .infinity)
        // The sidebar runs to the very top of the window, pinned or
        // floating; the content's top padding clears the traffic lights.
        .ignoresSafeArea(.container, edges: .top)
        .animation(animation, value: isExpanded)
        .onHover(perform: hover)
        .onAppear { expansion.collapsed = model.sidebarCollapsed }
        .onChange(of: model.sidebarCollapsed) { _, new in
            // The button already toggled expansion, so this only fires
            // for outside changes like the menu shortcut: route them
            // through toggle() so hover suppression still applies.
            if new != expansion.collapsed { toggleExpansion() }
        }
    }

    /// The collapse/expand button: its own row under the traffic lights,
    /// trailing-aligned. Expanded it hugs the sidebar's trailing edge; in
    /// the rail the trailing padding parks it right over the icon pill,
    /// and the animated padding slides it with the width change. It
    /// follows the pinned state only, so a hover expansion never slides
    /// it out from under the pointer.
    @State private var hoveringToggle = false

    private var toggleTrailingPadding: CGFloat {
        !expansion.collapsed ? 0
            : Theme.sidebarWidth - Theme.sidebarLeadingPadding - Theme.sidebarTrailingPadding
                - Theme.navIconPillWidth
    }

    private var toggleRow: some View {
        let label = model.sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"
        return Button {
            toggleExpansion()
            model.sidebarCollapsed = expansion.collapsed
        } label: {
            Image(systemName: "sidebar.left")
                .font(.system(size: 15, weight: .regular))
                .frame(width: 18)
                .foregroundStyle(Theme.textNav)
                .padding(.horizontal, 8)
                .frame(height: Theme.navRowHeight)
                .background(hoveringToggle ? Theme.hover : .clear,
                            in: .rect(cornerRadius: Theme.controlRadius))
                .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        // The shared plain button: the app's own focus ring on keyboard
        // focus, no stuck blue ring after a click.
        .plainButton(cornerRadius: Theme.controlRadius)
        .onHover { hoveringToggle = $0 }
        .accessibilityLabel(label)
        .help("\(label) ⌃⌘S")
        .frame(maxWidth: .infinity, alignment: .trailing)
        .padding(.trailing, toggleTrailingPadding)
    }

    /// Only a collapsed sidebar reacts to the pointer: in after 0.12 s,
    /// out after 0.2 s, so a quick pass over the rail does not flash it
    /// open and a short exit does not slam it shut.
    /// A toggle settles any pending hover change first, so a collapse
    /// right after entering still suppresses the hover expansion and a
    /// late entry can't reopen it.
    private func toggleExpansion() {
        pending?.cancel()
        expansion.pointer(inside: pointerInside)
        expansion.toggle()
    }

    private func hover(_ inside: Bool) {
        pending?.cancel()
        pointerInside = inside
        if !inside { expansion.pointerLeft() }
        guard model.sidebarCollapsed else {
            expansion.pointer(inside: inside)
            return
        }
        pending = Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(inside ? 120 : 200))
            guard !Task.isCancelled else { return }
            expansion.pointer(inside: inside)
        }
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
    /// The icon rail: the label fades and clips away, the pill hugs the
    /// icon (8 + 18 + 8) at the same x it has when expanded.
    var collapsed: Bool
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
                    // No truncation ellipsis mid-animation: the row clips.
                    .fixedSize()
                    .opacity(collapsed ? 0 : 1)
                Spacer(minLength: 0)
            }
            .foregroundStyle(isSelected || hovering ? Theme.textPrimary : Theme.textNav)
            .padding(.horizontal, 8)
            .frame(maxWidth: collapsed ? Theme.navIconPillWidth : .infinity, alignment: .leading)
            .frame(height: Theme.navRowHeight)
            .clipped()
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
        case .menuBar:
            MenuBarPage()
        case .widgets:
            WidgetsPage()
        case .serviceStatus:
            ServiceStatusPage()
        case .harnesses:
            HarnessesPage()
        case .compute:
            ComputePage()
        case .skills:
            SkillsPage()
        case .doctor:
            DoctorPage()
        case .settings:
            SettingsPage()
        }
    }
}
