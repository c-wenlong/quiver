import Foundation

public enum SweError: Error, Equatable, Sendable {
    case notInstalled(String)
    case commandFailed(status: Int32, message: String)
    case unsupportedSchema(Int)
    case jsonUnsupported
    case malformedOutput(String)
}

extension SweError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .notInstalled(let name):
            "Couldn't find \(name). Install quiver with pip, then reopen the app."
        case .commandFailed(_, let message):
            message.isEmpty ? "swe exited with an error." : message
        case .jsonUnsupported:
            "This swe is older than the app and has no --json output. Update quiver, or pick a newer swe in Settings."
        case .unsupportedSchema(let version):
            "This swe speaks schema \(version). Update the app."
        case .malformedOutput(let detail):
            "swe returned output the app couldn't read. \(detail)"
        }
    }
}

/// Talks to `swe … --json`.
///
/// Every reply is `{"schema": N, "data": …}` (see src/quiver/json_output.py).
/// A schema the app does not know is refused rather than half-decoded.
public struct SweClient: Sendable {
    public static let supportedSchema = 1

    let runner: CommandRunning

    public init(runner: CommandRunning = ProcessRunner()) {
        self.runner = runner
    }

    /// `excludingUsage` lists those harnesses without polling their usage.
    public func harnesses(withUsage usage: Bool, excludingUsage excluded: [String] = []) async throws -> [Harness] {
        var args = usage ? ["list", "--usage", "--json"] : ["list", "--json"]
        if usage && !excluded.isEmpty {
            args.append("--exclude=" + excluded.joined(separator: ","))
        }
        let data: HarnessList = try await call(args)
        return data.harnesses
    }

    func call<T: Decodable>(_ arguments: [String], allowFailure: Bool = false) async throws -> T {
        let result = try await runner.run(arguments)
        let stdoutText = String(decoding: result.stdout.prefix(500), as: UTF8.self)
        if Self.rejectsJSONFlag(stdoutText) || Self.rejectsJSONFlag(result.stderr) {
            throw SweError.jsonUnsupported
        }
        let isJSON = Self.plain(stdoutText).hasPrefix("{")
        if result.status != 0 && (!allowFailure || !isJSON) {
            // Refusals go to stderr, but an older swe printed them to stdout.
            let stderr = Self.plain(result.stderr)
            throw SweError.commandFailed(
                status: result.status,
                message: stderr.isEmpty && !isJSON ? Self.plain(stdoutText) : stderr)
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .secondsSince1970
        let header: SchemaHeader
        do {
            header = try decoder.decode(SchemaHeader.self, from: result.stdout)
        } catch {
            throw SweError.malformedOutput(Self.plain(String(stdoutText.prefix(200))))
        }
        guard header.schema == Self.supportedSchema else {
            throw SweError.unsupportedSchema(header.schema)
        }
        do {
            return try decoder.decode(Envelope<T>.self, from: result.stdout).data
        } catch {
            throw SweError.malformedOutput(String(describing: error))
        }
    }
}

extension SweClient {
    /// A swe from before --json says so in plain text instead of JSON.
    static func rejectsJSONFlag(_ text: String) -> Bool {
        let plain = plain(text)
        return !plain.hasPrefix("{") && plain.contains("--json")
            && (plain.contains("Unexpected args") || plain.contains("Unknown flag")
                || plain.contains("Unknown option"))
    }

    /// Text with terminal colour codes removed and edges trimmed.
    static func plain(_ text: String) -> String {
        text.replacing(/\u{1B}\[[0-9;]*[A-Za-z]/, with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// Which swe to run: one chosen in Settings, else whatever PATH finds.
public enum SweLocation {
    public static let defaultsKey = "swePath"

    public static func executable(override: String?) -> String {
        guard let path = override?.trimmingCharacters(in: .whitespaces), !path.isEmpty else {
            return "swe"
        }
        return path
    }
}

struct SchemaHeader: Decodable { let schema: Int }
struct Envelope<T: Decodable>: Decodable { let data: T }
struct HarnessList: Decodable { let harnesses: [Harness] }
