import Foundation
import Security
import Testing
@testable import QuiverCore

/// Temperatures and fans: the sensor fold, the SMC value codec, the root
/// helper's hold loop against a fake SMC, and the popover's model. Fakes
/// only; nothing here touches the real SMC.
@Suite struct FanTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    // MARK: Temperatures

    @Test func theDieSensorsAverageAndJunkSensorsDrop() {
        let reading = TemperatureReading.summarise([
            ("PMU tdie1", 50), ("PMU tdie2", 60), ("PMU tdie2", 70),
            ("PMU tdev1", -9202.9), ("PMU tcal", 51.8), ("NAND CH0 temp", 45),
            ("gas gauge battery", 34), ("gas gauge battery", 36),
        ])
        #expect(reading == TemperatureReading([.cpu: 60, .ssd: 45, .battery: 35]))
    }

    @Test func noKnownSensorMeansNoReading() {
        #expect(TemperatureReading.summarise([]) == nil)
        #expect(TemperatureReading.summarise([("PMU tdev1", -9000), ("PMU tcal", 50)]) == nil)
        #expect(TemperatureReading.summarise([("gas gauge battery", 30)])?[.cpu] == nil)
    }

    @Test func hidIsReadOnlyForWhatTheSMCLeftOut() {
        var asked = 0
        let hid = { () -> TemperatureReading? in
            asked += 1
            return TemperatureReading([.cpu: 70, .ssd: 40, .battery: 30])
        }
        // The SMC answered all three: the HID sensors are never read.
        let full = TemperatureReading([.cpu: 60, .gpu: 55, .ssd: 45, .battery: 35])
        #expect(MacTemperatureSampler.combine(full, hid: hid) == full)
        #expect(asked == 0)

        // No SSD key answered: HID is read and fills only the gap.
        let partial = TemperatureReading([.cpu: 60, .battery: 35])
        #expect(MacTemperatureSampler.combine(partial, hid: hid)
                == TemperatureReading([.cpu: 60, .ssd: 40, .battery: 35]))
        #expect(asked == 1)

        // No SMC at all: HID is the whole reading, and nothing means nil.
        #expect(MacTemperatureSampler.combine(nil, hid: hid)?[.cpu] == 70)
        #expect(MacTemperatureSampler.combine(nil, hid: { nil }) == nil)
    }

    @Test func chipNamesParse() {
        #expect(ThermalKeys.chip("Apple M4 Pro")! == (4, "pro"))
        #expect(ThermalKeys.chip("Apple M1")! == (1, "base"))
        #expect(ThermalKeys.chip("Apple M3 Ultra")! == (3, "ultra"))
        #expect(ThermalKeys.chip("Apple A18 Pro")! == (18, "a18pro"))
        #expect(ThermalKeys.chip("Intel(R) Core(TM) i9-9880H CPU @ 2.30GHz") == nil)
        #expect(ThermalKeys.chip("") == nil)
    }

    @Test func eachChipGetsItsOwnGPUKeys() {
        let pro = ThermalKeys.keys(forBrand: "Apple M4 Pro")
        #expect(pro[.gpu]?.contains("Tg1U") == true && pro[.gpu]?.contains("Tg0G") == false)
        let base = ThermalKeys.keys(forBrand: "Apple M4")
        #expect(base[.gpu]?.contains("Tg0G") == true && base[.gpu]?.contains("Tg1U") == false)
        #expect(ThermalKeys.keys(forBrand: "Apple M2")[.gpu] == ["Tg0f", "Tg0j"])
        // An unknown chip: the shared keys only, so HID fills the CPU.
        let unknown = ThermalKeys.keys(forBrand: "Apple M9 Max")
        #expect(unknown[.cpu] == nil && unknown[.gpu] == nil)
        #expect(unknown[.ssd] == ["TH0x"] && unknown[.display] == ["TL0P"])
    }

    @Test func smcKeysFoldPerComponentAndSkipJunk() {
        let smc = FakeSMC()
        smc.put("Tp01", 70); smc.put("Tp05", 80); smc.put("Te05", 60)
        smc.put("Tg1U", 55); smc.put("Tg0K", 65)
        smc.put("TH0x", 40)
        // An unused sensor answers about 8 °C; it must not drag a mean down.
        smc.put("TaLP", 8.3); smc.put("TaRF", 8.3)
        let reading = ThermalKeys.read(ThermalKeys.keys(forBrand: "Apple M4 Pro"), from: smc)
        #expect(reading == TemperatureReading([.cpu: 70, .gpu: 60, .ssd: 40]))
        #expect(ThermalKeys.read(ThermalKeys.keys(forBrand: "Apple M4 Pro"), from: FakeSMC()) == nil)
    }

    @Test func aFanCountsAsUpOnlyClearOfItsIdleDrift() {
        func fan(_ actual: Double) -> FanReading { FanReading(index: 0, actual: actual, minimum: 2317, maximum: 7826) }
        // An M4 Pro's second fan idles near 2,500 against a 2,317 floor.
        #expect(FanSummary.from([fan(2317), fan(2509)]) == FanSummary(rpm: 2509, up: false))
        #expect(FanSummary.from([fan(2317), fan(2700)]) == FanSummary(rpm: 2700, up: true))
        // Spun down is not up; no fans is no summary.
        #expect(FanSummary.from([fan(0)])?.up == false)
        #expect(FanSummary.from([]) == nil)
    }

    @Test func smcReadingsWinAndHIDFillsTheGaps() {
        let smc = TemperatureReading([.cpu: 70, .gpu: 60])
        let hid = TemperatureReading([.cpu: 55, .battery: 33])
        #expect(smc.filling(from: hid) == TemperatureReading([.cpu: 70, .gpu: 60, .battery: 33]))
        #expect(smc.filling(from: nil) == smc)
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
        let alive = Box(true)
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

    @Test func aRefusalSaysWhyOnceAndIsLogged() {
        let smc = FakeSMC.twoFans(gate: false)
        smc.refuseModeWrites = .max
        var reports: [FanHelperStatus] = []
        var lines: [String] = []
        var hold = hold(smc, [ask(0.5), ask(0.5), ask(0.5)])
        hold.report = { reports.append($0) }
        hold.log = { lines.append($0) }
        hold.run()
        #expect(reports.map(\.state) == [.refused])
        #expect(reports.first?.answers(ask(0.5)) == true)
        #expect(reports.first?.reason
            == "The SMC refused manual mode for Fan 1 (F0Md: refused), and this Mac has no Ftst gate to open.")
        #expect(lines.contains("Fan 1: F0Md type 'ui8 ' size 1 bytes 00 = 0.0; write 1 -> refused"))
        #expect(lines.contains("Fan 1: Ftst absent, no gate to open"))
    }

    @Test func aGateThatDoesNotHelpSaysSo() {
        let smc = FakeSMC.twoFans()
        smc.refuseModeWrites = .max
        var reports: [FanHelperStatus] = []
        var hold = hold(smc, [ask(0.5)])
        hold.report = { reports.append($0) }
        hold.run()
        #expect(reports.first?.reason == "The SMC refused manual mode for Fan 1 (F0Md: refused), even with Ftst open.")
    }

    @Test func aGateThatWouldNotOpenSaysSo() {
        let smc = FakeSMC.twoFans()
        smc.refuseModeWrites = .max
        smc.refuseGateWrites = true
        var reports: [FanHelperStatus] = []
        var hold = hold(smc, [ask(0.5)])
        hold.report = { reports.append($0) }
        hold.run()
        #expect(reports.first?.reason == "The SMC refused manual mode for Fan 1 (F0Md: refused), "
            + "and opening the Ftst gate failed (refused).")
    }

    @Test func everyPassBeatsForTheWatchdog() {
        var beats = 0
        var hold = hold(FakeSMC.twoFans(gate: false), [ask(0.5)])
        hold.beat = { beats += 1 }
        hold.run()
        // One pass takes the request, one empty one hands it back: a
        // pass that never ends is a beat that never comes.
        #expect(beats == 2)
    }

    @Test func aHeldRequestIsReportedOnce() {
        var reports: [FanHelperStatus] = []
        var hold = hold(FakeSMC.twoFans(gate: false), [ask(0.5), ask(0.5), ask(0.7, at: 2)])
        hold.report = { reports.append($0) }
        hold.run()
        #expect(reports.map(\.state) == [.held, .held])
        #expect(reports.last?.answers(ask(0.7, at: 2)) == true)
    }

    @Test func aRequestFromNoRunningQuiverIsReported() {
        var reports: [FanHelperStatus] = []
        var hold = hold(FakeSMC.twoFans(gate: false), [ask(1), ask(1)], ownerAlive: { _ in false })
        hold.report = { reports.append($0) }
        hold.run()
        #expect(reports.map(\.state) == [.refused])
        #expect(reports.first?.reason?.contains("pid 77") == true)
    }

    @Test func aWiderModeKeyIsWrittenAtItsOwnSize() {
        let smc = FakeSMC.twoFans(gate: false)
        smc.replace("F0Md", SMCValue(type: "ui16", bytes: [0, 0]))
        var modeWrites: [[UInt8]] = []
        smc.onWrite = { key, bytes in if key == "F0Md" { modeWrites.append(bytes) } }
        hold(smc, [ask(0.5)]).run()
        // Manual as a big-endian 1, then back to 0, never a lone byte.
        #expect(modeWrites == [[0, 1], [0, 0]])
    }

    @Test func aModeKeyOfAnUnknownTypeIsRefusedByName() {
        let smc = FakeSMC.twoFans(gate: false)
        smc.replace("F0Md", SMCValue(type: "{xyz", bytes: [0, 0, 0]))
        var reports: [FanHelperStatus] = []
        var hold = hold(smc, [ask(0.5)])
        hold.report = { reports.append($0) }
        hold.run()
        #expect(reports.first?.reason
            == "Fan 1's mode key F0Md is '{xyz' with 3 bytes, which the fan helper doesn't know how to write.")
    }

    @Test func valuesEncodeInTheKeysOwnTypeAndSize() {
        #expect(SMCValue(type: "ui8 ", bytes: [0]).encoding(1) == [1])
        #expect(SMCValue(type: "flag", bytes: [0]).encoding(1) == [1])
        #expect(SMCValue(type: "ui16", bytes: [0, 0]).encoding(1) == [0, 1])
        #expect(SMCValue(type: "ui32", bytes: [0, 0, 0, 0]).encoding(1) == [0, 0, 0, 1])
        #expect(SMCValue(type: "si16", bytes: [0, 0]).encoding(1) == nil)
        #expect(SMCValue(type: "ui8 ", bytes: [0]).encoding(256) == nil)
        // Whatever is written reads back as the same number.
        for type in ["ui8 ", "ui16", "ui32", "flt "] {
            let key = SMCValue(type: type, bytes: Array(repeating: 0, count: type == "ui8 " ? 1 : type == "ui16" ? 2 : 4))
            for value: UInt32 in [0, 1, 3] {
                #expect(key.encoding(value).flatMap { SMCValue(type: type, bytes: $0).number } == Double(value))
            }
        }
    }

    @Test func theSurveyListsEveryFanKeyAndTheGate() {
        let smc = FakeSMC.twoFans()
        smc.rename("F0Md", to: "F0md")
        let lines = FanKeys.survey(smc)
        #expect(lines.count == 1 + 2 * 6 + 1)
        #expect(lines.first == "FNum: type 'ui8 ' size 1 bytes 02 = 2.0")
        #expect(lines.contains("F0Md: absent"))
        #expect(lines.contains("F0md: type 'ui8 ' size 1 bytes 00 = 0.0"))
        #expect(lines.last == "Ftst: type 'ui8 ' size 1 bytes 00 = 0.0")
        #expect(FanKeys.survey(FakeSMC()) == ["FNum: absent", "Ftst: absent"])
    }

    @Test func theHelperStatusRoundTripsAndAnswersOnlyItsRequest() throws {
        let url = scratch.url.appending(path: "fan-status-\(UUID().uuidString).json")
        defer { try? FileManager.default.removeItem(at: url) }
        #expect(FanHelperStatus.read(at: url) == nil)
        let request = ask(0.5, at: 1_800_000_000.123)
        let status = FanHelperStatus(state: .refused, reason: "no", request: request,
                                     at: Date(timeIntervalSince1970: 5))
        try status.write(to: url)
        #expect(FanHelperStatus.read(at: url) == status)
        let mode = try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? Int
        #expect(mode == 0o644)
        #expect(status.answers(request))
        #expect(!status.answers(ask(0.5, at: 2)))
        #expect(!status.answers(ask(0.5, owner: 78, at: 1_800_000_000.123)))
        #expect(!status.answers(nil))
        #expect(!FanHelperStatus(state: .ready).answers(request))
    }

    /// The time limit catches a kill that never comes; the 60 s sleep
    /// would outlast it.
    @Test(.timeLimit(.minutes(1))) func aToolThatHangsIsKilledAndCountsAsNoAnswer() async {
        let (yes, no, hung, missing) = await offMain {
            (FanControl.exitStatus(of: "/usr/bin/true", [], within: 5),
             FanControl.exitStatus(of: "/usr/bin/false", [], within: 5),
             FanControl.exitStatus(of: "/bin/sleep", ["60"], within: 0.2),
             FanControl.exitStatus(of: "/nonexistent/\(UUID().uuidString)", [], within: 1))
        }
        #expect(yes == 0)
        #expect(no == 1)
        #expect(hung == nil)
        #expect(missing == nil)
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
            "--control", "/Users/me/.quiver/fan/control.json", "--helper-version", String(FanHelper.version)])
        #expect(job["KeepAlive"] as? Bool == true)
        #expect(job["RunAtLoad"] as? Bool == true)
    }

    @Test func theInstallScriptChecksTheCopyAndWritesTheJobAsRoot() throws {
        let command = AdministratorInstaller.installCommand(
            executable: "/Users/o'neil/Apps/Quiver.app/Contents/MacOS/Quiver", sha256: "abc123", team: nil,
            control: URL(fileURLWithPath: "/Users/o'neil/.quiver/fan/control.json"))
        #expect(command.contains(#"/usr/bin/install -m 755 -o root -g wheel '/Users/o'\''neil/Apps/Quiver.app/Contents/MacOS/Quiver' '/Library/PrivilegedHelperTools/com.cwenlong.quiver.fan.new'"#))
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

    /// A team-signed app checks the helper's signature on the root-owned
    /// copy, after the hash and before the ad-hoc re-sign erases it: a
    /// helper patched in the user-writable bundle never runs as root.
    @Test func aSignedAppChecksTheHelpersTeamBeforeRunningItAsRoot() throws {
        let command = AdministratorInstaller.installCommand(
            executable: "/Apps/Quiver.app/Contents/MacOS/QuiverFanHelper", sha256: "abc123", team: "ABCDE12345",
            control: URL(fileURLWithPath: "/Users/me/.quiver/fan/control.json"))
        let hash = try #require(command.range(of: "/usr/bin/shasum -a 256 "))
        let verify = try #require(command.range(
            of: #"/usr/bin/codesign --verify --strict -R '=identifier "QuiverFanHelper" and anchor apple generic"#
                + #" and certificate leaf[subject.OU] = "ABCDE12345"' '/Library/PrivilegedHelperTools/com.cwenlong.quiver.fan.new'"#))
        let sign = try #require(command.range(of: "/usr/bin/codesign --force --sign -"))
        let bootout = try #require(command.range(of: "/bin/launchctl bootout"))
        let move = try #require(command.range(of: "/bin/mv -f '/Library/PrivilegedHelperTools/com.cwenlong.quiver.fan.new'"
            + " '/Library/PrivilegedHelperTools/com.cwenlong.quiver.fan'"))
        let job = try #require(command.range(of: "/usr/bin/printf '%s'"))
        #expect(hash.lowerBound < verify.lowerBound)
        #expect(verify.lowerBound < sign.lowerBound)
        // The live executable an old KeepAlive job could relaunch changes
        // only after every check, and only once that job is unloaded.
        #expect(sign.lowerBound < bootout.lowerBound)
        #expect(bootout.lowerBound < move.lowerBound)
        #expect(move.lowerBound < job.lowerBound)
        // Nothing but the move writes the live path.
        #expect(command.components(separatedBy: "'/Library/PrivilegedHelperTools/com.cwenlong.quiver.fan'").count == 2)
        #expect(!command.contains("\n"))
        // An ad-hoc build has no team to check against and keeps the hash.
        let adHoc = AdministratorInstaller.installCommand(
            executable: "/Apps/Quiver.app/Contents/MacOS/QuiverFanHelper", sha256: "abc123", team: nil,
            control: URL(fileURLWithPath: "/Users/me/.quiver/fan/control.json"))
        #expect(!adHoc.contains("--verify"))
        #expect(adHoc.contains("/usr/bin/shasum -a 256 "))
    }

    /// The check itself, run: a helper that isn't the team's (here an
    /// ad-hoc signed copy) is deleted and the script stops with a reason.
    @Test func aHelperWithoutTheTeamsSignatureIsRefusedAndDeleted() throws {
        let tool = scratch.url.appending(path: "it's a helper").path
        try FileManager.default.copyItem(atPath: "/usr/bin/true", toPath: tool)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", AdministratorInstaller.signatureCheck(tool: tool, team: "ABCDE12345")
            + " && echo installed"]
        let out = Pipe(), err = Pipe()
        process.standardOutput = out
        process.standardError = err
        try process.run()
        process.waitUntilExit()
        #expect(process.terminationStatus == 1)
        #expect(String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self).isEmpty)
        #expect(String(decoding: err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
            .contains("not signed by the team that signed Quiver"))
        #expect(!FileManager.default.fileExists(atPath: tool))
    }

    @Test func theHelperRequirementCompiles() throws {
        var requirement: SecRequirement?
        let text = String(AdministratorInstaller.helperRequirement(team: "ABCDE12345").dropFirst())
        #expect(SecRequirementCreateWithString(text as CFString, [], &requirement) == errSecSuccess)
        #expect(requirement != nil)
    }

    @Test func onlyRealTeamIdentifiersReachTheScript() async {
        #expect(AdministratorInstaller.isTeamIdentifier("ABCDE12345"))
        for odd in ["", "abcde12345", "ABCDE1234", "ABCDE123456", "ABCDE\"1234", "ABCDE 1234"] {
            #expect(!AdministratorInstaller.isTeamIdentifier(odd), "\(odd)")
        }
        // Refused before any prompt: neither the hash nor osascript runs.
        let odd = AdministratorInstaller(team: { "AB'; rm -rf" })
        await #expect(throws: FanHelperInstallError.self) {
            try await odd.install(executable: "/nonexistent", control: URL(fileURLWithPath: "/tmp/c.json"))
        }
        let unreadable = AdministratorInstaller(team: { throw FanHelperInstallError.failed("no signature") })
        await #expect(throws: FanHelperInstallError.failed("no signature")) {
            try await unreadable.install(executable: "/nonexistent", control: URL(fileURLWithPath: "/tmp/c.json"))
        }
    }

    /// The test runner's own signature reads without error: a team for a
    /// signed build, nil for an ad-hoc one.
    @Test func theRunningCodesTeamReads() throws {
        let team = try AdministratorInstaller.signingTeam()
        if let team { #expect(AdministratorInstaller.isTeamIdentifier(team)) }
    }

    /// The job names the helper's version, so an install from before it
    /// (a copy of Quiver's executable, which crashed in dyld) or an older
    /// helper fails `helperReady`'s comparison and gets reinstalled.
    @Test func theJobCarriesTheHelperVersion() throws {
        let control = URL(fileURLWithPath: "/Users/me/.quiver/fan/control.json")
        let job = try PropertyListSerialization.propertyList(from: FanHelper.plist(control: control),
                                                             format: nil) as? [String: Any]
        let arguments = try #require(job?["ProgramArguments"] as? [String])
        #expect(arguments == [FanHelper.installedExecutable, FanHelper.flag, "--control", control.path,
                              "--helper-version", String(FanHelper.version)])
        #expect(arguments != [FanHelper.installedExecutable, FanHelper.flag, "--control", control.path])
        // The helper itself still finds its control file past the version.
        #expect(FanHelper.parse(arguments) == control)
    }

    @Test func theBundledHelperIsPreferredOverTheApp() throws {
        let dir = scratch.url.appending(path: "helper-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let app = dir.appending(path: "Quiver").path
        #expect(FanHelper.bundledExecutable(appExecutable: app) == app)
        let helper = dir.appending(path: FanHelper.bundledName).path
        FileManager.default.createFile(atPath: helper, contents: Data(), attributes: [.posixPermissions: 0o755])
        #expect(FanHelper.bundledExecutable(appExecutable: app) == helper)
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
        let url = scratch.url.appending(path: "hash-\(UUID().uuidString)")
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
        let folder = scratch.url.appending(path: "fan-\(UUID().uuidString)")
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
        let folder = scratch.url.appending(path: "fan-\(UUID().uuidString)")
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
                         clock: Box<Date> = Box(Date(timeIntervalSince1970: 1_800_000_000)),
                         status: Box<FanHelperStatus?> = Box(nil),
                         sleeper: Sleeper = Sleeper()) -> (FanControl, URL) {
        let url = scratch.url
            .appending(path: "fan-\(UUID().uuidString)/control.json")
        let control = FanControl(control: url, reader: { FanKeys.read(smc) }, status: { status.value },
                                 installer: installer,
                                 isInstalled: { _ in installer.checks += 1; return installer.installed.value },
                                 executable: "/Apps/Quiver", owner: 77, clock: { clock.value },
                                 sleep: { await sleeper.sleep($0) },
                                 defaults: TestDefaults("fan"))
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

    @MainActor @Test(skippedAsRoot) func automaticIsOnlyClaimedOnceTheRequestIsGone() async throws {
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

    // MARK: The threshold

    @MainActor private func installedControl(_ smc: FakeSMC = FakeSMC.twoFans()) async -> (FanControl, URL, FakeInstaller) {
        let installer = FakeInstaller()
        installer.installed.value = true
        let (fans, url) = control(smc, installer: installer)
        await fans.refresh()
        return (fans, url, installer)
    }

    @MainActor @Test func theThresholdHoldsAboveTheLineAndLetsGoFiveBelow() async {
        let (fans, url, _) = await installedControl()
        fans.thresholdEnabled = true
        fans.threshold = 80
        fans.level = 0.6
        await fans.checkTemperature(79)
        #expect(FanControlFile.request(at: url) == nil)
        await fans.checkTemperature(80)
        #expect(fans.heldByThreshold)
        #expect(FanControlFile.request(at: url)?.level == 0.6)
        // Hovering just under the line keeps the fans up.
        await fans.checkTemperature(77)
        #expect(fans.heldByThreshold)
        await fans.checkTemperature(75)
        #expect(!fans.heldByThreshold)
        #expect(fans.phase == .automatic)
        #expect(FanControlFile.request(at: url) == nil)
    }

    @MainActor @Test func aSpeedTheUserSetWinsOverTheThreshold() async {
        let (fans, url, _) = await installedControl()
        fans.thresholdEnabled = true
        fans.level = 1
        await fans.applyManual()
        await fans.checkTemperature(95)
        await fans.checkTemperature(40)
        // Neither the hot nor the cool reading touched the user's speed.
        #expect(!fans.heldByThreshold)
        #expect(fans.isHolding)
        #expect(FanControlFile.request(at: url)?.level == 1)
        // And taking over a threshold hold makes it the user's.
        fans.restoreAutomatic()
        await fans.checkTemperature(90)
        #expect(fans.heldByThreshold)
        await fans.applyManual()
        #expect(!fans.heldByThreshold)
        await fans.checkTemperature(40)
        #expect(fans.isHolding)
    }

    @MainActor @Test func theThresholdNeverPromptsFromTheBackground() async {
        let installer = FakeInstaller()
        let (fans, url) = control(FakeSMC.twoFans(), installer: installer)
        fans.thresholdEnabled = true
        await fans.checkTemperature(99)
        #expect(installer.installs == 0)
        #expect(FanControlFile.request(at: url) == nil)
        #expect(!fans.heldByThreshold)
    }

    @MainActor @Test func switchingTheThresholdOnInstallsTheHelperOnce() async {
        let installer = FakeInstaller()
        installer.error = .cancelled
        let (fans, url) = control(FakeSMC.twoFans(), installer: installer)
        await fans.enableThreshold()
        #expect(!fans.thresholdEnabled)
        installer.error = nil
        await fans.enableThreshold()
        #expect(fans.thresholdEnabled)
        #expect(installer.installs == 2)
        // Switching it on sets no speed by itself.
        #expect(FanControlFile.request(at: url) == nil)
    }

    @MainActor @Test func switchingTheThresholdOffWhileHoldingHandsTheFansBack() async {
        let (fans, url, _) = await installedControl()
        fans.thresholdEnabled = true
        await fans.checkTemperature(90)
        #expect(fans.heldByThreshold)
        fans.thresholdEnabled = false
        #expect(!fans.heldByThreshold)
        #expect(fans.phase == .automatic)
        #expect(FanControlFile.request(at: url) == nil)
    }

    /// Parks the fan read on its background thread until released.
    final class ReadGate: @unchecked Sendable {
        var armed = false
        let parked = DispatchSemaphore(value: 0)
        let release = DispatchSemaphore(value: 0)
    }

    @MainActor @Test func switchingOffMidTakeoverLeavesNothingHeld() async {
        let smc = FakeSMC.twoFans()
        let gate = ReadGate()
        let installer = FakeInstaller()
        installer.installed.value = true
        let url = scratch.url.appending(path: "fan-\(UUID().uuidString)/control.json")
        let fans = FanControl(control: url,
                              reader: { if gate.armed { gate.parked.signal(); gate.release.wait() }; return FanKeys.read(smc) },
                              status: { nil }, installer: installer, isInstalled: { _ in true }, executable: "/Apps/Quiver", owner: 77,
                              defaults: TestDefaults("fan"))
        await fans.refresh()
        fans.thresholdEnabled = true
        gate.armed = true
        let takeover = Task { await fans.checkTemperature(90) }
        await offMain { gate.parked.wait() }
        // The takeover is waiting on its fan read: switch the rule off now.
        fans.thresholdEnabled = false
        gate.armed = false
        gate.release.signal()
        await takeover.value
        #expect(!fans.heldByThreshold)
        #expect(fans.phase == .automatic)
        #expect(FanControlFile.request(at: url) == nil)
    }

    @MainActor @Test func theThresholdSpeedReachesTheFansAtOnce() async {
        let (fans, url, _) = await installedControl()
        fans.thresholdEnabled = true
        fans.level = 0.4
        await fans.checkTemperature(90)
        #expect(FanControlFile.request(at: url)?.level == 0.4)
        fans.setThresholdLevel(0.9)
        #expect(FanControlFile.request(at: url)?.level == 0.9)
        // The Manual slider waits for Update speed, even mid-hold.
        fans.level = 0.3
        #expect(FanControlFile.request(at: url)?.level == 0.9)
        await fans.applyManual()
        #expect(FanControlFile.request(at: url)?.level == 0.3)
        // And once the speed is the user's, the threshold slider does not
        // reach it either.
        fans.setThresholdLevel(0.2)
        #expect(FanControlFile.request(at: url)?.level == 0.3)
    }

    @MainActor @Test func aSuccessfulRetryLetsTheRuleAct() async {
        let installer = FakeInstaller()
        installer.error = .failed("launchctl said no")
        let (fans, url) = control(FakeSMC.twoFans(), installer: installer)
        await fans.enableThreshold()
        #expect(fans.phase == .failed("launchctl said no"))
        installer.error = nil
        await fans.enableThreshold()
        #expect(fans.phase == .automatic)
        await fans.checkTemperature(90)
        #expect(fans.heldByThreshold)
        #expect(FanControlFile.request(at: url) != nil)
    }

    @MainActor @Test func aTakeoverTheHelperNeverFinishesTimesOutInTheBackground() async {
        let clock = Box(Date(timeIntervalSince1970: 1_800_000_000))
        let installer = FakeInstaller()
        installer.installed.value = true
        let (fans, url) = control(FakeSMC.twoFans(), installer: installer, clock: clock)
        await fans.refresh()
        fans.thresholdEnabled = true
        await fans.checkTemperature(90)
        #expect(fans.phase == .starting)
        // No page, no popover: only the ticks read the fans.
        clock.value = clock.value.addingTimeInterval(FanControl.startTimeout + 1)
        await fans.checkTemperature(90)
        #expect(fans.phase == .failed(FanControl.notTaken))
        #expect(!fans.heldByThreshold)
        #expect(FanControlFile.request(at: url) == nil)
    }

    @MainActor @Test func aFailedTakeoverRetriesAfterAWait() async {
        let clock = Box(Date(timeIntervalSince1970: 1_800_000_000))
        let installer = FakeInstaller()
        installer.installed.value = true
        let (fans, url) = control(FakeSMC.twoFans(), installer: installer, clock: clock)
        await fans.refresh()
        fans.thresholdEnabled = true
        await fans.checkTemperature(90)
        #expect(fans.phase == .starting)
        clock.value = clock.value.addingTimeInterval(FanControl.startTimeout + 1)
        await fans.checkTemperature(90)
        #expect(fans.phase == .failed(FanControl.notTaken))
        #expect(FanControlFile.request(at: url) == nil)
        // Inside the backoff the failure stands.
        clock.value = clock.value.addingTimeInterval(FanControl.retryAfter - 1)
        await fans.checkTemperature(90)
        #expect(FanControlFile.request(at: url) == nil)
        #expect(fans.phase == .failed(FanControl.notTaken))
        // Past it the rule asks again instead of staying off until the
        // app happens to be poked.
        clock.value = clock.value.addingTimeInterval(2)
        await fans.checkTemperature(90)
        #expect(FanControlFile.request(at: url) != nil)
        #expect(fans.phase == .starting)
        #expect(fans.heldByThreshold)
    }

    @MainActor @Test func startingGivesUpOnItsOwnClockWhileAReadHangs() async {
        let smc = FakeSMC.twoFans()
        let gate = ReadGate()
        let sleeper = Sleeper()
        let installer = FakeInstaller()
        installer.installed.value = true
        let url = scratch.url.appending(path: "fan-\(UUID().uuidString)/control.json")
        let fans = FanControl(control: url,
                              reader: { if gate.armed { gate.parked.signal(); gate.release.wait() }; return FanKeys.read(smc) },
                              status: { nil }, installer: installer, isInstalled: { _ in true },
                              executable: "/Apps/Quiver", owner: 77, sleep: { await sleeper.sleep($0) },
                              defaults: TestDefaults("fan"))
        await fans.refresh()
        gate.armed = true
        let takeover = Task { await fans.applyManual() }
        await offMain { gate.parked.wait() }
        #expect(fans.phase == .starting)
        // The read never comes back, and no refresh settles anything: the
        // deadline set when Starting began ends it anyway.
        let deadline = fans.deadline
        sleeper.fire()
        await deadline?.value
        #expect(fans.phase == .failed(FanControl.notTaken))
        #expect(FanControlFile.request(at: url) == nil)
        gate.armed = false
        gate.release.signal()
        await takeover.value
        #expect(fans.phase == .failed(FanControl.notTaken))
    }

    @MainActor @Test func aDeadlineFromAnEarlierStartChangesNothing() async {
        let smc = FakeSMC.twoFans()
        let sleeper = Sleeper()
        let installer = FakeInstaller()
        installer.installed.value = true
        let (fans, url) = control(smc, installer: installer, sleeper: sleeper)
        await fans.refresh()
        await fans.applyManual()
        let first = fans.deadline
        #expect(first != nil)
        smc.set("F0Md", 1); smc.set("F1Md", 1)
        await fans.refresh()
        #expect(fans.phase == .manual)
        #expect(fans.deadline == nil)
        sleeper.fire()
        await first?.value
        #expect(fans.phase == .manual)
        #expect(FanControlFile.request(at: url) != nil)
    }

    @MainActor @Test func aRefusalShowsTheHelpersReasonAtOnce() async throws {
        let status = Box<FanHelperStatus?>(nil)
        let installer = FakeInstaller()
        installer.installed.value = true
        let (fans, url) = control(FakeSMC.twoFans(), installer: installer, status: status)
        await fans.refresh()
        await fans.applyManual()
        #expect(fans.phase == .starting)
        // An answer to an earlier request is not this one's.
        status.value = FanHelperStatus(state: .refused, reason: "old", request: ask(0.5, at: 5))
        await fans.refresh()
        #expect(fans.phase == .starting)
        let request = try #require(FanControlFile.request(at: url))
        let reason = "The SMC refused manual mode for Fan 1 (F0md: refused), and this Mac has no Ftst gate to open."
        status.value = FanHelperStatus(state: .refused, reason: reason, request: request)
        await fans.refresh()
        #expect(fans.phase == .failed(reason))
        #expect(FanControlFile.request(at: url) == nil)
    }

    @MainActor @Test func aHelperThatCannotRunSaysWhy() async {
        let status = Box<FanHelperStatus?>(nil)
        let installer = FakeInstaller()
        installer.installed.value = true
        let (fans, _) = control(FakeSMC.twoFans(), installer: installer, status: status)
        // An error from an earlier run, still in the file when the request
        // is made, says nothing about the helper now.
        status.value = FanHelperStatus(state: .error, reason: "old run",
                                       at: Date(timeIntervalSince1970: 1_799_999_990))
        await fans.refresh()
        await fans.applyManual()
        #expect(fans.phase == .starting)
        await fans.refresh()
        #expect(fans.phase == .starting)
        // The restarted helper failing after the request does.
        status.value = FanHelperStatus(state: .error, reason: "The fan helper couldn't open the SMC.",
                                       at: Date(timeIntervalSince1970: 1_800_000_005))
        await fans.refresh()
        #expect(fans.phase == .failed("The fan helper couldn't open the SMC."))
    }

    @MainActor @Test func manualModeThatNeverReadsBackTimesOutSayingSo() async throws {
        let clock = Box(Date(timeIntervalSince1970: 1_800_000_000))
        let status = Box<FanHelperStatus?>(nil)
        let installer = FakeInstaller()
        installer.installed.value = true
        let (fans, url) = control(FakeSMC.twoFans(), installer: installer, clock: clock, status: status)
        await fans.refresh()
        await fans.applyManual()
        // The helper says it holds them; the fans still read automatic.
        status.value = FanHelperStatus(state: .held, request: try #require(FanControlFile.request(at: url)))
        await fans.refresh()
        #expect(fans.phase == .starting)
        clock.value = clock.value.addingTimeInterval(FanControl.startTimeout + 1)
        await fans.refresh()
        #expect(fans.phase == .failed(FanControl.didNotStick))
    }

    @MainActor @Test func theThresholdIsClampedAndRemembered() async {
        let defaults = TestDefaults("fan")
        let url = scratch.url.appending(path: "fan-\(UUID().uuidString)/control.json")
        let fans = FanControl(control: url, reader: { [] }, status: { nil }, installer: FakeInstaller(),
                              isInstalled: { _ in false }, defaults: defaults)
        #expect(fans.threshold == 80 && !fans.thresholdEnabled)
        fans.threshold = 140
        #expect(fans.threshold == 100)
        fans.threshold = 72
        // Unpicked, the off temperature follows the line, so a restart
        // finds the same value it left rather than a shifted one.
        #expect(fans.releaseBelow == 67)
        let mid = FanControl(control: url, reader: { [] }, status: { nil }, installer: FakeInstaller(),
                             isInstalled: { _ in false }, defaults: defaults)
        #expect(mid.releaseBelow == 67)
        fans.thresholdEnabled = true
        fans.releaseBelow = 60
        let again = FanControl(control: url, reader: { [] }, status: { nil }, installer: FakeInstaller(),
                               isInstalled: { _ in false }, defaults: defaults)
        #expect(again.threshold == 72 && again.thresholdEnabled)
        #expect(again.releaseBelow == 60)
    }

    @MainActor @Test func theOffTemperatureDecidesWhereTheFansLetGo() async {
        let (fans, url, _) = await installedControl()
        fans.thresholdEnabled = true
        fans.threshold = 80
        fans.releaseBelow = 60
        await fans.checkTemperature(85)
        #expect(fans.heldByThreshold)
        // Under the line but over the off temperature, the fans stay held.
        await fans.checkTemperature(75)
        #expect(fans.heldByThreshold)
        await fans.checkTemperature(60)
        #expect(!fans.heldByThreshold)
        #expect(fans.phase == .automatic)
        #expect(FanControlFile.request(at: url) == nil)
    }

    @MainActor @Test func theOffTemperatureStaysUnderTheOnLine() async {
        let defaults = TestDefaults("fan")
        let url = scratch.url.appending(path: "fan-\(UUID().uuidString)/control.json")
        let fans = FanControl(control: url, reader: { [] }, status: { nil }, installer: FakeInstaller(),
                              isInstalled: { _ in false }, defaults: defaults)
        #expect(fans.releaseBelow == 75)
        // Unpicked, it follows the line at the default gap.
        fans.threshold = 90
        #expect(fans.releaseBelow == 85)
        // Picked, it cannot reach or pass the on line.
        fans.releaseBelow = 95
        #expect(fans.releaseBelow == 89)
        fans.releaseBelow = 60
        #expect(fans.releaseBelow == 60)
        // Moving the line under a pick drags it down with a degree to spare.
        fans.threshold = 55
        #expect(fans.releaseBelow == 54)
        // Nor under its floor.
        fans.releaseBelow = 10
        #expect(fans.releaseBelow == 30)
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
        /// Ftst writes to refuse, like a gate the SMC won't open.
        var refuseGateWrites = false
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
        func put(_ key: String, _ celsius: Double) { values[key] = SMCValue(type: "flt ", bytes: SMCValue.float(celsius)) }
        func set(_ key: String, _ value: UInt8) { values[key]?.bytes = [value] }

        func replace(_ key: String, _ value: SMCValue) { values[key] = value }

        func rename(_ key: String, to other: String) {
            values[other] = values.removeValue(forKey: key)
        }

        func read(_ key: String) -> SMCValue? { values[key] }

        @discardableResult
        func write(_ key: String, _ bytes: [UInt8]) -> Bool {
            guard let old = values[key], old.bytes.count == bytes.count else { return false }
            if key == "Ftst" && refuseGateWrites { return false }
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

    /// Stands in for the Starting deadline's sleep: every sleep waits
    /// until `fire`, and any sleep after it returns at once.
    final class Sleeper: @unchecked Sendable {
        private let lock = NSLock()
        private var waiting: [CheckedContinuation<Void, Never>] = []
        private var fired = false

        func sleep(_ seconds: TimeInterval) async {
            await withCheckedContinuation { continuation in
                let now = lock.withLock {
                    if !fired { waiting.append(continuation) }
                    return fired
                }
                if now { continuation.resume() }
            }
        }

        func fire() {
            let all = lock.withLock {
                fired = true
                defer { waiting = [] }
                return waiting
            }
            all.forEach { $0.resume() }
        }
    }
}
