import Foundation
import Testing
@testable import QuiverCore

/// Temperatures and fans: the sensor fold, the SMC value codec, the root
/// helper's hold loop against a fake SMC, and the popover's model. Fakes
/// only; nothing here touches the real SMC.
@Suite struct FanTests {
    // MARK: Temperatures

    @Test func theDieSensorsAverageAndJunkSensorsDrop() {
        let reading = TemperatureReading.summarise([
            ("PMU tdie1", 50), ("PMU tdie2", 60), ("PMU tdie2", 70),
            ("PMU tdev1", -9202.9), ("PMU tcal", 51.8),
            ("gas gauge battery", 34), ("gas gauge battery", 36),
        ])
        #expect(reading == TemperatureReading(cpu: 60, hottest: 70, battery: 35))
    }

    @Test func noKnownSensorMeansNoReading() {
        #expect(TemperatureReading.summarise([]) == nil)
        #expect(TemperatureReading.summarise([("PMU tdev1", -9000), ("NAND CH0 temp", 45)]) == nil)
        #expect(TemperatureReading.summarise([("gas gauge battery", 30)])?.cpu == nil)
    }

    // MARK: SMC values

    @Test func smcNumbersDecodeByType() {
        #expect(SMCValue(type: "flt ", bytes: SMCValue.float(2317)).number == 2317)
        #expect(SMCValue(type: "ui8 ", bytes: [2]).number == 2)
        #expect(SMCValue(type: "ui16", bytes: [0x01, 0x02]).number == 258)
        #expect(SMCValue(type: "ui32", bytes: [0, 0, 1, 0]).number == 256)
        #expect(SMCValue(type: "flt ", bytes: [1]).number == nil)
        #expect(SMCValue(type: "ch8*", bytes: [65]).number == nil)
    }

    @Test func fourCharacterCodesRoundTrip() {
        #expect(AppleSMC.fourCC("F0Ac") == 0x4630_4163)
        #expect(AppleSMC.string(0x4630_4163) == "F0Ac")
        #expect(AppleSMC.fourCC("TOO LONG") == nil)
    }

    // MARK: Fan readings

    @Test func aLevelMapsOntoTheFansOwnRange() {
        let fan = FanReading(index: 0, actual: 2400, minimum: 2000, maximum: 6000)
        #expect(fan.rpm(atLevel: 0) == 2000)
        #expect(fan.rpm(atLevel: 0.5) == 4000)
        #expect(fan.rpm(atLevel: 1) == 6000)
        // Never below the floor, never above the top.
        #expect(fan.rpm(atLevel: -1) == 2000)
        #expect(fan.rpm(atLevel: 7) == 6000)
    }

    @Test func fansReadFromTheSMC() {
        let smc = FakeSMC.twoFans()
        let fans = FanKeys.read(smc)
        #expect(fans.map(\.index) == [0, 1])
        #expect(fans[0] == FanReading(index: 0, actual: 2319, minimum: 2317, maximum: 7826,
                                      target: 2317, mode: 0))
        #expect(FanKeys.read(FakeSMC()).isEmpty)
    }

    @Test func m5SpellsTheModeKeyInLowerCase() {
        let smc = FakeSMC.twoFans()
        smc.rename("F0Md", to: "F0md")
        #expect(FanKeys.mode(0, in: smc) == "F0md")
        #expect(FanKeys.mode(1, in: smc) == "F1Md")
        #expect(FanKeys.read(smc)[0].mode == 0)
    }

    // MARK: The hold loop

    private func ask(_ level: Double, owner: Int32 = 77, at seconds: Double = 1) -> FanControlFile {
        FanControlFile(level: level, owner: owner, requested: Date(timeIntervalSince1970: seconds))
    }

    /// A loop that reads one request per pass, then one empty pass, then
    /// stops.
    private func hold(_ smc: FakeSMC, _ requests: [FanControlFile?],
                      ownerAlive: @escaping (Int32) -> Bool = { _ in true },
                      underPressure: Bool = false) -> FanHold {
        let counter = Counter()
        var hold = FanHold(
            smc: smc,
            request: { counter.passes < requests.count ? requests[counter.passes] : nil },
            ownerAlive: ownerAlive,
            underPressure: { underPressure },
            shouldStop: { counter.passes > requests.count },
            sleep: { seconds in if seconds >= 1 { counter.passes += 1 } })
        hold.unlockTimeout = 1
        return hold
    }

    @Test func aMacThatTakesTheModeWriteHoldsAtTheLevelThenLetsGo() {
        let smc = FakeSMC.twoFans(gate: false)
        var targets: [Double] = []
        var manualWhileHolding = false
        smc.onWrite = { key, bytes in
            if key == "F0Tg" { targets.append(SMCValue(type: "flt ", bytes: bytes).number ?? 0) }
            if key == "F1Tg" { manualWhileHolding = smc.number("F0Md") == 1 && smc.number("F1Md") == 1 }
        }
        hold(smc, [ask(0.5), ask(0.5)]).run()
        // Halfway between 2317 and 7826, written once and not rewritten.
        #expect(targets == [5071.5])
        #expect(manualWhileHolding)
        // The request went away: both fans automatic again.
        #expect(smc.number("F0Md") == 0 && smc.number("F1Md") == 0)
    }

    @Test func m4NeedsFtstAndRetriesUntilThermalmonitordLetsGo() {
        let smc = FakeSMC.twoFans()
        // The mode write fails until the gate is open and a few retries
        // have passed, as thermalmonitord takes a few seconds to let go.
        smc.refuseModeWrites = 5
        var sawManual = false
        smc.onWrite = { key, _ in if key == "F1Tg" { sawManual = smc.number("F0Md") == 1 } }
        hold(smc, [ask(0.5)]).run()
        #expect(sawManual)
        // Gate closed again once the fans are released.
        #expect(smc.number("Ftst") == 0)
        #expect(smc.number("F0Md") == 0)
    }

    @Test func aRequestTheFansRefuseIsNotRetriedUntilANewOne() {
        let smc = FakeSMC.twoFans()
        smc.refuseModeWrites = .max
        var gateOpens = 0
        smc.onWrite = { key, bytes in if key == "Ftst" && bytes == [1] { gateOpens += 1 } }
        hold(smc, [ask(0.5), ask(0.5), ask(0.5)]).run()
        #expect(gateOpens == 1)
        #expect(smc.number("Ftst") == 0)
        // Pressing Set speed again stamps a new request, which is tried.
        gateOpens = 0
        hold(smc, [ask(0.5, at: 1), ask(0.5, at: 1), ask(0.5, at: 2)]).run()
        #expect(gateOpens == 2)
    }

    @Test func noRequestOrAGoneOwnerNeverTouchesTheFans() {
        let smc = FakeSMC.twoFans(gate: false)
        var writes: [String] = []
        smc.onWrite = { key, _ in writes.append(key) }
        hold(smc, [nil, nil]).run()
        hold(smc, [ask(1), ask(1)], ownerAlive: { _ in false }).run()
        #expect(writes.isEmpty)
    }

    @Test func theOwnerQuittingHandsTheFansBack() {
        let smc = FakeSMC.twoFans(gate: false)
        let alive = Flag(true)
        var modeAfterQuit: Double?
        let counter = Counter()
        smc.onWrite = { key, _ in if key == "F0Md" { modeAfterQuit = smc.number("F0Md") } }
        hold(smc, [ask(1), ask(1), ask(1), ask(1)], ownerAlive: { _ in
            counter.passes += 1
            if counter.passes > 2 { alive.value = false }
            return alive.value
        }).run()
        #expect(modeAfterQuit == 0)
        #expect(smc.number("F0Md") == 0)
    }

    @Test func thermalPressureRunsTheFansFlatOut() {
        let smc = FakeSMC.twoFans(gate: false)
        var target: Double?
        smc.onWrite = { key, bytes in if key == "F0Tg" { target = SMCValue(type: "flt ", bytes: bytes).number } }
        hold(smc, [ask(0)], underPressure: true).run()
        #expect(target == 7826)
    }

    @Test func aReleaseThatDidNotStickIsRetried() {
        let smc = FakeSMC.twoFans(gate: false)
        smc.refuseReleaseWrites = 1
        var releaseWrites = 0
        smc.onWrite = { key, bytes in if key == "F0Md" && bytes == [0] { releaseWrites += 1 } }
        hold(smc, [ask(1), nil, nil]).run()
        #expect(releaseWrites == 1)
        #expect(smc.number("F0Md") == 0 && smc.number("F1Md") == 0)
    }

    @Test func automaticWhileUnlockingStopsTheRetries() {
        let smc = FakeSMC.twoFans()
        smc.refuseModeWrites = .max
        let reads = Counter()
        var targets = 0
        smc.onWrite = { key, _ in if key.hasSuffix("Tg") { targets += 1 } }
        let counter = Counter()
        let request = ask(1)
        var hold = FanHold(
            smc: smc,
            // The request is there for the pass, then withdrawn after a
            // few retries' worth of reads.
            request: { reads.passes += 1; return counter.passes == 0 && reads.passes < 4 ? request : nil },
            ownerAlive: { _ in true }, underPressure: { false },
            shouldStop: { counter.passes > 1 },
            sleep: { seconds in if seconds >= 1 { counter.passes += 1 } })
        hold.unlockTimeout = 60
        hold.run()
        // Far fewer than the 600 retries the timeout would allow.
        #expect(reads.passes < 10)
        #expect(targets == 0)
        #expect(smc.number("Ftst") == 0)
    }

    @Test func noFansMeansNothingToHold() {
        let smc = FakeSMC()
        hold(smc, [ask(1)]).run()
        #expect(FanKeys.read(smc).isEmpty)
    }

    // MARK: The helper's arguments and install

    @Test func helperArgumentsParse() {
        #expect(FanHelper.parse(["Quiver", "--fan-daemon", "--control", "/tmp/c.json"])?.path == "/tmp/c.json")
        #expect(FanHelper.parse(["Quiver", "--fan-daemon", "--control", "relative.json"]) == nil)
        #expect(FanHelper.parse(["Quiver", "--fan-daemon"]) == nil)
        #expect(FanHelper.parse(["Quiver", "--control", "/tmp/c.json"]) == nil)
        #expect(FanHelper.runIfRequested(["Quiver", "-swePath", "/x"]) == nil)
        #expect(FanHelper.runIfRequested(["Quiver", "--fan-daemon"]) == 2)
    }

    @Test func theLaunchdJobRunsTheRootOwnedCopyAndStaysUp() throws {
        let data = FanHelper.plist(control: URL(fileURLWithPath: "/Users/me/.quiver/fan/control.json"))
        let job = try #require(try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any])
        #expect(job["Label"] as? String == "com.cwenlong.quiver.fan")
        #expect(job["ProgramArguments"] as? [String] == [
            "/Library/PrivilegedHelperTools/com.cwenlong.quiver.fan", "--fan-daemon",
            "--control", "/Users/me/.quiver/fan/control.json"])
        #expect(job["KeepAlive"] as? Bool == true)
        #expect(job["RunAtLoad"] as? Bool == true)
    }

    @Test func theInstallScriptChecksTheCopyAndWritesTheJobAsRoot() throws {
        let command = AdministratorInstaller.installCommand(
            executable: "/Users/o'neil/Apps/Quiver.app/Contents/MacOS/Quiver", sha256: "abc123",
            control: URL(fileURLWithPath: "/Users/o'neil/.quiver/fan/control.json"))
        #expect(command.contains(#"/usr/bin/install -m 755 -o root -g wheel '/Users/o'\''neil/Apps/Quiver.app/Contents/MacOS/Quiver' '/Library/PrivilegedHelperTools/com.cwenlong.quiver.fan'"#))
        // The hash is checked on the root-owned copy, before re-signing
        // changes its bytes.
        let hash = try #require(command.range(of: "/usr/bin/shasum -a 256 '/Library/PrivilegedHelperTools/"))
        let sign = try #require(command.range(of: "/usr/bin/codesign --force --sign -"))
        #expect(hash.lowerBound < sign.lowerBound)
        #expect(command.contains("= 'abc123' ]"))
        // No file from the user's side becomes the job: root writes it.
        #expect(command.contains("/usr/bin/printf '%s' '<?xml"))
        #expect(command.contains(#"<string>/Users/o'\''neil/.quiver/fan/control.json</string>"#))
        #expect(command.contains("> '/Library/LaunchDaemons/com.cwenlong.quiver.fan.plist'"))
        #expect(!command.contains("\n"))
        #expect(command.hasSuffix("/bin/launchctl bootstrap system '/Library/LaunchDaemons/com.cwenlong.quiver.fan.plist'"))
        #expect(AdministratorInstaller.uninstallCommand.contains("/bin/rm -f '/Library/LaunchDaemons/"))
        #expect(AdministratorInstaller.script(#"echo "a\b" 'c'"#, prompt: #"Say "hi""#)
            == #"do shell script "echo \"a\\b\" 'c'" with prompt "Say \"hi\"" with administrator privileges"#)
    }

    @Test func theOneLineJobIsTheSameJob() throws {
        let control = URL(fileURLWithPath: "/Users/me/.quiver/fan/control.json")
        let line = FanHelper.plistLine(control: control)
        #expect(!line.contains("\n"))
        let parsed = try PropertyListSerialization.propertyList(from: Data(line.utf8), format: nil) as? NSDictionary
        let original = try PropertyListSerialization.propertyList(from: FanHelper.plist(control: control),
                                                                  format: nil) as? NSDictionary
        #expect(parsed == original)
    }

    @Test func theHashIsSHA256OfTheFile() throws {
        let url = FileManager.default.temporaryDirectory.appending(path: "hash-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: url) }
        try Data("abc".utf8).write(to: url)
        #expect(AdministratorInstaller.sha256(of: url.path)
            == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        #expect(AdministratorInstaller.sha256(of: "/nonexistent/\(UUID().uuidString)") == nil)
    }

    @Test func onlyAQuiverProcessCountsAsTheOwner() {
        // The test runner is not named Quiver; a pid nothing has is gone.
        #expect(!FanHelper.isQuiver(getpid()))
        #expect(!FanHelper.isQuiver(Int32.max))
    }

    @Test func theControlFileRoundTripsAndAnythingOddMeansAutomatic() throws {
        let folder = FileManager.default.temporaryDirectory.appending(path: "fan-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: folder) }
        let url = folder.appending(path: "fan/control.json")
        #expect(FanControlFile.request(at: url) == nil)
        try ask(0.25).write(to: url)
        #expect(FanControlFile.request(at: url) == ask(0.25))
        try ask(4).write(to: url)
        #expect(FanControlFile.request(at: url)?.level == 1)
        try FanControlFile(level: 0.5).write(to: url)
        #expect(FanControlFile.request(at: url) == nil)
        try FanControlFile(level: nil).write(to: url)
        #expect(FanControlFile.request(at: url) == nil)
        try Data("not json".utf8).write(to: url)
        #expect(FanControlFile.request(at: url) == nil)
    }

    @Test func theRootHelperNeverReadsBigFilesLinksOrPipes() throws {
        let folder = FileManager.default.temporaryDirectory.appending(path: "fan-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let good = folder.appending(path: "good.json")
        try ask(0.5).write(to: good)
        // Oversized, even when it starts with a valid request.
        let big = folder.appending(path: "big.json")
        var data = try JSONEncoder().encode(ask(0.5))
        data.append(Data(repeating: 0x20, count: 5000))
        try data.write(to: big)
        #expect(FanControlFile.request(at: big) == nil)
        // A link to a good file is still refused.
        let link = folder.appending(path: "link.json")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: good)
        #expect(FanControlFile.request(at: link) == nil)
        // A FIFO answers at once instead of blocking the loop.
        let fifo = folder.appending(path: "fifo.json")
        #expect(mkfifo(fifo.path, 0o600) == 0)
        #expect(FanControlFile.request(at: fifo) == nil)
        #expect(FanControlFile.request(at: good) == ask(0.5))
    }

    // MARK: The popover's model

    @MainActor
    private func control(_ smc: FakeSMC, installer: FakeInstaller,
                         clock: Box<Date> = Box(Date(timeIntervalSince1970: 1_800_000_000))) -> (FanControl, URL) {
        let url = FileManager.default.temporaryDirectory
            .appending(path: "fan-\(UUID().uuidString)/control.json")
        let control = FanControl(control: url, reader: { FanKeys.read(smc) }, installer: installer,
                                 isInstalled: { _ in installer.checks += 1; return installer.installed.value },
                                 executable: "/Apps/Quiver", owner: 77, clock: { clock.value },
                                 defaults: UserDefaults(suiteName: "fan-\(UUID().uuidString)")!)
        return (control, url)
    }

    @MainActor @Test func theFirstSpeedInstallsTheHelperThenOnlyTheFileChanges() async {
        let smc = FakeSMC.twoFans()
        let installer = FakeInstaller()
        let (fans, url) = control(smc, installer: installer)
        #expect(!fans.helperInstalled)
        fans.level = 0.75
        await fans.applyManual()
        #expect(installer.installs == 1)
        #expect(installer.lastControl == url)
        #expect(fans.helperInstalled)
        #expect(FanControlFile.request(at: url)?.level == 0.75)
        #expect(FanControlFile.request(at: url)?.owner == 77)
        #expect(fans.phase == .starting)
        // The helper took the fans.
        smc.set("F0Md", 1); smc.set("F1Md", 1)
        await fans.refresh()
        #expect(fans.phase == .manual)
        // A new level reaches the helper through the file alone.
        fans.level = 1
        await fans.applyManual()
        #expect(installer.installs == 1)
        #expect(FanControlFile.request(at: url)?.level == 1)
        #expect(fans.phase == .manual)
        fans.restoreAutomatic()
        #expect(FanControlFile.request(at: url) == nil)
        #expect(fans.phase == .automatic)
    }

    @MainActor @Test func cancellingThePasswordPromptLeavesTheFansAutomatic() async {
        let installer = FakeInstaller()
        installer.error = .cancelled
        let (fans, url) = control(FakeSMC.twoFans(), installer: installer)
        await fans.applyManual()
        #expect(fans.phase == .automatic)
        #expect(!fans.helperInstalled)
        #expect(FanControlFile.request(at: url) == nil)
    }

    @MainActor @Test func aFailedInstallSaysWhy() async {
        let installer = FakeInstaller()
        installer.error = .failed("launchctl said no")
        let (fans, url) = control(FakeSMC.twoFans(), installer: installer)
        await fans.applyManual()
        #expect(fans.phase == .failed("launchctl said no"))
        #expect(FanControlFile.request(at: url) == nil)
    }

    @MainActor @Test func startingTooLongGivesUp() async {
        let clock = Box(Date(timeIntervalSince1970: 1_800_000_000))
        let installer = FakeInstaller()
        installer.installed.value = true
        let (fans, url) = control(FakeSMC.twoFans(), installer: installer, clock: clock)
        await fans.refresh()
        await fans.applyManual()
        #expect(installer.installs == 0)
        #expect(fans.phase == .starting)
        clock.value = clock.value.addingTimeInterval(FanControl.startTimeout + 1)
        await fans.refresh()
        #expect(fans.phase == .failed(FanControl.notTaken))
        #expect(FanControlFile.request(at: url) == nil)
        // The helper may be gone or half installed: look again, so the
        // next Set speed can reinstall it.
        let checks = installer.checks
        await fans.refresh()
        #expect(installer.checks == checks + 1)
        await fans.refresh()
        #expect(installer.checks == checks + 1)
    }

    @MainActor @Test func automaticIsOnlyClaimedOnceTheRequestIsGone() async throws {
        let installer = FakeInstaller()
        installer.installed.value = true
        let (fans, url) = control(FakeSMC.twoFans(), installer: installer)
        await fans.refresh()
        await fans.applyManual()
        let folder = url.deletingLastPathComponent()
        try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: folder.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: folder.path) }
        fans.restoreAutomatic()
        guard case .failed(let message) = fans.phase else {
            Issue.record("expected a failure, got \(fans.phase)")
            return
        }
        #expect(message.contains("stay manual"))
        #expect(FanControlFile.request(at: url) != nil)
    }

    @MainActor @Test func fansTakenBackWhileHoldingWaitForTheHelperAgain() async {
        let smc = FakeSMC.twoFans()
        let installer = FakeInstaller()
        installer.installed.value = true
        let (fans, url) = control(smc, installer: installer)
        await fans.refresh()
        await fans.applyManual()
        smc.set("F0Md", 1); smc.set("F1Md", 1)
        await fans.refresh()
        #expect(fans.phase == .manual)
        // After sleep macOS has them; the request stays for the helper.
        smc.set("F0Md", 3); smc.set("F1Md", 3)
        await fans.refresh()
        #expect(fans.phase == .starting)
        #expect(fans.isHolding)
        #expect(FanControlFile.request(at: url) != nil)
    }

    @MainActor @Test func aRefreshDuringRemovalDoesNotLeaveItLookingInstalled() async {
        let installer = FakeInstaller()
        installer.installed.value = true
        let (fans, _) = control(FakeSMC.twoFans(), installer: installer)
        await fans.refresh()
        #expect(fans.helperInstalled)
        // The popover's own refresh lands while the password prompt is up.
        installer.duringUninstall = { await fans.refresh() }
        await fans.removeHelper()
        #expect(!fans.helperInstalled)
    }

    @MainActor @Test func removingTheHelperHandsTheFansBackFirst() async {
        let smc = FakeSMC.twoFans()
        let installer = FakeInstaller()
        installer.installed.value = true
        let (fans, url) = control(smc, installer: installer)
        await fans.refresh()
        await fans.applyManual()
        await fans.removeHelper()
        #expect(installer.uninstalls == 1)
        #expect(FanControlFile.request(at: url) == nil)
        #expect(fans.phase == .automatic)
        #expect(!fans.helperInstalled)
    }

    // MARK: - Fakes

    /// An SMC in a dictionary. Mode writes can be refused a number of times,
    /// and only once Ftst is 1 when the Mac has the gate, like an M4.
    final class FakeSMC: SMCAccess, @unchecked Sendable {
        private var values: [String: SMCValue] = [:]
        var refuseModeWrites = 0
        /// Mode writes back to automatic to refuse, like a busy SMC.
        var refuseReleaseWrites = 0
        var onWrite: (String, [UInt8]) -> Void = { _, _ in }

        static func twoFans(gate: Bool = true) -> FakeSMC {
            let smc = FakeSMC()
            smc.values["FNum"] = SMCValue(type: "ui8 ", bytes: [2])
            for fan in 0..<2 {
                smc.values["F\(fan)Ac"] = SMCValue(type: "flt ", bytes: SMCValue.float(fan == 0 ? 2319 : 2509))
                smc.values["F\(fan)Mn"] = SMCValue(type: "flt ", bytes: SMCValue.float(2317))
                smc.values["F\(fan)Mx"] = SMCValue(type: "flt ", bytes: SMCValue.float(7826))
                smc.values["F\(fan)Tg"] = SMCValue(type: "flt ", bytes: SMCValue.float(2317))
                smc.values["F\(fan)Md"] = SMCValue(type: "ui8 ", bytes: [0])
            }
            if gate { smc.values["Ftst"] = SMCValue(type: "ui8 ", bytes: [0]) }
            return smc
        }

        func number(_ key: String) -> Double? { values[key]?.number }
        func set(_ key: String, _ value: UInt8) { values[key]?.bytes = [value] }

        func rename(_ key: String, to other: String) {
            values[other] = values.removeValue(forKey: key)
        }

        func read(_ key: String) -> SMCValue? { values[key] }

        @discardableResult
        func write(_ key: String, _ bytes: [UInt8]) -> Bool {
            guard let old = values[key], old.bytes.count == bytes.count else { return false }
            // Going manual is gated; going back to automatic never is.
            if key.hasPrefix("F"), key.lowercased().hasSuffix("md"), bytes == [1] {
                let gated = values["Ftst"] != nil
                if gated && values["Ftst"]?.bytes != [1] && refuseModeWrites > 0 { return false }
                if refuseModeWrites > 0 {
                    refuseModeWrites -= 1
                    return false
                }
            }
            if key.hasPrefix("F"), key.lowercased().hasSuffix("md"), bytes == [0], refuseReleaseWrites > 0 {
                refuseReleaseWrites -= 1
                return false
            }
            values[key]?.bytes = bytes
            onWrite(key, bytes)
            return true
        }
    }

    final class FakeInstaller: FanHelperInstalling, @unchecked Sendable {
        let installed = Box(false)
        var checks = 0
        var installs = 0
        var uninstalls = 0
        var lastControl: URL?
        var error: FanHelperInstallError?

        func install(executable: String, control: URL) async throws {
            installs += 1
            lastControl = control
            if let error { throw error }
            installed.value = true
        }

        /// Runs while the removal "prompt" is up.
        var duringUninstall: (@MainActor () async -> Void)?

        func uninstall() async throws {
            uninstalls += 1
            await duringUninstall?()
            if let error { throw error }
            installed.value = false
        }
    }

    final class Counter: @unchecked Sendable {
        var passes = 0
    }

    final class Flag: @unchecked Sendable {
        var value: Bool
        init(_ value: Bool) { self.value = value }
    }

    final class Box<T>: @unchecked Sendable {
        var value: T
        init(_ value: T) { self.value = value }
    }
}
