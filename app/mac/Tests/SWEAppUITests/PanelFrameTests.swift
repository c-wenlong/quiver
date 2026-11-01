import AppKit
import SwiftUI
import Testing
@testable import SWEApp

/// `PanelFrame` keeps the window shell from measuring the page. These
/// tests host the shell's shape (a fixed sidebar column beside the panel
/// in an `HStack`) and record every size the page is asked for.
@Suite(.serialized)
@MainActor
struct PanelFrameTests {
    /// Records each proposal it is measured with, then fills it.
    private struct Probe: Layout {
        let log: Log

        final class Log {
            var proposals: [ProposedViewSize] = []
        }

        func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
            log.proposals.append(proposal)
            return proposal.replacingUnspecifiedDimensions()
        }

        func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
            for subview in subviews { subview.place(at: bounds.origin, proposal: ProposedViewSize(bounds.size)) }
        }
    }

    private func host(_ view: some View) -> NSHostingView<AnyView> {
        let host = NSHostingView(rootView: AnyView(view))
        host.frame = CGRect(x: 0, y: 0, width: 800, height: 600)
        host.layoutSubtreeIfNeeded()
        return host
    }

    @Test func pageIsOnlyAskedForThePanelSize() {
        let log = Probe.Log()
        let shell = HStack(spacing: 0) {
            Color.clear.frame(width: 200)
            PanelFrame {
                Probe(log: log) { ScrollView { Color.red.frame(height: 3000) } }
            }
        }
        _ = host(shell)
        #expect(!log.proposals.isEmpty)
        for proposal in log.proposals {
            #expect(proposal.width == 600 && proposal.height == 600, "page measured at \(proposal)")
        }
    }

    @Test func pageFillsThePanel() {
        final class Box { var frame = CGRect.zero }
        let box = Box()
        let shell = HStack(spacing: 0) {
            Color.clear.frame(width: 200)
            PanelFrame {
                Color.clear.onGeometryChange(for: CGRect.self) { $0.frame(in: .global) } action: { box.frame = $0 }
            }
        }
        _ = host(shell)
        #expect(box.frame == CGRect(x: 200, y: 0, width: 600, height: 600))
    }
}
