import SwiftUI

/// A broom, drawn because this macOS has no "broom" SF Symbol: a diagonal
/// handle and a fan of bristles sweeping down-left, stroked at the same
/// weight as the 16pt glyphs the icon buttons use. Inherits the
/// foreground style, so it tints and hovers like the symbols beside it.
struct BroomIcon: View {
    var body: some View {
        BroomShape()
            .stroke(style: StrokeStyle(lineWidth: 1.3, lineCap: .round, lineJoin: .round))
            .frame(width: 15, height: 15)
    }
}

private struct BroomShape: Shape {
    func path(in rect: CGRect) -> Path {
        func pt(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: rect.minX + x * rect.width, y: rect.minY + y * rect.height)
        }
        var p = Path()
        // Handle, top right down to the neck.
        p.move(to: pt(0.90, 0.10))
        p.addLine(to: pt(0.56, 0.44))
        // The head's crossbar.
        p.move(to: pt(0.60, 0.40))
        p.addLine(to: pt(0.40, 0.60))
        // Outer bristles and the fan's bottom edge.
        p.move(to: pt(0.60, 0.40))
        p.addLine(to: pt(0.10, 0.74))
        p.addLine(to: pt(0.26, 0.90))
        p.addLine(to: pt(0.40, 0.60))
        // Inner bristles.
        p.move(to: pt(0.52, 0.48))
        p.addLine(to: pt(0.15, 0.80))
        p.move(to: pt(0.48, 0.52))
        p.addLine(to: pt(0.20, 0.85))
        return p
    }
}
