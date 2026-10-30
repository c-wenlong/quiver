import SwiftUI
import XCTest
@testable import QuiverRemote

/// The user's text size (Dynamic Type) reaches only fonts that come
/// through `Theme.font(size:weight:design:)`.
@MainActor
final class DynamicTypeFontTests: XCTestCase {
    /// A `.system(size:)` written in a view keeps its size at every
    /// setting; a text style (`.caption`) scales but adds leading the
    /// Theme's fonts don't have, so it reads looser than its neighbours.
    func testEveryFontGoesThroughTheTheme() throws {
        let sources = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources/QuiverRemote")
        let files = try XCTUnwrap(FileManager.default.enumerator(at: sources, includingPropertiesForKeys: nil))
        let styles = "largeTitle|title|title2|title3|headline|subheadline|body|callout|footnote|caption|caption2"
        let unscaled = try Regex(#"\.system\(size:|[sS]ystemFont\(ofSize:|\.system\(\.(\#(styles))\b|\.font\(\.(\#(styles))\b"#)
        var offenders: [String] = []
        var scanned = 0
        for case let file as URL in files where file.pathExtension == "swift" && file.lastPathComponent != "Theme.swift" {
            scanned += 1
            let lines = try String(contentsOf: file, encoding: .utf8).split(separator: "\n", omittingEmptySubsequences: false)
            for (index, line) in lines.enumerated() where line.contains(unscaled) {
                offenders.append("\(file.lastPathComponent):\(index + 1)")
            }
        }
        XCTAssertGreaterThan(scanned, 20, "found no sources at \(sources.path)")
        XCTAssertEqual(offenders, [], "Use Theme.font(size:) so the user's text size reaches these")
    }

    /// At the default text size a Theme font draws exactly as the fixed
    /// system font of that size did, so turning Dynamic Type on changed
    /// nothing for someone who never moved the slider.
    func testThemeFontsDrawLikeTheSystemFontAtTheDefaultSize() {
        let text = "Type the code shown on a new device to approve it. Devices paired before are below."
        for (size, weight, design) in [(11.0, Font.Weight.regular, Font.Design.default), (13, .medium, .default),
                                       (15, .semibold, .default), (20, .semibold, .default),
                                       (12, .regular, .monospaced), (34, .semibold, .monospaced)] {
            let fixed = png(.system(size: size, weight: weight, design: design), text, .large)
            XCTAssertNotNil(fixed)
            XCTAssertEqual(png(Theme.font(size: size, weight: weight, design: design), text, .large), fixed,
                           "\(size)pt \(weight) \(design)")
        }
    }

    func testThemeFontsGrowWithTheTextSize() {
        let large = height(Theme.caption, .large)
        XCTAssertGreaterThan(height(Theme.caption, .xxxLarge), large)
        XCTAssertGreaterThan(height(Theme.caption, .accessibility5), height(Theme.caption, .xxxLarge))
        XCTAssertGreaterThan(height(Theme.font(size: 10), .accessibility3), height(Theme.font(size: 10), .large))
    }

    func testTheTerminalFontGrowsOnlyUpToXXLarge() {
        XCTAssertEqual(Theme.terminalFont(.large).pointSize, 13)
        let xxLarge = Theme.terminalFont(.xxLarge).pointSize
        XCTAssertGreaterThan(Theme.terminalFont(.xLarge).pointSize, 13)
        XCTAssertGreaterThan(xxLarge, Theme.terminalFont(.xLarge).pointSize)
        XCTAssertEqual(Theme.terminalFont(.accessibility5).pointSize, xxLarge)
    }

    private func render(_ font: Font, _ text: String, _ size: DynamicTypeSize) -> UIImage? {
        let renderer = ImageRenderer(content: Text(text).font(font)
            .frame(width: 200, alignment: .leading).fixedSize(horizontal: false, vertical: true)
            .background(.white).dynamicTypeSize(size))
        renderer.scale = 2
        return renderer.uiImage
    }

    private func png(_ font: Font, _ text: String, _ size: DynamicTypeSize) -> Data? {
        render(font, text, size)?.pngData()
    }

    private func height(_ font: Font, _ size: DynamicTypeSize) -> CGFloat {
        render(font, "Ag", size)?.size.height ?? 0
    }
}
