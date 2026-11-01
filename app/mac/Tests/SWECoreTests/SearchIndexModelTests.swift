import Foundation
import Testing
@testable import SWECore

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
        reconcileInterval: Duration = .seconds(60),
        pollSettled: Duration = .seconds(10)
    ) throws -> Stack {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("data/ledger"))
        let overview = OverviewModel(
            ledger: ledger, roots: { LedgerRoots() }, accountLabel: { $0 })
        let defaults = TestDefaults("search-index")
        defaults.set(enabled, forKey: SearchIndexModel.defaultsKey)
        let model = SearchIndexModel(
            quiver: temp.url, ledger: ledger, overview: overview,
            defaults: defaults, supported: supported,
            reconcileInterval: reconcileInterval, pollSettled: pollSettled)
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

        // A finished scan stamps the line instead of the "Synced:" prefix.
        let synced = SearchIndexModel.statusLine(status, scanning: false, synced: Date())
        #expect(synced.hasPrefix("Last synced "))
        #expect(synced.contains("3 sessions, 296,431 messages"))
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
        // Waits on start's own apply, through the models' own change
        // stream. Calling apply() here as well raced it: whichever resumed
        // second attached again, and its scan could still be running when
        // the status was read ("Indexing"). The attach stamps lastSynced
        // last, once its scan has finished.
        let settled = await observe {
            s.model.indexer != nil && s.overview.completedScans > 0
                && !s.overview.isLoading && s.model.lastSynced != nil
        }
        try #require(settled)

        #expect(s.model.indexer != nil)
        #expect(s.model.store?.fts5Available == true)
        await s.model.refreshStatus()
        // The scan attach kicked stamped lastSynced, so the line leads
        // with when rather than "Synced:".
        #expect(s.model.statusText.hasPrefix("Last synced "))
        #expect(s.model.statusText.contains("0 sessions, 0 messages"))
    }

    @Test func showingTheStatusLineReadsTheIndexAtOnce() async throws {
        let s = try stack()
        s.model.enabled = true
        await s.model.apply()
        #expect(s.model.statusText.contains("0 sessions"))

        // A row lands while the line is hidden; the poll doesn't read the
        // index then, so only showing the line can pick it up.
        _ = try #require(s.model.store).createSession(
            SearchSessionFields(agent: "claude", filePath: "/t/one.jsonl"))
        s.model.statusVisible = true
        let read = await observe { s.model.statusText.contains("1 session,") }
        #expect(read)
    }

    @Test func onlyAVisibleStatusLinePollsTheIndex() async throws {
        let s = try stack(enabled: true, pollSettled: .milliseconds(5))
        // One apply only: flipping `enabled` would start a second, whose
        // attach reads the index whenever it lands. The attach stamps
        // lastSynced last, once its scan has finished.
        s.model.start()
        #expect(await observe {
            s.model.indexer != nil && !s.overview.isLoading && s.model.lastSynced != nil
        })

        // Hidden: the poll keeps ticking but never reads the index.
        var reads = s.model.statusReads
        var ticks = s.model.pollTicks
        #expect(await observe { s.model.pollTicks >= ticks + 3 })
        #expect(s.model.statusReads == reads)

        // Shown: every tick reads it.
        s.model.statusVisible = true
        #expect(await observe { s.model.statusReads >= reads + 3 })

        // Hidden again: the reads stop.
        s.model.statusVisible = false
        reads = s.model.statusReads
        ticks = s.model.pollTicks
        #expect(await observe { s.model.pollTicks >= ticks + 3 })
        #expect(s.model.statusReads == reads)
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
        let defaults = TestDefaults("search-index")
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

        // The timeout turns a missing tick into a failure, not a hang.
        #expect(await observe { s.overview.completedScans > scanned })
    }

    @Test func theKeyNameIsSharedWithTheCLI() {
        // `swe session`'s index mode reads the same defaults key.
        #expect(SearchIndexModel.defaultsKey == "searchIndexingEnabled")
    }
}
