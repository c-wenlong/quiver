import AppKit
import Foundation
import Testing
@testable import QuiverCore

@Suite struct HarnessIconTests {
    /// The harnesses TokenTracker ships a logo for; the rest come from LobeHub
    /// and Simple Icons through app/script/import-harness-icons.py.
    let fromTokenTracker = ["claude", "codex", "gemini", "copilot", "cursor", "grok", "kiro", "devin",
                            "opencode", "kilo", "pi"]

    @Test func everyBundledIconDrawsAsAnImage() throws {
        for id in HarnessIcons.available {
            let url = try #require(HarnessIcons.url(for: id), "\(id)")
            let image = try #require(NSImage(contentsOf: url), "\(id) does not load")
            #expect(image.size.width > 0 && image.size.height > 0, "\(id)")
            // CoreSVG can load a file and draw nothing (it gives up on compact
            // path data), so check the mark covers a fair share of its box.
            #expect(coverage(image) > 0.08, "\(id) draws almost nothing")
        }
        #expect(Set(fromTokenTracker).isSubset(of: Set(HarnessIcons.available)))
    }

    @Test func iconsAreSingleColourAndInert() throws {
        for id in HarnessIcons.available {
            let text = try String(contentsOf: try #require(HarnessIcons.url(for: id)), encoding: .utf8)
            let paints = Set(text.matches(of: /(?:fill|stroke)="([^"]+)"/).map { String($0.1) })
            #expect(paints.isSubset(of: ["#000", "none"]), "\(id) paints \(paints)")
            #expect(text.range(of: #"<script|\son[a-z]+=|href=|<foreignObject|<image"#, options: .regularExpression) == nil,
                    "\(id)")
        }
    }

    @Test func aliasesAndUnknownHarnesses() {
        #expect(HarnessIcons.url(for: "claude-code") == HarnessIcons.url(for: "claude"))
        #expect(HarnessIcons.url(for: "Claude") == HarnessIcons.url(for: "claude"))
        #expect(HarnessIcons.url(for: "no-such-harness") == nil)
    }

    @Test func provenanceIsRecordedForEveryIcon() throws {
        let url = try #require(CoreResources.url("harness-icons", "tsv"))
        let ids = try String(contentsOf: url, encoding: .utf8).split(separator: "\n").dropFirst()
            .map { String($0.split(separator: "\t").first ?? "") }
        #expect(Set(ids) == Set(HarnessIcons.available))
    }

    /// Share of a 64 x 64 render that is painted.
    func coverage(_ image: NSImage) -> Double {
        let side = 64
        guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: side, pixelsHigh: side, bitsPerSample: 8,
                                         samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                         bytesPerRow: 0, bitsPerPixel: 0) else { return 0 }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        image.draw(in: NSRect(x: 0, y: 0, width: side, height: side))
        NSGraphicsContext.restoreGraphicsState()
        var painted = 0
        for x in 0..<side { for y in 0..<side where (rep.colorAt(x: x, y: y)?.alphaComponent ?? 0) > 0.5 { painted += 1 } }
        return Double(painted) / Double(side * side)
    }
}
