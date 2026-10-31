import Darwin
import Foundation

/// A part of the Mac the Temperature tab can draw a line for, in the
/// fixed order its colours are assigned.
public enum ThermalComponent: String, CaseIterable, Codable, Sendable, Comparable {
    case cpu, gpu, memory, ssd, battery, airflow, display

    public var title: String {
        switch self {
        case .cpu: "CPU"
        case .gpu: "GPU"
        case .memory: "Memory"
        case .ssd: "SSD"
        case .battery: "Battery"
        case .airflow: "Airflow"
        case .display: "Display"
        }
    }

    /// The lines a first look shows; the rest are a click away.
    public static let shownByDefault: [ThermalComponent] = [.cpu, .gpu, .ssd, .battery]

    public static func < (lhs: Self, rhs: Self) -> Bool {
        allCases.firstIndex(of: lhs)! < allCases.firstIndex(of: rhs)!
    }
}

/// What the fans were doing at one moment.
public struct FanSummary: Sendable, Equatable {
    /// The fastest fan's speed.
    public var rpm: Double
    /// Whether any fan ran clearly faster than its idle floor.
    public var up: Bool

    public init(rpm: Double, up: Bool) {
        self.rpm = rpm
        self.up = up
    }

    /// How far above its minimum a fan must run to count as up. A fan
    /// can idle a little over its floor (an M4 Pro's second fan sits near
    /// 2,500 rpm against a 2,317 minimum), so any rpm over it is not
    /// enough; 15% clears that idle drift.
    public static let upMargin = 0.15

    /// Nil on a Mac without fans or when the SMC does not answer.
    public static func from(_ fans: [FanReading]) -> FanSummary? {
        guard let fastest = fans.map(\.actual).max() else { return nil }
        let up = fans.contains { $0.minimum > 0 && $0.actual > $0.minimum * (1 + upMargin) }
        return FanSummary(rpm: fastest, up: up)
    }
}

/// The Mac's temperatures at one moment, in degrees Celsius: one value
/// per component that answered, the mean of that component's sensors,
/// and what the fans were doing.
public struct TemperatureReading: Sendable, Equatable {
    public var values: [ThermalComponent: Double]
    public var fans: FanSummary?

    public init(_ values: [ThermalComponent: Double], fans: FanSummary? = nil) {
        self.values = values
        self.fans = fans
    }

    public subscript(_ component: ThermalComponent) -> Double? { values[component] }

    /// Anything outside 10 to 150 °C is a sensor with nothing on it (the
    /// HID "tdev" ones answer about -9200, unused SMC keys about 8).
    static func plausible(_ celsius: Double) -> Bool { celsius > 10 && celsius < 150 }

    static func mean(_ values: [Double]) -> Double? {
        let kept = values.filter(plausible)
        return kept.isEmpty ? nil : kept.reduce(0, +) / Double(kept.count)
    }

    /// Folds HID (sensor name, value) pairs into a reading. Apple Silicon
    /// names its die sensors "PMU tdie<n>", the battery's "gas gauge
    /// battery" and the SSD's "NAND CH<n> temp", each listed once per
    /// controller. The die sensors cover the CPU and GPU cores together,
    /// so they stand in for the CPU only when the SMC has nothing better.
    /// Nil when no known sensor answered.
    public static func summarise(_ sensors: [(name: String, celsius: Double)]) -> TemperatureReading? {
        func group(_ prefix: String) -> [Double] { sensors.filter { $0.name.hasPrefix(prefix) }.map(\.celsius) }
        var values: [ThermalComponent: Double] = [:]
        values[.cpu] = mean(group("PMU tdie"))
        values[.battery] = mean(group("gas gauge battery"))
        values[.ssd] = mean(group("NAND CH"))
        return values.isEmpty ? nil : TemperatureReading(values)
    }

    /// `self` with `other` filling the components `self` lacks.
    public func filling(from other: TemperatureReading?) -> TemperatureReading {
        TemperatureReading(values.merging(other?.values ?? [:]) { mine, _ in mine }, fans: fans ?? other?.fans)
    }
}

/// Reads the Mac's temperatures. The real one asks the HID event system;
/// tests use a constant.
public protocol TemperatureSampling: Sendable {
    func read() -> TemperatureReading?
}

/// Temperatures from the HID event system, the same place `powermetrics`
/// and menu bar monitors read them, with no root needed. The calls are
/// private IOKit symbols, so they are looked up at run time: a macOS that
/// drops one answers nil and the Temperature tab says so.
public struct HIDTemperatureSampler: TemperatureSampling {
    public init() {}

    private typealias Create = @convention(c) (CFAllocator?) -> UnsafeMutableRawPointer?
    private typealias SetMatching = @convention(c) (UnsafeMutableRawPointer, CFDictionary) -> Int32
    private typealias CopyServices = @convention(c) (UnsafeMutableRawPointer) -> UnsafeMutableRawPointer?
    private typealias CopyProperty = @convention(c) (UnsafeRawPointer, CFString) -> UnsafeMutableRawPointer?
    private typealias CopyEvent = @convention(c) (UnsafeRawPointer, Int64, Int32, Int64) -> UnsafeMutableRawPointer?
    private typealias FloatValue = @convention(c) (UnsafeRawPointer, Int32) -> Double

    /// kIOHIDEventTypeTemperature, and its one field.
    private static let temperatureEvent: Int64 = 15
    private static let temperatureField = Int32(15 << 16)

    /// The six calls, resolved once for the process; nil when this macOS
    /// lacks any of them. IOKit is already loaded, so the one dlopen only
    /// takes a handle and is never closed.
    private struct Calls {
        var create: Create
        var setMatching: SetMatching
        var copyServices: CopyServices
        var copyProperty: CopyProperty
        var copyEvent: CopyEvent
        var floatValue: FloatValue
    }

    private static let calls: Calls? = {
        guard let handle = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_LAZY) else { return nil }
        func symbol<T>(_ name: String, as: T.Type) -> T? {
            dlsym(handle, name).map { unsafeBitCast($0, to: T.self) }
        }
        guard let create = symbol("IOHIDEventSystemClientCreate", as: Create.self),
              let setMatching = symbol("IOHIDEventSystemClientSetMatching", as: SetMatching.self),
              let copyServices = symbol("IOHIDEventSystemClientCopyServices", as: CopyServices.self),
              let copyProperty = symbol("IOHIDServiceClientCopyProperty", as: CopyProperty.self),
              let copyEvent = symbol("IOHIDServiceClientCopyEvent", as: CopyEvent.self),
              let floatValue = symbol("IOHIDEventGetFloatValue", as: FloatValue.self)
        else { return nil }
        return Calls(create: create, setMatching: setMatching, copyServices: copyServices,
                     copyProperty: copyProperty, copyEvent: copyEvent, floatValue: floatValue)
    }()

    public func read() -> TemperatureReading? {
        guard let calls = Self.calls, let rawClient = calls.create(kCFAllocatorDefault) else { return nil }
        let (setMatching, copyServices, copyProperty, copyEvent, floatValue) =
            (calls.setMatching, calls.copyServices, calls.copyProperty, calls.copyEvent, calls.floatValue)
        // Every Copy and Create hands back a +1 reference; ARC takes it.
        let client = Unmanaged<AnyObject>.fromOpaque(rawClient).takeRetainedValue()
        // Usage page 0xff00, usage 5: Apple's vendor temperature sensors.
        let matching = ["PrimaryUsagePage": 0xff00, "PrimaryUsage": 5] as CFDictionary
        _ = setMatching(rawClient, matching)
        guard let rawServices = copyServices(rawClient) else { return nil }
        let services = Unmanaged<CFArray>.fromOpaque(rawServices).takeRetainedValue() as [AnyObject]
        var sensors: [(name: String, celsius: Double)] = []
        for service in services {
            let pointer = Unmanaged.passUnretained(service).toOpaque()
            guard let rawName = copyProperty(pointer, "Product" as CFString),
                  let name = Unmanaged<AnyObject>.fromOpaque(rawName).takeRetainedValue() as? String,
                  let rawEvent = copyEvent(pointer, Self.temperatureEvent, 0, 0)
            else { continue }
            let event = Unmanaged<AnyObject>.fromOpaque(rawEvent).takeRetainedValue()
            sensors.append((name, floatValue(Unmanaged.passUnretained(event).toOpaque(), Self.temperatureField)))
        }
        withExtendedLifetime(client) {}
        return TemperatureReading.summarise(sensors)
    }
}

/// Which SMC keys hold which component's temperature, per chip. Apple
/// documents none of them; the map follows Stats (github.com/exelban/stats,
/// Modules/Sensors/values.swift), which keeps one list per generation
/// because the same key can mean a different core on another chip.
public enum ThermalKeys {
    /// "Apple M4 Pro" -> (4, "pro"); "Apple A18 Pro" -> (18, "a18pro").
    /// Nil for an Intel brand string or anything unrecognised.
    public static func chip(_ brand: String) -> (generation: Int, variant: String)? {
        let words = brand.lowercased().split(separator: " ")
        guard words.first == "apple", words.count >= 2 else { return nil }
        let name = words[1]
        let variant = words.dropFirst(2).first.map(String.init) ?? "base"
        if name.hasPrefix("m"), let generation = Int(name.dropFirst()) { return (generation, variant) }
        if name == "a18", variant == "pro" { return (18, "a18pro") }
        return nil
    }

    /// Keys every Apple Silicon Mac may answer; a missing one just reads nil.
    static let common: [ThermalComponent: [String]] = [
        .ssd: ["TH0x"],
        .battery: ["TB0T", "TB1T", "TB2T"],
        .airflow: ["TaLP", "TaRF"],
        .display: ["TL0P"],
    ]

    /// Every component's keys for the chip named by `brand`. An unknown
    /// chip gets the common keys only, so its CPU line comes from HID.
    public static func keys(forBrand brand: String) -> [ThermalComponent: [String]] {
        var keys = common
        guard let chip = chip(brand) else { return keys }
        switch chip.generation {
        case 1:
            keys[.cpu] = ["Tp01", "Tp05", "Tp09", "Tp0D", "Tp0H", "Tp0L", "Tp0P", "Tp0T", "Tp0X", "Tp0b"]
            keys[.gpu] = ["Tg05", "Tg0D", "Tg0L", "Tg0T"]
            keys[.memory] = ["Tm02", "Tm06", "Tm08", "Tm09"]
        case 2:
            keys[.cpu] = ["Tp01", "Tp05", "Tp09", "Tp0D", "Tp0X", "Tp0b", "Tp0f", "Tp0j",
                          "Tp1h", "Tp1l", "Tp1p", "Tp1t"]
            keys[.gpu] = ["Tg0f", "Tg0j"]
        case 3:
            keys[.cpu] = ["Te05", "Te0L", "Te0P", "Te0S", "Tf04", "Tf09", "Tf0A", "Tf0B", "Tf0D", "Tf0E",
                          "Tf44", "Tf49", "Tf4A", "Tf4B", "Tf4D", "Tf4E"]
            keys[.gpu] = ["Tf14", "Tf18", "Tf19", "Tf1A", "Tf24", "Tf28", "Tf29", "Tf2A"]
        case 4:
            keys[.cpu] = ["Te05", "Te0S", "Te09", "Te0H", "Tp01", "Tp05", "Tp09", "Tp0D",
                          "Tp0V", "Tp0Y", "Tp0b", "Tp0e"]
            keys[.gpu] = (chip.variant == "base" ? ["Tg0G", "Tg0H"] : ["Tg1U", "Tg1k"])
                + ["Tg0K", "Tg0L", "Tg0d", "Tg0e", "Tg0j", "Tg0k"]
            keys[.memory] = ["Tm0p", "Tm1p", "Tm2p"]
        case 5:
            keys[.cpu] = ["Tp00", "Tp04", "Tp08", "Tp0C", "Tp0G", "Tp0K", "Tp0O", "Tp0R", "Tp0U", "Tp0X",
                          "Tp0a", "Tp0d", "Tp0g", "Tp0j", "Tp0m", "Tp0p", "Tp0u", "Tp0y"]
            keys[.gpu] = ["Tg0U", "Tg0X", "Tg0d", "Tg0g", "Tg0j", "Tg1Y", "Tg1c", "Tg1g"]
        case 6:
            keys[.cpu] = ["Te07", "Te08", "Te09", "Te0c", "Te0h", "Te0i", "Tp05", "Tp07", "Tp09", "Tp0b",
                          "Tp0d", "Tp0E", "Tp0G", "Tp0g", "Tp0I", "Tp0j", "Tp0L", "Tp0m"]
            keys[.gpu] = ["Tg1e", "Tg0d", "Tg0c", "Tg0b", "Tg1c", "Tg1d", "Tg07", "Tg1f",
                          "Tg09", "Tg0a", "Tg1g", "Tg1h"]
        case 18:
            keys[.cpu] = ["Te05", "Te0S", "Tp05", "Tp0D"]
            keys[.gpu] = ["Tg05", "Tg0D", "Tg0L", "Tg0e"]
        default:
            break
        }
        return keys
    }

    /// The SMC's "flt " temperatures folded per component, nil when not
    /// one key answered with a plausible value.
    public static func read(_ keys: [ThermalComponent: [String]], from smc: any SMCAccess) -> TemperatureReading? {
        var values: [ThermalComponent: Double] = [:]
        for (component, names) in keys {
            values[component] = TemperatureReading.mean(names.compactMap { smc.read($0)?.number })
        }
        return values.isEmpty ? nil : TemperatureReading(values)
    }
}

/// The Mac's temperatures: the SMC's per-component keys first, the HID
/// sensors for whatever the SMC left out (the CPU on a chip the key map
/// does not know yet). Neither needs root.
public struct MacTemperatureSampler: TemperatureSampling {
    private let keys: [ThermalComponent: [String]]
    private let hid: any TemperatureSampling

    public init(brand: String = MacTemperatureSampler.brand(), hid: any TemperatureSampling = HIDTemperatureSampler()) {
        keys = ThermalKeys.keys(forBrand: brand)
        self.hid = hid
    }

    /// `machdep.cpu.brand_string`, "Apple M4 Pro" on this kind of Mac.
    public static func brand() -> String {
        var size = 0
        guard sysctlbyname("machdep.cpu.brand_string", nil, &size, nil, 0) == 0, size > 0 else { return "" }
        var buffer = [CChar](repeating: 0, count: size)
        guard sysctlbyname("machdep.cpu.brand_string", &buffer, &size, nil, 0) == 0 else { return "" }
        return String(decoding: buffer.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
    }

    /// The components `TemperatureReading.summarise` can take from HID.
    static let hidComponents: Set<ThermalComponent> = [.cpu, .battery, .ssd]

    public func read() -> TemperatureReading? {
        let smc = AppleSMC()
        let temperatures = smc.flatMap { ThermalKeys.read(keys, from: $0) }
        guard var reading = Self.combine(temperatures, hid: hid.read) else { return nil }
        reading.fans = smc.flatMap { FanSummary.from(FanKeys.read($0)) }
        return reading
    }

    /// The SMC's reading with HID filling the gaps. HID only fills what the
    /// SMC left out, and reading every sensor's event was the trend tick's
    /// largest cost, so `hid` is not called when the SMC already answered
    /// everything HID could add.
    static func combine(_ temperatures: TemperatureReading?,
                        hid: () -> TemperatureReading?) -> TemperatureReading? {
        let answered = Set(temperatures?.values.keys.map { $0 } ?? [])
        let fallback = hidComponents.isSubset(of: answered) ? nil : hid()
        guard temperatures != nil || fallback != nil else { return nil }
        return (temperatures ?? TemperatureReading([:])).filling(from: fallback)
    }
}
