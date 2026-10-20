import Foundation

/// An OpenAI-compatible endpoint the "Add key" sheet's Custom provider
/// path writes: a base URL, a key and a model, saved as three exports
/// (`X_BASE_URL`, `X_API_KEY`, `X_MODEL`) under the provider's heading.
/// The model list and the key check both come from `GET {base}/models`,
/// the endpoint the OpenAI SDK reads.
public enum CustomProvider {
    /// A model the endpoint lists, as the picker shows it.
    public struct Model: Equatable, Sendable, Identifiable {
        /// The value `X_MODEL` gets, e.g. "gemma4:26b".
        public var id: String
        /// The row's title: the endpoint's display name when it gives one.
        public var label: String
    }

    public enum FetchError: Error, Equatable, LocalizedError {
        /// The base URL can't produce a models URL.
        case badBaseURL
        /// The request never got an answer (offline, DNS, timeout).
        case unreachable(String)
        /// The endpoint answered with a non-2xx status.
        case http(Int, String?)

        public var errorDescription: String? {
            switch self {
            case .badBaseURL:
                "The base URL has to be an http(s) URL, like https://host/v1."
            case .unreachable(let detail): "Couldn't reach the endpoint: \(detail)"
            case .http(let status, let message):
                "The endpoint answered HTTP \(status)\(message.map { ": \($0)" } ?? "")."
            }
        }
    }

    /// The env-var prefix a provider name produces: uppercased, runs of
    /// non-letters-and-digits folded to one underscore, ends trimmed —
    /// "SoCLaaS" gives SOCLAAS and "NUS SoC" gives NUS_SOC. Empty when the
    /// name has no letter or digit at all.
    public static func envPrefix(for name: String) -> String {
        var prefix = ""
        var pendingUnderscore = false
        for character in name.uppercased() {
            if character.isLetter || character.isNumber {
                if pendingUnderscore, !prefix.isEmpty { prefix.append("_") }
                prefix.append(character)
                pendingUnderscore = false
            } else {
                pendingUnderscore = true
            }
        }
        return prefix
    }

    /// `{base}/models`, or nil when `base` isn't an http(s) URL. A trailing
    /// slash on the base is folded so "…/v1/" and "…/v1" agree.
    public static func modelsURL(_ base: String) -> URL? {
        let trimmed = base.trimmingCharacters(in: .whitespaces)
        guard let url = URL(string: trimmed),
              let scheme = url.scheme?.lowercased(),
              scheme == "https" || scheme == "http",
              url.host != nil
        else { return nil }
        let stem = trimmed.hasSuffix("/") ? String(trimmed.dropLast()) : trimmed
        return URL(string: stem + "/models")
    }

    /// The key check for a custom provider: `GET {base}/models` with the
    /// key as a bearer token — the same request the model picker sends.
    public static func probe(baseURL: String) -> APIKeyProbe? {
        guard let url = modelsURL(baseURL) else { return nil }
        return APIKeyProbe(hosts: [url], method: "GET", body: nil,
                           auth: .bearer, worksOnBadRequest: false)
    }

    /// Every var the save writes except the model's, in file order:
    /// the base URL first (the SoCLaaS convention), then the key.
    public static func entries(prefix: String, baseURL: String, key: String, model: String)
        -> [(name: String, value: String)] {
        var entries = [(name: prefix + "_BASE_URL", value: baseURL),
                       (name: prefix + "_API_KEY", value: key)]
        if !model.isEmpty {
            entries.append((name: prefix + "_MODEL", value: model))
        }
        return entries
    }

    /// The models an OpenAI-compatible list reply carries. Reads the
    /// `{"data": [{"id": …}]}` shape and a bare `[…]` list; a missing or
    /// malformed payload yields an empty list, never an error. `label`
    /// comes from a `display_name` or `name` on the entry or one level
    /// down (SoCLaaS nests it under `soclaas.display_name`).
    public static func models(in body: Data) -> [Model] {
        guard let object = try? JSONSerialization.jsonObject(with: body) else { return [] }
        let list: [Any]
        if let dict = object as? [String: Any], let data = dict["data"] as? [Any] {
            list = data
        } else if let array = object as? [Any] {
            list = array
        } else {
            return []
        }
        var models: [Model] = []
        for item in list {
            guard let dict = item as? [String: Any],
                  let id = dict["id"] as? String, !id.isEmpty else { continue }
            var label = dict["display_name"] as? String ?? dict["name"] as? String
            if label == nil {
                for value in dict.values {
                    if let nested = value as? [String: Any],
                       let name = nested["display_name"] as? String {
                        label = name
                        break
                    }
                }
            }
            models.append(Model(id: id, label: label ?? id))
        }
        return models
    }
}
