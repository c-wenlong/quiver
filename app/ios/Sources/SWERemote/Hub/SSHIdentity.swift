import CryptoKit
import Foundation
import NIOSSH
import Security

/// This phone's SSH key: one for the app, sent to every hub it pairs
/// with. On an iPhone it is a Secure Enclave P-256 key, so the private
/// half never leaves the chip; the Keychain holds only the enclave's
/// device-bound handle to it. The simulator has no enclave, so there it
/// is a software key kept in the Keychain instead.
struct SSHIdentity {
    let privateKey: NIOSSHPrivateKey
    /// `ecdsa-sha2-nistp256 AAAA…`, what the hub writes into authorized_keys.
    let publicKey: String
    let inSecureEnclave: Bool

    enum Failure: LocalizedError {
        case keychain(OSStatus)
        case enclave(String)
        var errorDescription: String? {
            switch self {
            case .keychain(let status): "Couldn't read or keep this phone's SSH key in the Keychain (error \(status))."
            case .enclave(let detail): "The Secure Enclave couldn't make this phone's SSH key: \(detail)"
            }
        }
    }

    private static let account = "ssh-identity"
    /// The stored item's first byte says what follows it.
    private static let enclaveTag: UInt8 = 1
    private static let softwareTag: UInt8 = 2

    /// The stored key, or a new one when there is none (or it no longer
    /// opens, as after restoring the Keychain to another phone).
    static func loadOrCreate(service: String = "com.swe.remote") throws -> SSHIdentity {
        switch keychainRead(service: service) {
        case .found(let data):
            if let identity = decode(data) { return identity }
            // Stored but unusable: an enclave handle restored onto another
            // phone opens nothing. Only then is the old item replaced.
        case .missing:
            break
        case .failed(let status):
            // Locked or busy is not "no key": making one now would replace
            // a key the machines still trust.
            throw Failure.keychain(status)
        }
        let (identity, data) = try make()
        let status = keychainWrite(data, service: service)
        guard status == errSecSuccess else { throw Failure.keychain(status) }
        return identity
    }

    /// Forgets the key, so the next console makes a new one (tests).
    static func delete(service: String) {
        SecItemDelete(query(service: service) as CFDictionary)
    }

    init(privateKey: NIOSSHPrivateKey, inSecureEnclave: Bool) {
        self.privateKey = privateKey
        self.publicKey = String(openSSHPublicKey: privateKey.publicKey)
        self.inSecureEnclave = inSecureEnclave
    }

    private static func make() throws -> (SSHIdentity, Data) {
        guard SecureEnclave.isAvailable else {
            // The simulator: no enclave, so a software key is all there is.
            let key = P256.Signing.PrivateKey()
            return (SSHIdentity(privateKey: NIOSSHPrivateKey(p256Key: key), inSecureEnclave: false),
                    Data([softwareTag]) + key.rawRepresentation)
        }
        // Usable only while the phone is unlocked, and never copied to
        // another device by a backup. A failure here is reported, never
        // swapped for a software key whose private bytes would leave the chip.
        var error: Unmanaged<CFError>?
        guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
                                                           .privateKeyUsage, &error) else {
            throw Failure.enclave(error?.takeRetainedValue().localizedDescription ?? "no access control")
        }
        do {
            let key = try SecureEnclave.P256.Signing.PrivateKey(accessControl: access)
            return (SSHIdentity(privateKey: NIOSSHPrivateKey(secureEnclaveP256Key: key), inSecureEnclave: true),
                    Data([enclaveTag]) + key.dataRepresentation)
        } catch {
            throw Failure.enclave(error.localizedDescription)
        }
    }

    private static func decode(_ data: Data) -> SSHIdentity? {
        guard let tag = data.first else { return nil }
        let body = data.dropFirst()
        switch tag {
        case enclaveTag:
            guard SecureEnclave.isAvailable,
                  let key = try? SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: body) else { return nil }
            return SSHIdentity(privateKey: NIOSSHPrivateKey(secureEnclaveP256Key: key), inSecureEnclave: true)
        case softwareTag:
            guard let key = try? P256.Signing.PrivateKey(rawRepresentation: body) else { return nil }
            return SSHIdentity(privateKey: NIOSSHPrivateKey(p256Key: key), inSecureEnclave: false)
        default:
            return nil
        }
    }

    private static func query(service: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private enum ReadResult {
        case found(Data)
        case missing
        case failed(OSStatus)
    }

    private static func keychainRead(service: String) -> ReadResult {
        var q = query(service: service)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        let status = SecItemCopyMatching(q as CFDictionary, &out)
        switch status {
        case errSecSuccess:
            guard let data = out as? Data else { return .failed(errSecDecode) }
            return .found(data)
        case errSecItemNotFound:
            return .missing
        default:
            return .failed(status)
        }
    }

    private static func keychainWrite(_ data: Data, service: String) -> OSStatus {
        SecItemDelete(query(service: service) as CFDictionary)
        var q = query(service: service)
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        return SecItemAdd(q as CFDictionary, nil)
    }
}
