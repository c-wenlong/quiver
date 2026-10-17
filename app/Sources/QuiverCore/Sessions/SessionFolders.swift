import Foundation

/// Which of a session's working folders names its project. A session can
/// log events from more than one folder: a Claude desktop side session
/// works in its own worktree and writes its report into the parent's
/// notes folder (`~/Library/Application Support/Claude/side-session-notes
/// /local_<parent id>`), a few events at most. The folder with the most
/// events wins, and a notes folder only when there is nothing else.
public enum SessionFolders {
    /// The Claude desktop app's per-session notes folders.
    public static func isAgentNotes(_ cwd: String) -> Bool {
        cwd.contains("/Library/Application Support/Claude/side-session-notes/")
    }

    /// The folder to show for a session, from its events per folder.
    /// Ties go to the folder seen later in `votes` order.
    public static func pick(_ votes: [(cwd: String, events: Int)]) -> String? {
        var best: (cwd: String, events: Int, notes: Bool)?
        for vote in votes where !vote.cwd.isEmpty {
            let notes = isAgentNotes(vote.cwd)
            guard let current = best else { best = (vote.cwd, vote.events, notes); continue }
            // A real folder always beats a notes folder; otherwise more
            // events win.
            if current.notes != notes {
                if current.notes { best = (vote.cwd, vote.events, notes) }
            } else if vote.events >= current.events {
                best = (vote.cwd, vote.events, notes)
            }
        }
        return best?.cwd
    }
}
