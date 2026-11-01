import XCTest
@testable import SWERemote

/// The issue page's comment box and the resolve sheet's "Also post as a
/// comment", over `POST /v1/issues/comment`.
final class IssueCommentsTests: XCTestCase {
    private let client = HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!, credential: "c")

    private func json(_ req: URLRequest) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: req.httpBody ?? Data()) as? [String: Any])
    }

    func testTheCommentPostsTheHubsBody() throws {
        let req = client.commentRequest(repo: "o/r", number: 5, body: "Looks fixed on main.\n\n`--repo x`")
        XCTAssertEqual(req.httpMethod, "POST")
        XCTAssertEqual(req.url?.path, "/v1/issues/comment")
        let body = try json(req)
        XCTAssertEqual(body["repo"] as? String, "o/r")
        XCTAssertEqual(body["number"] as? Int, 5)
        XCTAssertEqual(body["body"] as? String, "Looks fixed on main.\n\n`--repo x`")
    }

    func testResolveSendsTheContextOnlyWhenThereIsSome() throws {
        let with = try json(client.resolveIssueRequest(repo: "o/r", number: 5, harness: "claude",
                                                       prompt: "", context: "Use the new API.", model: "opus"))
        XCTAssertEqual(with["context"] as? String, "Use the new API.")
        XCTAssertEqual(with["model"] as? String, "opus")
        XCTAssertNil(with["prompt"], "an empty prompt lets the hub write it")
        let without = try json(client.resolveIssueRequest(repo: "o/r", number: 5, harness: "claude",
                                                          prompt: "Fix it"))
        XCTAssertNil(without["context"])
        XCTAssertEqual(without["prompt"] as? String, "Fix it")
    }

    func testTheReplyDecodes() throws {
        let reply = try Hub.decoder.decode(IssueCommentReply.self, from: Data("""
        {"ok": true, "github": {"status": "ok", "login": "octocat"}, "repo": "o/r",
         "number": 5, "machine": "hub", "machine_id": "m-1",
         "comment": {"author": "octocat", "body": "Done.", "created_at": "2026-10-31T00:00:00Z",
                     "url": "https://github.com/o/r/issues/5#issuecomment-9"}}
        """.utf8))
        XCTAssertEqual(reply.ok, true)
        XCTAssertEqual(reply.comment?.url, "https://github.com/o/r/issues/5#issuecomment-9")
    }

    func testTheContextPostsOnceWhenTickedAndNotBlank() {
        XCTAssertTrue(IssueComments.postsComment(context: " Use X. ", ticked: true, posted: nil))
        XCTAssertFalse(IssueComments.postsComment(context: " Use X. ", ticked: false, posted: nil))
        XCTAssertFalse(IssueComments.postsComment(context: " \n ", ticked: true, posted: nil))
        // A Start retried after a failed launch doesn't comment twice…
        XCTAssertFalse(IssueComments.postsComment(context: "Use X.", ticked: true, posted: "Use X."))
        // …but edited context is new text.
        XCTAssertTrue(IssueComments.postsComment(context: "Use Y.", ticked: true, posted: "Use X."))
    }

    func testTheWordsMatchTheOtherClients() {
        XCTAssertEqual(IssueComments.contextWarning("gh: API rate limit exceeded"),
                       "Couldn't post your context to GitHub: gh: API rate limit exceeded. The agent still gets it in the prompt.")
        XCTAssertEqual(IssueComments.contextWarning("The comment is empty."),
                       "Couldn't post your context to GitHub: The comment is empty. The agent still gets it in the prompt.")
        XCTAssertEqual(IssueComments.caption(login: "octocat", machine: "kai-mac"),
                       "Posts to GitHub as octocat, through gh on kai-mac. Markdown works.")
        XCTAssertEqual(IssueComments.caption(login: "", machine: nil),
                       "Posts to GitHub as the hub's gh account, through gh on the hub. Markdown works.")
    }

    @MainActor
    func testTheComposerClearsOnlyAfterAPost() async {
        let composer = IssueCommentComposer()
        XCTAssertFalse(composer.canPost, "Comment is off while the box is blank")
        composer.draft = "   "
        XCTAssertFalse(composer.canPost)

        composer.draft = "Looks fixed"
        var sent: [String] = []
        let refused = await composer.post { text in
            sent.append(text)
            throw HubError(status: 502, code: "comment_failed", message: "gh: API rate limit exceeded")
        }
        XCTAssertFalse(refused)
        XCTAssertEqual(composer.error, "gh: API rate limit exceeded")
        XCTAssertEqual(composer.draft, "Looks fixed", "a refusal keeps the draft")
        XCTAssertFalse(composer.posting)

        let posted = await composer.post { sent.append($0) }
        XCTAssertTrue(posted)
        XCTAssertEqual(sent, ["Looks fixed", "Looks fixed"])
        XCTAssertEqual(composer.draft, "")
        XCTAssertNil(composer.error)

        // A blank box posts nothing.
        let blank = await composer.post { sent.append($0) }
        XCTAssertFalse(blank)
        XCTAssertEqual(sent.count, 2)
    }
}
