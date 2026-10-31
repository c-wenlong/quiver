import Foundation
import Testing
@testable import QuiverCore

@MainActor
@Suite struct RemoteModelTests {
    /// `status` exits 1 with a JSON body when the hub is down — a report,
    /// not a failure.
    let hubDown = Data(#"""
    {"schema": 1, "data": {"hub": {"ok": false}, "hub_addr": "100.1.2.3:8477",
     "machines": [], "devices": []}}
    """#.utf8)

    let hubUp = Data(#"""
    {"schema": 1, "data": {"hub_addr": "100.1.2.3:8477",
      "hub": {"ok": true, "version": "1.2.3", "uptime_s": 3725,
              "machine": {"id": "mac-1", "name": "MacBook", "os": "macOS"}},
      "machines": [{"id": "mac-1", "name": "MacBook", "online": true,
                    "last_seen_at": 1760000000}],
      "devices": [{"id": "dev-1", "name": "Phone", "created_at": 1759999000,
                   "last_seen_at": 1760000000, "revoked": false}]}}
    """#.utf8)

    @Test func aDownHubStillLoads() async {
        let model = RemoteModel(swe: SweClient(runner: FakeRunner(.init(status: 1, stdout: hubDown, stderr: ""))))
        await model.load()
        guard case .loaded(let status) = model.status else {
            Issue.record("expected loaded, got \(model.status)")
            return
        }
        #expect(status.hub.ok == false)
        #expect(status.hubAddr == "100.1.2.3:8477")
        #expect(status.devices.isEmpty)
    }

    @Test func statusDecodesMachinesAndDevices() async throws {
        let model = RemoteModel(swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: hubUp, stderr: ""))))
        await model.load()
        guard case .loaded(let status) = model.status else {
            Issue.record("expected loaded, got \(model.status)")
            return
        }
        #expect(status.hub.machine?.name == "MacBook")
        #expect(status.hub.uptimeS == 3725)
        #expect(status.machines.first?.id == "mac-1")
        #expect(status.machines.first?.lastSeenAt == Date(timeIntervalSince1970: 1760000000))
        let device = try #require(status.devices.first)
        #expect(device.id == "dev-1")
        #expect(device.revoked == false)
        // Hubs older than the metadata change emit none of these.
        #expect(device.kind == nil)
        #expect(device.displayName == nil)
    }

    /// The paired-device fields the hub minted after #783: kind icon,
    /// model, client/OS and the tailnet node, plus the user's rename.
    @Test func statusDecodesDeviceMetadata() async throws {
        let body = Data(#"""
        {"schema": 1, "data": {"hub": {"ok": true}, "hub_addr": null,
         "machines": [],
         "devices": [{"id": "dev-1", "name": "phone",
                      "display_name": "Dev's iPhone",
                      "kind": "iphone", "model": "iPhone16,2",
                      "client": "Quiver Remote 1.0", "os": "iOS 18.5",
                      "node_name": "test-iphone.tail0000.ts.net",
                      "paired_via": "tailscale",
                      "created_at": 1759999000, "last_seen_at": 1760000000,
                      "revoked": false}]}}
        """#.utf8)
        let model = RemoteModel(swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: body, stderr: ""))))
        await model.load()
        guard case .loaded(let status) = model.status else {
            Issue.record("expected loaded, got \(model.status)")
            return
        }
        let device = try #require(status.devices.first)
        #expect(device.displayName == "Dev's iPhone")
        #expect(device.kind == "iphone")
        #expect(device.model == "iPhone16,2")
        #expect(device.client == "Quiver Remote 1.0")
        #expect(device.os == "iOS 18.5")
        #expect(device.nodeName == "test-iphone.tail0000.ts.net")
    }

    @Test func statusAsksForJSON() async {
        let runner = FakeRunner(.init(status: 0, stdout: hubUp, stderr: ""))
        await RemoteModel(swe: SweClient(runner: runner)).load()
        #expect(runner.calls == [["remote", "status", "--json"]])
    }

    @Test func mintingATicketDecodesBothLinks() async throws {
        let pair = Data(#"""
        {"schema": 1, "data": {"host": "100.1.2.3", "port": 8477, "token": "tok",
         "name": "MacBook", "url": "http://100.1.2.3:8477/#pair=tok",
         "deep_link": "quiver://pair?host=100.1.2.3&port=8477&token=tok&name=MacBook",
         "expires_in_s": 300}}
        """#.utf8)
        let runner = FakeRunner(.init(status: 0, stdout: pair, stderr: ""))
        let model = RemoteModel(swe: SweClient(runner: runner))
        await model.mintTicket()
        let ticket = try #require(model.ticket)
        #expect(runner.calls == [["remote", "pair", "--json"]])
        #expect(ticket.url == "http://100.1.2.3:8477/#pair=tok")
        #expect(ticket.deepLink.hasPrefix("quiver://pair?"))
        #expect(ticket.expiresInS == 300)
    }

    /// A slower mint can't land over a newer one, and a mint answering
    /// after the sheet closed shows nothing.
    @Test func onlyTheNewestMintLands() async throws {
        func pair(_ token: String) -> CommandResult {
            .init(status: 0, stdout: Data(#"""
            {"schema": 1, "data": {"host": "h", "port": 1, "token": "\#(token)", "name": null,
             "url": "http://h:1/#pair=\#(token)", "deep_link": "quiver://pair?token=\#(token)",
             "expires_in_s": 300}}
            """#.utf8), stderr: "")
        }
        // Each reply waits at its own gate, so the test picks the order.
        let runner = GatedReplies([pair("old"), pair("new")])
        let model = RemoteModel(swe: SweClient(runner: runner))
        let first = Task { await model.mintTicket() }
        #expect(await runner.gates[0].arrived())
        model.dismissTicket()
        let second = Task { await model.mintTicket() }
        #expect(await runner.gates[1].arrived())
        // The newer mint answers first; the older one lands after it.
        runner.gates[1].open()
        await second.value
        runner.gates[0].open()
        await first.value
        #expect(model.ticket?.token == "new")
        #expect(!model.pairing)

        let late = GatedReplies([pair("late")])
        let closed = RemoteModel(swe: SweClient(runner: late))
        let mint = Task { await closed.mintTicket() }
        #expect(await late.gates[0].arrived())
        closed.dismissTicket()
        late.gates[0].open()
        await mint.value
        #expect(closed.ticket == nil)

        // An older mint failing while the newer one runs leaves the
        // spinner on and no error up.
        let failing = GatedReplies([.init(status: 1, stdout: Data(), stderr: "boom"), pair("fresh")])
        let racing = RemoteModel(swe: SweClient(runner: failing))
        let older = Task { await racing.mintTicket() }
        #expect(await failing.gates[0].arrived())
        let newer = Task { await racing.mintTicket() }
        #expect(await failing.gates[1].arrived())
        failing.gates[0].open()
        await older.value
        #expect(racing.pairing)
        #expect(racing.pairError == nil)
        failing.gates[1].open()
        await newer.value
        #expect(!racing.pairing)
        #expect(racing.ticket?.token == "fresh")
    }

    /// A load cancelled with its page puts the previous status back
    /// instead of spinning for good.
    @Test func aCancelledLoadDoesNotSpinForever() async throws {
        // One reply: the first load lands, the second call hangs until
        // that load is cancelled.
        let runner = GatedReplies([.init(status: 0, stdout: hubUp, stderr: "")])
        runner.gates[0].open()
        let model = RemoteModel(swe: SweClient(runner: runner))
        await model.load()
        let loaded = model.status
        guard case .loaded = loaded else {
            Issue.record("expected loaded, got \(loaded)")
            return
        }
        let task = Task { await model.load() }
        #expect(await runner.hanging())
        task.cancel()
        await task.value
        #expect(model.status == loaded)
    }

    /// Cancelled before anything loaded, there is nothing to put back.
    @Test func aCancelledFirstLoadGoesBackToIdle() async throws {
        let runner = GatedReplies([])
        let model = RemoteModel(swe: SweClient(runner: runner))
        let task = Task { await model.load() }
        #expect(await runner.hanging())
        task.cancel()
        await task.value
        #expect(model.status == .idle)
    }

    @Test func theCountdownCountsDownFromMinting() async throws {
        let pair = Data(#"""
        {"schema": 1, "data": {"host": "h", "port": 1, "token": "t", "name": null,
         "url": "u", "deep_link": "d", "expires_in_s": 300}}
        """#.utf8)
        let model = RemoteModel(swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: pair, stderr: ""))))
        await model.mintTicket()
        let minted = try #require(model.ticketMintedAt)
        #expect(model.ticketSecondsLeft(at: minted) == 300)
        #expect(model.ticketSecondsLeft(at: minted.addingTimeInterval(299)) == 1)
        #expect(model.ticketSecondsLeft(at: minted.addingTimeInterval(301)) == 0)
        model.dismissTicket()
        #expect(model.ticketSecondsLeft(at: minted) == nil)
        #expect(model.ticket == nil)
    }

    @Test func aPairFailureShowsOnTheSheet() async {
        let model = RemoteModel(swe: SweClient(runner: FakeRunner(
            .init(status: 1, stdout: Data(), stderr: "hub unreachable"))))
        await model.mintTicket()
        #expect(model.ticket == nil)
        #expect(model.pairError?.contains("hub unreachable") == true)
    }

    @Test func revokingRunsTheRevokeCommandThenReloads() async {
        let runner = QueueRunner([
            .init(status: 0, stdout: Data(
                #"{"schema":1,"data":{"revoked":"dev-1","ok":true}}"#.utf8), stderr: ""),
            .init(status: 0, stdout: hubUp, stderr: ""),
        ])
        let model = RemoteModel(swe: SweClient(runner: runner))
        await model.revoke(RemoteStatus.Device(id: "dev-1"))
        #expect(runner.calls == [["remote", "devices", "--revoke=dev-1", "--json"],
                                 ["remote", "status", "--json"]])
        #expect(model.revoking.isEmpty)
        #expect(model.revokeErrors["dev-1"] == nil)
        guard case .loaded = model.status else {
            Issue.record("expected loaded, got \(model.status)")
            return
        }
    }

    /// swe answering ok:false means the hub had no such device: say so,
    /// and skip the reload.
    @Test func aRevokeSweDidNotFindSaysSo() async {
        let runner = QueueRunner([.init(status: 0, stdout: Data(
            #"{"schema":1,"data":{"revoked":"dev-1","ok":false}}"#.utf8), stderr: "")])
        let model = RemoteModel(swe: SweClient(runner: runner))
        await model.revoke(RemoteStatus.Device(id: "dev-1"))
        #expect(runner.calls.count == 1)
        #expect(model.revoking.isEmpty)
        #expect(model.revokeErrors["dev-1"] == "swe did not find that device")
        #expect(model.status == .idle)
    }

    /// A failed revoke shows its error on the row and does not reload.
    @Test func aFailedRevokeShowsWhyAndSkipsTheReload() async {
        let runner = QueueRunner([.init(status: 1, stdout: Data(), stderr: "hub unreachable")])
        let model = RemoteModel(swe: SweClient(runner: runner))
        await model.revoke(RemoteStatus.Device(id: "dev-1"))
        #expect(runner.calls.count == 1)
        #expect(model.revoking.isEmpty)
        #expect(model.revokeErrors["dev-1"]?.isEmpty == false)
        #expect(model.status == .idle)
    }
}
