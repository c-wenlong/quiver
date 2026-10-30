import Foundation
import Testing
@testable import QuiverCore

@Suite struct LedgerScanProgressTests {
    func claudeHome(_ temp: TempDir, sessions: Int) throws -> URL {
        let home = temp.path("claude")
        for index in 0..<sessions {
            let file = home.appendingPathComponent("projects/-p/s\(index).jsonl")
            try FileManager.default.createDirectory(
                at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data(claudeLine(message: "m\(index)", session: "s\(index)").utf8).write(to: file)
        }
        return home
    }

    @Test func aFullScanCountsEveryFileItListed() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let claude = try claudeHome(temp, sessions: 3)
        let codex = try temp.copyFixture("codex-home")
        let codexFiles = Ledger.jsonlFiles(under: codex.appendingPathComponent("sessions")).count
            + Ledger.jsonlFiles(under: codex.appendingPathComponent("archived_sessions")).count
        let ledger = try Ledger(directory: temp.path("cache"))
        #expect(ledger.scanProgress() == nil)

        try await ledger.scan(roots: LedgerRoots(claudeHomes: [claude], codexHomes: [codex]))
        let total = 3 + codexFiles
        #expect(await ledger.lastFullScanProgress == ScanProgress(filesDone: total, filesTotal: total))
        #expect(ledger.scanProgress() == nil)

        // Unchanged files still count: the bar measures files checked.
        try await ledger.scan(roots: LedgerRoots(claudeHomes: [claude], codexHomes: [codex]))
        #expect(await ledger.lastFullScanProgress == ScanProgress(filesDone: total, filesTotal: total))
    }

    @Test func aCappedScanPublishesNothing() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let claude = try claudeHome(temp, sessions: 3)
        let ledger = try Ledger(directory: temp.path("cache"))
        let report = try await ledger.scan(roots: LedgerRoots(claudeHomes: [claude]), recentLimit: 1)
        #expect(report.filesRead == 1)
        #expect(await ledger.lastFullScanProgress == nil)
        #expect(ledger.scanProgress() == nil)
    }

    @Test func aCancelledScanClearsItsProgress() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let claude = try claudeHome(temp, sessions: 50)
        let ledger = try Ledger(directory: temp.path("cache"))
        let scan = Task { try await ledger.scan(roots: LedgerRoots(claudeHomes: [claude])) }
        scan.cancel()
        _ = try? await scan.value
        #expect(ledger.scanProgress() == nil)
    }
}
