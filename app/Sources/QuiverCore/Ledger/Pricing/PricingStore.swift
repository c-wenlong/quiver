import Foundation

/// Fetches bytes from a URL. Tests pass a stub, so they never touch the network.
public protocol PricingTransport: Sendable {
    func fetch(_ url: URL) async throws -> Data
}

public struct URLSessionPricingTransport: PricingTransport {
    public init() {}

    public func fetch(_ url: URL) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(from: url)
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            throw PricingError.httpStatus(http.statusCode)
        }
        return data
    }
}

/// The optional daily refresh of LiteLLM's price list. The fetched file is
/// kept at `cacheFile` (production: `~/.quiver/cache/pricing/litellm.json`)
/// and its modification date records when it was fetched.
public struct PricingStore: Sendable {
    public static let liteLLMURL = URL(
        string: "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json")!

    public let cacheFile: URL
    public let transport: any PricingTransport
    public let maxAge: TimeInterval
    /// A download with fewer priced models than this is treated as garbage
    /// (an error page, a truncated body).
    public let minimumModels: Int

    public init(
        cacheFile: URL, transport: any PricingTransport = URLSessionPricingTransport(),
        maxAge: TimeInterval = 86_400, minimumModels: Int = 50
    ) {
        self.cacheFile = cacheFile
        self.transport = transport
        self.maxAge = maxAge
        self.minimumModels = minimumModels
    }

    /// The refreshed copy when there is a readable one, else the bundled snapshot.
    public func load() throws -> PricingCatalog {
        let supplement = try PricingSupplement.bundled()
        if let data = try? Data(contentsOf: cacheFile),
           let catalog = try? PricingCatalog(liteLLM: data, supplement: supplement),
           catalog.modelCount >= minimumModels {
            return catalog
        }
        return try PricingCatalog.bundled()
    }

    /// Downloads a fresh list when the cached one is missing or older than
    /// `maxAge`. Returns the new catalog, or nil when nothing changed: the
    /// cache is fresh, the network failed, or the download did not decode.
    /// A failure never replaces a good copy.
    public func refreshIfStale(now: Date = Date()) async -> PricingCatalog? {
        if let modified = (try? FileManager.default.attributesOfItem(atPath: cacheFile.path))?[.modificationDate] as? Date,
           now.timeIntervalSince(modified) < maxAge {
            return nil
        }
        guard let data = try? await transport.fetch(Self.liteLLMURL),
              let supplement = try? PricingSupplement.bundled(),
              let catalog = try? PricingCatalog(liteLLM: data, supplement: supplement),
              catalog.modelCount >= minimumModels
        else { return nil }
        do {
            try FileManager.default.createDirectory(
                at: cacheFile.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: cacheFile, options: .atomic)
            try FileManager.default.setAttributes([.modificationDate: now], ofItemAtPath: cacheFile.path)
        } catch {
            return nil
        }
        return catalog
    }
}
