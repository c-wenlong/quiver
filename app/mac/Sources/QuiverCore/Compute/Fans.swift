import Darwin
import Foundation
import os

/// One fan as the SMC reports it, speeds in rpm.
public struct FanReading: Sendable, Equatable, Identifiable {
    public var index: Int
    public var actual: Double
    public var minimum: Double
    public var maximum: Double
    public var target: Double?
    /// 0 automatic, 1 manual; 3 is thermalmonitord's own "system" mode.
    public var mode: UInt8?

    public var id: Int { index }
    public var isManual: Bool { mode == 1 }

    public init(index: Int, actual: Double, minimum: Double, maximum: Double,
                target: Double? = nil, mode: UInt8? = nil) {
        self.index = index
        self.actual = actual
        self.minimum = minimum
        self.maximum = maximum
        self.target = target
        self.mode = mode
    }

    /// The speed a level from 0 (the fan's minimum) to 1 (its maximum)
    /// asks of this fan. Levels outside that range are clamped, so the
    /// fan is never asked for less than its own floor.
    public func rpm(atLevel level: Double) -> Double {
        minimum + min(max(level, 0), 1) * (maximum - minimum)
    }
}

/// The SMC's fan keys. `F<n>Md` is the mode key up to M4; M5 spells it
/// `F<n>md`. `Ftst` is the gate M3 and M4 need set before they let go of
/// manual mode; a Mac without it takes the mode write directly.
public enum FanKeys {
    public static let count = "FNum"
    public static let unlock = "Ftst"

    public static func actual(_ fan: Int) -> String { "F\(fan)Ac" }
    public static func minimum(_ fan: Int) -> String { "F\(fan)Mn" }
    public static func maximum(_ fan: Int) -> String { "F\(fan)Mx" }
    public static func target(_ fan: Int) -> String { "F\(fan)Tg" }

    /// Whichever spelling of the mode key this Mac answers.
    public static func mode(_ fan: Int, in smc: any SMCAccess) -> String {
        let upper = "F\(fan)Md"
        return smc.read(upper) != nil ? upper : "F\(fan)md"
    }

    /// Every fan the SMC reports, or none on a Mac without fans or when
    /// the SMC does not answer.
    public static func read(_ smc: any SMCAccess) -> [FanReading] {
        guard let count = smc.read(Self.count)?.number, count > 0 else { return [] }
        return (0..<min(Int(count), 8)).compactMap { fan in
            guard let actual = smc.read(Self.actual(fan))?.number,
                  let minimum = smc.read(Self.minimum(fan))?.number,
                  let maximum = smc.read(Self.maximum(fan))?.number, maximum > minimum
            else { return nil }
            let mode = smc.read(Self.mode(fan, in: smc))?.number.map { UInt8(clamping: Int($0)) }
            return FanReading(index: fan, actual: actual, minimum: minimum, maximum: maximum,
                              target: smc.read(Self.target(fan))?.number, mode: mode)
        }
    }

    /// Every fan key, both mode spellings and the Ftst gate as the SMC
    /// reports them (type, size, bytes) or "absent", one line each: what
    /// a new Mac's fans look like, for `--fan-survey` and the helper's log.
    public static func survey(_ smc: any SMCAccess) -> [String] {
        func line(_ key: String) -> String { "\(key): " + (smc.read(key)?.summary ?? "absent") }
        let count = smc.read(Self.count)?.number.map { min(max(Int($0), 0), 8) } ?? 0
        let fans = (0..<count).flatMap { fan in
            [actual(fan), minimum(fan), maximum(fan), target(fan), "F\(fan)Md", "F\(fan)md"].map(line)
        }
        return [line(Self.count)] + fans + [line(Self.unlock)]
    }
}

/// What the root helper last made of a request, as JSON in
/// `FanHelper.statusFile`. It lives in a root-owned folder rather than
/// beside the control file: root must never write into a folder the user
/// owns, where a link could point the write at any file on the Mac.
public struct FanHelperStatus: Codable, Equatable, Sendable {
    public enum State: String, Codable, Sendable {
        /// The helper started and opened the SMC.
        case ready
        /// It holds the fans for `requested`.
        case held
        /// It gave up on `requested`, for `reason`.
        case refused
        /// It can't run at all, for `reason`; not tied to one request.
        case error
    }

    public var state: State
    public var reason: String?
    /// The `requested` stamp and owner of the request this answers.
    public var requested: Double?
    public var owner: Int32?
    public var at: Double
    /// The `FanHelper.version` of the helper that wrote it; nil from a
    /// helper older than 4, which didn't say.
    public var version: Int?

    public init(state: State, reason: String? = nil, request: FanControlFile? = nil, at: Date = .now,
                version: Int? = FanHelper.version) {
        self.state = state
        self.reason = reason
        requested = request?.requested
        owner = request?.owner
        self.at = at.timeIntervalSince1970
        self.version = version
    }

    /// Whether this answers `request`.
    public func answers(_ request: FanControlFile?) -> Bool {
        guard let request, let requested else { return false }
        return requested == request.requested && owner == request.owner
    }

    public static func read(at url: URL = URL(fileURLWithPath: FanHelper.statusFile)) -> FanHelperStatus? {
        guard let data = FanControlFile.boundedRead(url, limit: 4096) else { return nil }
        return try? JSONDecoder().decode(FanHelperStatus.self, from: data)
    }

    /// Written by root, readable by everyone.
    public func write(to url: URL = URL(fileURLWithPath: FanHelper.statusFile)) throws {
        try JSONEncoder().encode(self).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: url.path)
    }
}

/// What the app asks of the fan helper, as JSON in `~/.swe/fan/control.json`.
/// A level holds the fans at that share of their range for as long as the
/// Quiver process `owner` runs; no level (or no file) hands them back to
/// macOS. `requested` stamps each Set speed, so pressing it again after a
/// failure makes a new request the helper tries afresh.
public struct FanControlFile: Codable, Equatable, Sendable {
    public var level: Double?
    public var owner: Int32?
    public var requested: Double?

    public init(level: Double?, owner: Int32? = nil, requested: Date? = nil) {
        self.level = level
        self.owner = owner
        self.requested = requested?.timeIntervalSince1970
    }

    /// The request in the file at `url`, or nil when the file is missing,
    /// oversized, unreadable or asks for nothing: anything unclear means
    /// automatic. The level comes back clamped into 0...1.
    public static func request(at url: URL) -> FanControlFile? {
        guard let data = boundedRead(url, limit: 4096),
              var file = try? JSONDecoder().decode(FanControlFile.self, from: data),
              let level = file.level, level.isFinite, let owner = file.owner, owner > 1
        else { return nil }
        file.level = min(max(level, 0), 1)
        return file
    }

    /// At most `limit` bytes of a regular file, or nil for anything
    /// bigger, a symlink, a FIFO or a missing file. The root helper reads
    /// a file the user can write every second, so it must never follow a
    /// link, block on a pipe, or load an oversized file whole.
    static func boundedRead(_ url: URL, limit: Int) -> Data? {
        let fd = open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard fd >= 0 else { return nil }
        defer { close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_size <= limit else { return nil }
        var buffer = [UInt8](repeating: 0, count: limit + 1)
        let count = read(fd, &buffer, buffer.count)
        guard count >= 0, count <= limit else { return nil }
        return Data(buffer.prefix(count))
    }

    public func write(to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        try JSONEncoder().encode(self).write(to: url, options: .atomic)
    }
}

/// The root helper's loop. It runs for as long as launchd keeps it,
/// holding every fan at the requested level while the requesting Quiver
/// runs, and handing them back to macOS the moment the request goes, the
/// app quits, or the helper is stopped. M4 needs a live root process for
/// a manual speed to stick (thermalmonitord takes the fans back within
/// seconds of it exiting), which is why this is a loop and not a one-off
/// write. After wake the fans are taken again on the next pass.
public struct FanHold {
    public var smc: any SMCAccess
    /// The current request, or nil for automatic.
    public var request: () -> FanControlFile?
    /// Whether the Quiver that made the request still runs, signed the
    /// way the one that installed the helper was (`FanOwnerCheck`).
    public var ownerAlive: (Int32) -> Bool
    /// Whether macOS reports serious or critical thermal pressure. The
    /// fans then run flat out whatever level was asked for, so a low
    /// manual speed can never hold a hot Mac back from cooling.
    public var underPressure: () -> Bool
    public var shouldStop: () -> Bool
    public var sleep: (TimeInterval) -> Void
    /// How long to keep retrying the mode write after setting Ftst.
    /// thermalmonitord lets go after about 3 to 6 seconds on an M4.
    public var unlockTimeout: TimeInterval = 10
    /// One line for the helper's log: keys tried, write results, the gate.
    public var log: (String) -> Void = { _ in }
    /// What became of a request, for the app to show. Called once per
    /// change, not every pass.
    public var report: (FanHelperStatus) -> Void = { _ in }
    /// Called at the top of every pass. The daemon wires it to a
    /// watchdog, so a pass that never finishes — a wedged SMC call —
    /// gets the process relaunched instead of sitting deaf to every
    /// request while looking alive.
    public var beat: () -> Void = {}

    public init(smc: any SMCAccess, request: @escaping () -> FanControlFile?,
                ownerAlive: @escaping (Int32) -> Bool, underPressure: @escaping () -> Bool,
                shouldStop: @escaping () -> Bool, sleep: @escaping (TimeInterval) -> Void) {
        self.smc = smc
        self.request = request
        self.ownerAlive = ownerAlive
        self.underPressure = underPressure
        self.shouldStop = shouldStop
        self.sleep = sleep
    }

    /// What one pass came to.
    enum Pass: Equatable {
        case held
        /// The fans would not go manual, for the reason given: release,
        /// and leave this request.
        case refused(String)
        /// The request went away or its Quiver quit mid-pass: release.
        case withdrawn
    }

    /// Runs until stopped, a pass a second. A request the fans would not
    /// take is released and not retried until a new one replaces it. A
    /// release that did not stick is retried on the next pass.
    public func run() {
        var holding = false
        var failed: FanControlFile?
        // The last request and state reported, so the status file and the
        // log change once per outcome rather than every pass.
        var reported: (FanControlFile, FanHelperStatus.State)?
        func note(_ request: FanControlFile, _ state: FanHelperStatus.State, _ reason: String? = nil) {
            guard reported?.0 != request || reported?.1 != state else { return }
            reported = (request, state)
            log("request \(request.requested ?? 0) from pid \(request.owner ?? 0): \(state.rawValue)"
                + (reason.map { ": \($0)" } ?? ""))
            report(FanHelperStatus(state: state, reason: reason, request: request))
        }
        while !shouldStop() {
            beat()
            if let request = request(), request != failed, let level = request.level, let owner = request.owner {
                if ownerAlive(owner) {
                    let wanted = { self.request() == request && self.ownerAlive(owner) }
                    switch hold(level: underPressure() ? 1 : level, wanted: wanted) {
                    case .held:
                        holding = true
                        note(request, .held)
                    case .refused(let reason):
                        failed = request
                        holding = !release()
                        note(request, .refused, reason)
                    case .withdrawn:
                        holding = !release()
                    }
                } else {
                    if holding { holding = !release() }
                    // Not marked failed, as before: the request is left
                    // for its owner to withdraw, and the app hears why once.
                    note(request, .refused, "The fan helper doesn't see a running Quiver with pid \(owner) "
                         + "signed like the one that installed it.")
                }
            } else if holding {
                holding = !release()
            }
            sleep(1)
        }
        // Stopping: a few tries, so launchd stopping the job never leaves
        // a fan manual over one failed write.
        for _ in 0..<5 where holding {
            holding = !release()
            if holding { sleep(0.2) }
        }
    }

    /// One pass: every fan in manual mode at its share of the level, as
    /// long as `wanted` still holds.
    func hold(level: Double, wanted: () -> Bool = { true }) -> Pass {
        let fans = FanKeys.read(smc)
        guard !fans.isEmpty else { return .refused("The fan helper can't read any fan from the SMC.") }
        for fan in fans {
            if !fan.isManual {
                switch takeManual(fan.index, wanted: wanted) {
                case .held: break
                case let other: return other
                }
            }
            guard wanted() else { return .withdrawn }
            let rpm = fan.rpm(atLevel: level)
            if fan.target.map({ abs($0 - rpm) >= 1 }) ?? true {
                // Some firmware answers a size error yet takes the value,
                // so the result is not checked; the next pass rewrites it.
                smc.write(FanKeys.target(fan.index), SMCValue.float(rpm))
            }
        }
        return .held
    }

    /// The mode write, behind Ftst where the Mac has one. The retries
    /// stop as soon as the request is withdrawn. The value 1 goes in the
    /// mode key's own type and size: a "ui8 " key (every Mac so far) gets
    /// the single byte it always got; a wider key, which the old one-byte
    /// write could never fit, gets 1 the way `SMCValue.number` reads it.
    func takeManual(_ fan: Int, wanted: () -> Bool = { true }) -> Pass {
        let name = "Fan \(fan + 1)"
        let mode = FanKeys.mode(fan, in: smc)
        guard let current = smc.read(mode) else {
            log("\(name): neither F\(fan)Md nor F\(fan)md answers")
            return .refused("\(name) has no mode key the fan helper can read (F\(fan)Md or F\(fan)md).")
        }
        guard let manual = current.encoding(1) else {
            log("\(name): \(mode) is \(current.summary), which the helper can't encode")
            return .refused("\(name)'s mode key \(mode) is '\(current.type)' with \(current.bytes.count) bytes, "
                            + "which the fan helper doesn't know how to write.")
        }
        let first = smc.writeResult(mode, manual)
        log("\(name): \(mode) \(current.summary); write 1 -> \(first)")
        if first == .ok { return .held }
        guard let gate = smc.read(FanKeys.unlock) else {
            log("\(name): Ftst absent, no gate to open")
            return .refused("The SMC refused manual mode for \(name) (\(mode): \(first)), "
                            + "and this Mac has no Ftst gate to open.")
        }
        // Kept for the refusal: "even with Ftst open" only when it opened.
        var opened = SMCWriteResult.ok
        if gate.number != 1 {
            opened = smc.writeResult(FanKeys.unlock, gate.encoding(1) ?? [1])
            log("Ftst: \(gate.summary); write 1 -> \(opened)")
        } else {
            log("Ftst: \(gate.summary), already open")
        }
        var waited: TimeInterval = 0
        var last = first
        while waited < unlockTimeout {
            if shouldStop() || !wanted() { return .withdrawn }
            sleep(0.1)
            waited += 0.1
            last = smc.writeResult(mode, manual)
            if last == .ok {
                log("\(name): \(mode) took manual after \(String(format: "%.1f", waited)) s behind Ftst")
                return .held
            }
        }
        log("\(name): \(mode) still refused after \(Int(unlockTimeout)) s behind Ftst: \(last)")
        guard opened == .ok else {
            return .refused("The SMC refused manual mode for \(name) (\(mode): \(last)), "
                            + "and opening the Ftst gate failed (\(opened)).")
        }
        return .refused("The SMC refused manual mode for \(name) (\(mode): \(last)), even with Ftst open.")
    }

    /// Every fan back to automatic, then the gate closed so thermalmonitord
    /// takes over again. True once no fan reads manual and the gate reads
    /// closed (or the Mac has none).
    @discardableResult
    public func release() -> Bool {
        guard let count = smc.read(FanKeys.count)?.number, count > 0 else { return true }
        let fans = 0..<min(Int(count), 8)
        for fan in fans {
            let mode = FanKeys.mode(fan, in: smc)
            // Mode 3 is thermalmonitord's own; only a manual (or unread)
            // fan needs the write.
            let current = smc.read(mode)
            if current?.number == nil || current?.number == 1 { smc.write(mode, current?.encoding(0) ?? [0]) }
        }
        if let gate = smc.read(FanKeys.unlock), gate.number != 0 {
            smc.write(FanKeys.unlock, gate.encoding(0) ?? [0])
        }
        let fansBack = fans.allSatisfy { smc.read(FanKeys.mode($0, in: smc))?.number != 1 }
        let gateClosed = smc.read(FanKeys.unlock).map { $0.number != 1 } ?? true
        return fansBack && gateClosed
    }
}

/// The `--fan-daemon` mode of the QuiverFanHelper executable that ships
/// in Quiver.app. The one-time install copies it to a root-owned path and
/// has launchd run it as root with this flag. Quiver's own executable
/// still answers the flags (main.swift), for a build without the helper.
public enum FanHelper {
    public static let flag = "--fan-daemon"
    /// Bump when the helper's behaviour changes. It goes into the launchd
    /// job, and an installed job with another version (or none, from
    /// before helpers had one) counts as not installed, so the next Set
    /// speed reinstalls it once instead of steering an old or broken one.
    /// 2: its own executable, after copies of Quiver's crashed in dyld.
    /// 3: the watchdog, so a pass wedged on an SMC call relaunches it.
    /// 4: the owner is checked by code signature, pinned in the job at
    /// install (`FanOwnerPin`), instead of by its executable's name.
    public static let version = 4

    /// How often the watchdog checks the heartbeat, and how long may
    /// pass without a beat before the daemon counts itself wedged and
    /// exits for launchd to relaunch. Generous next to a healthy pass of
    /// about a second; thermalmonitord takes the fans back when the
    /// process dies, so a stuck SMC call must end the process, not be
    /// waited out.
    static let watchdogTick: TimeInterval = 10
    static let watchdogTimeout: TimeInterval = 30
    static let versionFlag = "--helper-version"
    /// The helper inside the app bundle, where build-app.sh puts it.
    public static let bundledName = "QuiverFanHelper"
    public static let label = "com.cwenlong.quiver.fan"
    /// Root-owned, so nothing running as the user can swap the binary
    /// launchd starts as root.
    public static let installedExecutable = "/Library/PrivilegedHelperTools/\(label)"
    public static let installedPlist = "/Library/LaunchDaemons/\(label).plist"
    /// Where the helper says what it made of the last request. /var/run is
    /// root's and cleared at boot; the app only reads it.
    public static let statusFile = "/var/run/\(label).json"
    /// Prints the SMC's fan keys and exits. Reads only, so it needs no
    /// root: `Quiver --fan-survey` on a new Mac shows what the helper
    /// will find there.
    public static let surveyFlag = "--fan-survey"

    /// The helper's log: the unified log (`log show --predicate
    /// 'subsystem == "com.cwenlong.quiver.fan"'`) and stderr, which shows
    /// when the helper is run by hand.
    static func log(_ line: String) {
        logger.notice("\(line, privacy: .public)")
        FileHandle.standardError.write(Data("fan helper: \(line)\n".utf8))
    }

    private static let logger = Logger(subsystem: label, category: "helper")

    /// The control file's path; nil unless `arguments` asks for the helper.
    public static func parse(_ arguments: [String]) -> URL? {
        guard arguments.dropFirst().first == flag else { return nil }
        var rest = arguments.dropFirst(2).makeIterator()
        while let argument = rest.next() {
            if argument == "--control", let path = rest.next(), path.hasPrefix("/") {
                return URL(fileURLWithPath: path)
            }
        }
        return nil
    }

    /// What launchd runs, which `helperState` also compares against.
    /// `owner` pins whose requests the helper takes.
    public static func programArguments(control: URL, owner: FanOwnerPin) -> [String] {
        [installedExecutable, flag, "--control", control.path] + owner.arguments + [versionFlag, String(version)]
    }

    /// The version an installed job names, 0 for a job from before jobs
    /// carried one, or nil when the arguments aren't a fan helper's.
    public static func version(in arguments: [String]) -> Int? {
        guard arguments.dropFirst().first == flag else { return nil }
        guard let at = arguments.firstIndex(of: versionFlag) else { return 0 }
        return arguments.indices.contains(at + 1) ? Int(arguments[at + 1]) : nil
    }

    /// The helper to install: QuiverFanHelper next to Quiver's executable,
    /// or Quiver's own when it isn't there (a `swift run` without the
    /// bundle), which answers the same flags.
    public static func bundledExecutable(appExecutable: String?) -> String {
        let app = appExecutable ?? CommandLine.arguments[0]
        let helper = (app as NSString).deletingLastPathComponent + "/" + bundledName
        return FileManager.default.isExecutableFile(atPath: helper) ? helper : app
    }

    /// The launchd job: run as root from boot, restarted if it exits,
    /// steered by the user's control file.
    public static func plist(control: URL, owner: FanOwnerPin) -> Data {
        let job: [String: Any] = [
            "Label": label,
            "ProgramArguments": programArguments(control: control, owner: owner),
            "RunAtLoad": true,
            "KeepAlive": true,
            "ProcessType": "Background",
        ]
        return (try? PropertyListSerialization.data(fromPropertyList: job, format: .xml, options: 0)) ?? Data()
    }

    /// The same job on one line, for the install script to write as root
    /// itself: a file prepared beforehand could be swapped by anything
    /// running as the user while the password prompt is up. Whitespace
    /// between tags means nothing to a plist; no value holds a line break
    /// (`parse` and the installer only take absolute paths without one).
    public static func plistLine(control: URL, owner: FanOwnerPin) -> String {
        String(decoding: plist(control: control, owner: owner), as: UTF8.self)
            .split(whereSeparator: \.isNewline)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .joined()
    }

    /// Runs the helper when the arguments ask for it and answers its exit
    /// status; nil means "not the helper, start the app".
    public static func runIfRequested(_ arguments: [String] = CommandLine.arguments) -> Int32? {
        if arguments.dropFirst().first == surveyFlag {
            guard let smc = AppleSMC() else {
                FileHandle.standardError.write(Data("Couldn't open the AppleSMC.\n".utf8))
                return 1
            }
            print(FanKeys.survey(smc).joined(separator: "\n"))
            return 0
        }
        guard arguments.dropFirst().first == flag else { return nil }
        guard let control = parse(arguments) else { return 2 }
        // No pin, or one that doesn't compile, means a job written by
        // something other than this version's install: take no one's
        // requests rather than anyone's.
        guard let pin = FanOwnerPin.parse(arguments), let owner = FanOwnerCheck(pin: pin) else {
            FileHandle.standardError.write(Data("fan helper: no valid owner signature in the arguments\n".utf8))
            return 2
        }
        guard getuid() == 0 else {
            FileHandle.standardError.write(Data("fan helper: needs root\n".utf8))
            return 2
        }
        func report(_ status: FanHelperStatus) {
            do { try status.write() } catch { log("couldn't write \(statusFile): \(error.localizedDescription)") }
        }
        log("started (pid \(getpid()), version \(version)), control \(control.path), owner \(pin.requirement)")
        // A pass runs on the one thread with no timeout around the SMC
        // calls, and one that hangs leaves the daemon deaf to every
        // request while looking alive to launchd. The watchdog exits so
        // KeepAlive relaunches it with a fresh connection.
        let heartbeat = Heartbeat()
        let watchdog = DispatchSource.makeTimerSource(queue: .global())
        watchdog.schedule(deadline: .now() + watchdogTick, repeating: watchdogTick)
        watchdog.setEventHandler {
            if heartbeat.silentFor > watchdogTimeout {
                log("no beat in \(Int(watchdogTimeout)) s; exiting so launchd restarts the helper")
                _exit(1)
            }
        }
        watchdog.resume()
        defer { watchdog.cancel() }
        heartbeat.beat()
        guard let smc = AppleSMC() else {
            log("couldn't open the AppleSMC")
            report(FanHelperStatus(state: .error, reason: "The fan helper couldn't open the SMC."))
            return 1
        }
        heartbeat.beat()
        FanKeys.survey(smc).forEach { log($0) }
        report(FanHelperStatus(state: .ready))
        heartbeat.beat()
        let stop = StopFlag()
        let sources = [SIGTERM, SIGINT, SIGHUP].map { sig in
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .global())
            source.setEventHandler { stop.set() }
            source.resume()
            return source
        }
        defer { sources.forEach { $0.cancel() } }
        var hold = FanHold(
            smc: smc,
            request: { FanControlFile.request(at: control) },
            ownerAlive: owner.isOwner,
            underPressure: {
                let state = ProcessInfo.processInfo.thermalState
                return state == .serious || state == .critical
            },
            shouldStop: { stop.isSet },
            sleep: { Thread.sleep(forTimeInterval: $0) }
        )
        hold.log = log
        hold.report = report
        hold.beat = { heartbeat.beat() }
        hold.run()
        log("stopped")
        return 0
    }
}

/// Set from a signal handler's queue, read from the helper's loop.
private final class StopFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false

    var isSet: Bool { lock.withLock { value } }
    func set() { lock.withLock { value = true } }
}

/// The last moment the helper got through a beat (a pass, a startup
/// step); the watchdog reads it off the loop's thread. Measured on the
/// uptime clock, which cannot jump like Date() (a clock set forward
/// must not kill a healthy helper) and pauses while the Mac sleeps,
/// like the passes do.
private final class Heartbeat: @unchecked Sendable {
    private let lock = NSLock()
    private var last = ProcessInfo.processInfo.systemUptime
    func beat() { lock.withLock { last = ProcessInfo.processInfo.systemUptime } }
    var silentFor: TimeInterval {
        lock.withLock { ProcessInfo.processInfo.systemUptime - last }
    }
}
