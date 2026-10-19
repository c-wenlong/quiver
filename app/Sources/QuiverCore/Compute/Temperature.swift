import Darwin
import Foundation

/// The Mac's temperatures at one moment, in degrees Celsius. Nil when no
/// sensor of that kind answered.
public struct TemperatureReading: Sendable, Equatable {
    /// The mean of the SoC die sensors, which cover the CPU and GPU cores.
    public var cpu: Double?
    /// The hottest single die sensor.
    public var hottest: Double?
    public var battery: Double?

    public init(cpu: Double?, hottest: Double?, battery: Double?) {
        self.cpu = cpu
        self.hottest = hottest
        self.battery = battery
    }

    /// Folds raw (sensor name, value) pairs into a reading. Apple Silicon
    /// names its die sensors "PMU tdie<n>" and the battery's "gas gauge
    /// battery", each listed once per controller; anything outside 0 to
    /// 150 °C is a sensor with nothing on it (the "tdev" ones answer
    /// about -9200) and is dropped. Nil when neither kind answered.
    public static func summarise(_ sensors: [(name: String, celsius: Double)]) -> TemperatureReading? {
        let plausible = sensors.filter { $0.celsius > 0 && $0.celsius < 150 }
        let dies = plausible.filter { $0.name.hasPrefix("PMU tdie") }.map(\.celsius)
        let battery = plausible.filter { $0.name.hasPrefix("gas gauge battery") }.map(\.celsius)
        guard !dies.isEmpty || !battery.isEmpty else { return nil }
        return TemperatureReading(cpu: mean(dies), hottest: dies.max(), battery: mean(battery))
    }

    private static func mean(_ values: [Double]) -> Double? {
        values.isEmpty ? nil : values.reduce(0, +) / Double(values.count)
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

    private static func symbol<T>(_ name: String, as: T.Type) -> T? {
        guard let handle = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_LAZY),
              let pointer = dlsym(handle, name) else { return nil }
        return unsafeBitCast(pointer, to: T.self)
    }

    public func read() -> TemperatureReading? {
        guard let create = Self.symbol("IOHIDEventSystemClientCreate", as: Create.self),
              let setMatching = Self.symbol("IOHIDEventSystemClientSetMatching", as: SetMatching.self),
              let copyServices = Self.symbol("IOHIDEventSystemClientCopyServices", as: CopyServices.self),
              let copyProperty = Self.symbol("IOHIDServiceClientCopyProperty", as: CopyProperty.self),
              let copyEvent = Self.symbol("IOHIDServiceClientCopyEvent", as: CopyEvent.self),
              let floatValue = Self.symbol("IOHIDEventGetFloatValue", as: FloatValue.self),
              let rawClient = create(kCFAllocatorDefault)
        else { return nil }
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
