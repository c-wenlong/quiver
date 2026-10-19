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

    /// Copy, ad-hoc sign the copy (it no longer sits in the bundle its
    /// signature sealed), install the job, and (re)start it.
    public static func installCommand(executable: String, plist: String) -> String {
        let tool = shell(FanHelper.installedExecutable), job = shell(FanHelper.installedPlist)
        return "/bin/mkdir -p /Library/PrivilegedHelperTools"
            + " && /usr/bin/install -m 755 -o root -g wheel \(shell(executable)) \(tool)"
            + " && /usr/bin/codesign --force --sign - \(tool)"
            + " && /usr/bin/install -m 644 -o root -g wheel \(shell(plist)) \(job)"
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

    public func install(executable: String, control: URL) async throws {
        let plist = FileManager.default.temporaryDirectory.appending(path: "\(FanHelper.label)-\(UUID().uuidString).plist")
        do {
            try FanHelper.plist(control: control).write(to: plist)
        } catch {
            throw FanHelperInstallError.failed("Couldn't write the helper's launchd job: \(error.localizedDescription)")
        }
        defer { try? FileManager.default.removeItem(at: plist) }
        try await run(Self.script(Self.installCommand(executable: executable, plist: plist.path),
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
    /// How long `starting` may last before the request counts as failed.
    /// Taking the fans on an M4 takes up to about ten seconds.
    static let startTimeout: TimeInterval = 20
    static let notTaken = "The fan helper didn't take the fans."

    private let reader: @Sendable () -> [FanReading]
    private let installer: any FanHelperInstalling
    private let isInstalled: @Sendable () -> Bool
    private let control: URL
    private let executable: String
    private let owner: Int32
    private let clock: @Sendable () -> Date
    private let defaults: UserDefaults
    private var startedAt: Date?

    public init(control: URL,
                reader: @escaping @Sendable () -> [FanReading] = { AppleSMC().map(FanKeys.read) ?? [] },
                installer: any FanHelperInstalling = AdministratorInstaller(),
                isInstalled: @escaping @Sendable () -> Bool = FanControl.helperOnDisk,
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
        helperInstalled = isInstalled()
        level = defaults.object(forKey: Self.levelKey) as? Double ?? 0.5
    }

    /// The helper's launchd job and executable are both in place.
    public nonisolated static func helperOnDisk() -> Bool {
        FileManager.default.fileExists(atPath: FanHelper.installedPlist)
            && FileManager.default.fileExists(atPath: FanHelper.installedExecutable)
    }

    /// Whether a manual speed is being held or about to be.
    public var isHolding: Bool { phase == .starting || phase == .manual }

    /// The speed the current level asks of the first fan, for the label.
    public var targetRPM: Double? { fans.first?.rpm(atLevel: level) }

    /// Read the fans, then settle the phase from what the SMC says:
    /// every fan manual means the helper holds them.
    public func refresh() async {
        let reader = self.reader, isInstalled = self.isInstalled
        let (fans, installed) = await runOffPool { (reader(), isInstalled()) }
        self.fans = fans
        helperInstalled = installed
        loaded = true
        settle()
    }

    private var fansManual: Bool { !fans.isEmpty && fans.allSatisfy(\.isManual) }

    private func settle() {
        switch phase {
        case .starting:
            if fansManual {
                phase = .manual
            } else if let startedAt, clock().timeIntervalSince(startedAt) > Self.startTimeout {
                clearRequest()
                phase = .failed(Self.notTaken)
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

    /// Hold the fans at `level`. The installed helper picks the request up
    /// within a second; the first time, this installs it, which asks for
    /// the password once.
    public func applyManual() async {
        do {
            try FanControlFile(level: level, owner: owner, requested: clock()).write(to: control)
        } catch {
            phase = .failed("Couldn't write \(control.path): \(error.localizedDescription)")
            return
        }
        if !helperInstalled {
            busy = true
            defer { busy = false }
            do {
                try await installer.install(executable: executable, control: control)
                helperInstalled = true
            } catch FanHelperInstallError.cancelled {
                clearRequest()
                phase = .automatic
                return
            } catch {
                clearRequest()
                phase = .failed(error.localizedDescription)
                return
            }
        }
        if phase != .manual {
            phase = .starting
            startedAt = clock()
        }
        await refresh()
    }

    /// Hand the fans back to macOS. The helper sees the empty request
    /// within a second and sets every fan to automatic.
    public func restoreAutomatic() {
        clearRequest()
        phase = .automatic
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
        await refresh()
    }

    private func clearRequest() {
        try? FanControlFile(level: nil).write(to: control)
        startedAt = nil
    }
}
