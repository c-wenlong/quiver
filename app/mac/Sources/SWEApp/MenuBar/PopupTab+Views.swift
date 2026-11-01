import SWECore
import SwiftUI

/// Everything the app draws per popup tab, in one place. Adding a tab:
/// a case on `PopupTab` in SWECore, then one branch in each switch
/// here, its content view under `MenuBar/` and its settings view on the
/// Menu bar page. The tab bar, the Tabs settings list and the preview
/// need no change.
@MainActor
extension PopupTab {
    /// What the popup shows under the tab bar.
    @ViewBuilder
    func content(openMain: @escaping () -> Void, dismiss: @escaping () -> Void) -> some View {
        switch self {
        case .usage: UsageTabContent(openMain: openMain, dismiss: dismiss)
        case .compute: ComputeTabContent()
        }
    }

    /// The tab's settings on the Menu bar page's Popup tab.
    @ViewBuilder
    var settings: some View {
        switch self {
        case .usage: UsageTabSettings()
        case .compute: ComputeTabSettings()
        }
    }

    /// The header's Refresh: reload what this tab shows. Only Usage's
    /// header offers the button — Compute's is the fans — but the compute
    /// arm stays a valid "tick now" if a caller asks for it.
    func refresh(_ app: AppModel) {
        switch self {
        case .usage:
            Task { await app.accounts.refreshFromButton() }
            Task { await app.others.load(refreshFailed: true) }
            Task { await app.overview?.load(scan: true) }
        case .compute:
            app.compute.tickTrendsNow()
        }
    }

    /// Refresh greys out while a reload it started is still running.
    func isRefreshing(_ app: AppModel) -> Bool {
        switch self {
        case .usage: app.accounts.isLoading
        case .compute: false
        }
    }
}
