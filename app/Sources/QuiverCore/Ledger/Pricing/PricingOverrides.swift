import Foundation

/// The user's own model prices at `~/.quiver/config/pricing.json`:
///
///     {"schema": 1, "models": {"<name>": {
///         "input": <usd per 1M>, "output": <usd per 1M>,
///         "cache_read": <usd per 1M>, "cache_write": <usd per 1M>}}}
///
/// A missing file is an empty list. Saved under the model's base name,
/// these merge into the `PricingCatalog` ahead of LiteLLM and the
/// supplement, so every form of the name reprices.
public struct PricingOverrides: Sendable {
    public struct Prices: Equatable, Sendable, Codable {
        public var input: Double
        public var output: Double
        public var cacheRead: Double
        public var cacheWrite: Double

        public init(input: Double, output: Double, cacheRead: Double = 0, cacheWrite: Double = 0) {
            self.input = input
            self.output = output
            self.cacheRead = cacheRead
            self.cacheWrite = cacheWrite
        }
    }

    public let url: URL
    public private(set) var models: [String: Prices]

    public init(url: URL) {
        self.url = url
        models = (try? Self.read(url)) ?? [:]
    }

    /// `models` as LiteLLM-shaped per-token entries for
    /// `PricingCatalog.init(liteLLM:supplement:overrides:)`.
    public var entries: [String: [String: Any]] {
        models.mapValues { prices in
            [
                "input_cost_per_token": prices.input / 1_000_000,
                "output_cost_per_token": prices.output / 1_000_000,
                "cache_read_input_token_cost": prices.cacheRead / 1_000_000,
                "cache_creation_input_token_cost": prices.cacheWrite / 1_000_000,
            ]
        }
    }

    /// Set `name`'s price and write the file atomically.
    public mutating func set(_ name: String, prices: Prices) throws {
        models[name] = prices
        let document: [String: Any] = [
            "schema": 1,
            "models": models.mapValues { prices in
                [
                    "input": prices.input, "output": prices.output,
                    "cache_read": prices.cacheRead, "cache_write": prices.cacheWrite,
                ]
            },
        ]
        let data = try JSONSerialization.data(
            withJSONObject: document, options: [.prettyPrinted, .sortedKeys])
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
    }

    private static func read(_ url: URL) throws -> [String: Prices] {
        let object = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any]
        var models: [String: Prices] = [:]
        for (name, value) in object?["models"] as? [String: [String: Any]] ?? [:] {
            guard let input = JSONValue.double(value["input"]),
                  let output = JSONValue.double(value["output"]),
                  input >= 0, output >= 0 else { continue }
            models[name] = Prices(
                input: input, output: output,
                cacheRead: JSONValue.double(value["cache_read"]) ?? 0,
                cacheWrite: JSONValue.double(value["cache_write"]) ?? 0)
        }
        return models
    }
}

extension JSONValue {
    /// A finite number from a JSON value, nil for anything else.
    static func double(_ value: Any?) -> Double? {
        guard let number = value as? NSNumber,
              !(number === kCFBooleanTrue || number === kCFBooleanFalse) else { return nil }
        let double = number.doubleValue
        return double.isFinite ? double : nil
    }
}
