import Foundation
import Observation

/// Posting on an issue through the hub (`POST /v1/issues/comment`):
/// the issue page's comment box and the resolve sheet's "Also post as a
/// comment". The rules follow the Mac's `IssueResolve`, so the clients
/// agree on when a comment goes out.
enum IssueComments {
    /// The resolve context posts as a comment only when it holds text,
    /// the switch is on, and that text didn't already post (a Start
    /// retried after a failed launch must not comment twice).
    static func postsComment(context: String, ticked: Bool, posted: String?) -> Bool {
        let text = context.trimmingCharacters(in: .whitespacesAndNewlines)
        guard ticked, !text.isEmpty else { return false }
        return text != posted?.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Why the resolve context didn't post. The launch goes ahead, so
    /// the note says the agent still has it.
    static func contextWarning(_ failure: String) -> String {
        let reason = failure.trimmingCharacters(in: .whitespacesAndNewlines)
        let stop = reason.last.map { ".!?".contains($0) } ?? true
        return "Couldn't post your context to GitHub: \(reason)\(stop ? "" : ".") The agent still gets it in the prompt."
    }

    /// The caption under the comment box: whose `gh` posts it, and where.
    static func caption(login: String?, machine: String?) -> String {
        let who = login.flatMap { $0.isEmpty ? nil : $0 } ?? "the hub's gh account"
        let place = machine.flatMap { $0.isEmpty ? nil : $0 } ?? "the hub"
        return "Posts to GitHub as \(who), through gh on \(place). Markdown works."
    }
}

/// The issue page's comment box: the draft, whether it is posting, and
/// why the last post failed.
@MainActor @Observable
final class IssueCommentComposer {
    var draft = ""
    private(set) var posting = false
    private(set) var error: String?

    /// Comment is off while the box is blank or a post is out.
    var canPost: Bool {
        !posting && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Posts the draft through `send`; true once it went out. A refusal
    /// keeps the draft and says why; a draft edited while the post ran
    /// is kept too.
    func post(_ send: (String) async throws -> Void) async -> Bool {
        guard canPost else { return false }
        let text = draft
        posting = true
        error = nil
        defer { posting = false }
        do {
            try await send(text)
        } catch is CancellationError {
            return false
        } catch {
            self.error = (error as? HubError)?.message ?? error.localizedDescription
            return false
        }
        if draft == text { draft = "" }
        return true
    }
}
