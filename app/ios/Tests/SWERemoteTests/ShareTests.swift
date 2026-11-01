import ImageIO
import UIKit
import UniformTypeIdentifiers
import XCTest
@testable import SWERemote

private func row(_ key: String, ago: Double, ownership: String = "attached", status: String = "idle",
                 remote: Bool? = nil) -> SessionRow {
    var r = SessionRow(key: key, harness: "claude", agent: "Claude Code", cwd: "/w/\(key)", title: key,
                       lastActiveAt: 1_000_000 - ago, status: status, machineId: "m1", ownership: ownership,
                       attachable: true)
    r.remote = remote
    return r
}

private func pairedHub(_ host: String = "mac.tail.ts.net") -> PairedHub {
    PairedHub(baseURL: URL(string: "http://\(host):8477")!,
              credential: Credential(deviceId: "d", credential: "c-\(host)", name: nil))
}

private func png(width: Int, height: Int) -> Data {
    let format = UIGraphicsImageRendererFormat()
    format.scale = 1
    return UIGraphicsImageRenderer(size: CGSize(width: width, height: height), format: format).pngData { ctx in
        UIColor.systemTeal.setFill()
        ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
    }
}

final class ShareKindTests: XCTestCase {
    func testTheFirstMatchingKindWins() {
        XCTAssertEqual(SharedKind.of(["public.heic", "public.file-url"]), .image("public.heic"))
        XCTAssertEqual(SharedKind.of(["public.file-url"]), .fileURL)
        // Safari: the page's URL and its title as text.
        XCTAssertEqual(SharedKind.of(["public.plain-text", "public.url"]), .link)
        XCTAssertEqual(SharedKind.of(["public.utf8-plain-text"]), .text)
        XCTAssertEqual(SharedKind.of(["com.adobe.pdf"]), .file("com.adobe.pdf"))
        XCTAssertEqual(SharedKind.of(["public.folder"]), .unsupported)
        XCTAssertEqual(SharedKind.of([]), .unsupported)
    }

    func testTheMessageCarriesTextAndLinksOnce() {
        let link = URL(string: "https://example.com/a")!
        let payloads: [SharedPayload] = [.text("  https://example.com/a "), .link(link), .text("quote"),
                                         .file(name: "a.pdf", file: URL(fileURLWithPath: "/tmp/a.pdf"), size: 3)]
        XCTAssertEqual(ShareDraft.text(message: " look at this\n", payloads: payloads),
                       "look at this\n\nhttps://example.com/a\n\nquote")
        XCTAssertEqual(ShareDraft.text(message: "", payloads: [.link(link)]), "https://example.com/a")
        XCTAssertEqual(ShareDraft.text(message: "  ", payloads: []), "")
    }

    func testOnlyTheFirstTenFilesAreKept() {
        let files = (0..<12).map { SharedPayload.file(name: "f\($0).txt", file: URL(fileURLWithPath: "/tmp/f\($0)"), size: 1) }
        let (kept, note) = ShareDraft.capped([.text("hi")] + files + [.text("bye")])
        XCTAssertEqual(kept.filter(\.isUpload).count, AttachmentRules.maxPerMessage)
        XCTAssertEqual(kept.first, .text("hi"))
        XCTAssertEqual(kept.last, .text("bye"))
        XCTAssertEqual(kept.filter(\.isUpload).last?.uploadName, "f9.txt")
        XCTAssertEqual(note, "Only the first 10 files are sent: 2 left out.")
        XCTAssertNil(ShareDraft.capped(Array(files.prefix(3))).1)
    }

    func testAFileNameIsTheSuggestedOneWithTheFilesExtension() {
        let temp = URL(fileURLWithPath: "/tmp/PNG image.png")
        XCTAssertEqual(ShareInbox.fileName(suggested: "shot", provided: temp, type: "public.png"), "shot.png")
        XCTAssertEqual(ShareInbox.fileName(suggested: "notes.md", provided: temp, type: "public.png"), "notes.md")
        let bare = URL(fileURLWithPath: "/tmp/download")
        XCTAssertEqual(ShareInbox.fileName(suggested: "Q3 report", provided: bare, type: "com.adobe.pdf"), "Q3 report.pdf")
        XCTAssertEqual(ShareInbox.fileName(suggested: "a/b.md", provided: bare, type: "com.adobe.pdf"), "a-b.md")
        XCTAssertEqual(ShareInbox.fileName(suggested: nil, provided: temp, type: "public.png"), "PNG image.png")
    }

    func testURLItemsInEveryShape() {
        let url = URL(string: "https://example.com/a?b=1")!
        XCTAssertEqual(ShareInbox.asURL(url as NSURL), url)
        XCTAssertEqual(ShareInbox.asURL(" https://example.com/a?b=1\n" as NSString), url)
        XCTAssertEqual(ShareInbox.asURL(Data("https://example.com/a?b=1".utf8) as NSData), url)
        XCTAssertNil(ShareInbox.asURL("not a link" as NSString))
        XCTAssertNil(ShareInbox.asURL(nil))
    }

    func testSessionsAreAttachedOnesMostRecentFirst() {
        let rows = [row("old", ago: 500), row("observed", ago: 1, ownership: "observed"), row("new", ago: 10)]
        XCTAssertEqual(ShareTargets.sessions(rows).map(\.key), ["new", "old"])
        XCTAssertEqual(ShareTargets.mode(for: row("a", ago: 0, status: "active")), "queue")
        XCTAssertEqual(ShareTargets.mode(for: row("a", ago: 0, status: "active", remote: true)), "steer")
        XCTAssertEqual(ShareTargets.mode(for: row("a", ago: 0, remote: true)), "queue")
    }

    func testAnImageFileIsDownscaledFromDisk() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("big.png")
        try png(width: 4000, height: 1000).write(to: file)
        let jpeg = try XCTUnwrap(PhotoPrep.jpeg(at: file))
        let source = try XCTUnwrap(CGImageSourceCreateWithData(jpeg as CFData, nil))
        let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
        XCTAssertEqual(props?[kCGImagePropertyPixelWidth] as? Int, AttachmentRules.photoMaxEdge)
        XCTAssertEqual(props?[kCGImagePropertyPixelHeight] as? Int, 512)
        XCTAssertNil(PhotoPrep.jpeg(at: dir.appendingPathComponent("missing.png")))

        let image = try XCTUnwrap(UIImage(data: png(width: 3000, height: 3000)))
        let small = try XCTUnwrap(UIImage(data: try XCTUnwrap(PhotoPrep.jpeg(from: image))))
        XCTAssertEqual(small.size.width * small.scale, CGFloat(AttachmentRules.photoMaxEdge))
    }
}

final class ShareInboxTests: XCTestCase {
    private var dir: URL!

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("share-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: dir)
    }

    private func file(_ name: String, _ data: Data) throws -> URL {
        let src = dir.appendingPathComponent("src", isDirectory: true)
        try FileManager.default.createDirectory(at: src, withIntermediateDirectories: true)
        let url = src.appendingPathComponent(name)
        try data.write(to: url)
        return url
    }

    func testTextLinksAndFilesAreRead() async throws {
        let out = dir.appendingPathComponent("out")
        let pdf = try XCTUnwrap(NSItemProvider(contentsOf: try file("spec.pdf", Data("%PDF-1.4".utf8))))
        let photo = try XCTUnwrap(NSItemProvider(contentsOf: try file("IMG_1.png", png(width: 8, height: 8))))
        // As Photos names one; the file it hands over can be "PNG image.png".
        photo.suggestedName = "shot"
        let providers = [
            NSItemProvider(object: "a quote" as NSString),
            NSItemProvider(object: URL(string: "https://example.com")! as NSURL),
            pdf,
            photo,
        ]
        let read = await ShareInbox.load(providers, into: out)
        XCTAssertEqual(read.notices, [])
        guard read.payloads.count == 4 else { return XCTFail("\(read.payloads)") }
        XCTAssertEqual(read.payloads[0], .text("a quote"))
        XCTAssertEqual(read.payloads[1], .link(URL(string: "https://example.com")!))
        guard case .file(let name, let copy, let size) = read.payloads[2] else { return XCTFail("\(read.payloads[2])") }
        XCTAssertEqual(name, "spec.pdf")
        XCTAssertEqual(size, 8)
        // Copied into the share's own folder, which outlives the provider.
        XCTAssertTrue(copy.path.hasPrefix(out.path))
        XCTAssertEqual(try Data(contentsOf: copy), Data("%PDF-1.4".utf8))
        guard case .image(let imageName, let imageFile) = read.payloads[3] else { return XCTFail("\(read.payloads[3])") }
        XCTAssertEqual(imageName, "shot.jpg")
        XCTAssertTrue(imageFile.path.hasPrefix(out.path))
    }

    func testFilesTheHubWouldRefuseAreRefusedUpFront() throws {
        let zip = try file("bundle.zip", Data("PK".utf8))
        XCTAssertEqual(ShareInbox.checked(zip), .failure(.init(message: "bundle.zip can't be attached: images, PDFs and text files only.")))
        let big = try file("huge.pdf", Data())
        let handle = try FileHandle(forWritingTo: big)
        try handle.truncate(atOffset: UInt64(AttachmentRules.maxBytes + 1))
        try handle.close()
        XCTAssertEqual(ShareInbox.checked(big), .failure(.init(message: "huge.pdf is too big (21 MB): files can be up to 20 MB.")))
        // Turned down from the source, before anything is copied.
        XCTAssertEqual(ShareInbox.precheck(big, name: "huge.pdf")?.message,
                       "huge.pdf is too big (21 MB): files can be up to 20 MB.")
        XCTAssertNotNil(ShareInbox.precheck(zip, name: "bundle.zip"))
        XCTAssertNil(ShareInbox.precheck(big, name: "huge.heic"))
        // A big photo is fine: it goes up as a downscaled JPEG.
        let heic = try file("IMG_1.HEIC", Data([1]))
        XCTAssertEqual(ShareInbox.checked(heic), .success(.image(name: "IMG_1.jpg", file: heic)))
    }
}

/// Records every call; uploads finish one at a time unless told.
private final class FakeShareHub: ShareHub, @unchecked Sendable {
    private let lock = NSLock()
    var rows: [SessionRow] = []
    var sessionsError: Error?
    var uploadError: Error?
    var sendError: Error?
    private(set) var calls: [String] = []
    private(set) var sent: [(key: String, text: String, mode: String, refs: [String])] = []
    private(set) var clientIds: [String] = []
    private var inFlight = 0
    private(set) var maxInFlight = 0

    private func record(_ s: String) { lock.lock(); calls.append(s); lock.unlock() }

    func sessions() async throws -> SessionsResponse {
        record("sessions")
        if let sessionsError { throw sessionsError }
        return SessionsResponse(sessions: rows, machines: nil, serverTime: nil)
    }

    private func stage(_ key: String, _ name: String, _ kind: String) async throws -> StagedAttachment {
        lock.lock(); inFlight += 1; maxInFlight = max(maxInFlight, inFlight); calls.append("\(kind) \(key) \(name)"); lock.unlock()
        try await Task.sleep(for: .milliseconds(5))
        lock.lock(); inFlight -= 1; lock.unlock()
        if let uploadError { throw uploadError }
        return StagedAttachment(ref: "ref-\(name)")
    }

    func uploadAttachment(_ key: String, name: String, data: Data,
                          progress: @escaping @Sendable (Double) -> Void) async throws -> StagedAttachment {
        try await stage(key, name, "data")
    }

    func uploadAttachment(_ key: String, name: String, file: URL,
                          progress: @escaping @Sendable (Double) -> Void) async throws -> StagedAttachment {
        try await stage(key, name, "file")
    }

    func sendMessage(_ key: String, text: String, mode: String, clientId: String,
                     attachments: [String]) async throws -> SendAccepted {
        record("send \(key)")
        clientIds.append(clientId)
        if let sendError { throw sendError }
        sent.append((key, text, mode, attachments))
        return SendAccepted(ok: true, waiting: false)
    }
}

@MainActor
final class ShareModelTests: XCTestCase {
    private func model(_ hub: FakeShareHub, hubs: [PairedHub] = [pairedHub()]) -> ShareModel {
        var list = HubList()
        for h in hubs { list.adopt(h) }
        list.activeId = hubs.first?.id
        let m = ShareModel(store: MemoryHubStore(list)) { _ in hub }
        m.convert = { _ in Data("jpeg".utf8) }
        return m
    }

    func testNotPairedRefusesWithoutCallingAHub() async {
        let hub = FakeShareHub()
        let m = model(hub, hubs: [])
        await m.start(payloads: [.text("hi")], notices: [])
        XCTAssertEqual(m.phase, .notPaired(ShareModel.notPairedNote))
        XCTAssertFalse(m.canSend)
        XCTAssertEqual(hub.calls, [])
    }

    func testAnUnreachableHubSaysSoAndRetries() async {
        let hub = FakeShareHub()
        hub.sessionsError = HubError.network(URLError(.timedOut))
        let m = model(hub)
        await m.start(payloads: [.text("hi")], notices: [])
        XCTAssertEqual(m.phase, .unreachable("Check that Tailscale is on, then try again."))
        XCTAssertFalse(m.canSend)
        hub.sessionsError = nil
        hub.rows = [row("a", ago: 1)]
        await m.refresh()
        XCTAssertEqual(m.phase, .ready)
        XCTAssertTrue(m.canSend)
    }

    func testARevokedPhoneIsNotPaired() async {
        let hub = FakeShareHub()
        hub.sessionsError = HubError(status: 401, code: "unauthorized", message: "This device isn't paired any more. Pair it again.")
        let m = model(hub)
        await m.start(payloads: [], notices: [])
        XCTAssertEqual(m.phase, .notPaired("This device isn't paired any more. Pair it again."))
    }

    func testTheMostRecentSessionIsPickedAndNoSessionsBlocksSend() async {
        let hub = FakeShareHub()
        hub.rows = [row("old", ago: 50), row("new", ago: 5), row("ro", ago: 0, ownership: "observed")]
        let m = model(hub)
        await m.start(payloads: [.text("hi")], notices: ["x.zip can't be attached"])
        XCTAssertEqual(m.sessions.map(\.key), ["new", "old"])
        XCTAssertEqual(m.selectedKey, "new")
        XCTAssertEqual(m.notices, ["x.zip can't be attached"])

        let empty = FakeShareHub()
        let e = model(empty)
        await e.start(payloads: [.text("hi")], notices: [])
        XCTAssertEqual(e.blocker, ShareModel.noSessionsNote)
        XCTAssertFalse(e.canSend)
    }

    func testFilesNeedAMessage() async {
        let hub = FakeShareHub()
        hub.rows = [row("a", ago: 1)]
        let m = model(hub)
        await m.start(payloads: [.imageData(name: "image.jpg", data: Data([1]))], notices: [])
        XCTAssertEqual(m.blocker, AttachmentRules.needsText)
        XCTAssertFalse(m.canSend)
        m.message = "what's this?"
        XCTAssertTrue(m.canSend)
    }

    func testFilesUploadOneAtATimeThenTheMessageGoesWithTheirRefs() async throws {
        let hub = FakeShareHub()
        hub.rows = [row("a", ago: 1)]
        let m = model(hub)
        let tmp = URL(fileURLWithPath: "/tmp/x")
        await m.start(payloads: [
            .link(URL(string: "https://example.com")!),
            .image(name: "shot.jpg", file: tmp),
            .file(name: "spec.pdf", file: tmp, size: 10),
            .imageData(name: "image.jpg", data: Data([1])),
        ], notices: [])
        m.message = "look"
        let ok = await m.send()
        XCTAssertTrue(ok)
        XCTAssertEqual(m.phase, .sent)
        XCTAssertEqual(hub.maxInFlight, 1)
        XCTAssertEqual(hub.calls, ["sessions", "data a shot.jpg", "file a spec.pdf", "data a image.jpg", "send a"])
        let sent = try XCTUnwrap(hub.sent.first)
        XCTAssertEqual(sent.text, "look\n\nhttps://example.com")
        XCTAssertEqual(sent.mode, "queue")
        XCTAssertEqual(sent.refs, ["ref-shot.jpg", "ref-spec.pdf", "ref-image.jpg"])
        XCTAssertEqual(m.sentNote, "The hub is passing it to the agent.")
    }

    func testAnUnansweredSendKeepsTheSheetAndRetryReusesFilesAndItsId() async {
        let hub = FakeShareHub()
        hub.rows = [row("a", ago: 1)]
        hub.sendError = HubError.network(URLError(.notConnectedToInternet))
        let m = model(hub)
        await m.start(payloads: [.file(name: "spec.pdf", file: URL(fileURLWithPath: "/tmp/x"), size: 1)], notices: [])
        m.message = "read this"
        let first = await m.send()
        XCTAssertFalse(first)
        XCTAssertEqual(m.phase, .ready)
        XCTAssertEqual(m.sendError, ShareModel.unconfirmedNote)
        // The first POST reached the hub after all, which still holds it.
        hub.sendError = HubError(status: 409, code: "duplicate_client_id", message: "That message is already queued.")
        let second = await m.send()
        XCTAssertTrue(second)
        XCTAssertEqual(m.phase, .sent)
        XCTAssertEqual(hub.calls.filter { $0.hasPrefix("file") }.count, 1)
        XCTAssertEqual(hub.clientIds.count, 2)
        XCTAssertEqual(Set(hub.clientIds).count, 1)
        XCTAssertEqual(m.sentNote, "The hub holds it until the agent finishes its turn.")
    }

    func testARefusedSendOrAChangedMessageGetsANewId() async {
        let hub = FakeShareHub()
        hub.rows = [row("a", ago: 1)]
        hub.sendError = HubError(status: 429, code: "backlog_full", message: "Too many messages are waiting. Try again shortly.")
        let m = model(hub)
        await m.start(payloads: [.text("hi")], notices: [])
        _ = await m.send()
        XCTAssertEqual(m.sendError, "Too many messages are waiting. Try again shortly.")
        hub.sendError = HubError.network(URLError(.timedOut))
        _ = await m.send()
        m.message = "edited"
        hub.sendError = nil
        _ = await m.send()
        XCTAssertEqual(hub.clientIds.count, 3)
        XCTAssertEqual(Set(hub.clientIds).count, 3)
        XCTAssertEqual(hub.sent.first?.text, "edited\n\nhi")
    }

    func testStagedFilesDontFollowASwitchToAnotherHub() async {
        let first = pairedHub("mac.tail.ts.net"), second = pairedHub("server.tail.ts.net")
        let hubA = FakeShareHub(), hubB = FakeShareHub()
        hubA.rows = [row("same", ago: 1)]
        hubB.rows = [row("same", ago: 1)]
        hubA.sendError = HubError.network(URLError(.timedOut))
        var list = HubList()
        list.adopt(first)
        list.adopt(second)
        list.activeId = first.id
        let m = ShareModel(store: MemoryHubStore(list)) { $0.id == first.id ? hubA : hubB }
        await m.start(payloads: [.file(name: "spec.pdf", file: URL(fileURLWithPath: "/tmp/x"), size: 1)], notices: [])
        m.message = "look"
        _ = await m.send()
        await m.selectHub(second.id)
        let ok = await m.send()
        XCTAssertTrue(ok)
        XCTAssertEqual(hubB.calls.filter { $0.hasPrefix("file") }.count, 1)
        XCTAssertEqual(hubB.sent.first?.refs, ["ref-spec.pdf"])
    }

    func testAnImageThatWontDecodeFailsTheSendWithItsName() async {
        let hub = FakeShareHub()
        hub.rows = [row("a", ago: 1)]
        let m = model(hub)
        m.convert = { _ in nil }
        await m.start(payloads: [.image(name: "shot.jpg", file: URL(fileURLWithPath: "/tmp/x"))], notices: [])
        m.message = "hi"
        let ok = await m.send()
        XCTAssertFalse(ok)
        XCTAssertEqual(m.sendError, "shot.jpg couldn't be read as an image.")
        XCTAssertEqual(hub.sent.count, 0)
    }

    func testSwitchingHubsListsThatHubsSessions() async {
        let first = pairedHub("mac.tail.ts.net"), second = pairedHub("server.tail.ts.net")
        let hubA = FakeShareHub(), hubB = FakeShareHub()
        hubA.rows = [row("a", ago: 1)]
        hubB.rows = [row("b", ago: 1)]
        var list = HubList()
        list.adopt(first)
        list.adopt(second)
        list.activeId = first.id
        let m = ShareModel(store: MemoryHubStore(list)) { $0.id == first.id ? hubA : hubB }
        await m.start(payloads: [.text("hi")], notices: [])
        XCTAssertEqual(m.selectedKey, "a")
        await m.selectHub(second.id)
        XCTAssertEqual(m.sessions.map(\.key), ["b"])
        XCTAssertEqual(m.selectedKey, "b")
    }
}
