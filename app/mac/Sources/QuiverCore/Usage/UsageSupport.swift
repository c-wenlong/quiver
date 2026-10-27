import CryptoKit
import Foundation

/// A credential read for one fetch. It lives only in memory for the length
/// of a call: it is never written anywhere, and printing it shows nothing.
struct UsageCredential: CustomStringConvertible, CustomDebugStringConvertible {
    var token: String
    var accountID: String?
    var expiresAt: Date?
    var plan: String?
    var email: String?

    /// A non-reversible tag for the token, so a new login can be told apart
    /// from the old one without keeping the token.
    var fingerprint: String { UsageHashing.sha256Hex(token).prefix(16).description }

    var description: String { "UsageCredential(<redacted>)" }
    var debugDescription: String { description }
}

/// The outcome of looking for an account's credential.
enum CredentialLookup {
    case found(UsageCredential)
    case signedOut
    /// Present but not usable for reading limits. The reason never quotes the file.
    case unusable(String)
}

/// The parts of a successful reading that come from the response.
struct ParsedUsage {
    var plan: String?
    var email: String?
    var windows: [UsageWindow]
}

enum UsageHashing {
    static func sha256Hex(_ text: String) -> String {
        SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

/// Loose readers for the undocumented JSON the usage endpoints return.
enum UsageJSON {
    static func object(_ data: Data) -> [String: Any]? {
        (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    /// Parses `text` as a JSON object, or as hex-encoded JSON, which is how
    /// `security -w` prints an item whose bytes are not all printable.
    static func objectAllowingHex(_ text: String) -> [String: Any]? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if let object = object(Data(trimmed.utf8)) { return object }
        var hex = Substring(trimmed)
        if hex.hasPrefix("0x") || hex.hasPrefix("0X") { hex = hex.dropFirst(2) }
        guard !hex.isEmpty, hex.count.isMultiple(of: 2), hex.allSatisfy(\.isHexDigit) else { return nil }
        var bytes: [UInt8] = []
        bytes.reserveCapacity(hex.count / 2)
        var index = hex.startIndex
        while index < hex.endIndex {
            let next = hex.index(index, offsetBy: 2)
            guard let byte = UInt8(hex[index..<next], radix: 16) else { return nil }
            bytes.append(byte)
            index = next
        }
        return object(Data(bytes))
    }

    /// A finite number from a JSON number or numeric string. Booleans are not numbers.
    static func number(_ value: Any?) -> Double? {
        if let number = value as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() { return nil }
            let double = number.doubleValue
            return double.isFinite ? double : nil
        }
        if let text = value as? String, let double = Double(text.trimmingCharacters(in: .whitespaces)) {
            return double.isFinite ? double : nil
        }
        return nil
    }

    static func string(_ value: Any?) -> String? {
        guard let text = value as? String else { return nil }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// The payload of a JWT, decoded without checking the signature. Only
    /// used to read display fields (email, plan) the CLI itself stored.
    static func jwtPayload(_ token: String) -> [String: Any]? {
        let parts = token.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count >= 2 else { return nil }
        var base64 = parts[1]
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        while !base64.count.isMultiple(of: 4) { base64 += "=" }
        guard let data = Data(base64Encoded: base64) else { return nil }
        return object(data)
    }

    /// A date from an ISO 8601 string (any number of fractional digits, any
    /// offset) or from epoch seconds or milliseconds.
    static func date(_ value: Any?) -> Date? {
        if let text = string(value) { return iso8601(text) }
        guard let number = number(value) else { return nil }
        return Date(timeIntervalSince1970: number > 100_000_000_000 ? number / 1000 : number)
    }

    static func iso8601(_ text: String) -> Date? {
        // ISO8601DateFormatter only takes three fractional digits, and the
        // Claude endpoint sends six, so the fraction is split off first.
        var base = text
        var fraction = 0.0
        if let dot = text.firstIndex(of: ".") {
            let digits = text[text.index(after: dot)...].prefix { $0.isNumber }
            fraction = Double("0." + digits) ?? 0
            base = String(text[..<dot]) + String(text[text.index(after: dot)...].dropFirst(digits.count))
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: base) else { return nil }
        return date.addingTimeInterval(fraction)
    }
}

enum RetryAfter {
    /// The moment a `Retry-After` value points at: delay seconds or an HTTP
    /// date (RFC 9110). Nil when absent or unparseable.
    static func date(_ value: String?, now: Date) -> Date? {
        guard let raw = value?.trimmingCharacters(in: .whitespaces), !raw.isEmpty else { return nil }
        if let seconds = Double(raw), seconds.isFinite {
            return now.addingTimeInterval(max(0, seconds))
        }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "GMT")
        formatter.dateFormat = "EEE, dd MMM yyyy HH:mm:ss zzz"
        return formatter.date(from: raw)
    }
}

/// Turns the plan slugs providers report into the names people know.
///
/// Ported from Switchboard's `describePlan`: Claude's credential says "max"
/// with a tier such as "default_claude_max_20x"; Codex says "pro" (the 20x
/// plan) or "prolite" (the 5x one).
enum PlanNames {
    private static let families: [(String, String)] = [
        ("enterprise", "Enterprise"), ("business", "Business"), ("team", "Team"),
        // Codex's education plans; checked before "pro" and "plus" so
        // "edu_pro" stays an Edu plan, and longest first since all share "edu".
        ("edu_plus", "Edu Plus"), ("edu_pro", "Edu Pro"), ("education", "Education"), ("edu", "Edu"),
        ("max", "Max"), ("pro", "Pro"), ("plus", "Plus"), ("free", "Free"),
    ]

    static func name(_ provider: UsageProvider, _ slugs: String?...) -> String? {
        let known = slugs.compactMap { $0 }.filter { !$0.isEmpty }
        guard let first = known.first else { return nil }
        let joined = known.joined(separator: " ").lowercased()
        let family = families.first { joined.contains($0.0) }?.1 ?? first
        let multiple: Int?
        if joined.contains("20x") {
            multiple = 20
        } else if joined.contains("5x") || joined.contains("prolite") || joined.contains("pro_lite") {
            multiple = 5
        } else if provider == .codex && family == "Pro" {
            multiple = 20
        } else {
            multiple = nil
        }
        return multiple.map { "\(family) \($0)x" } ?? family
    }
}
