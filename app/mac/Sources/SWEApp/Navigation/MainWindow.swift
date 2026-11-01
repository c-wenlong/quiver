import AppKit
import SWECore
import SwiftUI

/// The shell: a borderless sidebar on the window wash and the page filling
/// the rest of the window edge to edge, split from the sidebar by a hairline.
struct MainWindow: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var animation: Animation? { reduceMotion ? nil : Theme.sidebarAnimation }

    /// The live width while the edge is being dragged; nil otherwise,
    /// so the persisted value is written once, on drop.
    @State private var dragWidth: CGFloat?

    private var sidebarWidth: CGFloat { dragWidth ?? CGFloat(model.sidebarWidth) }

    /// An invisible 8pt strip centred on the sidebar's trailing edge:
    /// drag to resize live, double-click to reset to the standard
    /// width. Its centre column draws a hairline on hover and drag.
    @State private var hoveringHandle = false
    @GestureState private var draggingHandle = false
    /// The width the drag started from; set on the first change event.
    @State private var dragStart: Double?

    private var resizeHandle: some View {
        Color.clear
            .frame(width: Theme.sidebarResizeHandleWidth)
            .frame(maxHeight: .infinity)
            .contentShape(.rect)
            .overlay {
                Theme.border.frame(width: 1)
                    .opacity(hoveringHandle || draggingHandle ? 1 : 0)
                    .allowsHitTesting(false)
            }
            .onHover { hoveringHandle = $0 }
            .pointerStyle(.frameResize(position: .trailing))
            .animation(.easeOut(duration: Theme.fadeDuration),
                       value: hoveringHandle || draggingHandle)
            .highPriorityGesture(TapGesture(count: 2).onEnded { _ in
                dragWidth = nil
                withAnimation(animation) {
                    model.resetSidebarWidth()
                }
            })
            .gesture(DragGesture(minimumDistance: 1, coordinateSpace: .global)
                .updating($draggingHandle) { _, state, _ in state = true }
                .onChanged { value in
                    if dragStart == nil { dragStart = model.sidebarWidth }
                    var transaction = Transaction()
                    transaction.animation = nil
                    withTransaction(transaction) {
                        if let start = dragStart {
                            dragWidth = CGFloat(SidebarWidth.clamped(start + value.translation.width))
                        }
                    }
                }
                .onEnded { value in
                    if let start = dragStart {
                        model.sidebarWidth = SidebarWidth.clamped(start + value.translation.width)
                    }
                    dragStart = nil
                    dragWidth = nil
                })
            // A collapse mid-gesture removes the handle before onEnded:
            // drop the unfinished drag instead of saving it.
            .onDisappear {
                dragStart = nil
                dragWidth = nil
            }
            .accessibilityElement()
            .accessibilityLabel("Sidebar width")
            .accessibilityValue("\(Int(sidebarWidth)) points")
            .accessibilityAdjustableAction { direction in
                switch direction {
                case .increment:
                    model.adjustSidebarWidth(by: SidebarWidth.step)
                case .decrement:
                    model.adjustSidebarWidth(by: -SidebarWidth.step)
                default:
                    break
                }
            }
            .accessibilityAction(named: "Reset width") {
                model.resetSidebarWidth()
            }
    }

    var body: some View {
        HStack(spacing: 0) {
            // The page reflows only when the pinned state changes; a
            // hover expansion floats over the panel instead. A hub's web
            // app is the whole window, sidebar space included: it has
            // its own sidebar, so nesting it inside this one would show
            // two.
            Color.clear
                .frame(width: model.machines.showsHub ? 0
                    : model.sidebarCollapsed ? Theme.sidebarCollapsedWidth : sidebarWidth)
            // A page never sizes the window: it gets the panel's space.
            PanelFrame {
                // Another machine's hub replaces the native pages; This
                // Mac brings them back.
                if let hub = model.machines.selected {
                    HubWebPage(hub: hub)
                } else {
                    PageView(section: model.selection ?? .overview)
                }
            }
                .background(Theme.panel)
                .overlay(alignment: .leading) {
                    if !model.machines.showsHub {
                        Theme.border.frame(width: 1)
                    }
                }
        }
        .background(Theme.wash)
        .ignoresSafeArea(.container, edges: .top)
        // Modal: a setup you can't click away from mid-way. Its own
        // buttons finish or skip it.
        .quiverSheet(isPresented: Binding(
            get: { model.showsFirstRunSetup },
            set: { if !$0 { model.closeFirstRunSetup() } }), modal: true) {
            FirstRunSetupSheet()
        }
        .overlay(alignment: .topLeading) {
            // A hub's web app is the whole window; the sidebar, and the
            // way back to This Mac's pages through it, returns when the
            // pick does. While a hub is up the title bar strip's machine
            // menu does the switching.
            if !model.machines.showsHub {
                Sidebar(width: sidebarWidth)
                // The resize strip lives only while the sidebar is pinned
                // open: not in the rail, not while it floats. Padding, not
                // offset, keeps its hit area centred on the sidebar's edge.
                if !model.sidebarCollapsed {
                    resizeHandle.padding(.leading, sidebarWidth - Theme.sidebarResizeHandleWidth / 2)
                }
            }
        }
        .animation(animation, value: model.sidebarCollapsed)
        .animation(animation, value: model.machines.showsHub)
        .animation(animation, value: model.sidebarWidth)
        // A usage failure that needs the user: one banner at a time,
        // auto-dismissing, its Fix landing on Doctor where the same
        // issue is a row.
        .overlay(alignment: .top) {
            if let issue = model.issues.toast {
                IssueToast(issue: issue, onOpen: {
                    model.openDoctor()
                    model.issues.dismissToast()
                }, onDismiss: {
                    model.issues.dismissToast()
                })
                .padding(.top, 12)
                .transition(.move(edge: .top).combined(with: .opacity))
                .zIndex(1)
            }
        }
        .animation(reduceMotion ? nil : .easeOut(duration: Theme.fadeDuration),
                   value: model.issues.toast)
        // ⌃⌘S mid-drag removes the handle without onEnded: discard.
        .onChange(of: model.sidebarCollapsed) { _, _ in
            dragStart = nil
            dragWidth = nil
        }
        .onAppear {
            model.openMainWindow = { [openWindow] in openWindow(id: "main") }
        }
        // The hub this Mac's satellite agent reports to joins the machine
        // switcher. An older swe without `agent --status` keeps the list
        // as it was.
        .task(id: model.swePath) {
            if let status = try? await model.swe.remoteAgentStatus() {
                model.machines.setAgentHub(status.configured ? status.hub : nil)
            }
        }
        // Outermost, so a card from any page covers the sidebar too.
        .sheetHost()
    }
}

private struct Sidebar: View {
    /// The live expanded width: the model's value, or the pointer's
    /// while the resize strip is being dragged.
    var width: CGFloat
    @Environment(AppModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var focused: AppSection?
    /// Pointer state; `collapsed` mirrors the model's persisted choice.
    /// The delayed expand/hide and toggle-settles-hover logic is shared
    /// with the calendar's side panel through HoverExpansion.
    @State private var hoverExpansion = HoverExpansion()

    private var expansion: SidebarExpansion { hoverExpansion.expansion }
    private var animation: Animation? { reduceMotion ? nil : Theme.sidebarAnimation }
    private var isExpanded: Bool { expansion.isExpanded }
    private var isFloating: Bool { expansion.isFloating }
    /// Whether the list has scrolled under the header, which then shows
    /// a hairline along its bottom edge.
    @State private var scrolled = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Color.clear.frame(height: headerHeight)
            list
                .frame(width: isExpanded ? width : Theme.sidebarCollapsedWidth,
                       alignment: .leading)
                .clipped()
        }
        // Over the list, not above it: the list's scroll view reaches up
        // into the title bar and would otherwise lie on top of the toggle
        // and take its clicks. Unclipped, so in the rail the toggle sits
        // past the traffic lights, over the page's top edge.
        .overlay(alignment: .topLeading) { header }
        .frame(width: isExpanded ? width : Theme.sidebarCollapsedWidth,
               alignment: .leading)
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
        // floating; its header shares the traffic lights' row.
        .ignoresSafeArea(.container, edges: .top)
        .animation(animation, value: isExpanded)
        // The tracking area spans the full unclipped width, so where the
        // pointer is decides "inside": collapsed, only the icon strip
        // arms the expansion — a pointer crossing the rail to reach the
        // page's left edge never floats the panel over its target. The
        // header row is left out: in full screen the rail's toggle sits
        // there, and a float would slide it away from the click. Once
        // floating, the whole expanded panel keeps itself hovered.
        .onContinuousHover { phase in
            switch phase {
            case .active(let location):
                hover(isExpanded
                    || (location.x <= Theme.sidebarHoverWidth && location.y > headerHeight))
            case .ended:
                hover(false)
            }
        }
        .onAppear { hoverExpansion.expansion.collapsed = model.sidebarCollapsed }
        .onChange(of: model.sidebarCollapsed) { _, new in
            // The button already toggled expansion, so this only fires
            // for outside changes like the menu shortcut: route them
            // through toggle() so hover suppression still applies.
            if new != expansion.collapsed { toggleExpansion() }
        }
    }

    /// The pages, grouped, scrolling under the header.
    private var list: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                MachineSwitcherButton(collapsed: !isExpanded)
                ForEach(model.sidebar.visibleGroups) { group in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(group.title)
                            .font(Theme.font(size: 11))
                            .foregroundStyle(Theme.textMuted)
                            .padding(.horizontal, 10)
                            .padding(.bottom, 4)
                            // The rail keeps its height so the icons
                            // never move vertically.
                            .opacity(isExpanded ? 1 : 0)
                        ForEach(model.sidebar.sections(in: group)) { section in
                            NavItem(section: section,
                                    isSelected: model.selection == section && !model.machines.showsHub,
                                    isFocused: focused == section,
                                    collapsed: !isExpanded,
                                    badge: section == .inbox ? model.github.unreadCount
                                        : section == .settings ? model.issues.badgeCount : 0,
                                    shortcut: CommandKeyHold.shared.isHeld
                                        ? model.sidebar.shortcutNumber(of: section) : nil) {
                                // Reselecting the open page asks a detail
                                // view on it to go back.
                                model.select(section)
                            }
                            .focused($focused, equals: section)
                        }
                    }
                }
        }
            .padding(.leading, Theme.sidebarLeadingPadding)
            .padding(.trailing, Theme.sidebarTrailingPadding)
            .padding(.top, 8)
            // Always laid out at full width; the rail clips the right
            // side instead of reflowing, so icons keep their position.
            .frame(width: width, alignment: .leading)
            // Like a source list: up and down move the selection and focus.
            .onKeyPress(.upArrow) { move(-1) }
            .onKeyPress(.downArrow) { move(1) }
        }
        .scrollIndicators(.never)
        .onScrollGeometryChange(for: Bool.self) { geometry in
            geometry.contentOffset.y + geometry.contentInsets.top > 0
        } action: { _, new in
            scrolled = new
        }
    }

    /// The window's title bar, read from AppKit: the header sizes and
    /// places itself by it.
    @State private var titleBar = TitleBar()

    /// The first x clear of the traffic lights; in full screen, where they
    /// are hidden, the nav icons' column.
    private var titleBarLeading: CGFloat {
        titleBar.isFullScreen ? Theme.sidebarLeadingPadding + 8 : Theme.titleBarLeading
    }

    /// The title bar's height, so the header centres on the traffic lights.
    private var headerHeight: CGFloat { max(Theme.titleBarHeight, titleBar.height) }

    /// The wordmark and the collapse/expand button, in the title bar row
    /// beside the traffic lights, pinned over the top of the list.
    private var header: some View {
        HStack(spacing: 4) {
            Text("SWE")
                .font(Theme.font(size: 15, weight: .semibold))
                .foregroundStyle(Theme.textStrong)
                .fixedSize()
                // The rail folds the wordmark away so the toggle slides
                // over to where it began.
                .frame(width: isExpanded ? nil : 0, alignment: .leading)
                .clipped()
                .opacity(isExpanded ? 1 : 0)
                .accessibilityHidden(!isExpanded)
            toggleRow
        }
        .padding(.leading, isExpanded ? titleBarLeading : titleBarLeading - 8)
        .frame(width: width, height: headerHeight, alignment: .leading)
        .overlay(alignment: .bottomLeading) {
            Theme.border
                .frame(width: isExpanded ? width : Theme.sidebarCollapsedWidth, height: 1)
                .opacity(scrolled ? 1 : 0)
        }
        .background(TitleBarReader(titleBar: $titleBar))
    }

    /// The collapse/expand button: right after the wordmark while the
    /// sidebar is open (pinned or hover-floating), alone at the row's
    /// start in the collapsed rail.
    private var toggleRow: some View {
        SidebarToggle(label: model.sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar") {
            toggleExpansion()
            model.sidebarCollapsed = expansion.collapsed
        }
        .frame(width: Theme.navIconPillWidth, height: Theme.controlHeight)
    }

    /// Only a collapsed sidebar reacts to the pointer: in after 0.12 s,
    /// out after 0.2 s, so a quick pass over the rail does not flash it
    /// open and a short exit does not slam it shut.
    /// A toggle settles any pending hover change first, so a collapse
    /// right after entering still suppresses the hover expansion and a
    /// late entry can't reopen it.
    private func toggleExpansion() {
        hoverExpansion.toggle()
    }

    private func hover(_ inside: Bool) {
        hoverExpansion.hover(inside, collapsed: model.sidebarCollapsed)
    }


    private func move(_ offset: Int) -> KeyPress.Result {
        let next = model.sidebar.step(focused ?? model.selection ?? .overview, by: offset)
        model.selection = next
        if focused != nil { focused = next }
        return .handled
    }
}

/// The banner a new actionable usage issue raises over the window:
/// summary, Fix and dismiss, gone on its own after a few seconds.
private struct IssueToast: View {
    var issue: Issue
    var onOpen: () -> Void
    var onDismiss: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(Theme.font(size: 13))
                .foregroundStyle(Theme.warning)
            Text(issue.message)
                .font(Theme.bodySmall)
                .foregroundStyle(Theme.textPrimary)
                .lineLimit(2)
            Button("Fix", action: onOpen)
                .primaryButton()
            Button("Dismiss", systemImage: "xmark", action: onDismiss)
                .labelStyle(.iconOnly)
                .iconButton()
                .hoverTip("Dismiss")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(Theme.panel, in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
            .strokeBorder(Theme.border, lineWidth: 1))
        .shadow(color: .black.opacity(0.15), radius: 8, y: 2)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Issue: \(issue.message)")
        .task(id: issue.id) {
            try? await Task.sleep(for: .seconds(7))
            if !Task.isCancelled { onDismiss() }
        }
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
    /// Unread count on the row — the Inbox passes `unreadCount`; other
    /// rows pass nothing. Expanded: a source-list capsule. Collapsed: a
    /// dot on the icon's corner.
    var badge = 0
    /// While ⌘ is held, the digit of this row's ⌘-number shortcut. The
    /// cap is pinned to the row's trailing edge so every cap lines up in
    /// one column, covering the unread count; in the rail it sits on the
    /// icon.
    var shortcut: Int?
    var action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: section.symbol)
                    .font(Theme.font(size: 15, weight: .regular))
                    .frame(width: 18)
                Text(section.title)
                    .font(Theme.nav.weight(isSelected ? .medium : .regular))
                    // No truncation ellipsis mid-animation: the row clips.
                    .fixedSize()
                    .opacity(collapsed ? 0 : 1)
                Spacer(minLength: 0)
                if badge > 0 {
                    Text(badge > 99 ? "99+" : "\(badge)")
                        // The count is spoken through accessibilityValue;
                        // hiding the text keeps VoiceOver from reading
                        // "Inbox 3, 3 unread".
                        .accessibilityHidden(true)
                        .font(Theme.font(size: 10, weight: .semibold).monospacedDigit())
                        .foregroundStyle(Theme.textSecondary)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(Theme.subtle, in: .capsule)
                        .fixedSize()
                        .opacity(collapsed || shortcut != nil ? 0 : 1)
                }
            }
            .foregroundStyle(isSelected || hovering ? Theme.textPrimary : Theme.textNav)
            .padding(.horizontal, 8)
            .frame(maxWidth: collapsed ? Theme.navIconPillWidth : .infinity, alignment: .leading)
            .frame(height: Theme.navRowHeight)
            .clipped()
            .background(isSelected ? Theme.navActive : hovering ? Theme.hover : .clear,
                        in: .rect(cornerRadius: Theme.controlRadius))
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
            // An overlay, not a row item: a long label in a narrow
            // sidebar can't push the cap out of the column.
            .overlay(alignment: .trailing) {
                if !collapsed, let shortcut {
                    ShortcutCap(text: "⌘\(shortcut)", width: 26)
                        .padding(.trailing, 6)
                }
            }
            .overlay(alignment: .bottomTrailing) {
                if collapsed, let shortcut {
                    ShortcutCap(text: "\(shortcut)", width: 14)
                        .offset(x: 2, y: 2)
                }
            }
            .overlay(alignment: .topTrailing) {
                if collapsed, badge > 0 {
                    Circle()
                        .fill(Theme.interactive)
                        .frame(width: 6, height: 6)
                        .offset(x: -3, y: 6)
                }
            }
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))
        .focusRing(isFocused, cornerRadius: Theme.controlRadius)
        .onHover { hovering = $0 }
        .accessibilityAddTraits(isSelected ? .isSelected : [])
        .accessibilityValue(badge > 0 ? "\(badge) unread" : "")
    }
}

/// The sidebar toggle as an AppKit button. In the title bar row a SwiftUI
/// button never fires: AppKit lets the window's root hosting view treat a
/// click there as a window drag, and in a hosting view of its own the
/// press still never completed. An NSButton tracks the click itself and
/// never moves the window, like the traffic lights.
private struct SidebarToggle: NSViewRepresentable {
    var label: String
    var action: () -> Void

    func makeNSView(context: Context) -> ToggleButton {
        ToggleButton()
    }

    func updateNSView(_ button: ToggleButton, context: Context) {
        button.run = action
        // Here, not once at init, so it follows View › Zoom.
        button.image = NSImage(systemSymbolName: "sidebar.left", accessibilityDescription: nil)?
            .withSymbolConfiguration(.init(pointSize: Theme.scaled(15), weight: .regular))
        button.setAccessibilityLabel(label)
        button.toolTip = "\(label) ⌃⌘S"
    }

    final class ToggleButton: NSButton {
        var run: () -> Void = {}
        private var hovering = false { didSet { updateLayer() } }

        init() {
            super.init(frame: .zero)
            isBordered = false
            title = ""
            imagePosition = .imageOnly
            contentTintColor = NSColor(Theme.textNav)
            // Tab reaches it with Keyboard navigation on; a click never
            // makes an NSButton first responder, so no ring sticks.
            focusRingType = .default
            wantsLayer = true
            layer?.cornerRadius = Theme.controlRadius
            target = self
            action = #selector(fire)
        }

        required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

        @objc private func fire() { run() }

        override var wantsUpdateLayer: Bool { true }

        override func updateLayer() {
            super.updateLayer()
            // Resolved here so the fill follows light and dark.
            layer?.backgroundColor = hovering ? NSColor(Theme.hover).cgColor : nil
        }

        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

        override var focusRingMaskBounds: NSRect { bounds }

        override func drawFocusRingMask() {
            NSBezierPath(roundedRect: bounds, xRadius: Theme.controlRadius,
                         yRadius: Theme.controlRadius).fill()
        }

        override func updateTrackingAreas() {
            super.updateTrackingAreas()
            trackingAreas.forEach(removeTrackingArea)
            addTrackingArea(NSTrackingArea(rect: .zero,
                options: [.mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect],
                owner: self))
            // A new area sends no exit for a pointer the button slid out
            // from under, so read where the pointer is now.
            if let window {
                hovering = bounds.contains(convert(window.mouseLocationOutsideOfEventStream, from: nil))
            }
        }

        override func mouseEntered(with event: NSEvent) { hovering = true }
        override func mouseExited(with event: NSEvent) { hovering = false }
    }
}

/// The window's title bar: its height (0 in full screen) and whether
/// the window is in full screen, where the traffic lights are hidden.
/// Shared with `HubWebPage`, whose strip keeps the same row.
struct TitleBar: Equatable {
    var height: CGFloat = 0
    var isFullScreen = false
}

/// Reports its window's `TitleBar` on attach and on every full-screen
/// change.
struct TitleBarReader: NSViewRepresentable {
    @Binding var titleBar: TitleBar

    func makeNSView(context: Context) -> View {
        let view = View()
        view.report = { titleBar = $0 }
        return view
    }

    func updateNSView(_ view: View, context: Context) {
        view.report = { titleBar = $0 }
    }

    final class View: NSView {
        var report: ((TitleBar) -> Void)?
        private var last: TitleBar?
        private var observers: [NSObjectProtocol] = []

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            observers.forEach(NotificationCenter.default.removeObserver)
            observers = []
            guard let window else { return }
            for name in [NSWindow.didEnterFullScreenNotification, NSWindow.didExitFullScreenNotification] {
                observers.append(NotificationCenter.default.addObserver(
                    forName: name, object: window, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated { self?.update() }
                })
            }
            update()
        }

        private func update() {
            guard let window else { return }
            let titleBar = TitleBar(
                height: window.frame.height - window.contentLayoutRect.height,
                isFullScreen: window.styleMask.contains(.fullScreen))
            guard titleBar != last else { return }
            last = titleBar
            // Not mid-update: the first report lands while SwiftUI is
            // still attaching the view.
            DispatchQueue.main.async { [report] in report?(titleBar) }
        }
    }
}

/// Gives the page exactly the space the window offers and never asks the
/// page how big it wants to be. The root stack and the window's size
/// constraints otherwise measure the page at several widths on every pass
/// (zero and infinity to rank flexibility, then the real width), and a
/// scrolling page answers by laying out all of its content. On Overview
/// that was about 90% of the main thread during any geometry animation.
struct PanelFrame: Layout {
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        proposal.replacingUnspecifiedDimensions(by: .zero)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        for subview in subviews {
            subview.place(at: bounds.origin, proposal: ProposedViewSize(bounds.size))
        }
    }

    // The stack's centre alignment would otherwise ask the page too.
    func explicitAlignment(of guide: HorizontalAlignment, in bounds: CGRect, proposal: ProposedViewSize,
                           subviews: Subviews, cache: inout ()) -> CGFloat? { nil }

    func explicitAlignment(of guide: VerticalAlignment, in bounds: CGRect, proposal: ProposedViewSize,
                           subviews: Subviews, cache: inout ()) -> CGFloat? { nil }
}

/// The key cap a held ⌘ shows on a sidebar row: "⌘3" in the expanded
/// sidebar, "3" on a rail icon.
private struct ShortcutCap: View {
    var text: String
    var width: CGFloat

    var body: some View {
        Text(text)
            .font(Theme.font(size: 11).monospacedDigit())
            .foregroundStyle(Theme.textSecondary)
            .frame(width: width, height: 18)
            .background(Theme.panel, in: .rect(cornerRadius: 5))
            .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(Theme.borderStrong, lineWidth: 1))
            .fixedSize()
            // The menu items already speak the shortcut.
            .accessibilityHidden(true)
            .transition(.opacity)
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
        case .calendar:
            CalendarPage()
        case .pricing:
            PricingPage()
        case .repositories:
            RepositoriesPage()
        case .tasks:
            TasksPage()
        case .inbox:
            InboxPage()
        case .accounts:
            AccountsPage()
        case .agentsMd:
            AgentsMdPage()
        case .mcpServers:
            McpServersPage()
        case .models:
            ModelsPage()
        case .plugins:
            PluginsPage()
        case .hooks:
            HooksPage()
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
        case .settings:
            SettingsPage()
        }
    }
}
