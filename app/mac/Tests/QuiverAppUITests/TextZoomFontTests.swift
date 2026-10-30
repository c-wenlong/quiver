import Foundation
import QuiverCore
import Testing
@testable import QuiverApp

/// View › Zoom only reaches text whose font comes through `Theme`: a
/// `.system(size:)` or a text style (`.caption`) written in a view keeps
/// its size at every zoom.
@Suite(.serialized)
@MainActor
struct TextZoomFontTests {
    @Test func everyFontGoesThroughTheTheme() throws {
        let sources = URL(filePath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appending(path: "Sources/QuiverApp")
        let files = try #require(FileManager.default.enumerator(at: sources, includingPropertiesForKeys: nil))
        let unscaled = try #require(try Regex(#"\.system\(size:|\.font\(\.(largeTitle|title|title2|title3|headline|subheadline|body|callout|footnote|caption|caption2)\b"#))
        var offenders: [String] = []
        for case let file as URL in files where file.pathExtension == "swift" && file.lastPathComponent != "Theme.swift" {
            let lines = try String(contentsOf: file, encoding: .utf8).split(separator: "\n", omittingEmptySubsequences: false)
            for (index, line) in lines.enumerated() where line.contains(unscaled) {
                offenders.append("\(file.lastPathComponent):\(index + 1)")
            }
        }
        #expect(offenders.isEmpty, "Use Theme.font(size:) so View › Zoom reaches these: \(offenders)")
    }

    @Test func themeSizesFollowTheZoom() {
        // A model over a scratch suite stands in for the app's, so the
        // zoom it saves never lands in the real defaults.
        let defaults = TestDefaults("textzoom")
        let saved = TextZoomModel.shared
        defer { TextZoomModel.shared = saved }
        let model = TextZoomModel(defaults: defaults)
        TextZoomModel.shared = model
        model.zoom = 1
        #expect(Theme.scaled(14) == 14)
        model.zoom = 1.5
        #expect(Theme.scaled(14) == 21)
        model.zoom = 0.75
        #expect(Theme.scaled(28) == 21)
        #expect(Theme.scaled(10) == CGFloat(TextZoom.minimumPointSize))
        model.zoomOut()
        #expect(model.zoom == 0.75)
        #expect(!model.canZoomOut)
        model.actualSize()
        #expect(model.isActualSize)
        #expect(defaults.double(forKey: "textZoom") == TextZoom.standard)
        #expect(TextZoomModel(defaults: defaults).zoom == TextZoom.standard)
    }

    @Test func aSavedZoomComesBackClamped() {
        let defaults = TestDefaults("textzoom")
        #expect(TextZoomModel(defaults: defaults).zoom == TextZoom.standard)
        defaults.set(1.5, forKey: "textZoom")
        #expect(TextZoomModel(defaults: defaults).zoom == 1.5)
        defaults.set(99.0, forKey: "textZoom")
        #expect(TextZoomModel(defaults: defaults).zoom == TextZoom.range.upperBound)
    }
}
