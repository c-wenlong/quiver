import Darwin
import Foundation

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
}

/// What the app asks of the fan helper, as JSON in `~/.quiver/fan/control.json`.
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
    /// Whether the Quiver that made the request still runs.
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
        /// The fans would not go manual: release, and leave this request.
        case refused
        /// The request went away or its Quiver quit mid-pass: release.
        case withdrawn
    }

    /// Runs until stopped, a pass a second. A request the fans would not
    /// take is released and not retried until a new one replaces it. A
    /// release that did not stick is retried on the next pass.
    public func run() {
        var holding = false
        var failed: FanControlFile?
        while !shouldStop() {
            if let request = request(), request != failed, let level = request.level,
               let owner = request.owner, ownerAlive(owner) {
                let wanted = { self.request() == request && self.ownerAlive(owner) }
                switch hold(level: underPressure() ? 1 : level, wanted: wanted) {
                case .held:
                    holding = true
                case .refused:
                    failed = request
                    holding = !release()
                case .withdrawn:
                    holding = !release()
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
        guard !fans.isEmpty else { return .refused }
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
    /// stop as soon as the request is withdrawn.
    func takeManual(_ fan: Int, wanted: () -> Bool = { true }) -> Pass {
        let mode = FanKeys.mode(fan, in: smc)
        if smc.write(mode, [1]) { return .held }
        guard let gate = smc.read(FanKeys.unlock) else { return .refused }
        if gate.number != 1 { smc.write(FanKeys.unlock, [1]) }
        var waited: TimeInterval = 0
        while waited < unlockTimeout {
            if shouldStop() || !wanted() { return .withdrawn }
            sleep(0.1)
            waited += 0.1
            if smc.write(mode, [1]) { return .held }
        }
        return .refused
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
            let current = smc.read(mode)?.number
            if current == nil || current == 1 { smc.write(mode, [0]) }
        }
        if let gate = smc.read(FanKeys.unlock), gate.number != 0 {
            smc.write(FanKeys.unlock, [0])
        }
        let fansBack = fans.allSatisfy { smc.read(FanKeys.mode($0, in: smc))?.number != 1 }
        let gateClosed = smc.read(FanKeys.unlock).map { $0.number != 1 } ?? true
        return fansBack && gateClosed
    }
}

/// The `--fan-daemon` mode of Quiver's own executable. The one-time
/// install copies the executable to a root-owned path and has launchd run
/// it as root with this flag; main.swift calls this before the app
/// starts, so the helper never draws a window.
public enum FanHelper {
    public static let flag = "--fan-daemon"
    public static let label = "com.cwenlong.quiver.fan"
    /// Root-owned, so nothing running as the user can swap the binary
    /// launchd starts as root.
    public static let installedExecutable = "/Library/PrivilegedHelperTools/\(label)"
    public static let installedPlist = "/Library/LaunchDaemons/\(label).plist"

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

    /// The launchd job: run as root from boot, restarted if it exits,
    /// steered by the user's control file.
    public static func plist(control: URL) -> Data {
        let job: [String: Any] = [
            "Label": label,
            "ProgramArguments": [installedExecutable, flag, "--control", control.path],
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
    public static func plistLine(control: URL) -> String {
        String(decoding: plist(control: control), as: UTF8.self)
            .split(whereSeparator: \.isNewline)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .joined()
    }

    /// Whether `pid` is a running Quiver: a reused pid that now belongs
    /// to something else must not keep the fans held.
    public static func isQuiver(_ pid: Int32) -> Bool {
        guard kill(pid, 0) == 0 || errno == EPERM else { return false }
        var buffer = [CChar](repeating: 0, count: 4096)
        guard proc_pidpath(pid, &buffer, UInt32(buffer.count)) > 0 else { return false }
        let path = String(decoding: buffer.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
        return (path as NSString).lastPathComponent == "Quiver"
    }

    /// Runs the helper when the arguments ask for it and answers its exit
    /// status; nil means "not the helper, start the app".
    public static func runIfRequested(_ arguments: [String] = CommandLine.arguments) -> Int32? {
        guard arguments.dropFirst().first == flag else { return nil }
        guard let control = parse(arguments) else { return 2 }
        guard getuid() == 0 else {
            FileHandle.standardError.write(Data("fan helper: needs root\n".utf8))
            return 2
        }
        guard let smc = AppleSMC() else { return 1 }
        let stop = StopFlag()
        let sources = [SIGTERM, SIGINT, SIGHUP].map { sig in
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .global())
            source.setEventHandler { stop.set() }
            source.resume()
            return source
        }
        defer { sources.forEach { $0.cancel() } }
        FanHold(
            smc: smc,
            request: { FanControlFile.request(at: control) },
            ownerAlive: isQuiver,
            underPressure: {
                let state = ProcessInfo.processInfo.thermalState
                return state == .serious || state == .critical
            },
            shouldStop: { stop.isSet },
            sleep: { Thread.sleep(forTimeInterval: $0) }
        ).run()
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
