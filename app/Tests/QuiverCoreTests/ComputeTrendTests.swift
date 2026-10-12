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
                                     homeFolder: home + "/.claude", primaryColor: "#d97757")

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

    @Test func theWindowIsTheLastWindowPoints() {
        var history = ComputeHistory()
        for i in 0..<ComputeHistory.window + 10 {
            history.append("claude", Self.point(UInt64(i)))
        }
        let window = history.window(for: "claude")
        #expect(window.count == ComputeHistory.window)
        #expect(window.first?.memoryBytes == 10)
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
        #expect(GPUSampler.gpuTime(nil) == 0)
        #expect(GPUSampler.gpuTime("not a list") == 0)
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

    @Test func aValidPrimaryColourWins() {
        let svg = ##"<path fill="#ff6daa"/>"##
        let rgb = HarnessColor.resolve(id: "claude", primaryColor: "#12ab34", colorSVG: svg)
        #expect(rgb == HarnessRGB(red: 0x12 / 255, green: 0xab / 255, blue: 0x34 / 255))
    }

    @Test func anInvalidPrimaryColourFallsThroughToTheLogo() {
        let svg = ##"<path fill="#ff6daa"/>"##
        let rgb = HarnessColor.resolve(id: "claude", primaryColor: "banana", colorSVG: svg)
        #expect(rgb == HarnessRGB(hex: "#ff6daa"))
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
        let a = HarnessColor.resolve(id: "made-up-harness", primaryColor: nil, colorSVG: nil)
        let b = HarnessColor.resolve(id: "made-up-harness", primaryColor: nil, colorSVG: nil)
        #expect(a == b)
        #expect(HarnessColor.palette.contains(a))
    }

    @Test func aDarkColourIsLiftedToReadOnTheCard() {
        let rgb = HarnessColor.resolve(id: "cursor", primaryColor: "#000000", colorSVG: nil)
        #expect(rgb.luminance >= HarnessColor.luminanceFloor)
        #expect(rgb.red > 0 && rgb.green == rgb.red && rgb.blue == rgb.red)
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
                       clock: Clock,
                       sleep: (@Sendable (Duration) async throws -> Void)? = nil) -> ComputeModel {
        ComputeModel(sampler: sampler, matchers: { [Self.claude] },
                     gpu: gpu, clock: { clock.now },
                     sleep: sleep ?? { _ in },
                     defaults: UserDefaults(suiteName: "trend-\(UUID().uuidString)")!,
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
        await model.trendTick(at: Self.now.addingTimeInterval(11))
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

    @MainActor @Test func noGPUAnswerMeansNilPointsAndNoAvailability() async {
        let clock = Clock(Self.now)
        let sampler = CountingSampler([Self.proc(10, 1, path: "/opt/homebrew/bin/claude")])
        let model = model(sampler: sampler, gpu: FakeGPU([:]), clock: clock)
        await model.loadMatchers()
        await model.trendTick(at: Self.now)
        #expect(model.history.points(for: "claude").last?.gpuPercent == nil)
        #expect(!model.gpuAvailable)
    }

    @MainActor @Test func changingTheIntervalKeepsThePointsAndSaves() async {
        let suite = "trend-interval-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let clock = Clock(Self.now)
        let sampler = CountingSampler([Self.proc(10, 1, path: "/opt/homebrew/bin/claude")])
        let model = ComputeModel(sampler: sampler, matchers: { [Self.claude] },
                                 gpu: FakeGPU([:]), clock: { clock.now },
                                 defaults: defaults, selfPID: 99)
        await model.loadMatchers()
        await model.trendTick(at: Self.now)
        let before = model.history.points(for: "claude")
        model.trendInterval = .fiveMinutes
        #expect(model.history.points(for: "claude") == before)
        #expect(defaults.integer(forKey: TrendInterval.defaultsKey) == 300)
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

    // MARK: Harness decoding

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

