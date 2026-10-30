import CoreGraphics
import Testing
@testable import QuiverCore

/// `SVGPath` turns vendored `d` strings (octicons) into `CGPath`s —
/// run-together numbers, relative commands and the arc-heavy circles
/// the glyphs lean on.
@Suite struct SVGPathTests {

    @Test func moveLineClose() {
        let p = SVGPath.path("M0 0L8 0L8 8Z")
        #expect(p.boundingBox == CGRect(x: 0, y: 0, width: 8, height: 8))
        #expect(p.contains(CGPoint(x: 4, y: 4)))
    }

    @Test func relativeCommands() {
        let p = SVGPath.path("m2 2 l4 0 l0 4 l-4 0 z")
        #expect(p.boundingBox == CGRect(x: 2, y: 2, width: 4, height: 4))
    }

    @Test func implicitLinetoAfterMove() {
        // "M0 0 4 0" — the second pair is an implicit L.
        let p = SVGPath.path("M0 0 4 0 4 4")
        #expect(p.boundingBox == CGRect(x: 0, y: 0, width: 4, height: 4))
    }

    @Test func runTogetherNumbers() {
        // `.97.97` is two numbers; `-` starts a new token.
        let p = SVGPath.path("M0 0l.5-.5h2v2z")
        #expect(p.boundingBox.width > 1.9)
        #expect(p.boundingBox.height > 1.4)
    }

    @Test func horizontalVertical() {
        let p = SVGPath.path("M1 1H9V7H1Z")
        #expect(p.boundingBox == CGRect(x: 1, y: 1, width: 8, height: 6))
    }

    @Test func circleArcsMakeACircle() {
        // Two half-circle arcs: the SVG idiom for a circle.
        let p = SVGPath.path("M2 6a4 4 0 1 1 8 0a4 4 0 1 1-8 0Z")
        let b = p.boundingBox
        #expect(abs(b.midX - 6) < 0.05)
        #expect(abs(b.midY - 6) < 0.05)
        #expect(abs(b.width - 8) < 0.2)
        #expect(abs(b.height - 8) < 0.2)
        #expect(p.contains(CGPoint(x: 6, y: 6)))
    }

    @Test func arcsWithPackedFlags() {
        // `a.75.75 0 1 0 1.5 0` — flags may sit against neighbours.
        let p = SVGPath.path("M0 0a.75.75 0 1 0 1.5 0")
        #expect(p.boundingBox.width > 1.4)
        #expect(!p.boundingBox.isEmpty)
    }

    @Test func octiconPullRequestRenders() {
        // The real glyph: bounds inside the 16×16 box, visibly non-trivial.
        let p = SVGPath.path(OcticonFixtures.pullRequest)
        let b = p.boundingBox
        #expect(b.minX >= -0.01 && b.minY >= -0.01)
        #expect(b.maxX <= 16.01 && b.maxY <= 16.01)
        #expect(b.width > 10 && b.height > 13)
    }

    @Test func curves() {
        let p = SVGPath.path("M0 8C0 3 3 0 8 0S16 3 16 8")
        #expect(p.boundingBox.width > 15)
        #expect(p.boundingBox.maxY <= 8.01)
    }

    @Test func emptyAndGarbage() {
        #expect(SVGPath.path("").isEmpty)
        #expect(SVGPath.path("hello").isEmpty)
        #expect(SVGPath.path("M").isEmpty)
    }

    @Test func aNumberAfterCloseStopsInsteadOfLooping() {
        // Z takes no arguments, so a stray number after it used to make
        // the parser run Z forever.
        let p = SVGPath.path("M0 0L8 0L8 8Z 5")
        #expect(p.boundingBox == CGRect(x: 0, y: 0, width: 8, height: 8))
    }

    @Test func anArcWhoseRadiiOnlyJustReachStaysFinite() {
        // Radii scaled up to reach the end leave a tiny negative under the
        // centre's square root here, which used to put NaN in the path.
        // (boundingBox skips NaN points, so check every point.)
        let p = SVGPath.path("M0 0A1.2 0.71 17 0 1 10.44 -1.11L12 0")
        var points: [CGPoint] = []
        p.applyWithBlock { element in
            let count = switch element.pointee.type {
            case .moveToPoint, .addLineToPoint: 1
            case .addQuadCurveToPoint: 2
            case .addCurveToPoint: 3
            default: 0
            }
            for k in 0..<count { points.append(element.pointee.points[k]) }
        }
        #expect(points.count > 3)
        #expect(points.allSatisfy { $0.x.isFinite && $0.y.isFinite })
    }
}

/// The vendored glyphs, so a path-regression upstream (or an edit to
/// the vendored strings) trips a test, not a visual check.
enum OcticonFixtures {
    static let pullRequest = "M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.25 2.25 0 0 1 1.5 3.25Zm5.677-.177L9.573.677A.25.25 0 0 1 10 .854V2.5h1A2.5 2.5 0 0 1 13.5 5v5.628a2.251 2.251 0 1 1-1.5 0V5a1 1 0 0 0-1-1h-1v1.646a.25.25 0 0 1-.427.177L7.177 3.427a.25.25 0 0 1 0-.354ZM3.75 2.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm0 9.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm8.25.75a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Z"
}
