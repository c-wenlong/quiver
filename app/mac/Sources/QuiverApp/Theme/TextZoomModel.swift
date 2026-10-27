import Foundation
import Observation
import QuiverCore

/// The text zoom View › Zoom In / Zoom Out / Actual Size set, kept across
/// launches. `Theme.font(size:)` reads it, so a view that draws text redraws
/// when it changes, with its state (scroll, selection, open cards) intact.
@MainActor @Observable
final class TextZoomModel {
    static let shared = TextZoomModel()
    private static let defaultsKey = "textZoom"

    var zoom: Double = {
        // double(forKey:) also reads a `-textZoom 0.8` launch argument,
        // which arrives as a string.
        guard UserDefaults.standard.object(forKey: defaultsKey) != nil else { return TextZoom.standard }
        return TextZoom.clamped(UserDefaults.standard.double(forKey: defaultsKey))
    }() {
        didSet { UserDefaults.standard.set(zoom, forKey: Self.defaultsKey) }
    }

    var canZoomIn: Bool { zoom < TextZoom.range.upperBound }
    var canZoomOut: Bool { zoom > TextZoom.range.lowerBound }
    var isActualSize: Bool { zoom == TextZoom.standard }

    func zoomIn() { zoom = TextZoom.larger(than: zoom) }
    func zoomOut() { zoom = TextZoom.smaller(than: zoom) }
    func actualSize() { zoom = TextZoom.standard }
}
