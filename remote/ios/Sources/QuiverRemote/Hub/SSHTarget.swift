import Foundation

/// `GET /v1/ssh`: what the hub says about logging in to its machine.
struct SSHInfo: Decodable, Equatable {
    struct Device: Decodable, Equatable {
        var publicKey: String?
        var installed: Bool
    }

    var enabled: Bool
    var user: String
    var port: Int
    var listening: Bool
    var hostKeys: [String]
    var device: Device
}

/// Where the console dials and which host keys it trusts there. Kept per
/// hub, because the console's whole point is the hub being down: a
/// target saved while the hub answered is what it uses then.
struct SSHTarget: Codable, Equatable {
    var host: String
    var port: Int
    var user: String
    var hostKeys: [String]

    var address: String { "\(user)@\(host)" }
}

/// What the console does next, from the hub's answer.
enum SSHReadiness: Equatable {
    /// The hub has `swe remote ssh` off, so no phone key is installed.
    case hubOff
    /// Nothing answers on the SSH port (Remote Login is off).
    case sshdOff(port: Int)
    /// sshd has no host key this app can check, so it won't connect.
    case noHostKeys
    case ready

    static func of(_ info: SSHInfo) -> SSHReadiness {
        if !info.enabled { return .hubOff }
        if !info.listening { return .sshdOff(port: info.port) }
        if info.hostKeys.isEmpty { return .noHostKeys }
        return .ready
    }
}

/// The saved targets, one per hub id, in UserDefaults. Nothing secret:
/// host keys and a login name.
struct SSHTargetStore {
    private static let key = "quiver.sshTargets"
    var defaults = UserDefaults.standard

    func target(for hubId: String) -> SSHTarget? { all()[hubId] }

    func save(_ target: SSHTarget, for hubId: String) {
        var targets = all()
        targets[hubId] = target
        if let data = try? JSONEncoder().encode(targets) { defaults.set(data, forKey: Self.key) }
    }

    func remove(_ hubId: String) {
        var targets = all()
        guard targets.removeValue(forKey: hubId) != nil else { return }
        if let data = try? JSONEncoder().encode(targets) { defaults.set(data, forKey: Self.key) }
    }

    private func all() -> [String: SSHTarget] {
        guard let data = defaults.data(forKey: Self.key) else { return [:] }
        return (try? JSONDecoder().decode([String: SSHTarget].self, from: data)) ?? [:]
    }
}
