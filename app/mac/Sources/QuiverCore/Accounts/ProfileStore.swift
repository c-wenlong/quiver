import Foundation

/// Why a profile store call was refused.
public enum ProfileStoreError: Error, Equatable, Sendable {
    case emptyName
    case invalidColor(String)
    case notFound(String)
    case cannotRemoveDefault
    /// profiles.json exists but cannot be used. It is left exactly as found:
    /// it is the only record of which profile owns which directory, and those
    /// directories hold logins.
    case malformed(path: String, reason: String)
    /// profiles.json was written by a newer Quiver.
    case unsupportedSchema(Int)
}

extension ProfileStoreError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .emptyName: "A profile needs a name."
        case .invalidColor(let value): "\"\(value)\" is not a hex colour like #d97757."
        case .notFound(let id): "No profile with id \(id)."
        case .cannotRemoveDefault: "The default profile cannot be removed."
        case .malformed(let path, let reason):
            "Could not read \(path) (\(reason)). It was left untouched; fix or move it and try again."
        case .unsupportedSchema(let version):
            "profiles.json uses format \(version), which this version of Quiver does not understand."
        }
    }
}

/// The list of profiles, kept in `<root>/profiles.json`.
///
/// A profile is one account slot for one vendor. A default profile is the
/// vendor's own login in its usual place. Every other profile owns an isolated
/// tree under `<root>/<vendor>/<id>/`, so its CLI login, desktop session and
/// history never touch another profile's.
///
/// `root` is `~/.swe/accounts` in production. Nothing outside `root` and
/// `userHome` is ever read or written, and `userHome` is only ever used to
/// compute the default profiles' paths.
public final class ProfileStore: @unchecked Sendable {
    private let root: URL
    private let userHome: URL
    private let now: @Sendable () -> Date
    /// Serialises read-modify-write sequences within this process.
    private let lock = NSLock()

    /// `now` stamps `createdAt`; tests pass a fixed clock.
    public init(root: URL, userHome: URL, now: @escaping @Sendable () -> Date = { Date() }) {
        self.root = root
        self.userHome = userHome
        self.now = now
    }

    private var file: URL { root.appendingPathComponent("profiles.json") }

    // MARK: - Reading

    /// The profiles in display order: the two defaults first, then the rest in
    /// the order they were added. Works with no file on disk; throws, and
    /// leaves the file alone, if there is one that cannot be used.
    public func load() throws -> [Profile] {
        lock.lock()
        defer { lock.unlock() }
        return try loadLocked()
    }

    private func loadLocked() throws -> [Profile] {
        guard let stored = try readFile() else { return Self.defaultProfiles }

        // Each vendor's default first (synthesised if the file lacks it),
        // everything else after in file order.
        var defaults: [Profile] = []
        var used = Set<Int>()
        for vendor in Vendor.allCases where vendor.hasDefaultProfile {
            if let index = stored.firstIndex(where: { $0.isDefault && $0.vendor == vendor }) {
                defaults.append(stored[index])
                used.insert(index)
            } else if let fallback = Self.defaultProfiles.first(where: { $0.vendor == vendor }) {
                defaults.append(fallback)
            }
        }
        let rest = stored.enumerated().filter { !used.contains($0.offset) }.map(\.element)
        return defaults + rest
    }

    /// The stored profiles, or nil when there is no file yet.
    private func readFile() throws -> [Profile]? {
        let data: Data
        do {
            data = try Data(contentsOf: file)
        } catch let error as CocoaError where error.code == .fileReadNoSuchFile || error.code == .fileNoSuchFile {
            return nil
        }

        let malformed = { (reason: String) in
            ProfileStoreError.malformed(path: self.file.path, reason: reason)
        }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        do {
            let header = try decoder.decode(Header.self, from: data)
            guard header.schema == Self.schema else { throw ProfileStoreError.unsupportedSchema(header.schema) }
            let profiles = try decoder.decode(StoreFile.self, from: data).profiles

            var seen = Set<String>()
            let reserved = Set(Self.defaultProfiles.map(\.id))
            for profile in profiles {
                guard Self.isSafeId(profile.id) else { throw malformed("unusable profile id \"\(profile.id)\"") }
                guard seen.insert(profile.id).inserted else { throw malformed("duplicate profile id \(profile.id)") }
                // A non-default entry under a default's id would collide with
                // the default synthesised next to it.
                guard profile.isDefault || !reserved.contains(profile.id) else {
                    throw malformed("profile id \(profile.id) is reserved for a default")
                }
            }
            return profiles
        } catch let error as ProfileStoreError {
            throw error
        } catch {
            throw malformed(Self.describe(error))
        }
    }

    // MARK: - Changing

    /// Adds a profile and creates its empty directories. `color` defaults to
    /// the next one in the palette.
    public func add(vendor: Vendor, name: String, color: String? = nil) throws -> Profile {
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { throw ProfileStoreError.emptyName }
        if let color, !Self.isHexColor(color) { throw ProfileStoreError.invalidColor(color) }

        lock.lock()
        defer { lock.unlock() }
        let held = try FileLock(at: lockFile())
        defer { held.release() }
        var profiles = try loadLocked()

        // A directory left behind by an interrupted removal is never adopted by
        // a new profile of the same name: it could still hold another
        // account's login.
        let slug = Self.slug(clean)
        let vendorDirectory = root.appendingPathComponent(vendor.rawValue, isDirectory: true)
        let retired = Set(retiredIds())
        let taken = { (id: String) -> Bool in
            profiles.contains { $0.id == id } || retired.contains(id)
                || FileManager.default.fileExists(atPath: vendorDirectory.appendingPathComponent(id).path)
        }
        var id = "\(vendor.rawValue)-\(slug)"
        var n = 2
        while taken(id) {
            id = "\(vendor.rawValue)-\(slug)-\(n)"
            n += 1
        }

        let profile = Profile(
            id: id, vendor: vendor, name: clean,
            isDefault: false, color: color ?? Self.palette[profiles.count % Self.palette.count],
            // Whole seconds, as stored, so what `add` returns equals what `load` reads back.
            createdAt: Date(timeIntervalSince1970: now().timeIntervalSince1970.rounded(.down)))
        let paths = paths(for: profile)
        try createProfileDirectories(paths)
        profiles.append(profile)
        do {
            try save(profiles)
        } catch {
            // The id was free, so this directory is ours to take back.
            try? FileManager.default.removeItem(at: vendorDirectory.appendingPathComponent(id))
            throw error
        }
        return profile
    }

    /// Changes the display name. The id, and so the directories, stay.
    public func rename(id: String, to name: String) throws {
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { throw ProfileStoreError.emptyName }

        lock.lock()
        defer { lock.unlock() }
        let held = try FileLock(at: lockFile())
        defer { held.release() }
        var profiles = try loadLocked()
        guard let index = profiles.firstIndex(where: { $0.id == id }) else { throw ProfileStoreError.notFound(id) }
        profiles[index].name = clean
        try save(profiles)
    }

    /// Removes a profile and everything it owns: its CLI login, desktop
    /// session, history and settings. There is no keep-the-data variant; the
    /// directory means nothing without its entry in the store. A default
    /// profile is the vendor's own login and is never removed.
    public func remove(id: String) throws {
        lock.lock()
        defer { lock.unlock() }
        let held = try FileLock(at: lockFile())
        defer { held.release() }
        var profiles = try loadLocked()
        guard let index = profiles.firstIndex(where: { $0.id == id }) else { throw ProfileStoreError.notFound(id) }
        let profile = profiles[index]
        guard !profile.isDefault else { throw ProfileStoreError.cannotRemoveDefault }

        // The entry goes first. If deleting the directory then fails, the
        // leftover is never adopted by a new profile (see `add`).
        profiles.remove(at: index)
        try save(profiles, retired: retiredIds() + [profile.id])
        let directory = root.appendingPathComponent(profile.vendor.rawValue, isDirectory: true)
            .appendingPathComponent(profile.id, isDirectory: true)
        try FileManager.default.removeItem(at: directory)
    }

    // MARK: - Paths

    /// Where this profile's state lives. A default profile uses the vendor's own
    /// home and desktop location.
    public func paths(for profile: Profile) -> ProfilePaths {
        if profile.isDefault {
            let name = profile.vendor == .codex ? ".codex" : ".claude"
            return ProfilePaths(home: userHome.appendingPathComponent(name, isDirectory: true), desktopData: nil)
        }
        let base = root.appendingPathComponent(profile.vendor.rawValue, isDirectory: true)
            .appendingPathComponent(profile.id, isDirectory: true)
        return ProfilePaths(
            home: base.appendingPathComponent("home", isDirectory: true),
            desktopData: base.appendingPathComponent("desktop", isDirectory: true))
    }

    // MARK: - Persistence

    private static let schema = 1

    private struct Header: Decodable {
        var schema: Int
    }

    private struct StoreFile: Codable {
        var schema: Int
        var profiles: [Profile]
        /// Ids of removed profiles. Claude Code names its keychain item after
        /// the config home, so a reused id would hand the removed account's
        /// login to a new one; a retired id is never handed out again.
        var retired: [String]? = nil
    }

    private static func describe(_ error: any Error) -> String {
        switch error {
        case is DecodingError: "not a valid profile list"
        default: error.localizedDescription
        }
    }

    /// Ids removed so far, read from the file. Empty when there is none yet.
    private func retiredIds() -> [String] {
        guard let data = try? Data(contentsOf: file) else { return [] }
        return (try? JSONDecoder().decode(Retired.self, from: data))?.retired ?? []
    }

    private struct Retired: Decodable { var retired: [String]? }

    /// `retired` nil keeps whatever the file already lists.
    private func save(_ profiles: [Profile], retired: [String]? = nil) throws {
        try createRoot()
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let keep = retired ?? retiredIds()
        let data = try encoder.encode(StoreFile(schema: Self.schema, profiles: profiles,
                                                retired: keep.isEmpty ? nil : keep))

        // Write a private temp file next to the real one, then rename it over
        // the real one, so a crash can never leave a half-written store.
        let tmp = root.appendingPathComponent("profiles.json.\(UUID().uuidString).tmp")
        let fd = open(tmp.path, O_WRONLY | O_CREAT | O_EXCL, 0o600)
        guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        do {
            try handle.write(contentsOf: data)
            try handle.synchronize()
            try handle.close()
        } catch {
            try? FileManager.default.removeItem(at: tmp)
            throw error
        }
        guard Darwin.rename(tmp.path, file.path) == 0 else {
            let code = POSIXErrorCode(rawValue: errno) ?? .EIO
            try? FileManager.default.removeItem(at: tmp)
            throw POSIXError(code)
        }
    }

    /// Creates the accounts root, mode 0700. Its parents (`~/.swe`) are
    /// shared with `swe`, so they keep the default mode.
    /// The lock every running copy of Quiver takes around a whole read,
    /// change and save of profiles.json, so two copies never overwrite each
    /// other's change.
    private func lockFile() throws -> URL {
        try createRoot()
        return root.appendingPathComponent("profiles.lock")
    }

    private func createRoot() throws {
        try FileManager.default.createDirectory(
            at: root.deletingLastPathComponent(), withIntermediateDirectories: true)
        try makePrivateDirectory(root)
    }

    /// Creates `<root>/<vendor>/<id>/{home,desktop}`, every level mode 0700.
    private func createProfileDirectories(_ paths: ProfilePaths) throws {
        try createRoot()
        var leaves = [paths.home]
        if let desktop = paths.desktopData { leaves.append(desktop) }
        for leaf in leaves {
            let base = leaf.deletingLastPathComponent()
            try makePrivateDirectory(base.deletingLastPathComponent())
            try makePrivateDirectory(base)
            try makePrivateDirectory(leaf)
        }
    }

    /// Creates one directory with mode 0700 unless it exists, in which case
    /// it keeps whatever mode it has.
    private func makePrivateDirectory(_ url: URL) throws {
        var isDirectory: ObjCBool = false
        if FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory) {
            if isDirectory.boolValue { return }
            throw CocoaError(.fileWriteFileExists)
        }
        do {
            try FileManager.default.createDirectory(
                at: url, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        } catch CocoaError.fileWriteFileExists {
            // Another running copy made it between the check and the create.
            guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory),
                  isDirectory.boolValue else { throw CocoaError(.fileWriteFileExists) }
        }
    }

    // MARK: - Naming

    static let palette = ["#d97757", "#10a37f", "#3b82f6", "#a855f7", "#f59e0b", "#ec4899", "#14b8a6", "#64748b"]

    /// `#rgb` or `#rrggbb`.
    static func isHexColor(_ value: String) -> Bool {
        let digits = value.dropFirst()
        return value.hasPrefix("#") && [3, 6].contains(digits.count) && digits.allSatisfy(\.isHexDigit)
    }

    /// An id becomes a directory name, so it must not be able to name anything
    /// outside its vendor directory, whatever a hand-edited file says.
    static func isSafeId(_ id: String) -> Bool {
        !id.isEmpty && id.unicodeScalars.allSatisfy {
            $0.isASCII && (CharacterSet.alphanumerics.contains($0) || $0 == "-" || $0 == "_")
        }
    }

    static func slug(_ name: String) -> String {
        var out = ""
        var pendingDash = false
        for scalar in name.lowercased().unicodeScalars {
            if scalar.isASCII, CharacterSet.alphanumerics.contains(scalar) {
                if pendingDash, !out.isEmpty { out.append("-") }
                pendingDash = false
                out.unicodeScalars.append(scalar)
            } else {
                pendingDash = true
            }
        }
        return out.isEmpty ? "profile" : out
    }

    static let defaultProfiles: [Profile] = [
        Profile(id: "claude-default", vendor: .claude, name: "Default", isDefault: true, color: "#d97757"),
        Profile(id: "codex-default", vendor: .codex, name: "Default", isDefault: true, color: "#10a37f"),
    ]
}
