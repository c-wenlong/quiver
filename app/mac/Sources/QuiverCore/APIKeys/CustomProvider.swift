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

    /// The env-var prefix a provider name produces: uppercased and folded
    /// to ASCII ("Café" gives CAFE), runs of what is left that isn't a
    /// letter or digit become one underscore, ends trimmed — "SoCLaaS"
    /// gives SOCLAAS and "NUS SoC" gives NUS_SOC. Empty when nothing the
    /// shell accepts is left, so "學" can't save a variable.
    public static func envPrefix(for name: String) -> String {
        var prefix = ""
        var pendingUnderscore = false
        let folded = name.uppercased().folding(options: .diacriticInsensitive, locale: nil)
        for character in folded {
            if character.isASCII, character.isLetter || character.isNumber {
                if pendingUnderscore, !prefix.isEmpty { prefix.append("_") }
                prefix.append(character)
                pendingUnderscore = false
            } else {
                pendingUnderscore = true
            }
        }
        return prefix
    }

    /// `{base}/models`, or nil when `base` isn't an https URL, or an http
    /// one on this machine or a private network. The key rides every
    /// request as a bearer token, so plain http to a public host would
    /// hand it to anyone on the path. The append happens on the path so
    /// a base that carries a query or fragment keeps it in place; a
    /// trailing slash folds so "…/v1/" and "…/v1" agree.
    public static func modelsURL(_ base: String) -> URL? {
        let trimmed = base.trimmingCharacters(in: .whitespaces)
        guard var components = URLComponents(string: trimmed),
              let scheme = components.scheme?.lowercased(),
              let host = components.host, !host.isEmpty,
              scheme == "https" || (scheme == "http" && isPrivateHost(host))
        else { return nil }
        var path = components.percentEncodedPath
        while path.hasSuffix("/") { path.removeLast() }
        components.percentEncodedPath = path + "/models"
        return components.url
    }

    /// Loopback, link-local, RFC 1918, CGNAT (Tailscale) and unique-local
    /// addresses, `localhost` and mDNS `.local` names: where a cleartext
    /// local server (Ollama, LM Studio, a tailnet box) is expected.
    static func isPrivateHost(_ host: String) -> Bool {
        let host = host.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        if host == "localhost" || host.hasSuffix(".localhost") || host.hasSuffix(".local") {
            return true
        }
        if host.contains(":") {
            return host == "::1" || host.hasPrefix("fc") || host.hasPrefix("fd")
                || host.hasPrefix("fe80:")
        }
        // Every label must be an octet: "10.0.0.1.example.com" is a name.
        let labels = host.split(separator: ".", omittingEmptySubsequences: false)
        let parts = labels.compactMap { UInt8($0) }
        guard labels.count == 4, parts.count == 4 else { return false }
        switch (parts[0], parts[1]) {
        case (127, _), (10, _), (192, 168), (169, 254): return true
        case (172, 16...31), (100, 64...127): return true
        default: return false
        }
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
