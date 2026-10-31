import Foundation
import Testing
@testable import QuiverCore

/// The trend card's model side: history rings, the GPU counter math, the
/// line colour chain, and the background sampler's ticking. Fakes only;
/// nothing real is sampled.
@Suite struct ComputeTrendTests {
    static let now = Date(timeIntervalSince1970: 1_800_000_000)
    static let home = "/Users/test"
    static let claude = HarnessMatcher(harness: "claude", commands: ["claude"],
                                     homeFolder: home + "/.claude")

    static func proc(_ pid: Int32, _ ppid: Int32, path: String, mem: UInt64 = 1 << 20,
                     cpu: UInt64 = 0) -> ProcessSample {
        ProcessSample(pid: pid, ppid: ppid, path: path, arguments: [], cwd: nil,
                      startedAt: now, threads: 1, memoryBytes: mem, cpuTimeNanos: cpu)
    }

    static func point(_ memory: UInt64, at: Date = now, cpu: Double = 0,
                      gpu: Double? = nil) -> TrendPoint {
        TrendPoint(at: at, memoryBytes: memory, cpuPercent: cpu, gpuPercent: gpu)
    }

    // MARK: ComputeHistory

    @Test func theRingKeepsTheLastCapacityPoints() {
        var history = ComputeHistory()
        for i in 0..<ComputeHistory.capacity + 10 {
            history.append("claude", Self.point(UInt64(i), at: Self.now.addingTimeInterval(Double(i))))
        }
        let points = history.points(for: "claude")
        #expect(points.count == ComputeHistory.capacity)
        #expect(points.first?.memoryBytes == 10)
        #expect(points.last?.memoryBytes == UInt64(ComputeHistory.capacity + 9))
    }

    @Test func aHarnessShowsWhenItWasEverAboveTheThreshold() {
        var history = ComputeHistory()
        let limit: UInt64 = 2 << 30
        // Spiked once, back under now: still shown on every tab.
        history.append("claude", Self.point(UInt64(3) << 30))
        history.append("claude", Self.point(UInt64(1) << 30, at: Self.now.addingTimeInterval(10)))
        history.append("codex", Self.point(UInt64(1) << 30, at: Self.now.addingTimeInterval(10)))
        #expect(history.shown(above: limit) == ["claude"])
    }

    @Test func theWindowIsASharedTimeRange() {
        var history = ComputeHistory()
        let now = Self.now
        let range = history.window(endingAt: now, interval: .tenSeconds)
        // 120 intervals of 10 s: the window opens exactly 1,200 s back.
        #expect(range.lowerBound == now.addingTimeInterval(-1_200))
        #expect(range.upperBound == now)
        // A harness that stopped long ago has no points left in the
        // window; a live one's earliest point sits at the left edge.
        history.append("stopped", Self.point(1, at: now.addingTimeInterval(-1_300)))
        history.append("live", Self.point(1, at: now.addingTimeInterval(-1_200)))
        history.append("live", Self.point(1, at: now))
        #expect(history.window(for: "stopped", in: range).isEmpty)
        #expect(history.window(for: "live", in: range).count == 2)
        #expect(history.latest == now)
    }

    // MARK: GPU

    @Test func theUserClientCreatorGivesItsPid() {
        #expect(GPUSampler.creatorPID("pid 402, WindowServer") == 402)
        #expect(GPUSampler.creatorPID("pid 71") == 71)
        #expect(GPUSampler.creatorPID("WindowServer") == nil)
        #expect(GPUSampler.creatorPID("pid abc") == nil)
    }

    @Test func appUsageSumsEveryDict() {
        let usage: [[String: Any]] = [
            ["accumulatedGPUTime": 1_000 as NSNumber],
            ["accumulatedGPUTime": 500 as NSNumber],
            ["somethingElse": 7 as NSNumber],
        ]
        #expect(GPUSampler.gpuTime(usage) == 1_500)
        // No readable counter anywhere is nil, not zero.
        #expect(GPUSampler.gpuTime(nil) == nil)
        #expect(GPUSampler.gpuTime("not a list") == nil)
        #expect(GPUSampler.gpuTime([["somethingElse": 7 as NSNumber]]) == nil)
        // An explicit zero is a reading and stays a reading.
        #expect(GPUSampler.gpuTime([["accumulatedGPUTime": 0 as NSNumber]]) == 0)
    }

    @Test func aClientWithoutAReadableCounterIsSkipped() {
        let noCounter: [String: Any] = [
            "IOUserClientCreator": "pid 402, WindowServer",
            "AppUsage": [["somethingElse": 7 as NSNumber]],
        ]
        #expect(GPUSampler.clientSample(noCounter) == nil)
        let zero: [String: Any] = [
            "IOUserClientCreator": "pid 402, WindowServer",
            "AppUsage": [["accumulatedGPUTime": 0 as NSNumber]],
        ]
        #expect(GPUSampler.clientSample(zero)?.pid == 402)
        #expect(GPUSampler.clientSample(zero)?.nanos == 0)
    }

    @Test func theMeterTurnsDeltasIntoPercent() {
        var meter = GPUMeter()
        let t0 = Self.now
        // First sighting: no value yet.
        #expect(meter.update([10: 1_000_000_000], at: t0) == [:])
        // One second of GPU time over ten seconds of wall: 10%.
        #expect(meter.update([10: 2_000_000_000], at: t0.addingTimeInterval(10)) == [10: 10])
    }

    @Test func theMeterResetsACounterThatWentBackwards() {
        var meter = GPUMeter()
        let t0 = Self.now
        meter.update([10: 5_000_000_000], at: t0)
        meter.update([10: 6_000_000_000], at: t0.addingTimeInterval(10))
        // The process died and its client reset: no value this tick.
        #expect(meter.update([10: 1_000_000_000], at: t0.addingTimeInterval(20)) == [:])
        // Then it deltas from the new base.
        #expect(meter.update([10: 2_000_000_000], at: t0.addingTimeInterval(30)) == [10: 10])
    }

    // MARK: HarnessColour

    @Test func aHandPickedColourWinsOverTheLogo() {
        let svg = ##"<path fill="#ff6daa"/>"##
        #expect(HarnessColor.resolve(id: "codex", colorSVG: svg) == HarnessRGB(hex: "#3b7ff0"))
        #expect(HarnessColor.of("claude") == HarnessRGB(hex: "#d97757"))
    }

    @Test func aHarnessOutsideThePaletteTakesItsLogoColour() {
        let svg = ##"<path fill="#ff6daa"/>"##
        #expect(HarnessColor.resolve(id: "made-up-harness", colorSVG: svg) == HarnessRGB(hex: "#ff6daa"))
    }

    @Test func theLogoPickPrefersTheVividFill() {
        // A grey, a pale tint and the brand pink: the pink wins on
        // saturation times luminance.
        let svg = ##"""
        <svg><rect fill="#222222"/><path fill="#f5f5f5"/><path fill="#ff6daa"/></svg>
        """##
        #expect(HarnessColor.pick(in: svg) == HarnessRGB(hex: "#ff6daa"))
    }

    @Test func thePalettePickIsStableForAnId() {
        let a = HarnessColor.resolve(id: "made-up-harness", colorSVG: nil)
        let b = HarnessColor.resolve(id: "made-up-harness", colorSVG: nil)
        #expect(a == b)
        #expect(HarnessColor.palette.contains(a))
    }

    @Test func aDarkLogoIsLiftedToReadOnTheCard() {
        let rgb = HarnessColor.resolve(id: "made-up-harness", colorSVG: ##"<path fill="#200010"/>"##)
        #expect(rgb.luminance >= HarnessColor.luminanceFloor)
    }

    /// Every harness in the palette reads apart from the others; a
    /// repeat would merge two harnesses in the marble pile and calendar.
    @Test func theHandPickedColoursAreDistinct() {
        let hexes = HarnessColor.fixed.values.map(\.hex)
        #expect(Set(hexes).count == hexes.count)
    }

    // MARK: The trend sampler

    final class Clock: @unchecked Sendable {
        var now: Date
        init(_ now: Date) { self.now = now }
    }

    final class CountingSampler: ProcessSampling, @unchecked Sendable {
        var table: [ProcessSample]
        var calls = 0
        private let lock = NSLock()
        init(_ table: [ProcessSample]) { self.table = table }
        func sample() -> [ProcessSample] {
            lock.lock()
            defer { lock.unlock() }
            calls += 1
            return table
        }
        var callCount: Int {
            lock.lock()
            defer { lock.unlock() }
            return calls
        }
    }

    final class FakeGPU: GPUSampling, @unchecked Sendable {
        var table: [Int32: UInt64]
        init(_ table: [Int32: UInt64]) { self.table = table }
        func times() -> [Int32: UInt64] { table }
    }

    final class FakeThermometer: TemperatureSampling, @unchecked Sendable {
        var reading: TemperatureReading?
        init(_ reading: TemperatureReading?) { self.reading = reading }
        func read() -> TemperatureReading? { reading }
    }

    /// A GPU sampler whose next times() call parks until `release()`, so
    /// a tick can be held open across the write half of the work.
    final class GateGPU: GPUSampling, @unchecked Sendable {
        var table: [Int32: UInt64]
        private var armed: DispatchSemaphore?
        private var held: DispatchSemaphore?
        private(set) var parked = false
        private let lock = NSLock()
        private let parks = CountGate()

        init(_ table: [Int32: UInt64]) { self.table = table }

        /// Suspends until a gated read parks, no pool thread held.
        func waitForPark() async -> Bool { await parks.reach(1) }

        func gateNext() {
            lock.lock()
            armed = DispatchSemaphore(value: 0)
            lock.unlock()
        }

        var isParked: Bool {
            lock.lock()
            defer { lock.unlock() }
            return parked
        }

        func times() -> [Int32: UInt64] {
            lock.lock()
            let gate = armed
            armed = nil
            // Only a gated call owns `held` and `parked`: an ungated call
            // must not clear the state a parked call is waiting under.
            if gate != nil { held = gate; parked = true }
            lock.unlock()
            if gate != nil { Task { await parks.bump() } }
            gate?.wait()
            if gate != nil {
                lock.lock()
                parked = false
                held = nil
                lock.unlock()
            }
            return table
        }

        func release() {
            lock.lock()
            let gate = held ?? armed
            held = nil
            armed = nil
            lock.unlock()
            gate?.signal()
        }
    }

    /// A sampler whose next call parks on a semaphore, so a test can hold
    /// a slow trend sample open while a faster refresh finishes first.
    final class GateSampler: ProcessSampling, @unchecked Sendable {
        var table: [ProcessSample]
        private(set) var calls = 0
        private var armed: DispatchSemaphore?
        private var held: DispatchSemaphore?
        private(set) var parked = false
        private let lock = NSLock()
        private let parks = CountGate()

        init(_ table: [ProcessSample]) { self.table = table }

        /// Suspends until a gated sample parks, no pool thread held.
        func waitForPark() async -> Bool { await parks.reach(1) }

        /// The next sample() call, and only that one, blocks until
        /// `release()`.
        func gateNext() {
            lock.lock()
            armed = DispatchSemaphore(value: 0)
            lock.unlock()
        }

        var isParked: Bool {
            lock.lock()
            defer { lock.unlock() }
            return parked
        }

        func sample() -> [ProcessSample] {
            lock.lock()
            calls += 1
            let gate = armed
            armed = nil
            // Only a gated call owns `held` and `parked`: an ungated call
            // must not clear the state a parked call is waiting under.
            if gate != nil { held = gate; parked = true }
            lock.unlock()
            if gate != nil { Task { await parks.bump() } }
            gate?.wait()
            if gate != nil {
                lock.lock()
                parked = false
                held = nil
                lock.unlock()
            }
            return table
        }

        func release() {
            lock.lock()
            let gate = held ?? armed
            held = nil
            armed = nil
            lock.unlock()
            gate?.signal()
        }
    }

    final class SleepLog: @unchecked Sendable {
        var durations: [Duration] = []
        let limit: Int
        let clock: Clock
        init(limit: Int, clock: Clock) { self.limit = limit; self.clock = clock }
        func call(_ duration: Duration) throws {
            // Sleeping advances the fake clock, so the next tick samples anew.
            let seconds = Double(duration.components.seconds)
                + Double(duration.components.attoseconds) / 1e18
            clock.now = clock.now.addingTimeInterval(seconds)
            durations.append(duration)
            if durations.count >= limit { throw CancellationError() }
        }
    }

    @MainActor
    private func model(sampler: CountingSampler, gpu: any GPUSampling = FakeGPU([:]),
                       temperature: any TemperatureSampling = FakeThermometer(nil),
                       clock: Clock,
                       sleep: (@Sendable (Duration) async throws -> Void)? = nil) -> ComputeModel {
        ComputeModel(sampler: sampler, matchers: { [Self.claude] },
                     gpu: gpu, temperature: temperature, clock: { clock.now },
                     sleep: sleep ?? { _ in },
                     defaults: TestDefaults("trend"),
                     selfPID: 99)
    }

    @MainActor @Test func theLoopTicksAtTheChosenInterval() async {
        let clock = Clock(Self.now)
        let sampler = CountingSampler([Self.proc(10, 1, path: "/opt/homebrew/bin/claude")])
        let log = SleepLog(limit: 3, clock: clock)
        let model = model(sampler: sampler, clock: clock) { try log.call($0) }
        await model.loadMatchers()
        await model.runTrendLoop()
        #expect(log.durations == [.seconds(10), .seconds(10), .seconds(10)])
        #expect(model.history.points(for: "claude").count == 3)
        #expect(sampler.callCount == 3)
    }

    @MainActor @Test func aTickReusesAFreshPageSample() async {
        let clock = Clock(Self.now)
        let sampler = CountingSampler([Self.proc(10, 1, path: "/opt/homebrew/bin/claude")])
        let model = model(sampler: sampler, clock: clock)
        await model.loadMatchers()
        await model.refresh()
        #expect(sampler.callCount == 1)
        // Four seconds on, the page's sample is still fresher than half
        // the ten-second interval: no second sample.
        await model.trendTick(at: Self.now.addingTimeInterval(4))
        #expect(sampler.callCount == 1)
        #expect(model.history.points(for: "claude").count == 1)
        // Eleven seconds on, the sample is stale: the tick takes its own.
        clock.now = Self.now.addingTimeInterval(11)
        await model.trendTick(at: clock.now)
        #expect(sampler.callCount == 2)
        #expect(model.history.points(for: "claude").count == 2)
    }

    @MainActor @Test func aTickSumsGPUOverTheHarnessesPids() async {
        let clock = Clock(Self.now)
        // pid 11 hangs under pid 10, so both are claude's.
        let procs = [Self.proc(10, 1, path: "/opt/homebrew/bin/claude"),
                     Self.proc(11, 10, path: "/x/helper")]
        let sampler = CountingSampler(procs)
        let gpu = FakeGPU([10: 1_000_000_000, 11: 2_000_000_000])
        let model = model(sampler: sampler, gpu: gpu, clock: clock)
        await model.loadMatchers()
        await model.refresh()
        // First sighting: no deltas yet, but the Mac does report GPU, so
        // the point has a real (zero) reading rather than nil.
        await model.trendTick(at: Self.now)
        #expect(model.history.points(for: "claude").last?.gpuPercent == 0)
        #expect(model.gpuAvailable)
        // +1s and +2s of GPU time over ten seconds of wall: 10% + 20%.
        gpu.table = [10: 2_000_000_000, 11: 4_000_000_000]
        clock.now = Self.now.addingTimeInterval(10)
        await model.trendTick(at: clock.now)
        #expect(model.history.points(for: "claude").last?.gpuPercent == 30)
    }

    @MainActor @Test func aTickRecordsTheMacsTemperatures() async {
        let clock = Clock(Self.now)
        let thermometer = FakeThermometer(nil)
        let model = model(sampler: CountingSampler([]), temperature: thermometer, clock: clock)
        // No sensors: nothing recorded, and the tab can say so.
        await model.trendTick(at: Self.now)
        #expect(model.history.temperatures.isEmpty)
        #expect(!model.temperatureAvailable)
        // Recorded even with no harness running: the Mac is still warm.
        thermometer.reading = TemperatureReading([.cpu: 58, .gpu: 61, .battery: 35],
                                                 fans: FanSummary(rpm: 3100, up: true))
        clock.now = Self.now.addingTimeInterval(10)
        await model.trendTick(at: clock.now)
        #expect(model.history.temperatures.map { $0[.cpu] } == [58])
        #expect(model.history.temperatures.last?[.battery] == 35)
        #expect(model.history.temperatures.last?[.gpu] == 61)
        #expect(model.history.temperatures.last?.fans == FanSummary(rpm: 3100, up: true))
        // Each reading goes to the fan threshold as it is recorded.
        var heard: [Double?] = []
        model.onTemperature = { heard.append($0[.cpu]) }
        thermometer.reading = TemperatureReading([.cpu: 81])
        clock.now = Self.now.addingTimeInterval(20)
        await model.trendTick(at: clock.now)
        #expect(heard == [81])
        #expect(model.temperatureAvailable)
        #expect(model.history.isEmpty)
    }

    @Test func theTemperatureRingKeepsOnePointPerTickAndItsCapacity() {
        var history = ComputeHistory()
        let at = Self.now
        history.appendTemperature(TemperaturePoint(at: at, values: [.cpu: 50]))
        history.appendTemperature(TemperaturePoint(at: at, values: [.cpu: 52]))
        #expect(history.temperatures.map { $0[.cpu] } == [52])
        for i in 1...ComputeHistory.capacity + 5 {
            history.appendTemperature(TemperaturePoint(at: at.addingTimeInterval(Double(i)),
                                                       values: [.cpu: Double(i)]))
        }
        #expect(history.temperatures.count == ComputeHistory.capacity)
        // The window ends at the newest point and spans `window` intervals.
        let shown = history.temperatureWindow(interval: .threeSeconds)
        #expect(shown.last?[.cpu] == Double(ComputeHistory.capacity + 5))
        #expect(shown.count == ComputeHistory.window * 3 + 1)
    }

    @MainActor @Test func temperatureLinesDefaultToFourAndPersist() {
        let defaults = TestDefaults("trend")
        let model = ComputeModel(sampler: CountingSampler([]), matchers: { [] }, gpu: FakeGPU([:]),
                                 temperature: FakeThermometer(nil), defaults: defaults, selfPID: 99)
        #expect(model.temperatureLines == [.cpu, .gpu, .ssd, .battery])
        model.toggleTemperatureLine(.airflow)
        model.toggleTemperatureLine(.ssd)
        #expect(defaults.stringArray(forKey: ComputeModel.temperatureLinesKey) == ["cpu", "gpu", "battery", "airflow"])
        // A fresh model reads the choice back; unknown names are skipped,
        // and switching every line off stays off.
        defaults.set(["gpu", "fan-speed"], forKey: ComputeModel.temperatureLinesKey)
        #expect(ComputeModel.loadTemperatureLines(defaults) == [.gpu])
        defaults.set([String](), forKey: ComputeModel.temperatureLinesKey)
        #expect(ComputeModel.loadTemperatureLines(defaults).isEmpty)
    }

    private func fanPoint(_ seconds: Double, up: Bool?) -> TemperaturePoint {
        TemperaturePoint(at: Self.now.addingTimeInterval(seconds), values: [.cpu: 70],
                         fans: up.map { FanSummary(rpm: $0 ? 4000 : 2317, up: $0) })
    }

    private func at(_ seconds: Double) -> Date { Self.now.addingTimeInterval(seconds) }

    @Test func fanSpansReachHalfwayToTheirNeighbours() {
        // Up at 10, 20 and a late 33 join; the down tick at 40 ends it.
        // The lone up tick at 70 reaches halfway back to the reading at 50
        // (which had no fan data) and as far again ahead.
        let points = [fanPoint(0, up: false), fanPoint(10, up: true), fanPoint(20, up: true),
                      fanPoint(33, up: true), fanPoint(40, up: false), fanPoint(50, up: nil),
                      fanPoint(70, up: true)]
        #expect(ComputeHistory.fanSpans(points, interval: .tenSeconds) == [at(5)...at(36.5), at(60)...at(80)])
        #expect(ComputeHistory.fanSpans([fanPoint(0, up: false)], interval: .tenSeconds).isEmpty)
        #expect(ComputeHistory.fanSpans([fanPoint(0, up: true)], interval: .tenSeconds) == [at(-5)...at(5)])
    }

    @Test func changingTheIntervalNeverReshadesOldReadings() {
        // Recorded every 10 s: up, down, up. A minute-long picker must
        // not join them across the down reading or widen them.
        let points = [fanPoint(0, up: true), fanPoint(10, up: false), fanPoint(20, up: true)]
        let expected = [at(-5)...at(5), at(15)...at(25)]
        #expect(ComputeHistory.fanSpans(points, interval: .tenSeconds) == expected)
        #expect(ComputeHistory.fanSpans(points, interval: .oneMinute) == expected)
        #expect(ComputeHistory.fanSpans(points, interval: .fiveMinutes) == expected)
    }

    @Test func aLongGapIsNotShadedAsUp() {
        // The Mac slept an hour between two up readings.
        let spans = ComputeHistory.fanSpans([fanPoint(0, up: true), fanPoint(3600, up: true)], interval: .tenSeconds)
        #expect(spans == [at(-150)...at(150), at(3450)...at(3750)])
    }

    @Test func temperaturesAgeOutWhenTheSensorsStopAnswering() {
        var history = ComputeHistory()
        history.appendTemperature(TemperaturePoint(at: Self.now, values: [.cpu: 50]))
        #expect(history.temperatureWindow(interval: .threeSeconds).count == 1)
        // Ticks keep coming with no reading; the old one slides off.
        let later = Self.now.addingTimeInterval(Double(ComputeHistory.window) * 3 + 1)
        history.recordTick(later)
        #expect(history.temperatureWindow(interval: .threeSeconds).isEmpty)
        #expect(history.temperatureEnd == later)
    }

    @MainActor @Test func noGPUAnswerMeansNilPointsAndNoAvailability() async {
        let clock = Clock(Self.now)
        let sampler = CountingSampler([Self.proc(10, 1, path: "/opt/homebrew/bin/claude")])
        let model = model(sampler: sampler, gpu: FakeGPU([:]), temperature: FakeThermometer(nil), clock: clock)
        await model.loadMatchers()
        await model.trendTick(at: Self.now)
        #expect(model.history.points(for: "claude").last?.gpuPercent == nil)
        #expect(!model.gpuAvailable)
    }

    @MainActor @Test func changingTheIntervalKeepsThePointsAndSaves() async {
        let defaults = TestDefaults("trend-interval")
        let clock = Clock(Self.now)
        let sampler = CountingSampler([Self.proc(10, 1, path: "/opt/homebrew/bin/claude")])
        let model = ComputeModel(sampler: sampler, matchers: { [Self.claude] },
                                 gpu: FakeGPU([:]), temperature: FakeThermometer(nil), clock: { clock.now },
                                 defaults: defaults, selfPID: 99)
        await model.loadMatchers()
        await model.trendTick(at: Self.now)
        let before = model.history.points(for: "claude")
        model.trendInterval = .fiveMinutes
        #expect(model.history.points(for: "claude") == before)
        #expect(defaults.integer(forKey: TrendInterval.defaultsKey) == 300)
    }

    @MainActor @Test func aTickLoadsTheMatcherListOnlyOnce() async {
        // Nothing starred: the loader answers empty. Five ticks must not
        // respawn `swe list` five times.
        final class Empty: @unchecked Sendable { var calls = 0 }
        let empty = Empty()
        let clock = Clock(Self.now)
        let sampler = CountingSampler([Self.proc(10, 1, path: "/opt/homebrew/bin/claude")])
        let model = ComputeModel(sampler: sampler, matchers: { empty.calls += 1; return [] },
                                 gpu: FakeGPU([:]), temperature: FakeThermometer(nil), clock: { clock.now },
                                 defaults: TestDefaults("trend"),
                                 selfPID: 99)
        for _ in 0..<5 { await model.trendTick(at: clock.now) }
        #expect(empty.calls == 1)
    }

    @MainActor @Test func aFailedMatcherLoadRetriesOnceAMinute() async {
        final class Failing: @unchecked Sendable { var calls = 0 }
        struct LoaderError: Error {}
        let failing = Failing()
        let clock = Clock(Self.now)
        let model = ComputeModel(sampler: CountingSampler([]),
                                 matchers: { failing.calls += 1; throw LoaderError() },
                                 gpu: FakeGPU([:]), temperature: FakeThermometer(nil), clock: { clock.now },
                                 defaults: TestDefaults("trend"),
                                 selfPID: 99)
        await model.trendTick(at: clock.now)
        #expect(failing.calls == 1)
        // Inside the minute: no retry.
        clock.now = Self.now.addingTimeInterval(30)
        await model.trendTick(at: clock.now)
        #expect(failing.calls == 1)
        // Past it: one retry, then quiet again.
        clock.now = Self.now.addingTimeInterval(61)
        await model.trendTick(at: clock.now)
        #expect(failing.calls == 2)
        clock.now = Self.now.addingTimeInterval(70)
        await model.trendTick(at: clock.now)
        #expect(failing.calls == 2)
    }

    @MainActor @Test func aTickAggregatesMemoryLikeTheCardsDo() async {
        let clock = Clock(Self.now)
        let procs = [Self.proc(10, 1, path: "/opt/homebrew/bin/claude", mem: 3 << 30),
                     Self.proc(11, 10, path: "/x/helper", mem: 1 << 30)]
        let sampler = CountingSampler(procs)
        let model = model(sampler: sampler, clock: clock)
        await model.loadMatchers()
        await model.trendTick(at: Self.now)
        let point = model.history.points(for: "claude").last
        #expect(point?.memoryBytes == UInt64(4) << 30)
    }

    @MainActor @Test func aStaleSampleFinishingLateCannotMoveTheMetersBack() async {
        // A trend sample that started before a page refresh but finishes
        // after it must not feed the meter its older data: the refresh's
        // baseline and the busy streak it earned stand.
        let clock = Clock(Self.now)
        let claudeBin = "/opt/homebrew/bin/claude"
        let sampler = GateSampler([Self.proc(10, 1, path: claudeBin, cpu: 0)])
        let model = ComputeModel(sampler: sampler, matchers: { [Self.claude] },
                                 gpu: FakeGPU([:]), temperature: FakeThermometer(nil), clock: { clock.now },
                                 defaults: TestDefaults("trend"),
                                 selfPID: 99)
        await model.loadMatchers()

        // A 130 s run at 100% earns the busy flag.
        await model.refresh()
        clock.now = Self.now.addingTimeInterval(130)
        sampler.table = [Self.proc(10, 1, path: claudeBin, cpu: 130_000_000_000)]
        await model.refresh()
        #expect(model.processes["claude"]?.first?.flags.contains(.busy) == true)

        // The trend tick samples first but is held; the refresh's sample
        // is newer and finishes first, so it is the one that counts.
        clock.now = Self.now.addingTimeInterval(131)
        sampler.gateNext()
        sampler.table = [Self.proc(10, 1, path: claudeBin, cpu: 180_000_000_000)]
        let tick = Task { await model.trendTick(at: Self.now.addingTimeInterval(140)) }
        // Wait for the tick's sample to take the gate: only it can park,
        // since the refresh below has not started yet. A parked tick has
        // consumed `armed`, so the refresh can never gate instead.
        #expect(await sampler.waitForPark())

        clock.now = Self.now.addingTimeInterval(135)
        sampler.table = [Self.proc(10, 1, path: claudeBin, cpu: 200_000_000_000)]
        await model.refresh()
        // 70 s of CPU over 5 s of wall, over all cores.
        #expect(model.totals.cpuPercent == 1_400)

        // The held sample lands older than the accepted refresh: ignored
        // for the meters, but the tick still records the newest data.
        sampler.release()
        await tick.value
        #expect(model.history.points(for: "claude").count == 1)
        #expect(model.totals.cpuPercent == 1_400)

        // The baseline never regressed: the next refresh deltas off the
        // 200 s counter, not the stale 180, and the streak survives.
        clock.now = Self.now.addingTimeInterval(140)
        sampler.table = [Self.proc(10, 1, path: claudeBin, cpu: 210_000_000_000)]
        await model.refresh()
        #expect(model.totals.cpuPercent == 200)
        #expect(model.processes["claude"]?.first?.flags.contains(.busy) == true)
    }

    @MainActor @Test(.timeLimit(.minutes(1))) func changingTheIntervalWakesTheLoopRightAway() async {
        // A sleep that records each requested duration, then parks at
        // `ended` until its task is cancelled (as Task.sleep observes the
        // cancellation) or the test opens it, so no loop outlives the test.
        actor Parked {
            var durations: [Duration] = []
            nonisolated let calls = CountGate()
            nonisolated let ended = HoldGate()
            func call(_ duration: Duration) async throws {
                durations.append(duration)
                await calls.bump()
                // A restart cancels this sleep's task; the loop it belongs
                // to then returns on its own.
                try await ended.pass()
                throw CancellationError()
            }
        }
        let clock = Clock(Self.now)
        let sampler = CountingSampler([Self.proc(10, 1, path: "/opt/homebrew/bin/claude")])
        let parked = Parked()
        let model = ComputeModel(sampler: sampler, matchers: { [Self.claude] },
                                 gpu: FakeGPU([:]), temperature: FakeThermometer(nil), clock: { clock.now },
                                 sleep: { d in try await parked.call(d) },
                                 defaults: TestDefaults("trend"),
                                 selfPID: 99)
        await model.loadMatchers()
        model.startTrends()
        #expect(await parked.calls.reach(1))
        // Parked on the default 10 s: switching to 3 s ticks at once and
        // parks again on the new interval instead of waiting out the old.
        model.trendInterval = .threeSeconds
        #expect(await parked.calls.reach(2))
        #expect(await parked.durations == [.seconds(10), .seconds(3)])
        // Both ticks describe the same sample moment, so the second
        // replaces the first point rather than sharing its x.
        #expect(model.history.points(for: "claude").count == 1)
        // The parked sleep throws, which ends the loop.
        parked.ended.open()
    }

    // MARK: Harness decoding

    @Test func oneSampleMakesOnePoint() {
        // Two ticks reusing a page sample agree on `at`: the second
        // replaces the first instead of sharing its x position.
        var history = ComputeHistory()
        history.append("claude", TrendPoint(at: Self.now, memoryBytes: 1,
                                            cpuPercent: 1, gpuPercent: nil))
        history.append("claude", TrendPoint(at: Self.now, memoryBytes: 2,
                                            cpuPercent: 2, gpuPercent: 3))
        #expect(history.points(for: "claude").count == 1)
        #expect(history.points(for: "claude").last?.memoryBytes == 2)
        #expect(history.points(for: "claude").last?.gpuPercent == 3)
    }

    @MainActor @Test func aReplacedLoopsTickCannotWriteBack() async {
        // Generation zero's tick is parked on its GPU read while the
        // interval change makes it stale; releasing it must not let it
        // touch the meter or the history.
        let clock = Clock(Self.now)
        let claudeBin = "/opt/homebrew/bin/claude"
        let sampler = CountingSampler([Self.proc(10, 1, path: claudeBin, cpu: 0)])
        let gpu = GateGPU([10: 1_000])
        let model = ComputeModel(sampler: sampler, matchers: { [Self.claude] },
                                 gpu: gpu, temperature: FakeThermometer(nil), clock: { clock.now },
                                 defaults: TestDefaults("trend"),
                                 selfPID: 99)
        await model.loadMatchers()

        // Generation zero's tick: samples, then parks on the GPU read.
        gpu.gateNext()
        let staleTick = Task { await model.trendTick(at: clock.now, generation: 0) }
        #expect(await gpu.waitForPark())

        // The interval change ends generation zero; generation one's tick
        // runs to the end: a first GPU sighting counts no percent.
        model.trendInterval = .threeSeconds
        await model.trendTick(at: clock.now, generation: 1)
        #expect(model.history.points(for: "claude").count == 1)
        #expect(model.history.points(for: "claude").last?.gpuPercent == 0)

        // The stale tick resumes with a moved counter; had it written,
        // the delta would repaint the point and add GPU percent.
        clock.now = Self.now.addingTimeInterval(20)
        gpu.table = [10: 3_000]
        gpu.release()
        await staleTick.value
        #expect(model.history.points(for: "claude").count == 1)
        #expect(model.history.points(for: "claude").last?.gpuPercent == 0)
    }

    @MainActor @Test(.timeLimit(.minutes(1))) func refreshDuringATickDropsTheOlderTick() async {
        // The popup's Refresh restarts the loop while the loop's own tick
        // is parked on its GPU read: the parked tick must not write back,
        // or its older counters would reset the GPU baseline.
        let clock = Clock(Self.now)
        let claudeBin = "/opt/homebrew/bin/claude"
        let sampler = CountingSampler([Self.proc(10, 1, path: claudeBin, cpu: 0)])
        let gpu = GateGPU([10: 1_000])
        // The loop sleeps only after its tick has written, so the first
        // sleep is the sign the refreshed tick is done.
        let ticked = CountGate()
        let model = ComputeModel(sampler: sampler, matchers: { [Self.claude] },
                                 gpu: gpu, temperature: FakeThermometer(nil), clock: { clock.now },
                                 sleep: { _ in
                                     await ticked.bump()
                                     throw CancellationError()
                                 },
                                 defaults: TestDefaults("trend"),
                                 selfPID: 99)
        await model.loadMatchers()

        gpu.gateNext()
        let staleTick = Task { await model.trendTick(at: clock.now, generation: 0) }
        #expect(await gpu.waitForPark())

        // Refresh: a new loop ticks at once and finishes.
        model.tickTrendsNow()
        #expect(await ticked.reach(1))
        #expect(model.history.points(for: "claude").last?.gpuPercent == 0)

        clock.now = Self.now.addingTimeInterval(20)
        gpu.table = [10: 3_000]
        gpu.release()
        await staleTick.value
        #expect(model.history.points(for: "claude").count == 1)
        #expect(model.history.points(for: "claude").last?.gpuPercent == 0)
    }

    @Test func theHarnessDecodesPrimaryColour() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let withColour = try decoder.decode(Harness.self, from: Data(#"""
        {"name":"claude","command":"claude","description":"d","aliases":[],"tags":[],
         "state":"starred","primary_color":"#d97757"}
        """#.utf8))
        #expect(withColour.primaryColor == "#d97757")
        let without = try decoder.decode(Harness.self, from: Data(#"""
        {"name":"claude","command":"claude","description":"d","aliases":[],"tags":[],"state":"starred"}
        """#.utf8))
        #expect(without.primaryColor == nil)
    }
}


@Suite struct TrendScaleTests {
    @Test func ramRoundsUpByTwoGBWithTheThresholdPlusAStepAsFloor() {
        // Data under the threshold still leaves the flag line headroom.
        #expect(TrendScale.top([0.5, 1.2], step: 2, floor: 4) == 4)
        // Data over the threshold climbs by whole steps.
        #expect(TrendScale.top([5.7], step: 2, floor: 4) == 6)
        // Exactly on a step still gets the next one, so a line ending at
        // the top keeps its trailing icon inside the plot.
        #expect(TrendScale.top([6], step: 2, floor: 4) == 8)
        // No points yet: the threshold-based floor.
        #expect(TrendScale.top([], step: 2, floor: 4) == 4)
    }

    @Test func cpuClimbsPastHundredPercentByQuarters() {
        #expect(TrendScale.top([137], step: 25, floor: 25) == 150)
        #expect(TrendScale.top([100], step: 25, floor: 25) == 125)
        #expect(TrendScale.top([12], step: 25, floor: 25) == 25)
    }

    @Test func gpuNeverDropsBelowAHundred() {
        #expect(TrendScale.top([42], step: 25, floor: 100) == 100)
        #expect(TrendScale.top([], step: 25, floor: 100) == 100)
        #expect(TrendScale.top([130], step: 25, floor: 100) == 150)
    }
}
