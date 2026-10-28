import SwiftUI

/// Quiver Remote: the native iPhone client for `swe remote serve`.
///
/// Talks to the hub's `/v1` REST routes and per-session SSE streams over
/// the tailnet (see docs/remote-control.md and app/ios/ROADMAP.md). Push
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
    @State private var tasksPath: [Route] = []
    @State private var pullsPath: [Route] = []
    @State private var issuesPath: [Route] = []
    /// The Tasks page's model lives here so the task detail the stack
    /// pushes reads the same reply.
    @State private var tasksModel = TasksModel()

    enum Tab { case sessions, inbox, tasks, pulls, issues, usage }

    var body: some View {
        TabView(selection: $tab) {
            NavigationStack(path: $sessionsPath) {
                SessionsView(path: $sessionsPath)
                    .navigationDestination(for: Route.self) { destination($0, onStarted: { sessionsPath.append(.chat($0)) }) }
            }
            .tabItem { Label("Sessions", systemImage: "square.stack.3d.up") }
            .tag(Tab.sessions)

            NavigationStack(path: $inboxPath) {
                InboxView()
                    .navigationDestination(for: Route.self) { destination($0, onStarted: { inboxPath.append(.chat($0)) }) }
            }
            .tabItem { Label("Inbox", systemImage: "tray") }
            .badge(app.inboxBadge)
            .tag(Tab.inbox)

            NavigationStack(path: $tasksPath) {
                TasksView(model: tasksModel) { tasksPath.append(.chat($0)) }
                    .navigationDestination(for: Route.self) { destination($0, onStarted: { tasksPath.append(.chat($0)) }) }
            }
            .tabItem { Label("Tasks", systemImage: "checklist") }
            .tag(Tab.tasks)

            NavigationStack(path: $pullsPath) {
                PullsView()
                    .navigationDestination(for: Route.self) { destination($0, onStarted: { pullsPath.append(.chat($0)) }) }
            }
            .tabItem { Label("Pull requests", systemImage: "arrow.triangle.pull") }
            .tag(Tab.pulls)

            NavigationStack(path: $issuesPath) {
                IssuesView { issuesPath.append(.chat($0)) }
                    .navigationDestination(for: Route.self) { destination($0, onStarted: { issuesPath.append(.chat($0)) }) }
            }
            .tabItem { Label("Issues", systemImage: "exclamationmark.circle") }
            .tag(Tab.issues)

            NavigationStack {
                UsageView()
            }
            .tabItem { Label("Usage", systemImage: "gauge.with.dots.needle.33percent") }
            .tag(Tab.usage)
        }
        .onAppear {
            openIssue(app.pendingIssue)
            app.startRefreshing()
        }
        // A `quiver://issue/<o>/<r>/<n>` link opens the detail on the
        // issues stack, over whatever was there.
        .onChange(of: app.pendingIssue) { _, link in openIssue(link) }
    }

    private func openIssue(_ link: IssueLink?) {
        guard let link else { return }
        app.pendingIssue = nil
        tab = .issues
        issuesPath = [.issue(repo: link.repo, number: link.number)]
    }

    /// `onStarted` is the stack's own chat push: a launch begun on the
    /// Tasks stack pushes its chat there too, not on Pulls or Issues.
    @ViewBuilder private func destination(_ route: Route, onStarted: @escaping (String) -> Void) -> some View {
        switch route {
        case .chat(let key): ChatView(key: key)
        case .pull(let repo, let number):
            PullDetailView(repo: repo, number: number, onStarted: onStarted)
        case .issue(let repo, let number):
            IssueDetailView(repo: repo, number: number, onStarted: onStarted)
        case .task(let id):
            // Only the Tasks stack pushes this; its detail pushes the
            // linked chats, issues and pulls on that same stack.
            TaskDetailView(taskId: id, model: tasksModel, onRoute: { tasksPath.append($0) })
        }
    }
}
