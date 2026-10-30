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
/// with backoff until the consuming task is cancelled.
enum EventStream {
    /// The `?last=` to reconnect with once `env` arrived: the highest seq
    /// seen, except that `journal.truncated` carries the journal's head,
    /// which wins even when lower. A hub whose journal was wiped starts
    /// its seqs again, and a cursor left above the head hears nothing.
    static func resume(after env: Envelope, from cursor: Int) -> Int {
        guard let seq = env.seq else { return cursor }
        return env.type == "journal.truncated" ? seq : max(cursor, seq)
    }

    static func events(client: HubClient, key: String, last: Int) -> AsyncStream<StreamEvent> {
        AsyncStream { continuation in
            let task = Task {
                var cursor = last
                var delay: UInt64 = 1
                while !Task.isCancelled {
                    var req = client.request("GET", "/v1/sessions/\(HubClient.encodeKey(key))/events",
                                             query: ["last": "\(cursor)"])
                    req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    do {
                        let (bytes, response) = try await HubClient.streamSession.bytes(for: req)
                        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                        if status == 401 { continuation.yield(.unauthorized); break }
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
                            cursor = resume(after: env, from: cursor)
                            if env.type == "journal.truncated" { continuation.yield(.truncated(head: env.seq)); continue }
                            if env.type == "journal.caught-up" { continue }
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
    case connected, disconnected, unauthorized
    /// The replay can't cover the gap; `head` is the journal's newest
    /// seq, which the next resume starts from.
    case truncated(head: Int?)
    case event(Envelope)
}
