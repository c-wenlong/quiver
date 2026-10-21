import Foundation
import Observation
import Testing
@testable import QuiverCore

/// Races `wait` against a ten-second deadline: the first to finish wins
/// and the loser is cancelled. Returns false on timeout so a regression
/// fails the test instead of hanging the suite.
@MainActor
private func withinDeadline(_ wait: @escaping @MainActor @Sendable () async -> Bool) async -> Bool {
    await withTaskGroup(of: Bool.self) { group in
        group.addTask { await wait() }
        group.addTask {
            try? await Task.sleep(for: .seconds(10))
            return false
        }
        let winner = await group.next() ?? false
        group.cancelAll()
        return winner
    }
}

/// The experimental "Session search" switch: the status copy is honest
/// about counts, enabling attaches an indexer to the ledger and kicks the
/// same scan the app runs, off detaches without deleting, and Clear wipes
/// the index and starts it over.
@Suite @MainActor struct SearchIndexModelTests {
    private struct Stack {
        let temp: TempDir
        let ledger: Ledger
        let overview: OverviewModel
        let defaults: UserDefaults
        let model: SearchIndexModel
    }

    /// A real ledger and overview over an empty set of roots: scans run end
    /// to end and find no transcripts, which is enough to prove the wiring.
    private func stack(
        enabled: Bool = false,
        supported: Bool = true,
        reconcileInterval: Duration = .seconds(60)
    ) throws -> Stack {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("data/ledger"))
        let overview = OverviewModel(
            ledger: ledger, roots: { LedgerRoots() }, accountLabel: { $0 })
        let defaults = UserDefaults(suiteName: "search-index-\(UUID().uuidString)")!
        defaults.set(enabled, forKey: SearchIndexModel.defaultsKey)
        let model = SearchIndexModel(
            quiver: temp.url, ledger: ledger, overview: overview,
            defaults: defaults, supported: supported,
            reconcileInterval: reconcileInterval)
        return Stack(temp: temp, ledger: ledger, overview: overview,
                     defaults: defaults, model: model)
    }

    @Test func theStatusLineNeverInventsATotal() {
        #expect(SearchIndexModel.statusLine(nil, scanning: false) == "The index hasn't run yet.")

        var status = SearchStatus()
        status.filesByState = [.current: 2]
        // Mid-scan there is no M: the line says how far, not "of how many".
        #expect(SearchIndexModel.statusLine(status, scanning: true)
                == "Indexing: 2 files read so far")

        status.sessionCount = 1
        status.messageCount = 2
        #expect(SearchIndexModel.statusLine(status, scanning: false)
                == "Synced: 1 session, 2 messages")

        status.sessionCount = 3
        status.messageCount = 296_431
        status.filesByState = [.current: 10, .due: 1, .failed: 2]
        #expect(SearchIndexModel.statusLine(status, scanning: false)
                == "Synced: 3 sessions, 296,431 messages · 1 file behind · 2 files failed")
    }

    @Test func enablingAttachesTheIndexerAndRunsAScan() async throws {
        let s = try stack()

        s.model.enabled = true
        await s.model.apply()

        #expect(s.model.indexer != nil)
        #expect(s.model.store != nil)
        // The enable kicked the same load(scan:) the app runs elsewhere.
        #expect(s.overview.completedScans >= 1)
        // The index lives under <quiver>/data/search.
        let url = SearchLocation.directory(under: s.temp.url)
            .appending(path: SearchLocation.fileName)
        #expect(FileManager.default.fileExists(atPath: url.path))
    }

    @Test func thePersistedSwitchAttachesOnStart() async throws {
        let s = try stack(enabled: true)

        s.model.start()
        await s.model.apply()

        #expect(s.model.indexer != nil)
        #expect(s.model.store?.fts5Available == true)
        await s.model.refreshStatus()
        #expect(s.model.statusText == "Synced: 0 sessions, 0 messages")
    }

    @Test func disablingDetachesButKeepsTheFiles() async throws {
        let s = try stack()
        s.model.enabled = true
        await s.model.apply()
        let url = SearchLocation.directory(under: s.temp.url)
            .appending(path: SearchLocation.fileName)

        s.model.enabled = false
        await s.model.apply()

        #expect(s.model.indexer == nil)
        #expect(s.model.store == nil)
        #expect(s.model.statusText == "Off.")
        // Off is not Clear: the index stays on disk for the next enable.
        #expect(FileManager.default.fileExists(atPath: url.path))
    }

    @Test func clearDeletesTheIndexAndRebuildsIt() async throws {
        let s = try stack()
        s.model.enabled = true
        await s.model.apply()
        let url = SearchLocation.directory(under: s.temp.url)
            .appending(path: SearchLocation.fileName)

        await s.model.clearData()

        // Still enabled: a fresh store is attached and the counts read zero.
        #expect(s.model.store != nil)
        #expect(s.model.indexer != nil)
        #expect(try s.model.store?.status() == SearchStatus())
        #expect(FileManager.default.fileExists(atPath: url.path))
    }

    @Test func clearWithoutAStoreStillRemovesLeftoverFiles() async throws {
        let s = try stack()
        let directory = SearchLocation.directory(under: s.temp.url)
        // An index left behind by a previous run, with the feature off.
        try FileManager.default.createDirectory(
            at: directory, withIntermediateDirectories: true)
        try Data("stale".utf8).write(to: directory.appending(path: SearchLocation.fileName))
        try Data("stale".utf8).write(to: directory.appending(path: "search.sqlite-wal"))

        await s.model.clearData()

        #expect(s.model.store == nil)
        #expect(s.model.statusText == "Off.")
        #expect((try? FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: nil))?.isEmpty ?? true)
    }

    @Test func anUnsupportedBuildNeverAttaches() async throws {
        let s = try stack(supported: false)

        s.model.enabled = true
        await s.model.apply()

        #expect(s.model.indexer == nil)
        #expect(s.model.store == nil)
        #expect(s.model.statusText == "Off.")
    }

    @Test func aMissingLedgerReportsWhy() async throws {
        let temp = try TempDir()
        let defaults = UserDefaults(suiteName: "search-index-\(UUID().uuidString)")!
        let model = SearchIndexModel(
            quiver: temp.url, ledger: nil, overview: nil, defaults: defaults)

        model.enabled = true
        await model.apply()

        #expect(model.indexer == nil)
        #expect(model.statusText.contains("usage ledger"))
    }

    @Test func theReconcileTickScansAgain() async throws {
        let s = try stack(reconcileInterval: .milliseconds(30))
        s.model.enabled = true
        await s.model.apply()
        // Every apply is done here, so the next scan bump is the tick's.
        let scanned = s.overview.completedScans

        // The deadline turns a missing tick into a failure, not a hang.
        let ticked = await withinDeadline {
            for await count in Observations({ s.overview.completedScans }) {
                if Task.isCancelled || count > scanned { return count > scanned }
            }
            return false
        }
        #expect(ticked)
    }

    @Test func theKeyNameIsSharedWithTheCLI() {
        // `swe session`'s index mode reads the same defaults key.
        #expect(SearchIndexModel.defaultsKey == "searchIndexingEnabled")
    }
}
