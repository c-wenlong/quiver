import Foundation

/// One language model from Artificial Analysis's `/api/v2/data/llms/models`.
/// `id` is AA's stable key; names and slugs change. `evaluations` keeps
/// every metric AA publishes — the dict grows when they add a benchmark,
/// so the Models page reads keys rather than a fixed field list.
public struct AALanguageModel: Sendable, Equatable, Identifiable {
    public var id: String
    public var name: String
    public var slug: String
    /// model_creator's id, name and slug (the slug drives provider colours).
    public var creatorID: String
    public var creatorName: String
    public var creatorSlug: String
    /// evaluation key → score; AA's nulls are dropped on decode.
    public var evaluations: [String: Double]
    /// USD per 1M tokens, 3:1 input:output blend.
    public var priceBlended: Double?
    public var priceInput: Double?
    public var priceOutput: Double?
    /// Median output tokens per second.
    public var speed: Double?
    /// Median seconds to first token.
    public var timeToFirstToken: Double?
    /// Median seconds to first answer token (reasoning models).
    public var timeToFirstAnswer: Double?

    public init(id: String, name: String, slug: String,
                creatorID: String = "", creatorName: String = "", creatorSlug: String = "",
                evaluations: [String: Double] = [:],
                priceBlended: Double? = nil, priceInput: Double? = nil, priceOutput: Double? = nil,
                speed: Double? = nil, timeToFirstToken: Double? = nil,
                timeToFirstAnswer: Double? = nil) {
        self.id = id
        self.name = name
        self.slug = slug
        self.creatorID = creatorID
        self.creatorName = creatorName
        self.creatorSlug = creatorSlug
        self.evaluations = evaluations
        self.priceBlended = priceBlended
        self.priceInput = priceInput
        self.priceOutput = priceOutput
        self.speed = speed
        self.timeToFirstToken = timeToFirstToken
        self.timeToFirstAnswer = timeToFirstAnswer
    }
}

extension AALanguageModel {
    /// The intelligence index AA headlines with.
    public var intelligence: Double? { evaluations["artificial_analysis_intelligence_index"] }
    /// The coding score: AA's Terminal-Bench 4.0 pass rate as 0–100, run on
    /// every new model. AA's Coding Index (`artificial_analysis_coding_index`)
    /// is built on Terminal-Bench 2.1, which AA stopped running when
    /// Intelligence Index v4.3 (September 2026) moved to 4.0, so it is empty
    /// for every model released since. The versions are not on one scale
    /// (2.1 scores run about 50 points higher for the same model), so a
    /// model AA never ran on 4.0 has no score rather than an older one.
    public var coding: Double? { evaluations[Self.codingEvaluation].map { $0 * 100 } }
    public static let codingEvaluation = "terminalbench_v4_0"
    /// The hover note where `coding` is nil.
    public static let noCodingScore = "Artificial Analysis hasn't run Terminal-Bench 4.0 on this model"
    /// The reasoning score: AA's Humanity's Last Exam accuracy as 0–100,
    /// run on every new model. It stands in for AA's Maths Index
    /// (`artificial_analysis_math_index`, built on AIME 2025), which AA
    /// retired with Intelligence Index v4.0 (January 2026), so no model
    /// released since has one. HLE is the closest current evaluation:
    /// expert-level questions, about 40% of them maths, on one question set.
    public var reasoning: Double? { evaluations[Self.reasoningEvaluation].map { $0 * 100 } }
    public static let reasoningEvaluation = "hle"
    /// The hover note where `reasoning` is nil.
    public static let noReasoningScore = "Artificial Analysis hasn't run Humanity's Last Exam on this model"
}

extension AALanguageModel: Decodable {
    private enum CodingKeys: String, CodingKey {
        case id, name, slug
        case creator = "model_creator"
        case evaluations, pricing
        case speed = "median_output_tokens_per_second"
        case ttft = "median_time_to_first_token_seconds"
        case ttfat = "median_time_to_first_answer_token"
    }

    private enum PricingKeys: String, CodingKey {
        case blended = "price_1m_blended_3_to_1"
        case input = "price_1m_input_tokens"
        case output = "price_1m_output_tokens"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? id
        slug = try c.decodeIfPresent(String.self, forKey: .slug) ?? id
        let creator = try c.decodeIfPresent([String: String].self, forKey: .creator) ?? [:]
        creatorID = creator["id"] ?? ""
        creatorName = creator["name"] ?? ""
        creatorSlug = creator["slug"] ?? ""
        // [String: Double?] decodes every key AA adds; nulls drop out.
        let raw = try c.decodeIfPresent([String: Double?].self, forKey: .evaluations) ?? [:]
        evaluations = raw.compactMapValues { $0 }
        // AA writes 0 where it has no measurement: no API price (277 of
        // 704 models in October 2026) or no speed run. Zero is "unknown",
        // so every page shows "—" and the cost rank leaves them out rather
        // than ranking them cheapest.
        func measured(_ value: Double?) -> Double? { value.flatMap { $0 > 0 ? $0 : nil } }
        let pricing = try c.decodeIfPresent([String: Double?].self, forKey: .pricing) ?? [:]
        priceBlended = measured(pricing[PricingKeys.blended.rawValue] ?? nil)
        priceInput = measured(pricing[PricingKeys.input.rawValue] ?? nil)
        priceOutput = measured(pricing[PricingKeys.output.rawValue] ?? nil)
        speed = measured(try c.decodeIfPresent(Double.self, forKey: .speed))
        timeToFirstToken = measured(try c.decodeIfPresent(Double.self, forKey: .ttft))
        timeToFirstAnswer = measured(try c.decodeIfPresent(Double.self, forKey: .ttfat))
    }
}

/// The list endpoint's envelope: `{status, prompt_options, data: [...]}`.
public struct AAModelsResponse: Sendable {
    public var models: [AALanguageModel]

    public init(models: [AALanguageModel]) { self.models = models }

    /// Decodes the response body; throws on anything but a `data` array.
    public static func decode(_ data: Data) throws -> [AALanguageModel] {
        let root = try JSONSerialization.jsonObject(with: data)
        guard let dict = root as? [String: Any], let rows = dict["data"] as? [Any] else {
            throw AAError.badResponse
        }
        let decoded = try JSONDecoder().decode([AALanguageModel].self, from: JSONSerialization.data(withJSONObject: rows))
        return decoded
    }
}

public enum AAError: Error, Equatable {
    case noKey
    case http(Int)
    case badResponse
    case network(String)
}

/// The endpoints a refresh downloads. Media feeds are stored for a later
/// page; only `llms` is read today. `fileName` is the raw-response file
/// older builds wrote, read only by the one-time import.
public enum AAEndpoint: String, CaseIterable, Sendable {
    case llms = "llms"
    case textToImage = "text-to-image"
    case imageEditing = "image-editing"
    case textToSpeech = "text-to-speech"
    case textToVideo = "text-to-video"
    case imageToVideo = "image-to-video"

    public var fileName: String { "\(rawValue).json" }

    /// The path under https://artificialanalysis.ai.
    public var path: String {
        switch self {
        case .llms: "/api/v2/data/llms/models"
        case .textToImage: "/api/v2/data/media/text-to-image?include_categories=true"
        case .imageEditing: "/api/v2/data/media/image-editing"
        case .textToSpeech: "/api/v2/data/media/text-to-speech"
        case .textToVideo: "/api/v2/data/media/text-to-video?include_categories=true"
        case .imageToVideo: "/api/v2/data/media/image-to-video?include_categories=true"
        }
    }

    public var url: URL { URL(string: "https://artificialanalysis.ai\(path)")! }
}

/// Downloaded Artificial Analysis data, kept in the catalog database:
/// each endpoint's last good response, the language models as rows, and a
/// fetch row per attempt (status, error, rate-limit headers).
public struct ArtificialAnalysisStore: Sendable {
    /// When the models feed is this old the app quietly refreshes at launch.
    public static let staleAfter: TimeInterval = 7 * 86_400

    /// Per-endpoint bookkeeping: the last good fetch's time, and the latest
    /// attempt's status, error and headers. Also the shape of the
    /// `meta.json` older builds wrote.
    public struct Meta: Sendable, Equatable, Codable {
        public var fetchedAt: TimeInterval?
        /// The last HTTP status, nil when no request completed.
        public var status: Int?
        /// A failed refresh's message; cleared by the next success.
        public var error: String?
        /// Any X-RateLimit-* headers the last response carried.
        public var rateLimitHeaders: [String: String]?

        public init(fetchedAt: TimeInterval? = nil, status: Int? = nil,
                    error: String? = nil, rateLimitHeaders: [String: String]? = nil) {
            self.fetchedAt = fetchedAt
            self.status = status
            self.error = error
            self.rateLimitHeaders = rateLimitHeaders
        }
    }

    public let database: CatalogDatabase
    private let now: @Sendable () -> Date

    /// Where older builds kept the raw files; imported once.
    public static func defaultDirectory(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> URL {
        home.appending(path: ".quiver/data/artificial-analysis")
    }

    /// `QUIVER_AA_DATA_DIR` wins, so a test build never touches the real data.
    public static func resolvedDirectory(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> URL {
        if let override = ProcessInfo.processInfo.environment["QUIVER_AA_DATA_DIR"], !override.isEmpty {
            return URL(fileURLWithPath: override)
        }
        return defaultDirectory(home: home)
    }

    /// - Parameter legacyDirectory: the `data/artificial-analysis/` folder
    ///   older builds wrote; imported into `database` once if it holds no
    ///   AA data yet.
    public init(database: CatalogDatabase, legacyDirectory: URL? = nil,
                now: @escaping @Sendable () -> Date = Date.init) {
        self.database = database
        self.now = now
        if let legacyDirectory { database.importLegacyAA(directory: legacyDirectory) }
    }

    /// A store over `directory/catalog.sqlite` that imports any raw files
    /// already in `directory`: for tests and previews.
    public init(directory: URL, now: @escaping @Sendable () -> Date = Date.init) {
        self.init(database: CatalogDatabase(url: directory.appending(path: "catalog.sqlite")),
                  legacyDirectory: directory, now: now)
    }

    public func raw(_ endpoint: AAEndpoint) -> Data? {
        database.payload(endpoint)
    }

    /// The decoded language models, or nil when no good fetch has landed.
    public func languageModels() -> [AALanguageModel]? {
        database.languageModels()
    }

    public func meta() -> [String: Meta] {
        var result: [String: Meta] = [:]
        for endpoint in AAEndpoint.allCases {
            let source = CatalogDatabase.aaSource(endpoint)
            guard let latest = database.lastAttempt(source: source) else { continue }
            result[endpoint.rawValue] = Meta(
                fetchedAt: database.lastSuccess(source: source)?.fetchedAt?.timeIntervalSince1970,
                status: latest.status, error: latest.error, rateLimitHeaders: latest.rateLimitHeaders)
        }
        return result
    }

    /// The models feed's last good fetch: its time is the page's
    /// "Updated" stamp, and a new id means the models table changed.
    public func llmsFetch() -> CatalogDatabase.Fetch? {
        database.lastSuccess(source: CatalogDatabase.aaSource(.llms))
    }

    /// Whether the models feed is missing or past `staleAfter`, which is
    /// when a launch may refresh without being asked.
    public func llmsIsStale() -> Bool {
        guard let fetchedAt = llmsFetch()?.fetchedAt else { return true }
        return now().timeIntervalSince(fetchedAt) > Self.staleAfter
    }

    /// Stores one endpoint's good response and records the fetch.
    private func save(_ endpoint: AAEndpoint, data: Data, status: Int,
                      rateLimitHeaders: [String: String]) throws {
        try database.recordAAPayload(
            endpoint, body: data, at: now(), status: status,
            rateLimitHeaders: rateLimitHeaders.isEmpty ? nil : rateLimitHeaders,
            url: endpoint.url.absoluteString)
    }

    /// A failure is only recorded; stored data is never clobbered.
    private func recordFailure(_ endpoint: AAEndpoint, error: String, status: Int?,
                               rateLimitHeaders: [String: String]? = nil) {
        database.recordFailure(source: CatalogDatabase.aaSource(endpoint), url: endpoint.url, at: now(),
                               status: status, error: error, rateLimitHeaders: rateLimitHeaders)
    }

    /// Downloads all six endpoints. Each succeeds or records its own
    /// error; the errors dict tells the UI which ones failed.
    @discardableResult
    public func refresh(key: String, transport: any HTTPTransport) async -> [AAEndpoint: String] {
        var errors: [AAEndpoint: String] = [:]
        for endpoint in AAEndpoint.allCases {
            var request = URLRequest(url: endpoint.url)
            request.setValue(key, forHTTPHeaderField: "x-api-key")
            do {
                let (data, response) = try await transport.send(request)
                let headers = response.allHeaderFields.reduce(into: [String: String]()) { out, pair in
                    if let name = pair.key as? String, name.lowercased().hasPrefix("x-ratelimit-"),
                       let value = pair.value as? String {
                        out[name] = value
                    }
                }
                guard (200..<300).contains(response.statusCode) else {
                    let message = "HTTP \(response.statusCode)"
                    recordFailure(endpoint, error: message, status: response.statusCode,
                                  rateLimitHeaders: headers.isEmpty ? nil : headers)
                    errors[endpoint] = message
                    continue
                }
                if endpoint == .llms, (try? AAModelsResponse.decode(data)) == nil {
                    recordFailure(endpoint, error: "Unusable response", status: response.statusCode)
                    errors[endpoint] = "Unusable response"
                    continue
                }
                try save(endpoint, data: data, status: response.statusCode,
                         rateLimitHeaders: headers)
            } catch {
                let message = error.localizedDescription
                recordFailure(endpoint, error: message, status: nil)
                errors[endpoint] = message
            }
        }
        if errors[.llms] == nil { database.rebuildAliases() }
        return errors
    }
}
