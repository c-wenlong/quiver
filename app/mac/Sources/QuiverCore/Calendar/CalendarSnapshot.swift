import Foundation
import os

/// The most recent ranges' blocks, by provider, so the Calendar page's
/// first paint does not wait on a read. Saved to
/// `~/.swe/cache/app-calendar.json`; an out-of-date shape is ignored
/// like every other snapshot.
public struct CalendarSnapshot: SnapshotContent, Equatable {
    public static let currentVersion = 2
    public var version = Self.currentVersion
    public var savedAt = Date()

    /// One range's blocks, keyed by the provider's index in the model's
    /// `providers` array.
    public struct Entry: Codable, Sendable, Equatable {
        public var start: Date
        public var end: Date
        public var providers: [Int: [ActivityBlock]]
        public init(start: Date, end: Date, providers: [Int: [ActivityBlock]]) {
            self.start = start
            self.end = end
            self.providers = providers
        }
    }

    /// Newest first, capped at the model's keep count.
    public var ranges: [Entry]
    public init(ranges: [Entry]) { self.ranges = ranges }
}

extension Logger {
    static let calendar = Logger(subsystem: "com.cwenlong.quiver", category: "calendar")
}
