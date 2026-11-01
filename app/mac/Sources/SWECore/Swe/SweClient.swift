import Foundation

public enum SweError: Error, Equatable, Sendable {
    case notInstalled(String)
    case commandFailed(status: Int32, message: String)
    case unsupportedSchema(Int)
    case jsonUnsupported
    case malformedOutput(String)
    /// The subprocess ran past its deadline and was stopped.
    case timedOut
}

extension SweError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .notInstalled(let name):
            "Couldn't find \(name). Fix the swe path in Settings › General, or reinstall the app."
        case .commandFailed(_, let message):
            message.isEmpty ? "swe exited with an error." : message
        case .jsonUnsupported:
            "This swe is older than the app and has no --json output. Update quiver, or pick a newer swe in Settings."
        case .unsupportedSchema(let version):
            "This swe speaks schema \(version). Update the app."
        case .malformedOutput(let detail):
            "swe returned output the app couldn't read. \(detail)"
        case .timedOut:
            "The command took too long and was stopped."
        }
    }
}

/// Talks to `swe … --json`.
///
/// Every reply is `{"schema": N, "data": …}` (see src/swe/json_output.py).
/// A schema the app does not know is refused rather than half-decoded.
public struct SweClient: Sendable {
    public static let supportedSchema = 1

    let runner: CommandRunning

    public init(runner: CommandRunning = ProcessRunner()) {
        self.runner = runner
    }

    /// `excludingUsage` lists those harnesses without polling their usage.
    /// `cacheTTL` overrides swe's five-minute freshness for the run and
    /// `refreshFailed` asks it to refetch only the readings that failed;
    /// both need an swe new enough to know the flags.
    public func harnesses(withUsage usage: Bool, excludingUsage excluded: [String] = [],
                          cacheTTL: TimeInterval? = nil,
                          refreshFailed: Bool = false) async throws -> [Harness] {
        var args = usage ? ["list", "--usage", "--json"] : ["list", "--json"]
        if usage {
            if let cacheTTL { args.append("--cache-ttl=\(Int(cacheTTL))") }
            if refreshFailed { args.append("--refresh-failed") }
            if !excluded.isEmpty {
                args.append("--exclude=" + excluded.joined(separator: ","))
            }
        }
        let data: HarnessList = try await call(args)
        return data.harnesses
    }

    /// `@concurrent`: the envelope decode below runs wherever this is
    /// awaited — the main actor for most models — and a big swe payload
    /// would stall the UI there. Hop off the caller's executor.
    ///
    /// `timeout` overrides the runner's own bound for a command known to
    /// run longer (`remote setup --apply` waits on certificates and launchd).
    @concurrent
    func call<T: Decodable>(_ arguments: [String], input: Data? = nil,
                            allowFailure: Bool = false,
                            timeout: Duration? = nil) async throws -> T {
        let result = try await runner.run(arguments, input: input, timeout: timeout)
        // Cancelling stops the child, which then exits with a signal status
        // rather than throwing; that is a cancellation, not a swe failure.
        try Task.checkCancellation()
        let stdoutText = String(decoding: result.stdout.prefix(500), as: UTF8.self)
        if Self.rejectsJSONFlag(stdoutText) || Self.rejectsJSONFlag(result.stderr) {
            throw SweError.jsonUnsupported
        }
        let isJSON = Self.plain(stdoutText).hasPrefix("{")
        // `/usr/bin/env <swe>` exits 127 with its own message when the
        // binary is missing; that is "not installed", not a swe refusal.
        if result.status == 127, let missing = Self.envNotFoundName(result.stderr) {
            throw SweError.notInstalled(missing)
        }
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
        // One pass over the payload: the schema rides inside the same
        // envelope as the data instead of a separate header decode.
        do {
            let envelope = try decoder.decode(Envelope<T>.self, from: result.stdout)
            guard envelope.schema == Self.supportedSchema else {
                throw SweError.unsupportedSchema(envelope.schema)
            }
            return envelope.data
        } catch let error as SweError {
            throw error
        } catch {
            // The header may still be readable where the payload is not:
            // a newer schema gets named rather than reported as garbage.
            guard let header = try? decoder.decode(SchemaHeader.self, from: result.stdout)
            else { throw SweError.malformedOutput(Self.plain(String(stdoutText.prefix(200)))) }
            guard header.schema == Self.supportedSchema else {
                throw SweError.unsupportedSchema(header.schema)
            }
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
                || plain.contains("Unknown option")
                // `swe find` from before plugins had --json
                || plain.contains("--json is only available for skills"))
    }

    /// The binary `env` could not find, from "env: <name>: No such file
    /// or directory". Returns nil for anything else.
    static func envNotFoundName(_ stderr: String) -> String? {
        let plain = plain(stderr)
        guard plain.hasPrefix("env: "), plain.hasSuffix(": No such file or directory") else { return nil }
        let name = plain.dropFirst("env: ".count).dropLast(": No such file or directory".count)
        return name.isEmpty ? nil : String(name)
    }

    /// Text with terminal colour codes removed and edges trimmed.
    static func plain(_ text: String) -> String {
        text.replacing(/\u{1B}\[[0-9;]*[A-Za-z]/, with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// Which swe to run: one chosen in Settings, else the one bundled inside
/// SWE.app, else whatever PATH finds (a build without the bundle, such
/// as `swift run`).
public enum SweLocation {
    public static let defaultsKey = "swePath"

    /// `Contents/Resources/swe/bin/swe`, put there by
    /// `app/mac/script/bundle-swe.sh`, when this build carries it.
    public static let bundled: String? = bundled(in: Bundle.main)

    static func bundled(in bundle: Bundle) -> String? {
        guard let url = bundle.resourceURL?.appending(path: "swe/bin/swe"),
              FileManager.default.isExecutableFile(atPath: url.path) else { return nil }
        return url.path
    }

    public static func executable(override: String?, bundled: String? = SweLocation.bundled) -> String {
        guard let path = override?.trimmingCharacters(in: .whitespaces), !path.isEmpty else {
            return bundled ?? "swe"
        }
        let expanded = (path as NSString).expandingTildeInPath
        guard FileManager.default.isExecutableFile(atPath: expanded),
              PathSafety.rejection(of: expanded) == nil else {
            return bundled ?? "swe"
        }
        return expanded
    }
}

struct SchemaHeader: Decodable { let schema: Int }
struct Envelope<T: Decodable>: Decodable {
    let schema: Int
    let data: T
}

/// Decodes one element as nil when it is malformed, so a single bad row
/// degrades instead of failing the whole list.
struct LossyRow<Wrapped: Decodable>: Decodable {
    let value: Wrapped?

    init(from decoder: Decoder) throws {
        value = try? Wrapped(from: decoder)
    }
}

struct HarnessList: Decodable {
    let harnesses: [Harness]

    enum CodingKeys: String, CodingKey { case harnesses }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        harnesses = try c.decode([LossyRow<Harness>].self, forKey: .harnesses)
            .compactMap(\.value)
    }
}
