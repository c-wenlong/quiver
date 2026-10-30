import Foundation

/// The latest GitHub release, fetched through the HTTPTransport seam and
/// cached on disk so a failed check keeps showing the last answer.
public struct ReleaseCheck: Sendable {
    public struct Release: Codable, Sendable, Equatable {
        public var tag: String
        public var url: URL?
        public var publishedAt: Date?

        public init(tag: String, url: URL?, publishedAt: Date?) {
            self.tag = tag
            self.url = url
            self.publishedAt = publishedAt
        }
    }

    /// How the latest release compares to the running version.
    public enum Comparison: Sendable, Equatable {
        case upToDate
        case available
        /// The tag (or the installed version) is not dotted numbers.
        case incomparable
    }

    /// What `check` reports for the UI to render: the release it knows
    /// (fresh or cached), how it compares, and why the fetch failed.
    public struct ReleaseReport: Sendable, Equatable {
        public var release: Release?
        public var comparison: Comparison?
        public var error: String?

        public init(release: Release?, comparison: Comparison?, error: String?) {
            self.release = release
            self.comparison = comparison
            self.error = error
        }
    }

    public static let endpoint = URL(
        string: "https://api.github.com/repos/c-wenlong/quiver/releases/latest")!

    /// A cached answer younger than this skips the network.
    public static let cacheTTL: TimeInterval = 6 * 3600

    private let transport: HTTPTransport
    private let cacheURL: URL
    private let now: @Sendable () -> Date

    public init(transport: HTTPTransport, cacheURL: URL,
                now: @escaping @Sendable () -> Date = Date.init) {
        self.transport = transport
        self.cacheURL = cacheURL
        self.now = now
    }

    private struct Cached: Codable {
        var fetchedAt: Date
        var release: Release
    }

    /// Latest release state. A fresh-enough cache answers unless `force`;
    /// a failed fetch keeps reporting the cached release.
    public func check(current version: String, force: Bool) async -> ReleaseReport {
        let entry = readCache()
        if !force, let entry, now().timeIntervalSince(entry.fetchedAt) < Self.cacheTTL {
            return report(version: version, release: entry.release, error: nil)
        }
        do {
            var request = URLRequest(url: Self.endpoint)
            request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
            let (data, response) = try await transport.send(request)
            guard response.statusCode == 200 else {
                return report(version: version, release: entry?.release,
                              error: "GitHub answered \(response.statusCode)")
            }
            let release = try decode(data)
            writeCache(release)
            return report(version: version, release: release, error: nil)
        } catch {
            return report(version: version, release: entry?.release,
                          error: error.localizedDescription)
        }
    }

    private func report(version: String, release: Release?, error: String?) -> ReleaseReport {
        ReleaseReport(release: release,
                      comparison: release.map { Self.compare(version: version, to: $0) },
                      error: error)
    }

    /// Dotted numeric compare: "v1.0.10" beats "1.0.9", "1.0" equals
    /// "1.0.0". A non-numeric tag is not an update; it is incomparable.
    public static func compare(version current: String, to release: Release) -> Comparison {
        let tag = release.tag.hasPrefix("v") ? String(release.tag.dropFirst()) : release.tag
        guard let latest = numbers(tag), let installed = numbers(current) else {
            return .incomparable
        }
        if latest == installed { return .upToDate }
        for i in 0..<max(latest.count, installed.count) {
            let a = i < latest.count ? latest[i] : 0
            let b = i < installed.count ? installed[i] : 0
            if a != b { return a > b ? .available : .upToDate }
        }
        return .upToDate
    }

    private static func numbers(_ version: String) -> [Int]? {
        let parts = version.split(separator: ".").map(String.init)
        guard !parts.isEmpty, parts.allSatisfy({ !$0.isEmpty && Int($0) != nil })
        else { return nil }
        var nums = parts.compactMap(Int.init)
        while nums.count < 3 { nums.append(0) }
        return nums
    }

    private func decode(_ data: Data) throws -> Release {
        struct Payload: Decodable {
            let tag_name: String
            let html_url: URL?
            let published_at: Date?
        }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let payload = try decoder.decode(Payload.self, from: data)
        return Release(tag: payload.tag_name, url: payload.html_url,
                       publishedAt: payload.published_at)
    }

    private func readCache() -> Cached? {
        guard let data = try? Data(contentsOf: cacheURL) else { return nil }
        return try? JSONDecoder().decode(Cached.self, from: data)
    }

    private func writeCache(_ release: Release) {
        let entry = Cached(fetchedAt: now(), release: release)
        guard let data = try? JSONEncoder().encode(entry) else { return }
        try? FileManager.default.createDirectory(
            at: cacheURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: cacheURL, options: .atomic)
    }
}
