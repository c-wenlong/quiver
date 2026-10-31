import Foundation
import Testing
@testable import QuiverCore

/// The Sessions page's Other machines group: which hub rows it takes, how
/// it orders machines and rows, and what each row's details line says.
@Suite struct OtherMachinesTests {
    let now = Date(timeIntervalSince1970: 1_000_000)
    var t: Double { now.timeIntervalSince1970 }

    func row(_ key: String, machine: String?, remote: Bool? = true, status: String? = nil,
             ago: Double? = 60, cwd: String? = "/Users/kai/src/app", harness: String? = "claude",
             model: String? = nil, title: String? = nil) -> HubSessionRow {
        HubSessionRow(key: key, harness: harness, cwd: cwd, title: title, status: status,
                      machineId: machine, lastActiveAt: ago.map { t - $0 }, remote: remote, model: model)
    }

    @Test func thisMacsRowsStayOut() {
        let rows = [
            row("claude:a", machine: "me", remote: nil),
            // A remote flag on this Mac's own id still isn't another machine.
            row("claude:b@me", machine: "me"),
            row("claude:c@m2", machine: "m2"),
            row("claude:d", machine: nil, remote: nil),
        ]
        let group = OtherMachines.build(rows: rows, machines: [HubMachine(id: "m2", name: "studio", online: true)],
                                        localMachineId: "me", now: now)
        #expect(group.machines.map(\.id) == ["m2"])
        #expect(group.machines[0].rows.map(\.key) == ["claude:c@m2"])
        #expect(group.sessionCount == 1)
        #expect(group.caption == "from the hub · 1 session")
    }

    @Test func noOtherMachineNoGroup() {
        let group = OtherMachines.build(rows: [row("claude:a", machine: "me", remote: nil)],
                                        machines: [HubMachine(id: "me", online: true)],
                                        localMachineId: "me", now: now)
        #expect(group.isEmpty)
        // A satellite the hub lists with no sessions doesn't show either.
        #expect(OtherMachines.build(rows: [], machines: [HubMachine(id: "m2", online: true)],
                                    localMachineId: "me", now: now).isEmpty)
    }

    @Test func onlineMachinesLeadThenByNameAndRowsNewestFirst() {
        let rows = [
            row("claude:z1@zed", machine: "zed", ago: 50),
            row("claude:a1@alpha", machine: "alpha", ago: 3600),
            row("codex:a2@alpha", machine: "alpha", ago: 10),
            row("claude:o1@old", machine: "old", ago: 5),
            row("claude:a3@alpha", machine: "alpha", ago: nil),
        ]
        let machines = [
            HubMachine(id: "zed", name: "Zed", online: true),
            HubMachine(id: "alpha", name: "alpha.local", online: true),
            HubMachine(id: "old", name: "Aardvark", online: false, lastSeenAt: t - 86_400 * 2),
        ]
        let group = OtherMachines.build(rows: rows, machines: machines, localMachineId: nil, now: now)
        #expect(group.machines.map(\.name) == ["alpha", "Zed", "Aardvark"])
        #expect(group.machines[0].rows.map(\.key) == ["codex:a2@alpha", "claude:a1@alpha", "claude:a3@alpha"])
        #expect(group.machines.map(\.title) == ["alpha", "Zed", "Aardvark · offline"])
        #expect(group.caption == "from the hub · 5 sessions")
    }

    @Test func aMachineTheListMissesReadsOfflineByItsRowName() {
        let group = OtherMachines.build(rows: [{
            var r = row("claude:x@m9", machine: "m9")
            r.machine = "laptop.local"
            return r
        }()], machines: [], localMachineId: nil, now: now)
        #expect(group.machines.first?.name == "laptop")
        #expect(group.machines.first?.online == false)
    }

    @Test func detailsLineSaysStatusFolderHarnessAndModel() {
        let working = row("claude:a@m2", machine: "m2", status: "active",
                          model: "claude-opus-4-5-20251101")
        #expect(OtherMachines.details(working, online: true, lastSeenAt: nil, now: now)
                == "Working · ~/src/app · \(HarnessMark("claude").name) · claude-opus-4-5")
        // No model known: nothing in its place.
        let idle = row("codex:b@m2", machine: "m2", ago: 7200, cwd: "/home/kai/work", harness: "codex")
        #expect(OtherMachines.details(idle, online: true, lastSeenAt: nil, now: now)
                == "Idle 2h · ~/work · \(HarnessMark("codex").name)")
        // An offline machine's rows say when the hub last heard from it.
        #expect(OtherMachines.details(idle, online: false, lastSeenAt: t - 86_400, now: now)
                .hasPrefix("Last seen 1d · "))
    }

    @Test func statusWords() {
        func status(_ s: String?, ago: Double? = 30) -> String {
            OtherMachines.status(row("k", machine: "m", status: s, ago: ago), online: true, lastSeenAt: nil, now: now)
        }
        #expect(status("active") == "Working")
        #expect(status("followup") == "Waiting")
        #expect(status("error") == "Error")
        #expect(status("interrupted") == "Interrupted")
        #expect(status(nil) == "Idle")
        #expect(status(nil, ago: 300) == "Idle 5m")
        #expect(status(nil, ago: nil) == "Idle")
        #expect(OtherMachines.status(row("k", machine: "m", ago: nil), online: false, lastSeenAt: nil, now: now)
                == "Offline")
        #expect(OtherMachines.status(row("k", machine: "m"), online: false, lastSeenAt: t - 5, now: now)
                == "Last seen just now")
    }

    @Test func foldersUseTildeForTheOtherMachinesHome() {
        #expect(OtherMachines.folder("/Users/kai/src/app") == "~/src/app")
        #expect(OtherMachines.folder("/Users/kai") == "~")
        #expect(OtherMachines.folder("/home/dev/x") == "~/x")
        #expect(OtherMachines.folder("/root/x") == "~/x")
        #expect(OtherMachines.folder("/opt/work") == "/opt/work")
        #expect(OtherMachines.folder("/Users") == "/Users")
    }

    @Test func titlesPreferTheHubsThenTheSessionId() {
        #expect(OtherMachines.title(row("claude:abc@m2", machine: "m2", title: "Ship it")) == "Ship it")
        var bare = row("claude:abc@m2", machine: "m2", title: "  ")
        #expect(OtherMachines.title(bare) == "abc")
        bare.harnessTitle = "fix the build"
        #expect(OtherMachines.title(bare) == "fix the build")
        #expect(OtherMachines.sessionId("pi:abc:def@m2") == "abc:def")
    }
}

extension HubStubbed {
@MainActor
@Suite struct LocalHubOtherMachinesTests {
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    nonisolated static let listing = #"""
    {"sessions": [
      {"key": "claude:a", "machine_id": "me", "title": "here"},
      {"key": "claude:b@m2", "machine_id": "m2", "remote": true, "title": "there", "status": "active"}
    ],
     "machines": [
      {"id": "me", "name": "this-mac", "online": true},
      {"id": "m2", "name": "studio", "online": false, "last_seen_at": 5}
    ]}
    """#

    @Test func theListsReadKeepsRowsAndMachinesAndADownHubDropsThem() async throws {
        let store = HubDeviceStore(url: scratch.url.appending(path: "remote/app-device.json"))
        try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
        let hub = LocalHub(locator: FakeLocator(address: "127.0.0.1:8477"), store: store) {
            HubClient(baseURL: $0, credential: $1, session: HubStub.session())
        }
        HubStub.handler = { req in req.url?.path == "/v1/sessions" ? (200, Data(Self.listing.utf8)) : (404, Data()) }
        await hub.refreshKeys()
        #expect(hub.hubRows.map(\.key) == ["claude:a", "claude:b@m2"])
        #expect(hub.hubMachines.first { $0.id == "m2" }?.lastSeenAt == 5)
        let group = OtherMachines.build(rows: hub.hubRows, machines: hub.hubMachines, localMachineId: "me")
        #expect(group.machines.map(\.title) == ["studio · offline"])
        // The satellite's row offers the hub's pin and rename like any other.
        #expect(hub.meta(for: "claude:b@m2")?.title == "there")

        HubStub.handler = { _ in (503, Data()) }
        await hub.refreshKeys()
        #expect(hub.hubRows.isEmpty)
        #expect(hub.hubMachines.isEmpty)
    }
}
}
