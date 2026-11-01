import Network
import WebKit
import XCTest
@testable import SWERemote

final class PagePreviewPathTests: XCTestCase {
    func testAbsolutePathInsideCwdLosesThePrefix() {
        XCTAssertEqual(PagePreview.relative("/Users/k/proj/out/mock.html", to: "/Users/k/proj"), "out/mock.html")
        XCTAssertEqual(PagePreview.relative("/Users/k/proj/mock.html", to: "/Users/k/proj/"), "mock.html")
        XCTAssertEqual(PagePreview.relative("/Users/k//proj/./out/mock.html", to: "/Users/k/proj"), "out/mock.html")
    }

    func testOutsideCwdIsNotPreviewable() {
        XCTAssertNil(PagePreview.relative("/Users/k/other/mock.html", to: "/Users/k/proj"))
        // A sibling folder sharing the name's start is outside.
        XCTAssertNil(PagePreview.relative("/Users/k/project2/mock.html", to: "/Users/k/proj"))
        XCTAssertNil(PagePreview.relative("/Users/k/proj", to: "/Users/k/proj"))
        XCTAssertNil(PagePreview.relative("/Users/k/proj/../secret.html", to: "/Users/k/proj"))
    }

    func testAbsolutePathNeedsAnAbsoluteCwd() {
        XCTAssertNil(PagePreview.relative("/Users/k/proj/a.html", to: nil))
        XCTAssertNil(PagePreview.relative("/Users/k/proj/a.html", to: "proj"))
    }

    func testRelativePathsPassThroughTidied() {
        XCTAssertEqual(PagePreview.relative("./out/a.html", to: "/w"), "out/a.html")
        XCTAssertEqual(PagePreview.relative("a.svg", to: nil), "a.svg")
        XCTAssertNil(PagePreview.relative("../a.html", to: "/w"))
        XCTAssertNil(PagePreview.relative("out/../../a.html", to: "/w"))
        XCTAssertNil(PagePreview.relative("~/proj/a.html", to: "/Users/k"))
        XCTAssertNil(PagePreview.relative("", to: "/w"))
        XCTAssertNil(PagePreview.relative("a\0.html", to: "/w"))
    }

    func testPrivateTmpMatchesTmp() {
        XCTAssertEqual(PagePreview.relative("/private/tmp/w/a.html", to: "/tmp/w"), "a.html")
        XCTAssertEqual(PagePreview.relative("/var/folders/x/a.html", to: "/private/var/folders/x"), "a.html")
    }

    func testIsPage() {
        XCTAssertTrue(PagePreview.isPage("out/Mock.HTML"))
        XCTAssertTrue(PagePreview.isPage("a.htm"))
        XCTAssertTrue(PagePreview.isPage("/x/logo.svg"))
        XCTAssertFalse(PagePreview.isPage("a.html.bak"))
        XCTAssertFalse(PagePreview.isPage(".html"))
        XCTAssertFalse(PagePreview.isPage("html"))
        XCTAssertFalse(PagePreview.isPage("notes.md"))
    }
}

final class PagePreviewToolTests: XCTestCase {
    private func call(_ name: String, _ args: String) -> ToolCall {
        ToolCall(name: name, callId: "c", argsSummary: nil, argsFull: args)
    }

    func testClaudeWriteAndEditOfAPage() {
        let write = call("Write", #"{"file_path":"/w/out/mock.html","content":"<h1>hi</h1>"}"#)
        XCTAssertEqual(PagePreview.targets(of: write, cwd: "/w").map(\.path), ["out/mock.html"])
        let edit = call("Edit", #"{"file_path":"/w/logo.SVG","old_string":"a","new_string":"b"}"#)
        XCTAssertEqual(PagePreview.targets(of: edit, cwd: "/w").map(\.path), ["logo.SVG"])
        XCTAssertEqual(PagePreview.targets(of: edit, cwd: "/w").first?.name, "logo.SVG")
        let multi = call("MultiEdit", #"{"file_path":"/w/a.htm","edits":[]}"#)
        XCTAssertEqual(PagePreview.targets(of: multi, cwd: "/w").map(\.path), ["a.htm"])
    }

    func testOtherHarnessesWriteTools() {
        XCTAssertEqual(PagePreview.files(of: call("write", #"{"filePath":"/w/a.html","content":"x"}"#)), ["/w/a.html"])
        XCTAssertEqual(PagePreview.files(of: call("write_file", #"{"file_path":"/w/a.html","content":"x"}"#)), ["/w/a.html"])
        XCTAssertEqual(PagePreview.files(of: call("str_replace_based_edit_tool",
                                                  #"{"command":"create","path":"/w/a.html","file_text":"x"}"#)), ["/w/a.html"])
        XCTAssertEqual(PagePreview.files(of: call("str_replace_based_edit_tool",
                                                  #"{"command":"view","path":"/w/a.html"}"#)), [])
    }

    func testNonPagesAndNonWritesGetNoButton() {
        XCTAssertEqual(PagePreview.files(of: call("Write", #"{"file_path":"/w/notes.md","content":"see a.html"}"#)), [])
        XCTAssertEqual(PagePreview.files(of: call("Read", #"{"file_path":"/w/a.html"}"#)), [])
        XCTAssertEqual(PagePreview.files(of: call("Bash", #"{"command":"open a.html"}"#)), [])
        XCTAssertEqual(PagePreview.files(of: ToolCall(name: "Write", callId: nil, argsSummary: "Write · a.html", argsFull: nil)), [])
        XCTAssertEqual(PagePreview.files(of: call("Write", #"{"file_path":"/w/a.html""#)), [])
    }

    func testCodexPatchAddsAndUpdatesButNotDeletes() {
        let patch = """
        *** Begin Patch
        *** Add File: site/index.html
        +<h1>hi</h1>
        *** Update File: src/app.py
        @@
        -a
        +b
        *** Update File: site/old.htm
        *** Move to: site/new.htm
        *** Delete File: site/gone.html
        *** Add File: site/index.html
        *** End Patch
        """
        let encoded = String(decoding: try! JSONSerialization.data(withJSONObject: patch, options: .fragmentsAllowed), as: UTF8.self)
        let tool = call("apply_patch", encoded)
        // The moved file is previewed where it landed, and the repeated
        // add shows once.
        XCTAssertEqual(PagePreview.targets(of: tool, cwd: "/w").map(\.path), ["site/index.html", "site/new.htm"])
        let wrapped = String(decoding: try! JSONSerialization.data(withJSONObject: ["input": patch]), as: UTF8.self)
        XCTAssertEqual(PagePreview.targets(of: call("apply_patch", wrapped), cwd: "/w").count, 2)
    }

    func testOutsideTheFolderGetsNoButton() {
        let write = call("Write", #"{"file_path":"/tmp/elsewhere/a.html","content":"x"}"#)
        XCTAssertEqual(PagePreview.targets(of: write, cwd: "/w"), [])
    }
}

final class PagePreviewDrawTests: XCTestCase {
    func testKindFromTheHubsTypeThenTheName() {
        XCTAssertEqual(PagePreview.Page(path: "a.svg", body: Data(), contentType: "text/html; charset=utf-8").kind, .html)
        XCTAssertEqual(PagePreview.Page(path: "a.html", body: Data(), contentType: "image/svg+xml").kind, .svg)
        XCTAssertEqual(PagePreview.Page(path: "a.SVG", body: Data(), contentType: nil).kind, .svg)
        XCTAssertEqual(PagePreview.Page(path: "a.htm", body: Data(), contentType: nil).kind, .html)
    }

    func testSVGIsDrawnAsAnImage() {
        let svg = #"<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>"#
        let doc = PagePreview.Page(path: "a.svg", body: Data(svg.utf8), contentType: "image/svg+xml").document
        XCTAssertTrue(doc.contains("<img alt=\"\" src=\"data:image/svg+xml;base64,\(Data(svg.utf8).base64EncodedString())\">"))
        XCTAssertFalse(doc.contains("<script"))
    }

    func testHTMLIsItsOwnText() {
        let page = PagePreview.Page(path: "a.html", body: Data("<h1>é</h1>".utf8), contentType: "text/html")
        XCTAssertEqual(page.document, "<h1>é</h1>")
    }

    func testOnlyTheFirstBlankMainFrameLoadIsAllowed() {
        let blank = URL(string: "about:blank")
        XCTAssertTrue(PagePreview.allowsNavigation(firstLoadPending: true, isMainFrame: true, url: blank))
        XCTAssertFalse(PagePreview.allowsNavigation(firstLoadPending: false, isMainFrame: true, url: blank))
        XCTAssertFalse(PagePreview.allowsNavigation(firstLoadPending: true, isMainFrame: false, url: blank))
        XCTAssertFalse(PagePreview.allowsNavigation(firstLoadPending: true, isMainFrame: true,
                                                    url: URL(string: "https://example.com/")))
        XCTAssertFalse(PagePreview.allowsNavigation(firstLoadPending: true, isMainFrame: true, url: nil))
    }

    func testContentRulesBlockAllButBlankAndData() throws {
        let rules = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(PagePreview.contentRules.utf8)) as? [[String: Any]])
        XCTAssertEqual(rules.count, 3)
        XCTAssertEqual((rules[0]["action"] as? [String: String])?["type"], "block")
        XCTAssertEqual((rules[0]["trigger"] as? [String: String])?["url-filter"], ".*")
        XCTAssertEqual(rules.dropFirst().compactMap { ($0["trigger"] as? [String: String])?["url-filter"] }, ["^about:", "^data:"])
    }

    func testPreviewQueryKeepsAPlus() {
        let client = HubClient(baseURL: URL(string: "http://hub:8477")!, credential: nil)
        let url = client.url("/v1/sessions/k/preview", query: ["path": "out/a+b c.html"])
        XCTAssertEqual(url.query, "path=out/a%2Bb%20c.html")
    }
}

final class PagePreviewMessageTests: XCTestCase {
    private func message(_ status: Int, _ code: String, reason: String? = nil) -> String {
        PagePreview.message(for: HubError(status: status, code: code, message: "hub said", reason: reason))
    }

    func testRouteErrorsReadPlainly() {
        XCTAssertEqual(message(404, "not_found", reason: "no such file: a.html"), "That file isn't on disk any more.")
        XCTAssertEqual(message(404, "not_found", reason: "unknown route"), "Update the hub to preview pages from the phone.")
        XCTAssertEqual(message(413, "too_large"), "This file is too big to preview (the hub's limit is 2 MB).")
        XCTAssertEqual(message(415, "unsupported_type"), "Only .html, .htm and .svg files preview.")
        XCTAssertEqual(message(422, "bad_path"), "That path can't be previewed.")
        XCTAssertEqual(message(422, "preview_unavailable"), "Preview works only for sessions on the hub's own machine for now.")
        XCTAssertEqual(message(403, "outside_folder"), "That file is outside the session's folder.")
    }

    func testUnknownCodesFallBackOnStatus() {
        XCTAssertEqual(message(413, "http_413"), "This file is too big to preview.")
        XCTAssertEqual(message(422, "something_new"), "That path can't be previewed.")
        XCTAssertEqual(message(500, "boom"), "hub said")
        XCTAssertEqual(PagePreview.message(for: CancellationError()), "Couldn't load the preview.")
    }
}

/// The locked web view itself, in a real WKWebView.
@MainActor
final class LockedWebViewTests: XCTestCase {
    private func loaded(_ html: String) async throws -> (WKWebView, LockedWebView.Lock) {
        let rules = try await PreviewRules.compiled()
        let lock = LockedWebView.Lock()
        let view = LockedWebView.make(rules: rules, lock: lock)
        LockedWebView.draw(PagePreview.Page(path: "a.html", body: Data(html.utf8), contentType: "text/html"),
                           in: view, lock: lock)
        for _ in 0..<100 where view.isLoading || view.url == nil {
            try await Task.sleep(for: .milliseconds(50))
        }
        return (view, lock)
    }

    func testScriptsDontRunAndDataImagesDraw() async throws {
        let dot = Data(#"<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>"#.utf8).base64EncodedString()
        let (view, lock) = try await loaded("""
        <html><head><title>before</title></head><body>
        <img id="i" src="data:image/svg+xml;base64,\(dot)">
        <script>document.title = "ran"</script></body></html>
        """)
        XCTAssertEqual(view.url?.absoluteString, "about:blank")
        XCTAssertFalse(lock.firstLoadPending)
        let title = try await view.evaluateJavaScript("document.title") as? String
        XCTAssertEqual(title, "before")
        let width = try await view.evaluateJavaScript("document.getElementById('i').naturalWidth") as? Int
        XCTAssertEqual(width, 4)
    }

    func testNavigationAfterTheFirstLoadIsCancelled() async throws {
        let server = try await LoopbackServer.start()
        defer { server.stop() }
        let (view, lock) = try await loaded("<html><head><title>page</title></head><body>hi</body></html>")
        // The web view holds its delegate weakly: keep the lock alive to
        // the end, or the navigation below would meet no delegate at all.
        defer { withExtendedLifetime(lock) {} }
        _ = try? await view.evaluateJavaScript("location.href = '\(server.base)/next'; 1")
        try await Task.sleep(for: .milliseconds(500))
        XCTAssertEqual(view.url?.absoluteString, "about:blank")
        let title = try await view.evaluateJavaScript("document.title") as? String
        XCTAssertEqual(title, "page")
        XCTAssertEqual(server.connections, 0, "the cancelled navigation reached the server")
    }

    func testExternalLoadsNeverLeaveWhileDataImagesDraw() async throws {
        let dot = Data(#"<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>"#.utf8).base64EncodedString()
        func page(_ base: String) -> String {
            """
            <html><head><link rel="stylesheet" href="\(base)/s.css">
            <style>body{background:url(\(base)/bg.png)}</style></head><body>
            <img src="\(base)/a.png"><img id="i" src="data:image/svg+xml;base64,\(dot)"></body></html>
            """
        }

        // Control: the same page in an unlocked web view does reach the
        // server, so a zero below means the rules stopped the loads.
        let control = try await LoopbackServer.start()
        defer { control.stop() }
        let open = WKWebView()
        open.loadHTMLString(page(control.base), baseURL: nil)
        for _ in 0..<100 where control.connections == 0 {
            try await Task.sleep(for: .milliseconds(50))
        }
        XCTAssertGreaterThan(control.connections, 0, "the control page never reached the server")

        let server = try await LoopbackServer.start()
        defer { server.stop() }
        let (view, lock) = try await loaded(page(server.base))
        defer { withExtendedLifetime(lock) {} }
        try await Task.sleep(for: .seconds(1))
        XCTAssertEqual(server.connections, 0, "a locked preview reached the network")
        let width = try await view.evaluateJavaScript("document.getElementById('i').naturalWidth") as? Int
        XCTAssertEqual(width, 4)
    }
}

/// A TCP listener on 127.0.0.1 that counts the connections it gets.
@MainActor
private final class LoopbackServer {
    private let listener: NWListener
    private(set) var connections = 0
    private(set) var port: UInt16 = 0
    var base: String { "http://127.0.0.1:\(port)" }

    private init(_ listener: NWListener) { self.listener = listener }

    static func start() async throws -> LoopbackServer {
        let params = NWParameters.tcp
        params.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
        let server = LoopbackServer(try NWListener(using: params))
        server.listener.newConnectionHandler = { [weak server] conn in
            conn.cancel()
            Task { @MainActor in server?.connections += 1 }
        }
        try await withCheckedThrowingContinuation { (done: CheckedContinuation<Void, Error>) in
            var resumed = false
            server.listener.stateUpdateHandler = { state in
                guard !resumed else { return }
                switch state {
                case .ready: resumed = true; done.resume()
                case .failed(let error): resumed = true; done.resume(throwing: error)
                default: break
                }
            }
            server.listener.start(queue: .main)
        }
        server.port = server.listener.port?.rawValue ?? 0
        return server
    }

    func stop() { listener.cancel() }
}
