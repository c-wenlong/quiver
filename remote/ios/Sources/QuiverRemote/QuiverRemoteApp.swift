import SwiftUI

/// Quiver Remote: the native iPhone client for `swe remote serve`.
///
/// Talks to the hub's `/v1` REST routes and per-session SSE streams over
/// the tailnet (see docs/remote-control.md and remote/ROADMAP.md). Push
/// still comes from ntfy; the app holds no push code yet.
@main
struct QuiverRemoteApp: App {
    @State private var app = AppModel()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(app)
                .onOpenURL { url in Task { await app.handle(url) } }
                .onChange(of: phase) { _, new in
                    if new == .active, app.hub != nil { app.startRefreshing() }
                    if new == .background { app.stopRefreshing() }
                }
        }
    }
}

struct RootView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        Group {
            if app.hub == nil {
                WelcomeView()
            } else {
                // A fresh set of tabs per hub: navigation, search and the
                // Usage page's figures belong to the hub they came from.
                MainTabs().id(app.hub?.id)
            }
        }
        .tint(Theme.textPrimary)
        .background(Theme.wash.ignoresSafeArea())
    }
}

struct MainTabs: View {
    @Environment(AppModel.self) private var app
    @State private var tab: Tab = .sessions
    @State private var sessionsPath: [Route] = []
    @State private var inboxPath: [Route] = []

    enum Tab { case sessions, inbox, usage }

    var body: some View {
        TabView(selection: $tab) {
            NavigationStack(path: $sessionsPath) {
                SessionsView(path: $sessionsPath).navigationDestination(for: Route.self, destination: destination)
            }
            .tabItem { Label("Sessions", systemImage: "square.stack.3d.up") }
            .tag(Tab.sessions)

            NavigationStack(path: $inboxPath) {
                InboxView().navigationDestination(for: Route.self, destination: destination)
            }
            .tabItem { Label("Inbox", systemImage: "tray") }
            .badge(app.inboxBadge)
            .tag(Tab.inbox)

            NavigationStack {
                UsageView()
            }
            .tabItem { Label("Usage", systemImage: "gauge.with.dots.needle.33percent") }
            .tag(Tab.usage)
        }
        .onAppear { app.startRefreshing() }
    }

    @ViewBuilder private func destination(_ route: Route) -> some View {
        switch route {
        case .chat(let key): ChatView(key: key)
        }
    }
}
