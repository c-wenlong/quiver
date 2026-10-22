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

    @Test func revokingRunsTheRevokeCommand() async {
        let runner = FakeRunner(.init(status: 0, stdout: Data(
            #"{"schema":1,"data":{"revoked":"dev-1","ok":true}}"#.utf8), stderr: ""))
        let model = RemoteModel(swe: SweClient(runner: runner))
        let device = RemoteStatus.Device(id: "dev-1")
        // The second FakeRunner reply is the reload after a successful revoke.
        await model.revoke(device)
        #expect(runner.calls.first == ["remote", "devices", "--revoke=dev-1", "--json"])
    }
}
