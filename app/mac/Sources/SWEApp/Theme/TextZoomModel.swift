import Foundation
import Observation
import SWECore

/// The text zoom View › Zoom In / Zoom Out / Actual Size set, kept across
/// launches. `Theme.font(size:)` reads it, so a view that draws text redraws
/// when it changes, with its state (scroll, selection, open cards) intact.
@MainActor @Observable
final class TextZoomModel {
    /// A `var` only so tests can put in a model over a scratch suite.
    static var shared = TextZoomModel()
    private static let defaultsKey = "textZoom"

    @ObservationIgnored private let defaults: UserDefaults

    var zoom: Double {
        didSet { defaults.set(zoom, forKey: Self.defaultsKey) }
    }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        // double(forKey:) also reads a `-textZoom 0.8` launch argument,
        // which arrives as a string.
        zoom = defaults.object(forKey: Self.defaultsKey) == nil
            ? TextZoom.standard : TextZoom.clamped(defaults.double(forKey: Self.defaultsKey))
    }

    var canZoomIn: Bool { zoom < TextZoom.range.upperBound }
    var canZoomOut: Bool { zoom > TextZoom.range.lowerBound }
    var isActualSize: Bool { zoom == TextZoom.standard }

    func zoomIn() { zoom = TextZoom.larger(than: zoom) }
    func zoomOut() { zoom = TextZoom.smaller(than: zoom) }
    func actualSize() { zoom = TextZoom.standard }
}
