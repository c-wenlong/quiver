import Foundation
import Testing
@testable import QuiverCore

@Suite struct PopupTabsTests {
    private func defaults() -> UserDefaults {
        let suite = TestDefaults("PopupTabsTests")
        return suite
    }

    // MARK: ToggleList

    @Test func unorderedItemsFollowTheDraggedOnesInTheGivenOrder() {
        var list = ToggleList()
        #expect(list.ordered(["a", "b", "c"]) == ["a", "b", "c"])
        list.move("c", before: "a", in: ["a", "b", "c"])
        #expect(list.ordered(["a", "b", "c"]) == ["c", "a", "b"])
        // A new item joins at the end; an absent one keeps its place.
        #expect(list.ordered(["a", "c", "d"]) == ["c", "a", "d"])
        list.moveToEnd("c", in: ["a", "b", "c"])
        #expect(list.ordered(["a", "b", "c"]) == ["a", "b", "c"])
    }

    @Test func hiddenItemsStayInOrderedButLeaveVisible() {
        var list = ToggleList()
        list.setShown(false, "b")
        #expect(list.ordered(["a", "b"]) == ["a", "b"])
        #expect(list.visible(["a", "b"]) == ["a"])
        #expect(!list.shows("b"))
        list.setShown(true, "b")
        #expect(list.visible(["a", "b"]) == ["a", "b"])
    }

    @Test func aToggleListRoundTripsThroughDefaults() {
        let store = defaults()
        var list = ToggleList()
        list.move("b", before: "a", in: ["a", "b"])
        list.setShown(false, "a")
        list.save(to: store, orderKey: "o", hiddenKey: "h")
        #expect(ToggleList(defaults: store, orderKey: "o", hiddenKey: "h") == list)
    }

    // MARK: Tabs

    @Test func byDefaultUsageThenComputeAndUsageIsOpen() {
        let tabs = PopupTabs(defaults: defaults())
        #expect(tabs.visible == [.usage, .compute])
        #expect(tabs.current == .usage)
    }

    @Test func theLastShownTabCannotBeSwitchedOff() {
        var tabs = PopupTabs(defaults: defaults())
        tabs.setShown(false, .usage)
        #expect(tabs.visible == [.compute])
        #expect(!tabs.canHide(.compute))
        tabs.setShown(false, .compute)
        #expect(tabs.visible == [.compute])
        // A hidden tab can always come back.
        #expect(tabs.canHide(.usage))
    }

    @Test func aHiddenSelectedTabFallsBackToTheFirstShown() {
        var tabs = PopupTabs(defaults: defaults())
        tabs.selected = .compute
        #expect(tabs.current == .compute)
        tabs.setShown(false, .compute)
        #expect(tabs.current == .usage)
        // The choice is kept for when the tab is switched back on.
        tabs.setShown(true, .compute)
        #expect(tabs.current == .compute)
    }

    @Test func tabsRoundTripThroughDefaults() {
        let store = defaults()
        var tabs = PopupTabs(defaults: store)
        tabs.move("compute", before: "usage")
        tabs.setShown(false, .usage)
        tabs.selected = .compute
        tabs.save(to: store)
        let read = PopupTabs(defaults: store)
        #expect(read.all == [.compute, .usage])
        #expect(read.visible == [.compute])
        #expect(read.current == .compute)
    }

    @Test func aStoreHidingEveryTabStillShowsOne() {
        let store = defaults()
        store.set(["usage", "compute"], forKey: PopupTabs.Key.hidden)
        store.set("nonsense", forKey: PopupTabs.Key.selected)
        let tabs = PopupTabs(defaults: store)
        #expect(tabs.visible == [.usage])
        #expect(tabs.current == .usage)
    }

    @Test func anUnknownStoredTabIsIgnored() {
        let store = defaults()
        store.set(["widgets", "compute"], forKey: PopupTabs.Key.order)
        let tabs = PopupTabs(defaults: store)
        #expect(tabs.all == [.compute, .usage])
    }

    // MARK: Compute tab

    @Test func computeChartsDefaultToRamCpuGpuTemperature() {
        let popup = ComputePopup(defaults: defaults())
        #expect(popup.visibleMetrics == [.ram, .cpu, .gpu, .temperature])
    }

    @Test func computeChartsReorderHideAndRoundTrip() {
        let store = defaults()
        var popup = ComputePopup(defaults: store)
        popup.moveMetric("gpu", before: "ram")
        popup.metrics.setShown(false, "cpu")
        popup.moveMetric("ram", before: nil)
        popup.metrics.setShown(false, "temperature")
        popup.harnesses.setShown(false, "cursor")
        popup.harnesses.move("codex", before: "claude", in: ["claude", "codex", "cursor"])
        popup.save(to: store)
        let read = ComputePopup(defaults: store)
        #expect(read.allMetrics == [.gpu, .cpu, .temperature, .ram])
        #expect(read.visibleMetrics == [.gpu, .ram])
        #expect(read.harnesses.visible(["claude", "codex", "cursor"]) == ["codex", "claude"])
    }

    // MARK: Trend totals

    private let t0 = Date(timeIntervalSince1970: 1_000_000)

    private func point(_ at: Date, gb: Double, cpu: Double, gpu: Double?) -> TrendPoint {
        TrendPoint(at: at, memoryBytes: UInt64(gb * Double(UInt64(1) << 30)),
                   cpuPercent: cpu, gpuPercent: gpu)
    }

    @Test func theTotalSumsOnlyHarnessesAtTheNewestTick() {
        var history = ComputeHistory()
        history.append("claude", point(t0, gb: 1, cpu: 10, gpu: 1))
        history.append("codex", point(t0, gb: 2, cpu: 5, gpu: 2))
        // Codex stopped; only Claude reads at the newest tick.
        history.append("claude", point(t0 + 10, gb: 3, cpu: 20, gpu: 4))
        #expect(history.total(of: .ram, harnesses: ["claude", "codex"]) == 3)
        #expect(history.total(of: .cpu, harnesses: ["claude", "codex"]) == 20)
        #expect(history.total(of: .cpu, harnesses: ["codex"]) == 0)
        #expect(history.total(of: .gpu, harnesses: ["claude"]) == 4)
        #expect(ComputeHistory().total(of: .ram, harnesses: ["claude"]) == nil)
    }

    @Test func noGpuReadingMeansNoGpuTotal() {
        var history = ComputeHistory()
        history.append("claude", point(t0, gb: 1, cpu: 10, gpu: nil))
        #expect(history.total(of: .gpu, harnesses: ["claude"]) == nil)
        #expect(history.total(of: .ram, harnesses: ["claude"]) == 1)
    }

    @Test func temperatureIsNotAPerHarnessSum() {
        var history = ComputeHistory()
        history.append("claude", point(t0, gb: 1, cpu: 10, gpu: 1))
        history.appendTemperature(TemperaturePoint(at: t0, values: [.cpu: 60, .ssd: 40]))
        // Whole-Mac readings have no harness total, and no TrendPoint
        // ever carries a temperature for a line to draw.
        #expect(history.total(of: .temperature, harnesses: ["claude"]) == nil)
        #expect(!TrendMetric.temperature.reads(point(t0, gb: 1, cpu: 1, gpu: 1)))
        #expect(TrendMetric(rawValue: "temperature") == .temperature)
    }

    @Test func aTickWithNothingRunningZeroesTheTotal() {
        var history = ComputeHistory()
        history.append("claude", point(t0, gb: 2, cpu: 30, gpu: 5))
        #expect(history.total(of: .ram, harnesses: ["claude"]) == 2)
        // The last harness stopped: the next tick adds no point.
        history.recordTick(t0 + 10)
        #expect(history.total(of: .ram, harnesses: ["claude"]) == 0)
        #expect(history.total(of: .cpu, harnesses: ["claude"]) == 0)
        #expect(history.total(of: .gpu, harnesses: ["claude"]) == 0)
        // An older tick never moves "now" back.
        history.recordTick(t0)
        #expect(history.lastTick == t0 + 10)
    }

    @Test func activeListsHarnessesWithAPointInTheWindow() {
        var history = ComputeHistory()
        history.append("codex", point(t0, gb: 1, cpu: 1, gpu: nil))
        history.append("claude", point(t0 + 100, gb: 1, cpu: 1, gpu: nil))
        #expect(history.active(in: (t0 + 50)...(t0 + 100)) == ["claude"])
        #expect(history.active(in: t0...(t0 + 100)) == ["claude", "codex"])
    }

    @Test func metricScalesMatchTheComputePage() {
        let flag = UInt64(4) << 30
        #expect(TrendMetric.ram.top([1], flagBytes: flag) == 6)
        #expect(TrendMetric.ram.top([7.5], flagBytes: flag) == 8)
        #expect(TrendMetric.cpu.top([30], flagBytes: flag) == 50)
        #expect(TrendMetric.gpu.top([30], flagBytes: flag) == 100)
        #expect(TrendMetric(rawValue: "cpu") == .cpu)
        #expect(!TrendMetric.gpu.reads(point(t0, gb: 1, cpu: 1, gpu: nil)))
        #expect(TrendMetric.ram.reads(point(t0, gb: 1, cpu: 1, gpu: nil)))
    }
}
