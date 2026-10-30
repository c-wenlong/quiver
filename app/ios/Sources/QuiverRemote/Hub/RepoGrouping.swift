import Foundation

/// One pinned repo's slot in a GitHub list reply (`/v1/pulls`,
/// `/v1/issues`): its rows, or the `gh` failure that left it empty.
protocol RepoListing {
    associatedtype Row
    var name: String { get }
    /// "ok", or `gh`'s outcome word.
    var status: String? { get }
    var error: String? { get }
    var rows: [Row] { get }
}

/// One section of a GitHub list page: a pinned repo's matching rows, or
/// the error that dimmed it.
struct RepoSection<Row>: Identifiable {
    let name: String
    let rows: [Row]?
    let error: String?
    var id: String { name }
}

/// The pulls and issues pages' grouping and counting, pure so tests can
/// feed it hub rows without an actor.
enum RepoGrouping {
    /// The repo sections as a page draws them under a filter: an errored
    /// repo keeps its slot with the error to show; a quiet one (no
    /// matching rows) drops out entirely.
    static func sections<R: RepoListing>(of repos: [R], where includes: (R.Row) -> Bool) -> [RepoSection<R.Row>] {
        repos.compactMap { repo in
            guard repo.status == "ok" else {
                return RepoSection(name: repo.name, rows: nil,
                                   error: repo.error ?? repo.status ?? "unknown error")
            }
            let rows = repo.rows.filter(includes)
            return rows.isEmpty ? nil : RepoSection(name: repo.name, rows: rows, error: nil)
        }
    }

    /// The filter's matching count across every repo: the filter chips.
    static func count<R: RepoListing>(_ repos: [R], where includes: (R.Row) -> Bool) -> Int {
        repos.reduce(0) { $0 + $1.rows.filter(includes).count }
    }
}
