import Foundation

/// ISO 8601 timestamps as the harnesses write them. The common UTC shape
/// (`2026-10-03T10:00:03.123Z`) is parsed by hand because a full history has
/// millions of them; anything else goes through Foundation.
enum Timestamp {
    static func parse(_ text: String) -> Date? {
        if let fast = parseUTC(Array(text.utf8)) { return fast }
        if let date = try? Date(text, strategy: Date.ISO8601FormatStyle(includingFractionalSeconds: true)) {
            return date
        }
        return try? Date(text, strategy: Date.ISO8601FormatStyle())
    }

    private static func parseUTC(_ b: [UInt8]) -> Date? {
        // YYYY-MM-DDTHH:MM:SS[.fraction]Z
        guard b.count >= 20, b[4] == 0x2D, b[7] == 0x2D, b[10] == 0x54 || b[10] == 0x20,
              b[13] == 0x3A, b[16] == 0x3A, b.last == 0x5A
        else { return nil }
        func digits(_ start: Int, _ count: Int) -> Int? {
            var value = 0
            for index in start..<(start + count) {
                let c = b[index]
                guard c >= 0x30, c <= 0x39 else { return nil }
                value = value * 10 + Int(c - 0x30)
            }
            return value
        }
        guard let year = digits(0, 4), let month = digits(5, 2), let day = digits(8, 2),
              let hour = digits(11, 2), let minute = digits(14, 2), let second = digits(17, 2),
              (1...12).contains(month), (1...31).contains(day), hour < 24, minute < 60, second < 61
        else { return nil }
        var fraction = 0.0
        if b.count > 20 {
            guard b[19] == 0x2E else { return nil }
            var scale = 0.1
            for index in 20..<(b.count - 1) {
                let c = b[index]
                guard c >= 0x30, c <= 0x39 else { return nil }
                fraction += Double(c - 0x30) * scale
                scale /= 10
            }
        } else if b[19] != 0x5A {
            return nil
        }
        // Days from civil (Howard Hinnant's algorithm).
        let y = month <= 2 ? year - 1 : year
        let era = (y >= 0 ? y : y - 399) / 400
        let yoe = y - era * 400
        let mp = (month + 9) % 12
        let doy = (153 * mp + 2) / 5 + day - 1
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy
        let days = era * 146_097 + doe - 719_468
        let seconds = Double(days * 86_400 + hour * 3_600 + minute * 60 + second) + fraction
        return Date(timeIntervalSince1970: seconds)
    }
}
