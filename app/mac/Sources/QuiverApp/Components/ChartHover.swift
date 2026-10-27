import Observation

/// Which bar's card the Models chart shows. The first card waits
/// `openDelay` so sweeping across the chart never flashes cards; with a
/// card up, entering another bar swaps it in at once. Leaving a bar keeps
/// its card for `closeDelay`, so crossing the gap to the next bar switches
/// the card instead of closing it.
@MainActor @Observable
final class ChartHover {
    static let openDelay: Duration = .milliseconds(400)
    static let closeDelay: Duration = .milliseconds(180)

    /// The bar whose card shows, by its row's id: an index would point
    /// at a different model once a rebuild adds or drops a row.
    private(set) var shown: String?
    /// The bar waiting out the open delay.
    @ObservationIgnored private var pending: String?
    /// The open or close wait in flight; a newer one cancels it.
    @ObservationIgnored private var wait: Task<Void, Never>?
    @ObservationIgnored private let sleep: @MainActor (Duration) async throws -> Void

    init(sleep: @escaping @MainActor (Duration) async throws -> Void = { try await Task.sleep(for: $0) }) {
        self.sleep = sleep
    }

    /// The pointer entered the bar with row id `index`.
    func enter(_ index: String) {
        if index == shown {
            cancelWait()
            return
        }
        // A card is already up (or still inside its close delay): the new
        // bar's card replaces it at once, in its own place.
        if shown != nil {
            cancelWait()
            shown = index
            return
        }
        guard index != pending else { return }
        cancelWait()
        pending = index
        after(Self.openDelay) { [weak self] in
            self?.shown = index
            self?.pending = nil
        }
    }

    /// The pointer left the bar with row id `index`. Leaving a bar that is neither shown
    /// nor about to show changes nothing: the next bar's enter can arrive
    /// before this bar's leave.
    func leave(_ index: String) {
        guard (pending ?? shown) == index else { return }
        cancelWait()
        after(Self.closeDelay) { [weak self] in self?.shown = nil }
    }

    /// Where the shown card's bar sits in `ids` (the chart's row ids, in
    /// order) now: it follows its model when rows move, and is nil, so
    /// the card hides, once that model is gone.
    func index(in ids: [String]) -> Int? {
        shown.flatMap { ids.firstIndex(of: $0) }
    }

    /// Waits for the wait in flight to finish, for tests.
    func settle() async { await wait?.value }

    private func cancelWait() {
        wait?.cancel()
        wait = nil
        pending = nil
    }

    private func after(_ delay: Duration, _ action: @escaping @MainActor () -> Void) {
        wait = Task { @MainActor [sleep] in
            do { try await sleep(delay) } catch { return }
            guard !Task.isCancelled else { return }
            action()
        }
    }
}
