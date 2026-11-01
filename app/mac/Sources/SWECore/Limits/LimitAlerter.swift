import Foundation

/// A notification to post about one limit window.
public struct LimitAlert: Sendable, Equatable {
    public enum Kind: String, Sendable, Equatable { case crossed, reset }
    public var kind: Kind
    public var reading: LimitReading

    public init(kind: Kind, reading: LimitReading) {
        self.kind = kind
        self.reading = reading
    }

    public var title: String {
        let window = reading.window.isEmpty ? "" : "\(UsageFormat.window(reading.window)) "
        switch kind {
        case .crossed: return "\(reading.title) is at \(reading.percent)% of its \(window)limit"
        case .reset: return "\(reading.title): \(window)limit has reset"
        }
    }

    public func body(now: Date) -> String {
        switch kind {
        case .crossed:
            guard let reset = reading.resetsAt else { return "No reset time reported." }
            let formatter = RelativeDateTimeFormatter()
            formatter.locale = Locale(identifier: "en_US")
            formatter.unitsStyle = .full
            return "Resets \(formatter.localizedString(for: reset, relativeTo: now))."
        case .reset:
            return "Now at \(reading.percent)%."
        }
    }
}

/// Decides which limit windows to notify about: once when a window reaches
/// 90%, and once when a window that did so resets. Its state is saved
/// between launches so a relaunch never repeats a notification.
public struct LimitAlerter: Sendable {
    public struct State: Codable, Sendable, Equatable {
        public struct Entry: Codable, Sendable, Equatable {
            /// The reset time the window reported when it crossed.
            public var resetsAt: Date?
            public var alertedAt: Date
        }
        /// Windows that crossed and have not reset yet, by `LimitReading.id`.
        public var alerted: [String: Entry] = [:]

        public init() {}
    }

    public private(set) var state: State
    public var threshold: Double = 90
    /// A window that drops below this without a reset has rolled back on its
    /// own; it may notify again the next time it crosses.
    public var rearmBelow: Double = 80
    /// How far a reported reset time may drift before it counts as a new
    /// window. Codex derives it from "seconds until reset", so it wobbles.
    public var resetDrift: TimeInterval = 600
    /// Forget a crossing that never saw its reset, such as a removed account.
    public var forgetAfter: TimeInterval = 30 * 86_400

    public init(state: State = State()) {
        self.state = state
    }

    public mutating func evaluate(_ readings: [LimitReading], now: Date) -> [LimitAlert] {
        var alerts: [LimitAlert] = []
        for reading in readings where !reading.isStale {
            let taken = reading.fetchedAt ?? now
            let over = Double(reading.percent) >= threshold && reading.isCurrent(now: now)
            if let entry = state.alerted[reading.id] {
                let passed = entry.resetsAt.map { taken >= $0 } ?? false
                let moved = zip(reading.resetsAt, entry.resetsAt).map { $0 > $1 + resetDrift } ?? false
                if passed || moved {
                    if over {
                        // Already full again in the new window.
                        state.alerted[reading.id] = .init(resetsAt: reading.resetsAt, alertedAt: now)
                        alerts.append(LimitAlert(kind: .crossed, reading: reading))
                    } else {
                        state.alerted[reading.id] = nil
                        alerts.append(LimitAlert(kind: .reset, reading: reading))
                    }
                } else if reading.usedPercent < rearmBelow {
                    state.alerted[reading.id] = nil
                }
            } else if over {
                state.alerted[reading.id] = .init(resetsAt: reading.resetsAt, alertedAt: now)
                alerts.append(LimitAlert(kind: .crossed, reading: reading))
            }
        }
        state.alerted = state.alerted.filter { now.timeIntervalSince($0.value.alertedAt) < forgetAfter }
        return alerts
    }
}

private func zip<A, B>(_ a: A?, _ b: B?) -> (A, B)? {
    guard let a, let b else { return nil }
    return (a, b)
}
