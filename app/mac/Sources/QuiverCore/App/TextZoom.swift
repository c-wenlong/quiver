import Foundation

/// The app's text size, as a multiple of the designed point sizes: View ›
/// Zoom In / Zoom Out / Actual Size (⌘+ / ⌘− / ⌘0), in browser-like steps.
/// Zooming out stops at `steps.first` and never takes text under
/// `minimumPointSize`; text designed under it keeps its size when zooming
/// out. Zooming in grows every size, small ones included.
public enum TextZoom {
    public static let standard = 1.0
    public static let steps: [Double] = [0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5]
    public static var range: ClosedRange<Double> { steps[0]...steps[steps.count - 1] }
    /// The smallest text zooming out makes, in points.
    public static let minimumPointSize = 9.0

    public static func clamped(_ zoom: Double) -> Double {
        guard zoom.isFinite else { return standard }
        return min(max(zoom, range.lowerBound), range.upperBound)
    }

    /// The next step up, or the largest when already there. A zoom between
    /// steps (an edited default) moves to the step past it.
    public static func larger(than zoom: Double) -> Double {
        steps.first { $0 > zoom + 0.001 } ?? range.upperBound
    }

    public static func smaller(than zoom: Double) -> Double {
        steps.last { $0 < zoom - 0.001 } ?? range.lowerBound
    }

    /// `size` points at `zoom`, never under the floor once zoomed out.
    public static func pointSize(_ size: Double, at zoom: Double) -> Double {
        max(size * clamped(zoom), min(size, minimumPointSize))
    }
}
