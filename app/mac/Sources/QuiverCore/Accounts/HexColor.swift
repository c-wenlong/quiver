/// `#rrggbb` or CSS-style `#rgb`, as the profile store accepts them.
public enum HexColor {
    public struct RGB: Equatable, Sendable { public var red, green, blue: UInt8 }

    public static func rgb(_ hex: String) -> RGB? {
        var digits = Substring(hex)
        if digits.hasPrefix("#") { digits = digits.dropFirst() }
        if digits.count == 3 { digits = Substring(digits.map { "\($0)\($0)" }.joined()) }
        guard digits.count == 6, let value = UInt32(digits, radix: 16) else { return nil }
        return RGB(red: UInt8((value >> 16) & 0xff), green: UInt8((value >> 8) & 0xff), blue: UInt8(value & 0xff))
    }
}
