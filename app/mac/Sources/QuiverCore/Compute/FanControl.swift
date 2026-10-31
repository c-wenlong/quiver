import CryptoKit
import Darwin
import Foundation
import Observation
import Security

/// Installs and removes the root fan helper. The real one goes through
/// the macOS administrator prompt; tests count calls.
public protocol FanHelperInstalling: Sendable {
    /// Throws `FanHelperInstallError.cancelled` when the user dismissed
    /// the password prompt.
    func install(executable: String, control: URL) async throws
    func uninstall() async throws
}

public enum FanHelperInstallError: Error, Equatable, LocalizedError {
    case cancelled
    case failed(String)

    public var errorDescription: String? {
        switch self {
        case .cancelled: "Cancelled."
        case .failed(let message): message
        }
    }
}

/// One `osascript … with administrator privileges` per install or
/// removal: the system password prompt, then a shell script run as root.
/// Installing copies Quiver's executable to a root-owned path, writes the
/// launchd job and starts it; from then on Quiver steers it through the
/// control file without asking again.
public struct AdministratorInstaller: FanHelperInstalling {
    /// The team that signed the running app (`signingTeam`); tests swap it.
    private let team: @Sendable () throws -> String?

    public init(team: @escaping @Sendable () throws -> String? = AdministratorInstaller.signingTeam) {
        self.team = team
    }

    static func shell(_ text: String) -> String {
        "'" + text.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    /// The AppleScript around a root shell command: single-quoted paths
    /// inside, then escaped for the AppleScript string.
    static func script(_ command: String, prompt: String) -> String {
        func apple(_ text: String) -> String {
            text.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
        }
        return "do shell script \"\(apple(command))\" with prompt \"\(apple(prompt))\" with administrator privileges"
    }

    /// The code requirement the helper must meet before it runs as root:
    /// QuiverFanHelper, signed through Apple by the team that signed the
    /// app. Nothing running as the user can produce that signature, so a
    /// helper patched in the (user-writable) bundle fails it.
    static func helperRequirement(team: String) -> String {
        "=identifier \"\(FanHelper.bundledName)\" and anchor apple generic"
            + " and certificate leaf[subject.OU] = \"\(team)\""
    }

    /// The root script's signature step: `tool` must meet
    /// `helperRequirement`, or it is deleted and the install stops.
    static func signatureCheck(tool: String, team: String) -> String {
        "{ /usr/bin/codesign --verify --strict -R \(shell(helperRequirement(team: team))) \(shell(tool)) 2>/dev/null"
            + " || { /bin/rm -f \(shell(tool)); echo 'The fan helper is not signed by the team that signed Quiver.' >&2;"
            + " exit 1; }; }"
    }

    /// A team identifier as Apple issues them: ten capitals and digits.
    static func isTeamIdentifier(_ value: String) -> Bool {
        value.count == 10 && value.allSatisfy { ("A"..."Z").contains($0) || ("0"..."9").contains($0) }
    }

    /// Copy the executable, check the copy is byte for byte the one
    /// hashed before the prompt (anything running as the user could swap
    /// the bundle's copy while the prompt is up), and, when the app is
    /// signed by a team, that the copy carries that team's signature: the
    /// hash only proves the file didn't change during the prompt, not that
    /// it is the helper Quiver shipped. Then ad-hoc sign it, unload the old
    /// job, move the copy into place, write the job from trusted text as
    /// root, and start it. Every check runs on the root-owned staged copy,
    /// which nothing running as the user can change, and the live path
    /// only changes once the old job can no longer relaunch it.
    /// `team` is nil only for an ad-hoc build, which has no signature to
    /// check against and keeps the hash alone.
    public static func installCommand(executable: String, sha256: String, team: String?,
                                      control: URL) -> String {
        let staged = FanHelper.installedExecutable + ".new"
        let tool = shell(FanHelper.installedExecutable), new = shell(staged), job = shell(FanHelper.installedPlist)
        let signature = team.map { " && " + signatureCheck(tool: staged, team: $0) } ?? ""
        // Everything is checked on a staged copy beside the live one: an
        // installed KeepAlive job may relaunch its executable at any
        // moment, so the live path only ever changes to a checked file,
        // and only once the old job is unloaded.
        return "/bin/mkdir -p /Library/PrivilegedHelperTools"
            + " && /usr/bin/install -m 755 -o root -g wheel \(shell(executable)) \(new)"
            + " && { [ \"$(/usr/bin/shasum -a 256 \(new) | /usr/bin/cut -d ' ' -f 1)\" = \(shell(sha256)) ]"
            + " || { /bin/rm -f \(new); echo 'The fan helper changed during install.' >&2; exit 1; }; }"
            + signature
            + " && /usr/bin/codesign --force --sign - \(new)"
            // A reinstall unloads the old job first; launchd needs a beat
            // before the label can load again.
            + " && { /bin/launchctl bootout system/\(FanHelper.label) 2>/dev/null && /bin/sleep 1; true; }"
            + " && /bin/mv -f \(new) \(tool)"
            + " && /usr/bin/printf '%s' \(shell(FanHelper.plistLine(control: control))) > \(job)"
            + " && /usr/sbin/chown root:wheel \(job) && /bin/chmod 644 \(job)"
            + " && /bin/launchctl bootstrap system \(job)"
    }

    /// Stopping the job sends it SIGTERM, which hands the fans back first.
    public static var uninstallCommand: String {
        "{ /bin/launchctl bootout system/\(FanHelper.label) 2>/dev/null; true; }"
            + " && /bin/rm -f \(shell(FanHelper.installedPlist)) \(shell(FanHelper.installedExecutable))"
    }

    static func sha256(of path: String) -> String? {
        guard let data = FileManager.default.contents(atPath: path) else { return nil }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// The team that signed the running app, or nil for an ad-hoc build.
    /// Throws when the signature can't be read or the code on disk no
    /// longer matches what is running: a bundle re-signed under the app
    /// must not read as "ad-hoc" and skip the helper's check.
    public static func signingTeam() throws -> String? {
        func check(_ status: OSStatus, _ what: String) throws {
            guard status == errSecSuccess else {
                throw FanHelperInstallError.failed("Couldn't check Quiver's own signature (\(what): \(status)).")
            }
        }
        var code: SecCode?
        try check(SecCodeCopySelf([], &code), "self")
        guard let code else { throw FanHelperInstallError.failed("Couldn't check Quiver's own signature.") }
        try check(SecCodeCheckValidity(code, [], nil), "validity")
        var staticCode: SecStaticCode?
        try check(SecCodeCopyStaticCode(code, [], &staticCode), "static code")
        guard let staticCode else { throw FanHelperInstallError.failed("Couldn't check Quiver's own signature.") }
        var info: CFDictionary?
        try check(SecCodeCopySigningInformation(staticCode, SecCSFlags(rawValue: kSecCSSigningInformation), &info),
                  "signing information")
        let team = (info as? [String: Any])?[kSecCodeInfoTeamIdentifier as String] as? String
        guard let team, !team.isEmpty else { return nil }
        guard isTeamIdentifier(team) else {
            throw FanHelperInstallError.failed("Quiver's signature names an odd team (\(team)).")
        }
        return team
    }

    public func install(executable: String, control: URL) async throws {
        guard !control.path.contains(where: \.isNewline), !executable.contains(where: \.isNewline) else {
            throw FanHelperInstallError.failed("Quiver's paths can't hold a line break for the fan helper.")
        }
        let team = try self.team()
        if let team, !Self.isTeamIdentifier(team) {
            throw FanHelperInstallError.failed("Quiver's signature names an odd team (\(team)).")
        }
        guard let hash = await offMain({ Self.sha256(of: executable) }) else {
            throw FanHelperInstallError.failed("Couldn't read \(executable).")
        }
        try await run(Self.script(Self.installCommand(executable: executable, sha256: hash, team: team,
                                                      control: control),
                                  prompt: "Quiver wants to install a helper that sets the fan speed. "
                                      + "You'll only be asked once."))
    }

    public func uninstall() async throws {
        try await run(Self.script(Self.uninstallCommand, prompt: "Quiver wants to remove its fan helper."))
    }

    private func run(_ script: String) async throws {
        let (status, error) = await offMain { () -> (Int32, String) in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
            process.arguments = ["-e", script]
            let err = Pipe()
            process.standardOutput = FileHandle.nullDevice
            process.standardError = err
            do { try process.run() } catch { return (-1, error.localizedDescription) }
            let data = err.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            return (process.terminationStatus, String(decoding: data, as: UTF8.self))
        }
        // -128 is AppleScript's "User canceled".
        if error.contains("-128") { throw FanHelperInstallError.cancelled }
        guard status == 0 else {
            let message = error.trimmingCharacters(in: .whitespacesAndNewlines)
            throw FanHelperInstallError.failed(message.isEmpty ? "The fan helper didn't install." : message)
        }
    }
}

/// The Compute page's fan popover: what the fans are doing, and a manual
/// speed held by the root helper until the user picks Automatic or quits.
@MainActor
@Observable
public final class FanControl {
    public enum Phase: Equatable, Sendable {
        case automatic
        /// Asked for, waiting for the helper to take the fans.
        case starting
        case manual
        case failed(String)
    }

    public private(set) var fans: [FanReading] = []
    public private(set) var phase: Phase = .automatic {
        didSet { failedAt = if case .failed = phase { clock() } else { nil } }
    }
    /// Whether the SMC has answered at least once.
    public private(set) var loaded = false
    /// Whether the root helper is installed; the first Set speed installs it.
    public private(set) var helperInstalled: Bool
    /// Whether an install or removal is waiting on the password prompt.
    public private(set) var busy = false
    /// The level the slider shows, 0 (the fans' minimum) to 1 (their
    /// maximum). Kept in UserDefaults so the last choice comes back.
    public var level: Double {
        didSet { defaults.set(level, forKey: Self.levelKey) }
    }

    /// The threshold's own slider. Its controls have no Set button, so a
    /// new speed reaches the fans the threshold holds at once. The Manual
    /// slider sets `level` alone and waits for Update speed, even while
    /// the threshold holds.
    public func setThresholdLevel(_ value: Double) {
        level = value
        if heldByThreshold { try? writeRequest() }
    }

    public static let levelKey = "fanManualLevel"

    /// The optional rule: above `threshold` °C on the CPU, hold the fans
    /// at `level` until it falls to `releaseBelow`. Only while Quiver
    /// runs, like any manual speed.
    public var thresholdEnabled: Bool {
        didSet {
            defaults.set(thresholdEnabled, forKey: Self.thresholdEnabledKey)
            if !thresholdEnabled && heldByThreshold { restoreAutomatic() }
        }
    }

    public var threshold: Double {
        didSet {
            let clamped = min(max(threshold, Self.thresholdRange.lowerBound), Self.thresholdRange.upperBound)
            if clamped != threshold { threshold = clamped; return }
            defaults.set(threshold, forKey: Self.thresholdKey)
            // A picked off temperature must stay under the line, or a held
            // fan would drop out while the CPU is still over it; an
            // unpicked one just follows the line at the default gap.
            if let releasePick, releasePick >= threshold { self.releasePick = threshold - 1 }
        }
    }

    /// Where the fans go back to macOS: the "Fan off temperature" field's
    /// value in effect — the user's pick, or the default gap under the
    /// line until they pick one. Writing it pins a temperature.
    public var releaseBelow: Double {
        get { releasePick ?? threshold - Self.thresholdHysteresis }
        set { releasePick = newValue }
    }

    /// The user's picked off temperature; nil means the field follows
    /// the line at `thresholdHysteresis`. Only a pick is remembered, so
    /// a restart cannot move an unpicked value.
    private var releasePick: Double? {
        didSet {
            guard let releasePick else {
                defaults.removeObject(forKey: Self.releaseBelowKey)
                return
            }
            let clamped = Self.clampRelease(releasePick, threshold: threshold)
            if clamped != releasePick { self.releasePick = clamped; return }
            defaults.set(releasePick, forKey: Self.releaseBelowKey)
        }
    }

    /// Whether the speed being held is the threshold's, not the user's.
    public private(set) var heldByThreshold = false

    public static let thresholdEnabledKey = "fanThresholdEnabled"
    public static let thresholdKey = "fanThresholdCelsius"
    public static let thresholdRange = 50.0...100.0
    public static let defaultThreshold = 80.0
    public static let releaseBelowKey = "fanReleaseBelowCelsius"
    /// The off temperature's own bounds; its live ceiling is a degree
    /// under `threshold`, which the clamp applies on top.
    public static let releaseRange = 30.0...99.0
    /// The gap an unpicked off temperature keeps under the line, so a
    /// CPU hovering at it does not flap the fans.
    public static let thresholdHysteresis = 5.0

    /// The off temperature inside its range and a degree under the line.
    private static func clampRelease(_ value: Double, threshold: Double) -> Double {
        min(max(value, releaseRange.lowerBound), min(releaseRange.upperBound, threshold - 1))
    }
    /// How long `starting` may last before the request counts as failed.
    /// Taking the fans on an M4 takes up to about ten seconds.
    static let startTimeout: TimeInterval = 20
    /// How long after a failed takeover the rule may try again. A helper
    /// that never answers used to switch it off until someone toggled the
    /// rule or set a speed by hand; a minute's wait lets it recover on
    /// its own without pestering a helper that is still down.
    static let retryAfter: TimeInterval = 60
    /// The timeout's message when the helper never said what happened:
    /// not running, or a copy from before it wrote a status. Reinstalling
    /// puts the current one in place.
    static let notTaken = "The fan helper didn't answer. Remove it and set the speed again to reinstall it."
    /// The helper took manual mode, yet the fans never read manual.
    static let didNotStick = "The fan helper set manual mode, but the fans didn't stay manual."
    /// How long `launchctl print` may take before the helper counts as
    /// not loaded.
    nonisolated static let launchctlTimeout: TimeInterval = 2

    private let reader: @Sendable () -> [FanReading]
    private let status: @Sendable () -> FanHelperStatus?
    private let installer: any FanHelperInstalling
    private let isInstalled: @Sendable (URL) -> Bool
    private let control: URL
    private let executable: String
    private let owner: Int32
    private let clock: @Sendable () -> Date
    private let sleep: @Sendable (TimeInterval) async -> Void
    private let defaults: UserDefaults
    private var startedAt: Date?
    /// When the phase last went `failed`; once `retryAfter` has passed
    /// the rule may take the fans again instead of staying off forever.
    private var failedAt: Date?
    /// The request last written, to match the helper's status against.
    private var current: FanControlFile?
    /// The helper's status as of the last refresh.
    private var helperStatus: FanHelperStatus?
    /// Ends `starting` after `startTimeout` on its own clock, so a refresh
    /// that never comes back (a hung read) can't hold the phase there.
    private(set) var deadline: Task<Void, Never>?

    public init(control: URL,
                reader: @escaping @Sendable () -> [FanReading] = { AppleSMC().map(FanKeys.read) ?? [] },
                status: @escaping @Sendable () -> FanHelperStatus? = { FanHelperStatus.read() },
                installer: any FanHelperInstalling = AdministratorInstaller(),
                isInstalled: @escaping @Sendable (URL) -> Bool = FanControl.helperReady,
                executable: String = FanHelper.bundledExecutable(appExecutable: Bundle.main.executablePath),
                owner: Int32 = getpid(),
                clock: @escaping @Sendable () -> Date = { .now },
                sleep: @escaping @Sendable (TimeInterval) async -> Void = { try? await Task.sleep(for: .seconds($0)) },
                defaults: UserDefaults = .standard) {
        self.control = control
        self.reader = reader
        self.status = status
        self.installer = installer
        self.isInstalled = isInstalled
        self.executable = executable
        self.owner = owner
        self.clock = clock
        self.sleep = sleep
        self.defaults = defaults
        helperInstalled = false
        level = defaults.object(forKey: Self.levelKey) as? Double ?? 0.5
        thresholdEnabled = defaults.bool(forKey: Self.thresholdEnabledKey)
        let line = defaults.object(forKey: Self.thresholdKey) as? Double ?? Self.defaultThreshold
        threshold = line
        releasePick = (defaults.object(forKey: Self.releaseBelowKey) as? Double)
            .map { Self.clampRelease($0, threshold: line) }
    }

    /// The helper is installed, steered by this control file, at this
    /// version, and loaded: files left by a half-finished install, a job
    /// another account installed for its own file, or an older helper
    /// (one from before `FanHelper.version`, which crashed at launch)
    /// count as not installed, so Set speed reinstalls rather than waits
    /// on a helper that will never come.
    public nonisolated static func helperReady(control: URL) -> Bool {
        guard FileManager.default.isExecutableFile(atPath: FanHelper.installedExecutable),
              let data = FileManager.default.contents(atPath: FanHelper.installedPlist),
              let job = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              let arguments = job["ProgramArguments"] as? [String],
              arguments == FanHelper.programArguments(control: control)
        else { return false }
        return exitStatus(of: "/bin/launchctl", ["print", "system/\(FanHelper.label)"],
                          within: launchctlTimeout) == 0
    }

    /// Runs a tool and answers its exit status, or nil when it couldn't
    /// start or didn't finish `within` seconds (it is killed then). It
    /// blocks, so it never runs on the main thread.
    nonisolated static func exitStatus(of path: String, _ arguments: [String],
                                       within timeout: TimeInterval) -> Int32? {
        assert(!Thread.isMainThread, "exitStatus blocks; call it off the main thread")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: path)
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        let exited = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in exited.signal() }
        guard (try? process.run()) != nil else { return nil }
        guard exited.wait(timeout: .now() + timeout) == .success else {
            process.terminate()
            if exited.wait(timeout: .now() + 0.5) != .success {
                kill(process.processIdentifier, SIGKILL)
                _ = exited.wait(timeout: .now() + 0.5)
            }
            return nil
        }
        return process.terminationStatus
    }

    /// Whether a manual speed is being held or about to be.
    public var isHolding: Bool { phase == .starting || phase == .manual }

    /// The speed the current level asks of the first fan, for the label.
    public var targetRPM: Double? { fans.first?.rpm(atLevel: level) }

    /// Read the fans, then settle the phase from what the SMC says:
    /// every fan manual means the helper holds them.
    public func refresh() async {
        let reader = self.reader, status = self.status
        (fans, helperStatus) = await offMain { (reader(), status()) }
        // The install check spawns launchctl, so it runs on the first read
        // and after a request the helper never took, not every tick.
        // Cleared before the wait, so overlapping refreshes don't each
        // spawn one, and a reason to look again that arrives during it
        // (a timeout, a removal) isn't wiped by the answer.
        if installUnchecked {
            installUnchecked = false
            let isInstalled = self.isInstalled, control = self.control
            helperInstalled = await offMain { isInstalled(control) }
        }
        loaded = true
        settle()
    }

    private var installUnchecked = true

    private var fansManual: Bool { !fans.isEmpty && fans.allSatisfy(\.isManual) }

    private func settle() {
        switch phase {
        case .starting:
            if fansManual {
                phase = .manual
                stopDeadline()
            } else if let reason = refusal {
                give(up: reason)
            } else if let startedAt, clock().timeIntervalSince(startedAt) > Self.startTimeout {
                give(up: timeoutReason)
            }
        case .manual:
            // macOS took them back (after sleep, say): the helper takes
            // them again on its next pass, so wait rather than give up.
            if !fansManual { beginStarting() }
        case .automatic, .failed:
            break
        }
    }

    /// Why the helper gave up on the current request, once it says so:
    /// no need to wait out the timeout for an answer already in.
    private var refusal: String? {
        guard let helperStatus else { return nil }
        switch helperStatus.state {
        // Not tied to a request, so only one written after this request
        // was made counts: an error left by an earlier run (install and
        // removal leave the file) says nothing about the helper now.
        case .error where current?.requested.map { helperStatus.at >= $0 } ?? false:
            return helperStatus.reason ?? Self.notTaken
        case .refused where helperStatus.answers(current):
            return helperStatus.reason ?? Self.notTaken
        case .ready, .held, .refused, .error:
            return nil
        }
    }

    private var timeoutReason: String {
        if let helperStatus, helperStatus.state == .held, helperStatus.answers(current) {
            return Self.didNotStick
        }
        return refusal ?? Self.notTaken
    }

    private func beginStarting() {
        phase = .starting
        let began = clock()
        startedAt = began
        deadline?.cancel()
        let sleep = self.sleep
        deadline = Task { [weak self] in
            await sleep(Self.startTimeout)
            guard !Task.isCancelled else { return }
            self?.expire(began)
        }
    }

    private func stopDeadline() {
        deadline?.cancel()
        deadline = nil
    }

    /// The deadline for the `starting` that began at `began`; a later
    /// one, or a phase that has moved on, is left alone.
    private func expire(_ began: Date) {
        guard phase == .starting, startedAt == began else { return }
        give(up: timeoutReason)
    }

    private func give(up reason: String) {
        phase = clearRequest() ?? .failed(reason)
        installUnchecked = true
    }

    /// The user's Set speed: hold the fans at `level`, and from then on
    /// the speed is theirs, not the threshold's.
    public func applyManual() async {
        heldByThreshold = false
        await hold()
    }

    /// Hold the fans at `level`. The installed helper picks the request up
    /// within a second; the first time, this installs it, which asks for
    /// the password once.
    private func request() -> FanControlFile {
        FanControlFile(level: level, owner: owner, requested: clock())
    }

    private func writeRequest() throws {
        let request = request()
        try request.write(to: control)
        current = request
    }

    private func hold() async {
        do {
            try writeRequest()
        } catch {
            phase = .failed("Couldn't write \(control.path): \(error.localizedDescription)")
            return
        }
        if !helperInstalled {
            switch await installHelper() {
            case .installed: break
            case .cancelled:
                phase = clearRequest() ?? .automatic
                return
            case .failed(let message):
                phase = clearRequest() ?? .failed(message)
                return
            }
        }
        if phase != .manual { beginStarting() }
        await refresh()
    }

    private enum Install { case installed, cancelled, failed(String) }

    /// One password prompt.
    private func installHelper() async -> Install {
        busy = true
        defer { busy = false }
        do {
            try await installer.install(executable: executable, control: control)
            helperInstalled = true
            // Same as removal: check again once the prompt is done.
            installUnchecked = true
            return .installed
        } catch FanHelperInstallError.cancelled {
            return .cancelled
        } catch {
            return .failed(error.localizedDescription)
        }
    }

    /// Hand the fans back to macOS. The helper sees the empty request
    /// within a second and sets every fan to automatic.
    public func restoreAutomatic() {
        heldByThreshold = false
        phase = clearRequest() ?? .automatic
    }

    /// Switch the threshold on, installing the helper first if it is not
    /// there: the rule acts from the background, where a password prompt
    /// must never appear, so the one prompt happens here, on a click.
    public func enableThreshold() async {
        if installUnchecked { await refresh() }
        if !helperInstalled {
            switch await installHelper() {
            case .installed:
                // An earlier failed try must not keep the rule from acting:
                // it only takes the fans from Automatic.
                if case .failed = phase { phase = .automatic }
            case .cancelled: return
            case .failed(let message):
                phase = .failed(message)
                return
            }
        }
        thresholdEnabled = true
    }

    /// Whether the rule may take the fans now: from Automatic always, or
    /// back out of a failure once `retryAfter` has passed, and never
    /// while a speed is already held or being waited on.
    private var thresholdMayAct: Bool {
        switch phase {
        case .automatic: true
        case .failed: failedAt.map { clock().timeIntervalSince($0) >= Self.retryAfter } ?? false
        case .starting, .manual: false
        }
    }

    /// The rule, run on each trend tick with the CPU's temperature. It
    /// takes the fans only when `thresholdMayAct` (a speed the user set
    /// wins) and only once the helper is installed; it gives them back
    /// once the CPU reaches `releaseBelow`, or as soon as anything else
    /// (the user, a failure) has moved them off its speed.
    public func checkTemperature(_ cpu: Double?) async {
        guard thresholdEnabled, let cpu else { return }
        if heldByThreshold {
            // Read the fans here too: with the page and popover closed
            // nothing else would, and a takeover the helper never finished
            // must time out and be reported rather than sit in Starting.
            await refresh()
            guard heldByThreshold else { return }
            if !isHolding {
                heldByThreshold = false
            } else if cpu <= releaseBelow {
                restoreAutomatic()
            }
            return
        }
        guard thresholdMayAct, cpu >= threshold else { return }
        if installUnchecked { await refresh() }
        guard helperInstalled, thresholdEnabled, thresholdMayAct, !heldByThreshold else { return }
        // Claimed before the wait, so switching the rule off (or the user
        // taking over) during it is seen and undone, not left held.
        heldByThreshold = true
        await hold()
        guard heldByThreshold else { return }
        if !thresholdEnabled {
            restoreAutomatic()
            return
        }
        heldByThreshold = isHolding
    }

    /// Fans back to automatic, then the helper uninstalled (one password).
    public func removeHelper() async {
        restoreAutomatic()
        busy = true
        defer { busy = false }
        do {
            try await installer.uninstall()
        } catch FanHelperInstallError.cancelled {
        } catch {
            phase = .failed(error.localizedDescription)
        }
        // Marked only now: a refresh while the prompt was up checked the
        // helper as it was then, and must not stand for after.
        installUnchecked = true
        await refresh()
    }

    /// Withdraw the request. Nil once it is gone; otherwise the failure
    /// to show, since a request left in place keeps the fans held while
    /// the popover would claim Automatic.
    private func clearRequest() -> Phase? {
        startedAt = nil
        stopDeadline()
        do {
            try FanControlFile(level: nil).write(to: control)
            current = nil
            return nil
        } catch {
            if (try? FileManager.default.removeItem(at: control)) != nil
                || !FileManager.default.fileExists(atPath: control.path) { return nil }
            return .failed("Couldn't hand the fans back (\(error.localizedDescription)). "
                           + "They stay manual until Quiver quits.")
        }
    }
}
