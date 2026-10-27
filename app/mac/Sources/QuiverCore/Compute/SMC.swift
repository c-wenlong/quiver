import Darwin
import Foundation
import IOKit

/// One SMC key's raw value: its four-letter type ("flt ", "ui8 ") and
/// its bytes as the SMC holds them.
public struct SMCValue: Sendable, Equatable {
    public var type: String
    public var bytes: [UInt8]

    public init(type: String, bytes: [UInt8]) {
        self.type = type
        self.bytes = bytes
    }

    /// The value as a number, for the types the fan keys use. Apple
    /// Silicon stores "flt " little-endian; the integer types are
    /// big-endian, as on Intel.
    public var number: Double? {
        switch type {
        case "flt " where bytes.count == 4:
            return Double(bytes.withUnsafeBytes { Float(bitPattern: UInt32(littleEndian: $0.loadUnaligned(as: UInt32.self))) })
        case "ui8 " where bytes.count == 1:
            return Double(bytes[0])
        case "ui16" where bytes.count == 2:
            return Double(UInt16(bytes[0]) << 8 | UInt16(bytes[1]))
        case "ui32" where bytes.count == 4:
            return Double(bytes.reduce(UInt32(0)) { $0 << 8 | UInt32($1) })
        default:
            return nil
        }
    }

    /// A float's bytes the way "flt " keys take them.
    public static func float(_ value: Double) -> [UInt8] {
        withUnsafeBytes(of: Float(value).bitPattern.littleEndian) { Array($0) }
    }

    /// `value` in this key's own type and size, by the same rules
    /// `number` reads it: integers big-endian, "flt " little-endian. Any
    /// one-byte key takes the byte itself, which is all the fan keys
    /// written so far ("ui8 " mode keys and Ftst) have needed. Nil for a
    /// wider type `number` doesn't know, rather than guess its layout.
    public func encoding(_ value: UInt32) -> [UInt8]? {
        switch (type, bytes.count) {
        case (_, 1) where value <= 0xff:
            return [UInt8(value)]
        case ("ui16", 2) where value <= 0xffff:
            return [UInt8(value >> 8), UInt8(value & 0xff)]
        case ("ui32", 4):
            return [24, 16, 8, 0].map { UInt8((value >> $0) & 0xff) }
        case ("flt ", 4):
            return Self.float(Double(value))
        default:
            return nil
        }
    }

    /// The type, size and raw bytes, for the helper's log.
    public var summary: String {
        let hex = bytes.map { String(format: "%02x", $0) }.joined()
        return "type '\(type)' size \(bytes.count) bytes \(hex)" + (number.map { " = \($0)" } ?? "")
    }
}

/// What came of one SMC write, so a refusal can say why.
public enum SMCWriteResult: Equatable, Sendable, CustomStringConvertible {
    case ok
    /// The SMC doesn't know the key.
    case noKey
    /// The key holds a different number of bytes than were given.
    case sizeMismatch(type: String, size: Int, given: Int)
    /// The driver call failed (`kern`) or the SMC answered an error code.
    case failed(kern: Int32, result: UInt8)
    /// Refused, with no detail (an `SMCAccess` that only answers yes or no).
    case rejected

    public var description: String {
        switch self {
        case .ok: "ok"
        case .noKey: "no such key"
        case let .sizeMismatch(type, size, given):
            "the key is '\(type)' with \(size) bytes, \(given) given"
        case let .failed(kern, result):
            "refused (kern 0x\(String(UInt32(bitPattern: kern), radix: 16)), SMC result 0x\(String(result, radix: 16)))"
        case .rejected: "refused"
        }
    }
}

/// Reads and writes SMC keys. The real one talks to the AppleSMC driver;
/// tests use a dictionary. Reads need no privileges; writes need root.
public protocol SMCAccess {
    func read(_ key: String) -> SMCValue?
    /// Whether the SMC took the write.
    @discardableResult func write(_ key: String, _ bytes: [UInt8]) -> Bool
    /// The same write, saying why when it fails.
    func writeResult(_ key: String, _ bytes: [UInt8]) -> SMCWriteResult
}

public extension SMCAccess {
    func writeResult(_ key: String, _ bytes: [UInt8]) -> SMCWriteResult {
        write(key, bytes) ? .ok : .rejected
    }
}

/// The AppleSMC user client. The connection closes when this goes away.
public final class AppleSMC: SMCAccess {
    /// The driver's SMCParamStruct, 80 bytes. The explicit padding keeps
    /// Swift from packing `result` into keyInfo's tail as C would not.
    private struct Param {
        var key: UInt32 = 0
        var version: (UInt8, UInt8, UInt8, UInt8, UInt16) = (0, 0, 0, 0, 0)
        var limits: (UInt16, UInt16, UInt32, UInt32, UInt32) = (0, 0, 0, 0, 0)
        var dataSize: UInt32 = 0
        var dataType: UInt32 = 0
        var dataAttributes: UInt8 = 0
        var padding: (UInt8, UInt8, UInt8) = (0, 0, 0)
        var result: UInt8 = 0
        var status: UInt8 = 0
        var command: UInt8 = 0
        var data32: UInt32 = 0
        var bytes: (UInt64, UInt64, UInt64, UInt64) = (0, 0, 0, 0)
    }

    private enum Command: UInt8 {
        case read = 5, write = 6, keyInfo = 9
    }

    private let connection: io_connect_t

    public init?() {
        let service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("AppleSMC"))
        guard service != 0 else { return nil }
        defer { IOObjectRelease(service) }
        var connection: io_connect_t = 0
        guard IOServiceOpen(service, mach_task_self_, 0, &connection) == KERN_SUCCESS else { return nil }
        self.connection = connection
    }

    deinit { IOServiceClose(connection) }

    static func fourCC(_ key: String) -> UInt32? {
        let scalars = Array(key.utf8)
        guard scalars.count == 4 else { return nil }
        return scalars.reduce(0) { $0 << 8 | UInt32($1) }
    }

    static func string(_ code: UInt32) -> String {
        String(decoding: [24, 16, 8, 0].map { UInt8((code >> $0) & 0xff) }, as: UTF8.self)
    }

    private func call(_ input: Param) -> Param? {
        let (status, output) = rawCall(input)
        return status == KERN_SUCCESS && output.result == 0 ? output : nil
    }

    private func rawCall(_ input: Param) -> (kern_return_t, Param) {
        var input = input
        var output = Param()
        var size = MemoryLayout<Param>.stride
        let status = IOConnectCallStructMethod(connection, 2, &input, MemoryLayout<Param>.stride,
                                               &output, &size)
        return (status, output)
    }

    private func info(_ key: UInt32) -> Param? {
        var input = Param()
        input.key = key
        input.command = Command.keyInfo.rawValue
        return call(input)
    }

    public func read(_ key: String) -> SMCValue? {
        guard let code = Self.fourCC(key), let info = info(code), info.dataSize <= 32 else { return nil }
        var input = Param()
        input.key = code
        input.dataSize = info.dataSize
        input.command = Command.read.rawValue
        guard let output = call(input) else { return nil }
        let bytes = withUnsafeBytes(of: output.bytes) { Array($0.prefix(Int(info.dataSize))) }
        return SMCValue(type: Self.string(info.dataType), bytes: bytes)
    }

    @discardableResult
    public func write(_ key: String, _ bytes: [UInt8]) -> Bool {
        writeResult(key, bytes) == .ok
    }

    /// The SMC takes a write of exactly the key's own size, so a mismatch
    /// is answered here, by name, rather than sent to the driver.
    public func writeResult(_ key: String, _ bytes: [UInt8]) -> SMCWriteResult {
        guard let code = Self.fourCC(key), let info = info(code) else { return .noKey }
        guard Int(info.dataSize) == bytes.count, bytes.count <= 32 else {
            return .sizeMismatch(type: Self.string(info.dataType), size: Int(info.dataSize), given: bytes.count)
        }
        var input = Param()
        input.key = code
        input.dataSize = info.dataSize
        input.command = Command.write.rawValue
        withUnsafeMutableBytes(of: &input.bytes) { buffer in
            for (index, byte) in bytes.enumerated() { buffer[index] = byte }
        }
        let (status, output) = rawCall(input)
        return status == KERN_SUCCESS && output.result == 0 ? .ok : .failed(kern: status, result: output.result)
    }
}
