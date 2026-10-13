import Foundation
import Observation

/// The Updates settings section: which build this is, whether the local
/// checkout has moved on, what GitHub's latest release says, and the one
/// rebuild at a time a dev build offers.
@MainActor
@Observable
public final class UpdatesModel {
    public let build: BuildInfo
    /// The effective choices: saved overrides over the stamped defaults.
    public private(set) var channel: UpdateChannel
    public private(set) var checkout: String?
    /// swe's own version string, nil when the configured swe cannot say.
    public private(set) var sweVersion: String?
    public private(set) var sweVersionKnown = false

    public private(set) var local: LocalBuildCheck.Result?
    public private(set) var release: ReleaseCheck.ReleaseReport?
    public private(set) var checking = false

    public enum RebuildState: Equatable {
        case idle
        case building
        case built(URL)
        case failed(String)
    }
    public private(set) var rebuildState: RebuildState = .idle

    private var preferences: UpdatePreferences
    private let localCheck: LocalBuildCheck
    private let releaseCheck: ReleaseCheck
    private let swe: @Sendable () -> SweClient
    private let rebuildRunner: any CommandRunning
    /// Rebuilds land in per-build folders under here, never on top of
    /// the running bundle.
    private let rebuildDir: URL
    /// The UUID folder this model's last build wrote into, so a reset or
    /// a new build can delete it without touching anything else.
    private var buildDir: URL?
    private var generation = 0

    /// `swe` is resolved at call time so a changed swe executable in
    /// Settings applies to the next refresh.
    public init(build: BuildInfo, preferences: UpdatePreferences,
                localCheck: LocalBuildCheck = LocalBuildCheck(),
                releaseCheck: ReleaseCheck,
                swe: @escaping @Sendable () -> SweClient = { SweClient() },
                rebuildRunner: any CommandRunning = ProcessRunner(executable: "env"),
                rebuildDir: URL = FileManager.default.homeDirectoryForCurrentUser
                    .appending(path: ".quiver/cache/rebuild")) {
        self.build = build
        self.preferences = preferences
        self.localCheck = localCheck
        self.releaseCheck = releaseCheck
        self.swe = swe
        self.rebuildRunner = rebuildRunner
        self.rebuildDir = rebuildDir
        channel = preferences.channel(build: build)
        checkout = preferences.checkout(build: build)
    }

    /// The saved Source checkout text, for the PathRow's draft seeding.
    public var checkoutOverride: String { preferences.checkoutOverride }

    /// The stamped source folder the PathRow hints at, when it exists.
    public var stampedCheckout: String? {
        guard let path = build.sourcePath,
              FileManager.default.fileExists(atPath: path) else { return nil }
        return path
    }

    /// The stored channel pick changed.
    public func setChannel(_ channel: UpdateChannel) {
        preferences.channelOverride = channel.rawValue
        self.channel = preferences.channel(build: build)
    }

    /// The Source checkout field changed. Stale answers and finished
    /// rebuilds belong to the old checkout; a build still running is
    /// discarded when it finishes.
    public func setCheckout(_ path: String) {
        preferences.checkoutOverride = path
        checkout = preferences.checkout(build: build)
        generation += 1
        local = nil
        switch rebuildState {
        case .built, .failed:
            rebuildState = .idle
            removeBuildDir()
        case .idle, .building: break
        }
        Task { await refresh(force: false) }
    }

    /// The app's version and swe's disagree.
    public var versionMismatch: (app: String, swe: String)? {
        guard sweVersionKnown, let swe = sweVersion, swe != build.version
        else { return nil }
        return (build.version, swe)
    }

    /// Read the local checkout and, when due, GitHub's release page.
    /// `force` is the Check now button: it fetches first so upstreamBehind
    /// is real, and skips the release cache.
    public func refresh(force: Bool) async {
        generation += 1
        let mine = generation
        checking = true
        defer { if mine == generation { checking = false } }

        let path = checkout
        let commit = build.commit
        let dirty = build.dirty
        let check = localCheck
        let swe = self.swe()
        async let localResult: LocalBuildCheck.Result? = {
            guard let path else { return nil }
            if force { _ = await check.fetch(path: path) }
            return await check.check(path: path, builtCommit: commit, builtDirty: dirty)
        }()
        async let releaseResult = releaseCheck.check(current: build.version, force: force)
        async let sweAnswer: String? = {
            struct Version: Decodable { let version: String }
            guard let reply: Version = try? await swe.call(["version", "--json"])
            else { return nil }
            return reply.version
        }()

        let (localAnswer, releaseAnswer, version) = await (localResult, releaseResult, sweAnswer)
        guard mine == generation else { return }
        local = localAnswer
        release = releaseAnswer
        sweVersion = version
        sweVersionKnown = version != nil
    }

    /// Rebuild the app from the checkout's build script into a fresh
    /// per-build folder under the rebuild cache, never over the running
    /// bundle or another finished build. One build at a time; the last
    /// lines of output explain a failure. If the checkout changed while
    /// the build ran, the result is stale and discarded.
    public func rebuild() async {
        guard case .idle = rebuildState, let checkout else { return }
        rebuildState = .building
        removeBuildDir()
        let buildDir = rebuildDir.appending(path: UUID().uuidString)
        let startedCheckout = checkout
        try? FileManager.default.createDirectory(at: buildDir,
                                                 withIntermediateDirectories: true)
        let script = checkout + "/app/script/build-app.sh"
        let result = try? await rebuildRunner.run(
            ["QUIVER_BUILD_DIR=\(buildDir.path)", "bash", script])
        guard self.checkout == startedCheckout else {
            try? FileManager.default.removeItem(at: buildDir)
            rebuildState = .idle
            return
        }
        guard let result else {
            try? FileManager.default.removeItem(at: buildDir)
            rebuildState = .failed("The build could not be started.")
            return
        }
        if result.status == 0 {
            self.buildDir = buildDir
            rebuildState = .built(buildDir.appending(path: "Quiver.app"))
        } else {
            try? FileManager.default.removeItem(at: buildDir)
            let lines = (result.stderr + "\n" + String(decoding: result.stdout, as: UTF8.self))
                .split(separator: "\n")
                .map(String.init)
                .filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
            rebuildState = .failed(lines.suffix(6).joined(separator: "\n"))
        }
    }

    /// The build finished earlier; reset so another rebuild can run.
    public func resetRebuild() {
        switch rebuildState {
        case .idle, .built, .failed:
            rebuildState = .idle
            removeBuildDir()
        case .building: break
        }
    }

    /// Drop the folder this model's last build wrote into. Only ever the
    /// UUID folder it created, so another running copy's build survives.
    private func removeBuildDir() {
        if let buildDir {
            try? FileManager.default.removeItem(at: buildDir)
            self.buildDir = nil
        }
    }
}
