import CryptoKit
import Darwin
import Foundation
import Observation

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
    public init() {}

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

    /// Copy the executable, check the copy is byte for byte the one
    /// hashed before the prompt (anything running as the user could swap
    /// the bundle's copy while the prompt is up), ad-hoc sign it (it no
    /// longer sits in the bundle its signature sealed), write the job from
    /// trusted text as root, and (re)start it.
    public static func installCommand(executable: String, sha256: String, control: URL) -> String {
        let tool = shell(FanHelper.installedExecutable), job = shell(FanHelper.installedPlist)
        return "/bin/mkdir -p /Library/PrivilegedHelperTools"
            + " && /usr/bin/install -m 755 -o root -g wheel \(shell(executable)) \(tool)"
            + " && { [ \"$(/usr/bin/shasum -a 256 \(tool) | /usr/bin/cut -d ' ' -f 1)\" = \(shell(sha256)) ]"
            + " || { /bin/rm -f \(tool); echo 'The Quiver executable changed during install.' >&2; exit 1; }; }"
            + " && /usr/bin/codesign --force --sign - \(tool)"
            + " && /usr/bin/printf '%s' \(shell(FanHelper.plistLine(control: control))) > \(job)"
            + " && /usr/sbin/chown root:wheel \(job) && /bin/chmod 644 \(job)"
            // A reinstall unloads the old job first; launchd needs a beat
            // before the label can load again.
            + " && { /bin/launchctl bootout system/\(FanHelper.label) 2>/dev/null && /bin/sleep 1; true; }"
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

    public func install(executable: String, control: URL) async throws {
        guard !control.path.contains(where: \.isNewline), !executable.contains(where: \.isNewline) else {
            throw FanHelperInstallError.failed("Quiver's paths can't hold a line break for the fan helper.")
        }
        guard let hash = await runOffPool({ Self.sha256(of: executable) }) else {
            throw FanHelperInstallError.failed("Couldn't read \(executable).")
        }
        try await run(Self.script(Self.installCommand(executable: executable, sha256: hash, control: control),
                                  prompt: "Quiver wants to install a helper that sets the fan speed. "
                                      + "You'll only be asked once."))
    }

    public func uninstall() async throws {
        try await run(Self.script(Self.uninstallCommand, prompt: "Quiver wants to remove its fan helper."))
    }

    private func run(_ script: String) async throws {
        let (status, error) = await runOffPool { () -> (Int32, String) in
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
    public private(set) var phase: Phase = .automatic
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

    public static let levelKey = "fanManualLevel"

    /// The optional rule: above `threshold` °C on the CPU, hold the fans
    /// at `level` until it falls `thresholdHysteresis` below. Only while
    /// Quiver runs, like any manual speed.
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
        }
    }

    /// Whether the speed being held is the threshold's, not the user's.
    public private(set) var heldByThreshold = false

    public static let thresholdEnabledKey = "fanThresholdEnabled"
    public static let thresholdKey = "fanThresholdCelsius"
    public static let thresholdRange = 50.0...100.0
    public static let defaultThreshold = 80.0
    /// How far below the threshold the CPU must fall before the fans go
    /// back, so a reading hovering at the line does not flap them.
    public static let thresholdHysteresis = 5.0

    /// Where the fans go back to macOS.
    public var releaseBelow: Double { threshold - Self.thresholdHysteresis }
    /// How long `starting` may last before the request counts as failed.
    /// Taking the fans on an M4 takes up to about ten seconds.
    static let startTimeout: TimeInterval = 20
    static let notTaken = "The fan helper didn't take the fans."

    private let reader: @Sendable () -> [FanReading]
    private let installer: any FanHelperInstalling
    private let isInstalled: @Sendable (URL) -> Bool
    private let control: URL
    private let executable: String
    private let owner: Int32
    private let clock: @Sendable () -> Date
    private let defaults: UserDefaults
    private var startedAt: Date?

    public init(control: URL,
                reader: @escaping @Sendable () -> [FanReading] = { AppleSMC().map(FanKeys.read) ?? [] },
                installer: any FanHelperInstalling = AdministratorInstaller(),
                isInstalled: @escaping @Sendable (URL) -> Bool = FanControl.helperReady,
                executable: String = Bundle.main.executablePath ?? CommandLine.arguments[0],
                owner: Int32 = getpid(),
                clock: @escaping @Sendable () -> Date = { .now },
                defaults: UserDefaults = .standard) {
        self.control = control
        self.reader = reader
        self.installer = installer
        self.isInstalled = isInstalled
        self.executable = executable
        self.owner = owner
        self.clock = clock
        self.defaults = defaults
        helperInstalled = false
        level = defaults.object(forKey: Self.levelKey) as? Double ?? 0.5
        thresholdEnabled = defaults.bool(forKey: Self.thresholdEnabledKey)
        threshold = defaults.object(forKey: Self.thresholdKey) as? Double ?? Self.defaultThreshold
    }

    /// The helper is installed, steered by this control file, and loaded:
    /// files left by a half-finished install, or a job another account
    /// installed for its own file, count as not installed, so Set speed
    /// reinstalls rather than waits on a helper that will never come.
    public nonisolated static func helperReady(control: URL) -> Bool {
        guard FileManager.default.isExecutableFile(atPath: FanHelper.installedExecutable),
              let data = FileManager.default.contents(atPath: FanHelper.installedPlist),
              let job = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              let arguments = job["ProgramArguments"] as? [String],
              arguments == [FanHelper.installedExecutable, FanHelper.flag, "--control", control.path]
        else { return false }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = ["print", "system/\(FanHelper.label)"]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        guard (try? process.run()) != nil else { return false }
        process.waitUntilExit()
        return process.terminationStatus == 0
    }

    /// Whether a manual speed is being held or about to be.
    public var isHolding: Bool { phase == .starting || phase == .manual }

    /// The speed the current level asks of the first fan, for the label.
    public var targetRPM: Double? { fans.first?.rpm(atLevel: level) }

    /// Read the fans, then settle the phase from what the SMC says:
    /// every fan manual means the helper holds them.
    public func refresh() async {
        let reader = self.reader
        fans = await runOffPool { reader() }
        // The install check spawns launchctl, so it runs on the first read
        // and after a request the helper never took, not every tick.
        if installUnchecked {
            let isInstalled = self.isInstalled, control = self.control
            helperInstalled = await runOffPool { isInstalled(control) }
            installUnchecked = false
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
            } else if let startedAt, clock().timeIntervalSince(startedAt) > Self.startTimeout {
                phase = clearRequest() ?? .failed(Self.notTaken)
                installUnchecked = true
            }
        case .manual:
            // macOS took them back (after sleep, say): the helper takes
            // them again on its next pass, so wait rather than give up.
            if !fansManual {
                phase = .starting
                startedAt = clock()
            }
        case .automatic, .failed:
            break
        }
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
    private func hold() async {
        do {
            try FanControlFile(level: level, owner: owner, requested: clock()).write(to: control)
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
        if phase != .manual {
            phase = .starting
            startedAt = clock()
        }
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
            case .installed: break
            case .cancelled: return
            case .failed(let message):
                phase = .failed(message)
                return
            }
        }
        thresholdEnabled = true
    }

    /// The rule, run on each trend tick with the CPU's temperature. It
    /// takes the fans only from Automatic (a speed the user set wins) and
    /// only once the helper is installed; it gives them back once the CPU
    /// is `thresholdHysteresis` below the line, or as soon as anything
    /// else (the user, a failure) has moved them off its speed.
    public func checkTemperature(_ cpu: Double?) async {
        guard thresholdEnabled, let cpu else { return }
        if heldByThreshold {
            if !isHolding {
                heldByThreshold = false
            } else if cpu <= releaseBelow {
                restoreAutomatic()
            }
            return
        }
        guard phase == .automatic, cpu >= threshold else { return }
        if installUnchecked { await refresh() }
        guard helperInstalled else { return }
        await hold()
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
        do {
            try FanControlFile(level: nil).write(to: control)
            return nil
        } catch {
            if (try? FileManager.default.removeItem(at: control)) != nil
                || !FileManager.default.fileExists(atPath: control.path) { return nil }
            return .failed("Couldn't hand the fans back (\(error.localizedDescription)). "
                           + "They stay manual until Quiver quits.")
        }
    }
}
