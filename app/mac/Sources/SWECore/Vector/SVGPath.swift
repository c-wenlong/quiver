import CoreGraphics

/// An SVG path-data string (`d="M…"`) as a `CGPath`. Enough of the
/// spec for vendored icon paths (octicons, simple marks): every
/// command including elliptical arcs, relative forms, run-together
/// numbers (`l.97-.97`), and flag args packed against their
/// neighbours (`a.75.75 0 1 0 1.5 0`).
public enum SVGPath {
    public static func path(_ d: String) -> CGPath {
        var p = Parser(d)
        p.run()
        return p.path
    }
}

private struct Parser {
    let s: String
    var i: String.Index
    var path = CGMutablePath()
    var cur = CGPoint.zero          // current point
    var anchor = CGPoint.zero       // subpath start, for Z
    var cubicCp: CGPoint?           // last cubic control point, for S
    var quadCp: CGPoint?            // last quadratic control, for T
    var lastCmd: Character = " "

    init(_ d: String) {
        s = d
        i = d.startIndex
    }

    mutating func run() {
        while true {
            sep()
            guard i < s.endIndex else { return }
            let start = i
            var cmd = lastCmd
            if s[i].isASCII, s[i].isLetter {
                cmd = s[i]; i = s.index(after: i)
            } else if lastCmd == "M" { cmd = "L" }   // implicit lineto
            else if lastCmd == "m" { cmd = "l" }
            // A command that read nothing would run again forever: a number
            // after Z repeats Z, which takes no arguments, so stop there.
            guard exec(cmd), i > start else { return }
            lastCmd = cmd
        }
    }

    mutating func exec(_ cmd: Character) -> Bool {
        let rel = cmd.isLowercase
        func pt(_ x: Double, _ y: Double) -> CGPoint {
            rel ? CGPoint(x: cur.x + x, y: cur.y + y) : CGPoint(x: x, y: y)
        }
        // S/T read the previous command's control point before it clears.
        let prevCubic = cubicCp, prevQuad = quadCp
        cubicCp = nil; quadCp = nil
        switch cmd.uppercased().first! {
        case "M":
            guard let x = num(), let y = num() else { return false }
            cur = pt(x, y); anchor = cur
            path.move(to: cur)
        case "L":
            guard let x = num(), let y = num() else { return false }
            cur = pt(x, y)
            path.addLine(to: cur)
        case "H":
            guard let x = num() else { return false }
            cur = CGPoint(x: rel ? cur.x + x : x, y: cur.y)
            path.addLine(to: cur)
        case "V":
            guard let y = num() else { return false }
            cur = CGPoint(x: cur.x, y: rel ? cur.y + y : y)
            path.addLine(to: cur)
        case "C":
            guard let x1 = num(), let y1 = num(),
                  let x2 = num(), let y2 = num(),
                  let x = num(), let y = num() else { return false }
            cubicCp = pt(x2, y2)
            path.addCurve(to: pt(x, y), control1: pt(x1, y1),
                          control2: cubicCp!)
            cur = pt(x, y)
        case "S":
            guard let x2 = num(), let y2 = num(),
                  let x = num(), let y = num() else { return false }
            let c1 = Self.reflect(prevCubic,
                                  valid: "CcSs".contains(lastCmd),
                                  over: cur)
            cubicCp = pt(x2, y2)
            path.addCurve(to: pt(x, y), control1: c1, control2: cubicCp!)
            cur = pt(x, y)
        case "Q":
            guard let x1 = num(), let y1 = num(),
                  let x = num(), let y = num() else { return false }
            quadCp = pt(x1, y1)
            path.addQuadCurve(to: pt(x, y), control: quadCp!)
            cur = pt(x, y)
        case "T":
            guard let x = num(), let y = num() else { return false }
            quadCp = Self.reflect(prevQuad,
                                  valid: "QqTt".contains(lastCmd),
                                  over: cur)
            path.addQuadCurve(to: pt(x, y), control: quadCp!)
            cur = pt(x, y)
        case "A":
            guard let rx = num(), let ry = num(), let rot = num(),
                  let large = flag(), let sweep = flag(),
                  let x = num(), let y = num() else { return false }
            arc(rx: abs(rx), ry: abs(ry), rotation: rot,
                largeArc: large, sweep: sweep, to: pt(x, y))
        case "Z":
            path.closeSubpath()
            cur = anchor
        default:
            return false
        }
        return true
    }

    /// S/T's first control point: the mirror of the previous command's
    /// control point across the current point — or the current point
    /// itself when the previous command wasn't of that kind.
    private static func reflect(_ cp: CGPoint?, valid: Bool,
                                over cur: CGPoint) -> CGPoint {
        guard let cp, valid else { return cur }
        return CGPoint(x: 2 * cur.x - cp.x, y: 2 * cur.y - cp.y)
    }

    // MARK: - elliptical arc → cubics (SVG spec F.6.5)

    private mutating func arc(rx: Double, ry: Double, rotation: Double,
                              largeArc: Bool, sweep: Bool,
                              to end: CGPoint) {
        var rx = rx, ry = ry
        guard rx > 0, ry > 0, end != cur else {
            cur = end
            return
        }
        let phi = rotation * .pi / 180
        let cosP = cos(phi), sinP = sin(phi)
        let dx = (cur.x - end.x) / 2, dy = (cur.y - end.y) / 2
        let x1p = cosP * dx + sinP * dy
        let y1p = -sinP * dx + cosP * dy
        var lambda = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry)
        if lambda > 1 {
            lambda = lambda.squareRoot()
            rx *= lambda; ry *= lambda
        }
        let num = rx * rx * ry * ry - rx * rx * y1p * y1p
                  - ry * ry * x1p * x1p
        let den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
        // Radii scaled up to just reach the end make `num` 0, which
        // rounding can leave a hair below: its root would be NaN, and
        // every point after it too.
        let coef = (largeArc == sweep ? -1.0 : 1.0)
            * max(0, num / den).squareRoot()
        let cxp = coef * rx * y1p / ry
        let cyp = -coef * ry * x1p / rx
        let cx = cosP * cxp - sinP * cyp + (cur.x + end.x) / 2
        let cy = sinP * cxp + cosP * cyp + (cur.y + end.y) / 2
        func angle(_ ux: Double, _ uy: Double,
                   _ vx: Double, _ vy: Double) -> Double {
            let dot = ux * vx + uy * vy
            let len = (ux * ux + uy * uy).squareRoot()
                      * (vx * vx + vy * vy).squareRoot()
            var a = acos(max(-1, min(1, dot / len)))
            if ux * vy - uy * vx < 0 { a = -a }
            return a
        }
        let th1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
        var dth = angle((x1p - cxp) / rx, (y1p - cyp) / ry,
                        (-x1p - cxp) / rx, (-y1p - cyp) / ry)
        if !sweep, dth > 0 { dth -= 2 * .pi }
        if sweep, dth < 0 { dth += 2 * .pi }
        let segs = max(1, Int(ceil(abs(dth) / (.pi / 2))))
        let delta = dth / Double(segs)
        // α = 4/3 tan(Δ/4): the cubic approximation of an arc segment.
        let alpha = 4.0 / 3.0 * tan(delta / 4)
        var th = th1
        for _ in 0..<segs {
            let th2 = th + delta
            let c1 = cos(th), s1 = sin(th), c2 = cos(th2), s2 = sin(th2)
            func point(_ ct: Double, _ st: Double) -> CGPoint {
                CGPoint(x: cx + rx * ct * cosP - ry * st * sinP,
                        y: cy + rx * ct * sinP + ry * st * cosP)
            }
            let p1 = point(c1, s1), p2 = point(c2, s2)
            // Tangents at p1/p2, un-rotated then re-rotated like the points.
            func tangent(_ ct: Double, _ st: Double) -> CGVector {
                CGVector(dx: -rx * st * cosP - ry * ct * sinP,
                         dy: -rx * st * sinP + ry * ct * cosP)
            }
            let t1 = tangent(c1, s1), t2 = tangent(c2, s2)
            path.addCurve(
                to: p2,
                control1: CGPoint(x: p1.x + alpha * t1.dx,
                                  y: p1.y + alpha * t1.dy),
                control2: CGPoint(x: p2.x - alpha * t2.dx,
                                  y: p2.y - alpha * t2.dy))
            th = th2
        }
        cur = end
    }

    // MARK: - tokens

    mutating func sep() {
        while i < s.endIndex, s[i] == " " || s[i] == "," || s[i] == "\n"
                || s[i] == "\t" || s[i] == "\r" {
            i = s.index(after: i)
        }
    }

    /// An SVG number: `[+-]? (digits[.digits?] | .digits) ([eE][+-]?digits)?`.
    /// A second `.` or sign starts a new token, so `.97.97` is two.
    mutating func num() -> Double? {
        sep()
        guard i < s.endIndex else { return nil }
        var j = i
        if s[j] == "+" || s[j] == "-" { j = s.index(after: j) }
        var digits = false
        while j < s.endIndex, s[j].isNumber {
            digits = true; j = s.index(after: j)
        }
        if j < s.endIndex, s[j] == "." {
            j = s.index(after: j)
            while j < s.endIndex, s[j].isNumber {
                digits = true; j = s.index(after: j)
            }
        }
        guard digits else { return nil }
        if j < s.endIndex, s[j] == "e" || s[j] == "E" {
            var k = s.index(after: j)
            if k < s.endIndex, s[k] == "+" || s[k] == "-" {
                k = s.index(after: k)
            }
            if k < s.endIndex, s[k].isNumber {
                while k < s.endIndex, s[k].isNumber {
                    k = s.index(after: k)
                }
                j = k
            }
        }
        let value = Double(s[i..<j])
        i = j
        return value
    }

    /// A single `0`/`1` character — SVG arc flags pack tight
    /// (`0 1 0` may also come as `010`).
    mutating func flag() -> Bool? {
        sep()
        guard i < s.endIndex, s[i] == "0" || s[i] == "1" else { return nil }
        defer { i = s.index(after: i) }
        return s[i] == "1"
    }
}
