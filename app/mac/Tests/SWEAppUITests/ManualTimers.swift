import Foundation
@testable import SWEApp

/// Timers a test fires by hand, in the order they were set, instead of
/// running the main run loop for an animation's length and hoping its
/// timer came due inside that window.
@MainActor
final class ManualTimers {
    private var pending: [(delay: TimeInterval, body: @MainActor () -> Void)] = []

    var count: Int { pending.count }
    /// The longest delay asked for among the timers still set.
    var longest: TimeInterval? { pending.map(\.delay).max() }

    var timer: MainTimer {
        MainTimer { [unowned self] delay, body in self.pending.append((delay, body)) }
    }

    /// Fires every timer set so far, oldest first, as their delays would.
    func fireAll() {
        let due = pending.sorted { $0.delay < $1.delay }
        pending = []
        for entry in due { entry.body() }
    }
}
