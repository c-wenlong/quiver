import Testing
@testable import SWEApp
@testable import SWECore

/// The row filter the repo surfaces share. Its job is a search box:
/// an empty query must leave the surface alone — on this Foundation
/// `localizedCaseInsensitiveContains("")` is false, so filtering with
/// no query drops every row and a loaded board renders as "No pull
/// requests." That regression showed up as an empty focus page while
/// swe was returning a full listing.
@Suite @MainActor struct RepoSurfacesFilterTests {
    private let rows: Loadable<[String]> = .loaded(["alpha", "beta", "gamma"])

    private func parts(_ row: String) -> [String?] { [row] }

    /// The empty-query path is the whole point: filtering with no
    /// query must not touch the rows.
    @Test func emptyQueryKeepsEveryRow() {
        guard case .loaded(let got) = RepoSurfaces.filtered(
            rows, parts, query: "", unfiltered: false)
        else { Issue.record("expected a loaded state"); return }
        #expect(got == ["alpha", "beta", "gamma"])
    }

    /// A real query still narrows — the box is not decorative.
    @Test func queryNarrowsRows() {
        guard case .loaded(let got) = RepoSurfaces.filtered(
            rows, parts, query: "alp", unfiltered: false)
        else { Issue.record("expected a loaded state"); return }
        #expect(got == ["alpha"])
    }

    /// A surface exempted by name match keeps its whole board even
    /// under a query.
    @Test func unfilteredSkipsTheFilter() {
        guard case .loaded(let got) = RepoSurfaces.filtered(
            rows, parts, query: "zzz", unfiltered: true)
        else { Issue.record("expected a loaded state"); return }
        #expect(got.count == 3)
    }
}
