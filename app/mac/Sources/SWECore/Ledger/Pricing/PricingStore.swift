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

/// The optional daily refresh of LiteLLM's price list, kept in the catalog
/// database with every price each key has held, so a price change never
/// reprices usage logged before it.
public struct PricingStore: Sendable {
    public static let liteLLMURL = URL(
        string: "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json")!

    public let database: CatalogDatabase
    public let transport: any PricingTransport
    public let maxAge: TimeInterval
    /// A download with fewer priced models than this is treated as garbage
    /// (an error page, a truncated body).
    public let minimumModels: Int
    /// The user's own prices (`config/pricing.json`), merged ahead of both
    /// other sources; nil in callers that want the published lists only.
    public let overridesFile: URL?

    /// - Parameter legacyCacheFile: the `cache/pricing/litellm.json` older
    ///   builds kept; imported into `database` once if it has no list yet.
    public init(
        database: CatalogDatabase, legacyCacheFile: URL? = nil,
        transport: any PricingTransport = URLSessionPricingTransport(),
        maxAge: TimeInterval = 86_400, minimumModels: Int = 50, overridesFile: URL? = nil
    ) {
        self.database = database
        self.transport = transport
        self.maxAge = maxAge
        self.minimumModels = minimumModels
        self.overridesFile = overridesFile
        if let legacyCacheFile { database.importLegacyPricing(cacheFile: legacyCacheFile) }
    }

    private var overrides: [String: [String: Any]] {
        overridesFile.map { PricingOverrides(url: $0).entries } ?? [:]
    }

    /// The downloaded list with its price history when there is a usable
    /// one, else the bundled snapshot.
    public func load() throws -> PricingCatalog {
        let supplement = try PricingSupplement.bundled()
        if let entries = database.currentPriceEntries() {
            let catalog = PricingCatalog(liteLLMEntries: entries, supplement: supplement, overrides: overrides)
            if catalog.modelCount >= minimumModels {
                return catalog.withHistory(database.priceHistory())
            }
        }
        return try PricingCatalog(liteLLM: PricingCatalog.bundledLiteLLM(), supplement: supplement,
                                  overrides: overrides)
    }

    /// Downloads a fresh list when the last good one is missing or older
    /// than `maxAge`. Returns the new catalog, or nil when nothing changed:
    /// the list is fresh, the download failed or did not decode, or no
    /// price moved. A failure is recorded and never replaces a good list.
    public func refreshIfStale(now: Date = Date()) async -> PricingCatalog? {
        let source = CatalogDatabase.liteLLMSource
        if let fetched = database.lastSuccess(source: source)?.fetchedAt,
           now.timeIntervalSince(fetched) < maxAge {
            return nil
        }
        let data: Data
        do {
            data = try await transport.fetch(Self.liteLLMURL)
        } catch {
            let status = (error as? PricingError).flatMap { if case .httpStatus(let code) = $0 { code } else { nil } }
            database.recordFailure(source: source, url: Self.liteLLMURL, at: now, status: status,
                                   error: status.map { "HTTP \($0)" } ?? error.localizedDescription)
            return nil
        }
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              case let entries = CatalogDatabase.pricedEntries(object),
              entries.count >= minimumModels
        else {
            database.recordFailure(source: source, url: Self.liteLLMURL, at: now, status: 200,
                                   error: "Unusable response")
            return nil
        }
        guard let changed = try? database.recordPrices(
            entries, at: now, url: Self.liteLLMURL.absoluteString, sha: CatalogDatabase.sha256(data))
        else { return nil }
        guard changed > 0 else { return nil }
        database.rebuildAliases()
        return try? load()
    }
}
