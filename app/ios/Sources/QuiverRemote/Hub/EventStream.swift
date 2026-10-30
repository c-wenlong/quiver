import Foundation

/// Splits a server-sent-events byte stream into `data:` payloads.
///
/// The hub writes `id:`, `event:` and one `data:` line per frame, then a
/// blank line; keepalives are `: ka` comments. Only `data:` matters: the
/// JSON envelope inside carries the type and seq.
struct SSEParser {
    private var dataLines: [String] = []

    /// Feeds one line (without its newline). Returns a frame's data when
    /// the line ends that frame.
    mutating func feed(_ line: String) -> String? {
        if line.isEmpty {
            defer { dataLines.removeAll() }
            return dataLines.isEmpty ? nil : dataLines.joined(separator: "\n")
        }
        if line.hasPrefix(":") { return nil }
        if line.hasPrefix("data:") {
            var value = line.dropFirst(5)
            if value.first == " " { value = value.dropFirst() }
            dataLines.append(String(value))
        }
        return nil
    }
}

/// One session's live events, resuming from `last` and reconnecting
/// with backoff until the consuming task is cancelled, or until the hub
/// says the stream will never open (`ends(status:)`).
enum EventStream {
    /// A refusal no retry will change: 403 (this device may not read
    /// it) at once, and 404 `unknown_session` once it has come back
    /// `notFoundLimit` times in a row. A hub that just restarted answers
    /// 404 for every session until its first catalog scan lands, so one
    /// 404 isn't proof the session is gone. Anything else, a restart's
    /// 502 say, is always worth reconnecting for.
    static func ends(status: Int, notFoundInARow: Int) -> Bool {
        status == 403 || (status == 404 && notFoundInARow >= notFoundLimit)
    }

    /// With the backoff (1, 2, 4, 8, 15 s) that is about half a minute of
    /// 404s, longer than a hub takes to scan its sessions after a start.
    static let notFoundLimit = 6

    static func events(client: HubClient, key: String, last: Int) -> AsyncStream<StreamEvent> {
        AsyncStream { continuation in
            let task = Task {
                var cursor = last
                var delay: UInt64 = 1
                var notFoundInARow = 0
                while !Task.isCancelled {
                    var req = client.request("GET", "/v1/sessions/\(HubClient.encodeKey(key))/events",
                                             query: ["last": "\(cursor)"])
                    req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    do {
                        let (bytes, response) = try await HubClient.streamSession.bytes(for: req)
                        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                        if status == 401 { continuation.yield(.unauthorized); break }
                        notFoundInARow = status == 404 ? notFoundInARow + 1 : 0
                        if ends(status: status, notFoundInARow: notFoundInARow) {
                            var body = Data()
                            do { for try await byte in bytes.prefix(4096) { body.append(byte) } } catch {}
                            continuation.yield(.gone(HubClient.error(status: status, body: body)))
                            break
                        }
                        guard status == 200 else { throw HubError(status: status, code: "stream", message: "stream \(status)") }
                        continuation.yield(.connected)
                        delay = 1
                        var parser = SSEParser()
                        // `lines` drops the blank separator lines, so each
                        // data line is a whole frame here: the hub never
                        // splits a JSON envelope across lines.
                        for try await line in bytes.lines {
                            guard let payload = parser.feed(line) ?? parser.feed("") else { continue }
                            guard let env = try? Hub.decoder.decode(Envelope.self, from: Data(payload.utf8)) else { continue }
                            if env.type == "journal.truncated" { continuation.yield(.truncated); continue }
                            if env.type == "journal.caught-up" { continue }
                            if let seq = env.seq { cursor = max(cursor, seq) }
                            continuation.yield(.event(env))
                        }
                    } catch {
                        if Task.isCancelled { break }
                    }
                    continuation.yield(.disconnected)
                    try? await Task.sleep(nanoseconds: delay * 1_000_000_000)
                    delay = min(delay * 2, 15)
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

enum StreamEvent {
    case connected, disconnected, unauthorized, truncated
    /// The stream won't open again; the error says why.
    case gone(HubError)
    case event(Envelope)
}
