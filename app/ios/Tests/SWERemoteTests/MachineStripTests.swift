import XCTest
@testable import SWERemote

final class MachineStripTests: XCTestCase {
    private let now = 1_800_000_000.0

    private func machine(_ id: String, _ name: String, online: Bool?, seen: Double? = nil) -> Machine {
        Machine(id: id, name: name, os: nil, online: online, lastSeenAt: seen, harnesses: nil)
    }

    private func row(_ key: String, on machineId: String?, _ status: String?) -> SessionRow {
        SessionRow(key: key, harness: "claude", agent: nil, cwd: "/w", title: key, lastActiveAt: now,
                   status: status, machineId: machineId, ownership: nil, attachable: nil)
    }

    func testStripHidesWithOneMachineOrNone() {
        XCTAssertTrue(MachineStrip.chips(machines: [], rows: [], now: now).isEmpty)
        XCTAssertTrue(MachineStrip.chips(machines: [machine("a", "studio", online: true)], rows: [], now: now).isEmpty)
    }

    func testChipsListOnlineFirstThenByName() {
        let chips = MachineStrip.chips(machines: [
            machine("c", "zed", online: true),
            machine("b", "alpha", online: false, seen: now - 7200),
            machine("a", "mini", online: true),
            machine("d", "beta.local", online: nil),
        ], rows: [], now: now)
        XCTAssertEqual(chips.map(\.name), ["mini", "zed", "alpha", "beta"])
    }

    func testOnlineChipCountsWorkingAndWaiting() {
        let rows = [
            row("1", on: "a", "active"), row("2", on: "a", "active"), row("3", on: "a", "followup"),
            row("4", on: "a", "approval"), row("5", on: "a", "done"), row("6", on: "b", "active"),
            row("7", on: nil, "active"),
        ]
        let chips = MachineStrip.chips(machines: [machine("a", "studio", online: true), machine("b", "mini", online: true)],
                                       rows: rows, now: now)
        XCTAssertEqual(chips.first { $0.id == "a" }?.detail, "2 working · 2 waiting")
        XCTAssertEqual(chips.first { $0.id == "b" }?.detail, "1 working · 0 waiting")
    }

    func testOfflineChipReadsLastSeen() {
        let chips = MachineStrip.chips(machines: [
            machine("a", "up", online: true),
            machine("b", "down", online: false, seen: now - 2 * 3600),
        ], rows: [row("1", on: "b", "active")], now: now)
        XCTAssertEqual(chips.last?.detail, "last seen 2h")
        XCTAssertEqual(MachineStrip.lastSeen(now - 10, now: now), "last seen just now")
        XCTAssertEqual(MachineStrip.lastSeen(nil, now: now), "offline")
    }

    func testTappingTogglesAndAStaleChoiceClears() {
        XCTAssertEqual(MachineStrip.toggled(nil, tapping: "a"), "a")
        XCTAssertEqual(MachineStrip.toggled("a", tapping: "b"), "b")
        XCTAssertNil(MachineStrip.toggled("a", tapping: "a"))
        let chips = MachineStrip.chips(machines: [machine("a", "x", online: true), machine("b", "y", online: true)],
                                       rows: [], now: now)
        XCTAssertEqual(MachineStrip.effective("a", chips: chips), "a")
        XCTAssertNil(MachineStrip.effective("gone", chips: chips))
        XCTAssertNil(MachineStrip.effective("a", chips: []))
    }

    func testFilterKeepsOnlyTheChosenMachinesRows() {
        let rows = [row("1", on: "a", "done"), row("2", on: "b", "done"), row("3", on: nil, "done")]
        XCTAssertEqual(MachineStrip.filter(rows, machine: "a").map(\.key), ["1"])
        XCTAssertEqual(MachineStrip.filter(rows, machine: nil).count, 3)
    }
}
