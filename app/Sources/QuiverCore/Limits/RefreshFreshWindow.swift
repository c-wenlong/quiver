import Foundation

/// How long a good reading still counts as fresh when a Refresh button is
/// pressed. Within it, Refresh re-reads only what failed; past it, Refresh
/// fetches everything. One setting drives both the per-account reads and
/// the TTL passed to `swe list`.
public enum RefreshFreshWindow {
    /// The out-of-box window: five minutes, matching swe's own cache TTL.
    public static let defaultMinutes = 5
    /// What the Settings field accepts, in minutes.
    public static let range = 1...120

    /// `typed` clamped into the accepted range.
    public static func minutes(_ typed: Int) -> Int {
        min(max(typed, range.lowerBound), range.upperBound)
    }

    /// The same window in seconds, for `UsageService` and `--cache-ttl`.
    public static func seconds(_ minutes: Int) -> TimeInterval {
        TimeInterval(Self.minutes(minutes) * 60)
    }
}
