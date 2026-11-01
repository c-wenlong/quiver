import Foundation

/// Where the full-text session-search index lives.
///
/// The index is a cache over the transcripts, never a source: every row can
/// be rebuilt by reading the transcript files again, so a damaged or stale
/// database is deleted and recreated rather than repaired. It lives in
/// `~/.swe/data/search/` beside the ledger — `data/`, not `cache/`,
/// because a rebuild costs a full re-read of every transcript.
public enum SearchLocation {
    public static let fileName = "search.sqlite"

    /// The directory the index lives in, under the `~/.swe` root.
    public static func directory(under swe: URL) -> URL {
        swe.appending(path: "data/search")
    }

    /// The database file and the journal files SQLite keeps beside it.
    static func removeDatabase(at url: URL) {
        LedgerLocation.removeDatabase(at: url)
    }
}
