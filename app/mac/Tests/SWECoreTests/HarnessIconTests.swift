import AppKit
import Foundation
import Testing
@testable import SWECore

@Suite struct HarnessIconTests {
    /// The harnesses TokenTracker ships a logo for; the rest come from
    /// LobeHub and the products' own sites through import-harness-icons.py.
    let fromTokenTracker = ["claude", "codex", "gemini", "copilot", "cursor", "grok", "kiro", "devin",
                            "opencode", "kilo", "pi"]
    /// Shown in their brand colours, as TokenTracker does.
    let inColour = ["claude", "codex", "gemini", "opencode"]

    func icons() throws -> [(id: String, icon: HarnessIcons.Icon)] {
        try HarnessIcons.available.map { ($0, try #require(HarnessIcons.icon(for: $0), "\($0)")) }
    }

    @Test func everyIconDrawsAMarkWithoutAPlate() throws {
        for (id, icon) in try icons() {
            let image = try #require(NSImage(contentsOf: icon.url), "\(id) does not load")
            let bitmap = try #require(render(image))
            // CoreSVG can load a file and draw nothing (it gives up on compact
            // path data), so the mark has to cover a fair share of its box.
            #expect(coverage(bitmap) > 0.08, "\(id) draws almost nothing")
            // A leftover background plate paints all four corners; a mark
            // may reach one or two (goose's wings do). npm, uv, arcade,
            // browserbase, cartesia and zapier are exempt: each mark is the
            // square itself, the glyph drawn on or cut out of the tile.
            let corners = [(2, 2), (61, 2), (2, 61), (61, 61)]
                .filter { (bitmap.colorAt(x: $0.0, y: $0.1)?.alphaComponent ?? 0) > 0.5 }
            #expect(corners.count < 4 || ["npm", "uv", "arcade", "browserbase", "cartesia", "zapier"].contains(id),
                    "\(id) sits on a background plate")
        }
        #expect(Set(fromTokenTracker).isSubset(of: Set(HarnessIcons.available)))
    }

    @Test func theLiteLLMMarkDrawsAndIsNoHarness() throws {
        let url = try #require(PricingSourceIcons.liteLLM)
        let image = try #require(NSImage(contentsOf: url))
        let bitmap = try #require(render(image))
        #expect(coverage(bitmap) > 0.08)
        #expect(!HarnessIcons.available.contains("litellm"))
    }

    @Test func brandColoursAreKeptWhereTokenTrackerShowsThem() throws {
        for id in inColour {
            #expect(HarnessIcons.icon(for: id)?.isColor == true, "\(id)")
        }
        #expect(HarnessIcons.icon(for: "cursor")?.isColor == false)
    }

    @Test func monoIconsAreSingleColourAndAllAreInert() throws {
        for (id, icon) in try icons() {
            let text = try String(contentsOf: icon.url, encoding: .utf8)
            if !icon.isColor {
                let paints = Set(text.matches(of: /(?:fill|stroke)="([^"]+)"/).map { String($0.1) })
                #expect(paints.isSubset(of: ["#000", "none"]), "\(id) paints \(paints)")
            }
            #expect(text.range(of: #"<script|\son[a-z]+=|href="[^#]|<foreignObject|<image"#, options: .regularExpression) == nil,
                    "\(id)")
        }
    }

    /// A `-color.svg` draws untinted, so a part painted currentColor would
    /// come out black on a dark card; marks like that import as mono.
    @Test func colourIconsPaintNothingInTextColour() throws {
        for (id, icon) in try icons() where icon.isColor {
            let text = try String(contentsOf: icon.url, encoding: .utf8)
            #expect(!text.contains("currentColor"), "\(id)")
        }
    }

    @Test func aliasesAndUnknownHarnesses() {
        #expect(HarnessIcons.icon(for: "claude-code") == HarnessIcons.icon(for: "claude"))
        #expect(HarnessIcons.icon(for: "Claude") == HarnessIcons.icon(for: "claude"))
        #expect(HarnessIcons.icon(for: "no-such-harness") == nil)
    }

    /// What the widgets draw beside a limit's title: the logo HarnessMark
    /// picks, never a stand-in.
    @Test func logoResolvesTheWayHarnessMarkDoes() {
        #expect(HarnessIcons.logo(for: "claude") == HarnessIcons.icon(for: "claude"))
        #expect(HarnessIcons.logo(for: "claude-desktop") == HarnessIcons.icon(for: "claude"))
        #expect(HarnessIcons.logo(for: "codex-acc2") == HarnessIcons.icon(for: "codex"))
        #expect(HarnessIcons.logo(for: "github-copilot") == HarnessIcons.icon(for: "copilot"))
        #expect(HarnessIcons.logo(for: "factory") == HarnessIcons.icon(for: "droid"))
        #expect(HarnessIcons.logo(for: "codebuff") == HarnessIcons.icon(for: "freebuff"))
        #expect(HarnessIcons.logo(for: "claude")?.isColor == true)
        #expect(HarnessIcons.logo(for: "cursor")?.isColor == false)
        #expect(HarnessIcons.logo(for: "lmstudio") == nil)
        #expect(HarnessIcons.logo(for: "zed-agent") == nil)
        for id in HarnessIcons.available {
            #expect(HarnessIcons.logo(for: id) == HarnessIcons.icon(for: id), "\(id)")
        }
    }

    @Test func provenanceIsRecordedForEveryIcon() throws {
        let url = try #require(CoreResources.url("harness-icons", "tsv"))
        let rows = try String(contentsOf: url, encoding: .utf8).split(separator: "\n").dropFirst()
            .map { $0.split(separator: "\t", omittingEmptySubsequences: false).map(String.init) }
        #expect(Set(rows.map { $0[0] }) == Set(HarnessIcons.available))
        for row in rows {
            #expect((row[1] == "color") == (HarnessIcons.icon(for: row[0])?.isColor ?? false), "\(row[0])")
        }
    }

    func render(_ image: NSImage) -> NSBitmapImageRep? {
        let side = 64
        guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: side, pixelsHigh: side, bitsPerSample: 8,
                                         samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                         bytesPerRow: 0, bitsPerPixel: 0) else { return nil }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        image.draw(in: NSRect(x: 0, y: 0, width: side, height: side))
        NSGraphicsContext.restoreGraphicsState()
        return rep
    }

    /// Share of the render that is painted.
    func coverage(_ rep: NSBitmapImageRep) -> Double {
        var painted = 0
        for x in 0..<rep.pixelsWide {
            for y in 0..<rep.pixelsHigh where (rep.colorAt(x: x, y: y)?.alphaComponent ?? 0) > 0.5 { painted += 1 }
        }
        return Double(painted) / Double(rep.pixelsWide * rep.pixelsHigh)
    }
}
