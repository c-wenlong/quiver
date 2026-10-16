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
    public var coding: Double? { evaluations["artificial_analysis_coding_index"] }
    public var maths: Double? { evaluations["artificial_analysis_math_index"] }
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

    private enum CreatorKeys: String, CodingKey { case id, name, slug }
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
        let pricing = try c.decodeIfPresent([String: Double?].self, forKey: .pricing) ?? [:]
        priceBlended = pricing[PricingKeys.blended.rawValue] ?? nil
        priceInput = pricing[PricingKeys.input.rawValue] ?? nil
        priceOutput = pricing[PricingKeys.output.rawValue] ?? nil
        speed = try c.decodeIfPresent(Double.self, forKey: .speed)
        timeToFirstToken = try c.decodeIfPresent(Double.self, forKey: .ttft)
        timeToFirstAnswer = try c.decodeIfPresent(Double.self, forKey: .ttfat)
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
/// page; only `llms` is read today. Each maps to one file under the data
/// directory, which holds raw response bytes.
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

/// Where downloaded Artificial Analysis responses live. This is data, not
/// cache: `~/.quiver/data/artificial-analysis/` survives cache clears, one
/// raw file per endpoint plus `meta.json` bookkeeping. `QUIVER_AA_DATA_DIR`
/// points test builds somewhere throwaway.
public struct ArtificialAnalysisStore: Sendable {
    /// When `llms.json` is this old the app quietly refreshes at launch.
    public static let staleAfter: TimeInterval = 7 * 86_400

    /// Per-endpoint bookkeeping in meta.json.
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

    public let directory: URL
    private let now: @Sendable () -> Date

    /// The directory Quiver uses when the environment doesn't say.
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

    public init(directory: URL, now: @escaping @Sendable () -> Date = Date.init) {
        self.directory = directory
        self.now = now
    }

    private func file(for endpoint: AAEndpoint) -> URL {
        directory.appending(path: endpoint.fileName)
    }

    private var metaFile: URL { directory.appending(path: "meta.json") }

    public func raw(_ endpoint: AAEndpoint) -> Data? {
        FileManager.default.contents(atPath: file(for: endpoint).path)
    }

    /// The decoded language models, or nil when no good fetch has landed.
    public func languageModels() -> [AALanguageModel]? {
        guard let data = raw(.llms) else { return nil }
        return try? AAModelsResponse.decode(data)
    }

    public func meta() -> [String: Meta] {
        guard let data = FileManager.default.contents(atPath: metaFile.path),
              let decoded = try? JSONDecoder().decode([String: Meta].self, from: data)
        else { return [:] }
        return decoded
    }

    /// Whether the models feed is missing or past `staleAfter`, which is
    /// when a launch may refresh without being asked.
    public func llmsIsStale() -> Bool {
        guard let fetchedAt = meta()[AAEndpoint.llms.rawValue]?.fetchedAt,
              FileManager.default.fileExists(atPath: file(for: .llms).path)
        else { return true }
        return now().timeIntervalSince1970 - fetchedAt > Self.staleAfter
    }

    /// Writes one endpoint's raw bytes atomically and records the fetch.
    private func save(_ endpoint: AAEndpoint, data: Data, status: Int,
                      rateLimitHeaders: [String: String]) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try data.write(to: file(for: endpoint), options: .atomic)
        var meta = meta()
        meta[endpoint.rawValue] = Meta(fetchedAt: now().timeIntervalSince1970,
                                       status: status, error: nil,
                                       rateLimitHeaders: rateLimitHeaders.isEmpty ? nil : rateLimitHeaders)
        try JSONEncoder().encode(meta).write(to: metaFile, options: .atomic)
    }

    /// A failure only updates meta.json — stored data is never clobbered.
    private func recordFailure(_ endpoint: AAEndpoint, error: String, status: Int?) {
        var meta = meta()
        var entry = meta[endpoint.rawValue] ?? Meta()
        entry.error = error
        entry.status = status
        meta[endpoint.rawValue] = entry
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try? JSONEncoder().encode(meta).write(to: metaFile, options: .atomic)
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
                    recordFailure(endpoint, error: message, status: response.statusCode)
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
        return errors
    }
}
