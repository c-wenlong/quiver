import Darwin
import Foundation
import Security

/// Which app may own a fan request, pinned when the helper is installed:
/// the signature of the SWE that installed it. It goes into the root
/// launchd job's arguments, which only root can change, because the
/// installed helper is re-signed ad hoc and has no team of its own to
/// compare against.
public enum FanOwnerPin: Equatable, Sendable {
    /// A team-signed SWE: code Apple signed for this team, under the
    /// app's own signing identifier. The team alone isn't enough: the
    /// team also signs the Python bundled for swe, which runs any script.
    case team(String, identifier: String)
    /// An ad-hoc SWE: exactly the build that installed the helper,
    /// by its code directory hash (lowercase hex, 20 bytes).
    case cdhash(String)

    static let teamFlag = "--owner-team"
    static let identifierFlag = "--owner-identifier"
    static let cdhashFlag = "--owner-cdhash"

    /// The team to check the bundled helper against before it runs as
    /// root; nil for an ad-hoc build, which keeps the hash check alone.
    public var team: String? {
        if case .team(let team, _) = self { team } else { nil }
    }

    /// The pin's place in the launchd job's arguments.
    public var arguments: [String] {
        switch self {
        case .team(let team, let identifier): [Self.teamFlag, team, Self.identifierFlag, identifier]
        case .cdhash(let hash): [Self.cdhashFlag, hash]
        }
    }

    /// The code requirement an owner must meet. Built only from values
    /// `isValid` accepted, so nothing in it comes from outside unchecked.
    public var requirement: String {
        switch self {
        case .team(let team, let identifier):
            "identifier \"\(identifier)\" and anchor apple generic and certificate leaf[subject.OU] = \"\(team)\""
        case .cdhash(let hash): "cdhash H\"\(hash)\""
        }
    }

    public var isValid: Bool {
        switch self {
        case .team(let team, let identifier):
            AdministratorInstaller.isTeamIdentifier(team) && Self.isSigningIdentifier(identifier)
        case .cdhash(let hash): hash.count == 40 && hash.allSatisfy { "0123456789abcdef".contains($0) }
        }
    }

    /// A signing identifier as codesign writes one from a bundle id:
    /// letters, digits, dots and dashes, so it can't break out of the
    /// requirement's quotes.
    static func isSigningIdentifier(_ value: String) -> Bool {
        (1...255).contains(value.utf8.count) && value.utf8.allSatisfy {
            ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x41 && $0 <= 0x5A) || ($0 >= 0x61 && $0 <= 0x7A)
                || $0 == 0x2E || $0 == 0x2D
        }
    }

    /// The pin in the helper's arguments, or nil when there is none, more
    /// than one, a team without its identifier, or a value that isn't a
    /// real team id, identifier or cdhash: the helper then refuses to
    /// start rather than trust anyone.
    public static func parse(_ arguments: [String]) -> FanOwnerPin? {
        var teams: [String] = [], identifiers: [String] = [], hashes: [String] = []
        var rest = arguments.dropFirst().makeIterator()
        while let argument = rest.next() {
            switch argument {
            case teamFlag: teams.append(rest.next() ?? "")
            case identifierFlag: identifiers.append(rest.next() ?? "")
            case cdhashFlag: hashes.append(rest.next() ?? "")
            default: break
            }
        }
        let pin: FanOwnerPin
        switch (teams.count, identifiers.count, hashes.count) {
        case (1, 1, 0): pin = .team(teams[0], identifier: identifiers[0])
        case (0, 0, 1): pin = .cdhash(hashes[0])
        default: return nil
        }
        return pin.isValid ? pin : nil
    }

    /// The running app's pin: its team and identifier when a team signed
    /// it, else its cdhash. Throws when the signature can't be read or the code on disk
    /// no longer matches what is running: a bundle re-signed under the app
    /// must not read as "ad-hoc" and skip the helper's check.
    public static func current() throws -> FanOwnerPin {
        func check(_ status: OSStatus, _ what: String) throws {
            guard status == errSecSuccess else {
                throw FanHelperInstallError.failed("Couldn't check SWE's own signature (\(what): \(status)).")
            }
        }
        var code: SecCode?
        try check(SecCodeCopySelf([], &code), "self")
        guard let code else { throw FanHelperInstallError.failed("Couldn't check SWE's own signature.") }
        try check(SecCodeCheckValidity(code, [], nil), "validity")
        var staticCode: SecStaticCode?
        try check(SecCodeCopyStaticCode(code, [], &staticCode), "static code")
        guard let staticCode else { throw FanHelperInstallError.failed("Couldn't check SWE's own signature.") }
        var info: CFDictionary?
        try check(SecCodeCopySigningInformation(staticCode, SecCSFlags(rawValue: kSecCSSigningInformation), &info),
                  "signing information")
        let fields = info as? [String: Any]
        if let team = fields?[kSecCodeInfoTeamIdentifier as String] as? String, !team.isEmpty {
            guard AdministratorInstaller.isTeamIdentifier(team) else {
                throw FanHelperInstallError.failed("SWE's signature names an odd team (\(team)).")
            }
            let identifier = fields?[kSecCodeInfoIdentifier as String] as? String ?? ""
            guard isSigningIdentifier(identifier) else {
                throw FanHelperInstallError.failed("SWE's signature names an odd identifier (\(identifier)).")
            }
            return .team(team, identifier: identifier)
        }
        guard let unique = fields?[kSecCodeInfoUnique as String] as? Data, unique.count == 20 else {
            throw FanHelperInstallError.failed("SWE's signature has no code directory hash.")
        }
        return .cdhash(unique.map { String(format: "%02x", $0) }.joined())
    }
}

/// The helper's owner check: whether the process that a request names is
/// running and signed the way the pin says. Never by name: any program
/// the user runs can call itself SWE.
///
/// The process is looked up by audit token, which carries the pid's
/// version as well as the pid, and each answer is kept for that version:
/// the check runs a few times a second, a signature can't change under a
/// running process, and a pid reused by another process gets a new
/// version, so it is checked afresh.
public final class FanOwnerCheck: @unchecked Sendable {
    private let requirement: SecRequirement
    private let token: (Int32) -> audit_token_t?
    private let satisfies: (audit_token_t, SecRequirement) -> Bool
    private let lock = NSLock()
    private var cached: (pid: Int32, version: UInt32, owner: Bool)?

    /// Nil when the pin's requirement doesn't compile; the helper then
    /// refuses to start.
    public init?(pin: FanOwnerPin,
                 token: @escaping (Int32) -> audit_token_t? = FanOwnerCheck.auditToken,
                 satisfies: @escaping (audit_token_t, SecRequirement) -> Bool = FanOwnerCheck.satisfies) {
        guard pin.isValid else { return nil }
        var requirement: SecRequirement?
        guard SecRequirementCreateWithString(pin.requirement as CFString, [], &requirement) == errSecSuccess,
              let requirement else { return nil }
        self.requirement = requirement
        self.token = token
        self.satisfies = satisfies
    }

    /// Whether `pid` is a running process that meets the pin. A pid
    /// nothing has, or that now belongs to something else, does not.
    public func isOwner(_ pid: Int32) -> Bool {
        guard pid > 1, let token = token(pid), Self.pid(of: token) == pid else { return false }
        let version = Self.pidVersion(of: token)
        return lock.withLock {
            if let cached, cached.pid == pid, cached.version == version { return cached.owner }
            let owner = satisfies(token, requirement)
            cached = (pid, version, owner)
            return owner
        }
    }

    /// The pid and its version, from the token's fields as the kernel
    /// fills them (`audit_token_to_pid` and `audit_token_to_pidversion`
    /// read the same two; they live in libbsm, which nothing else here
    /// links).
    static func pid(of token: audit_token_t) -> Int32 { Int32(bitPattern: token.val.5) }
    static func pidVersion(of token: audit_token_t) -> UInt32 { token.val.7 }

    /// The running process's audit token, or nil when there is no such
    /// process. A task name port is the least a caller can hold on a
    /// task; root gets one for any process.
    public static func auditToken(of pid: Int32) -> audit_token_t? {
        var task = mach_port_name_t()
        guard task_name_for_pid(mach_task_self_, pid, &task) == KERN_SUCCESS else { return nil }
        defer { mach_port_deallocate(mach_task_self_, task) }
        var token = audit_token_t()
        var count = mach_msg_type_number_t(MemoryLayout<audit_token_t>.size / MemoryLayout<natural_t>.size)
        let status = withUnsafeMutablePointer(to: &token) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(task, task_flavor_t(TASK_AUDIT_TOKEN), $0, &count)
            }
        }
        return status == KERN_SUCCESS ? token : nil
    }

    /// Whether the process behind `token` is validly signed and meets
    /// `requirement`, checked by the Security framework against the
    /// running code, not a file at some path.
    public static func satisfies(_ token: audit_token_t, _ requirement: SecRequirement) -> Bool {
        let audit = withUnsafeBytes(of: token) { Data($0) }
        var code: SecCode?
        guard SecCodeCopyGuestWithAttributes(nil, [kSecGuestAttributeAudit: audit] as CFDictionary, [], &code)
                == errSecSuccess,
              let code else { return false }
        return SecCodeCheckValidity(code, [], requirement) == errSecSuccess
    }
}
