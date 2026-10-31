import AppKit
import QuiverCore
import SwiftUI
import Testing
@testable import QuiverApp

/// The Sessions page's Other machines group draws from fixture rows. Set
/// QUIVER_SNAPSHOT_DIR to also write it as other-machines.png there.
@Suite @MainActor struct OtherMachinesGroupTests {
    static func fixture(now: Date = .now) -> OtherMachines {
        let t = now.timeIntervalSince1970
        let rows = [
            HubSessionRow(key: "claude:a1@studio", harness: "claude", cwd: "/Users/kai/src/quiver",
                          title: "Fix the hub's satellite resync", status: "active", machineId: "studio",
                          lastActiveAt: t - 40, remote: true, model: "claude-opus-4-5-20251101"),
            HubSessionRow(key: "codex:b2@studio", harness: "codex", cwd: "/Users/kai/src/landing",
                          title: "Landing page copy pass", status: nil, machineId: "studio",
                          lastActiveAt: t - 7200, remote: true, model: "gpt-5-codex"),
            HubSessionRow(key: "claude:c3@linux", harness: "claude", cwd: "/home/kai/robot",
                          title: "Tune the arm controller", status: nil, machineId: "linux",
                          lastActiveAt: t - 90_000, remote: true),
        ]
        let machines = [
            HubMachine(id: "studio", name: "kai-studio.local", online: true),
            HubMachine(id: "linux", name: "lab-box", online: false, lastSeenAt: t - 86_400),
        ]
        return OtherMachines.build(rows: rows, machines: machines, localMachineId: "me", now: now)
    }

    @Test func drawsTheFixture() throws {
        let group = Self.fixture()
        #expect(group.machines.map(\.title) == ["kai-studio", "lab-box · offline"])
        let view = OtherMachinesGroup(group: group,
                                      meta: { $0 == "claude:a1@studio" ? HubSessionMeta(pinned: true, pinnedAt: 1) : nil },
                                      open: { _ in }, act: { _, _ in })
            .padding(24)
            .frame(width: 760)
            .background(Theme.panel)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        let image = try #require(renderer.nsImage)
        #expect(image.size.width > 0)
        guard let dir = ProcessInfo.processInfo.environment["QUIVER_SNAPSHOT_DIR"] else { return }
        let rep = try #require(image.tiffRepresentation.flatMap(NSBitmapImageRep.init(data:)))
        let png = try #require(rep.representation(using: .png, properties: [:]))
        try png.write(to: URL(fileURLWithPath: dir).appending(path: "other-machines.png"))
    }
}
