import SwiftUI

/// Which side of the GitHub tab is showing.
enum GitHubSection: String, CaseIterable, Identifiable {
    case issues, pulls

    var id: String { rawValue }

    var title: String {
        switch self {
        case .issues: "Issues"
        case .pulls: "Pull requests"
        }
    }
}

/// The GitHub tab: the pinned repos' issues and pull requests behind one
/// switch, so the tab bar stays at five tabs and iPhone never moves a
/// page behind "More". Each side keeps its own model (owned by the tab
/// bar), so switching doesn't throw away what a side already loaded.
struct GitHubView: View {
    @Binding var section: GitHubSection
    let issues: IssuesModel
    let pulls: PullsModel
    /// The stack's own chat push, for a launch begun from either side.
    var onStarted: (String) -> Void

    var body: some View {
        Group {
            switch section {
            case .issues: IssuesView(onStarted: onStarted, model: issues)
            case .pulls: PullsView(model: pulls)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Picker("Show", selection: $section) {
                    ForEach(GitHubSection.allCases) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                .fixedSize()
                .accessibilityLabel("Issues or pull requests")
            }
        }
    }
}
