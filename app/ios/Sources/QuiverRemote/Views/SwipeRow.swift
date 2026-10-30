import SwiftUI
import UIKit
import UIKit.UIGestureRecognizerSubclass

/// Which row is swiped open, so opening one closes the rest and a scroll
/// closes it.
@MainActor @Observable
final class SwipeCoordinator {
    var openKey: String?
}

/// Where a swiped row sits and what letting go does. Pure, so the feel's
/// numbers are tested apart from the gesture.
struct SwipeMath {
    var width: CGFloat
    var leadingCount: Int
    var trailingCount: Int

    static let buttonWidth: CGFloat = 76

    var leadingOpen: CGFloat { CGFloat(leadingCount) * Self.buttonWidth }
    var trailingOpen: CGFloat { CGFloat(trailingCount) * Self.buttonWidth }

    /// Past this the edge action stretches across and runs on release:
    /// over half the row, and well clear of the open buttons.
    func fullSwipe(_ open: CGFloat) -> CGFloat { max(open + 64, width * 0.55) }

    /// The row's offset for a raw drag position: free up to the row's
    /// width on a side with actions, then a stiff rubber band; a side
    /// with none barely moves.
    func offset(_ raw: CGFloat) -> CGFloat {
        let hasSide = raw > 0 ? leadingCount > 0 : trailingCount > 0
        let limit = hasSide ? width : 0
        let magnitude = abs(raw)
        let bounded = magnitude <= limit ? magnitude : limit + Self.rubber(magnitude - limit, span: max(width, 1))
        return raw < 0 ? -bounded : bounded
    }

    static func rubber(_ over: CGFloat, span: CGFloat) -> CGFloat {
        (1 - 1 / (over * 0.55 / span + 1)) * span * 0.25
    }

    func armed(_ offset: CGFloat) -> Bool {
        if offset > 0 { return leadingCount > 0 && offset >= fullSwipe(leadingOpen) }
        if offset < 0 { return trailingCount > 0 && -offset >= fullSwipe(trailingOpen) }
        return false
    }

    enum Release: Equatable { case close, openLeading, openTrailing, runLeading, runTrailing }

    /// Letting go at `offset` moving at `velocity` (points a second). A
    /// flick carries the row a short way, the way UIKit projects scrolls.
    func release(offset: CGFloat, velocity: CGFloat) -> Release {
        if armed(offset) { return offset > 0 ? .runLeading : .runTrailing }
        let projected = offset + velocity * 0.12
        if offset > 0, leadingCount > 0, projected > leadingOpen / 2 { return .openLeading }
        if offset < 0, trailingCount > 0, -projected > trailingOpen / 2 { return .openTrailing }
        return .close
    }
}

/// A list row with swipe actions, as `List`'s `.swipeActions` gives,
/// inside the card lists the app draws on a `ScrollView`. Swipe right for
/// `leading`, left for `trailing`; the first action of each sits at the
/// edge and a full swipe runs it. A tap on an open row closes it.
struct SwipeRow<Content: View>: View {
    let key: String
    let leading: [SessionAction]
    let trailing: [SessionAction]
    let coordinator: SwipeCoordinator
    let perform: (SessionAction) -> Void
    /// Holds the row at this offset with nothing to drag, for renders.
    var frozenOffset: CGFloat?
    @ViewBuilder var content: Content

    /// Where the row rests: closed, or open on one side.
    @State private var settled: CGFloat = 0
    /// The finger's travel while a swipe is under way.
    @State private var travel: CGFloat?
    @State private var width: CGFloat = 0

    private var math: SwipeMath { SwipeMath(width: width, leadingCount: leading.count, trailingCount: trailing.count) }
    private var offset: CGFloat {
        if let frozenOffset { return frozenOffset }
        return math.offset(settled + (travel ?? 0))
    }
    private var armed: Bool { math.armed(offset) }

    var body: some View {
        content
            .background(Theme.card)
            .overlay {
                // An open row closes on a tap instead of opening the chat.
                if offset != 0, travel == nil, frozenOffset == nil {
                    Color.clear.contentShape(.rect).onTapGesture { close() }
                }
            }
            .offset(x: offset)
            // Behind the row and sized by it, so the buttons never change its height.
            .background {
                if offset > 0 { buttons(leading, revealed: offset, edge: .leading) }
                if offset < 0 { buttons(trailing, revealed: -offset, edge: .trailing) }
            }
            .clipped()
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
        .modifier(HorizontalSwipe(onChange: changed, onEnd: ended, onCancel: cancelled, onVertical: {
            // iOS 17 has no scroll phase to watch: a vertical drag on any
            // row closes an open one, as a scroll does on iOS 18.
            if coordinator.openKey != nil { coordinator.openKey = nil }
        }))
        .sensoryFeedback(trigger: armed) { _, now in now ? .impact(weight: .medium) : nil }
        .animation(.spring(response: 0.28, dampingFraction: 0.9), value: armed)
        .onChange(of: coordinator.openKey) { _, open in
            if open != key, settled != 0 { withAnimation(Self.settle) { settled = 0 } }
        }
        .accessibilityActions {
            ForEach(leading + trailing) { action in
                Button(action.title) { perform(action) }
            }
        }
    }

    private static var settle: Animation { .spring(response: 0.32, dampingFraction: 0.86) }

    private func buttons(_ actions: [SessionAction], revealed: CGFloat, edge: HorizontalEdge) -> some View {
        // Laid out from the row's edge: the first action is outermost.
        let ordered = edge == .leading ? actions : actions.reversed()
        let edgeAction = actions.first
        return HStack(spacing: 0) {
            ForEach(ordered) { action in
                let share: CGFloat = armed ? (action == edgeAction ? revealed : 0) : revealed / CGFloat(max(actions.count, 1))
                Button {
                    run(action)
                } label: {
                    VStack(spacing: 4) {
                        Image(systemName: action.symbol).font(Theme.font(size: 17, weight: .semibold))
                        Text(action.title).font(Theme.badge).lineLimit(1)
                    }
                    .foregroundStyle(.white)
                    .frame(width: SwipeMath.buttonWidth)
                    // A stretched edge button keeps its label by the row.
                    .frame(maxWidth: .infinity, maxHeight: .infinity,
                           alignment: armed && action == edgeAction ? (edge == .leading ? .trailing : .leading) : .center)
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .frame(width: share)
                .frame(maxHeight: .infinity)
                .background { Rectangle().fill(Theme.swipe(action)) }
                .clipShape(Rectangle())
            }
        }
        .frame(width: revealed)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: edge == .leading ? .leading : .trailing)
    }

    private func changed(_ x: CGFloat) {
        if travel == nil, coordinator.openKey != key { coordinator.openKey = key }
        travel = x
    }

    private func ended(_ x: CGFloat, _ velocity: CGFloat) {
        let at = math.offset(settled + x)
        let release = math.release(offset: at, velocity: velocity)
        withAnimation(Self.settle) {
            travel = nil
            switch release {
            case .openLeading: settled = math.leadingOpen
            case .openTrailing: settled = -math.trailingOpen
            default: settled = 0
            }
        }
        switch release {
        case .runLeading: if let a = leading.first { finish(a) }
        case .runTrailing: if let a = trailing.first { finish(a) }
        case .close: if coordinator.openKey == key { coordinator.openKey = nil }
        default: break
        }
    }

    private func cancelled() {
        withAnimation(Self.settle) { travel = nil }
    }

    private func run(_ action: SessionAction) {
        withAnimation(Self.settle) { settled = 0 }
        finish(action)
    }

    private func finish(_ action: SessionAction) {
        if coordinator.openKey == key { coordinator.openKey = nil }
        perform(action)
    }

    private func close() {
        withAnimation(Self.settle) { settled = 0 }
        if coordinator.openKey == key { coordinator.openKey = nil }
    }
}

/// A sideways drag that never fights the list's vertical scroll. On iOS
/// 18 and later it is a UIKit pan the scroll view waits on, which gives
/// up the moment the finger moves more up or down than across; iOS 17
/// gets a SwiftUI drag that locks to whichever axis moves first.
private struct HorizontalSwipe: ViewModifier {
    var onChange: (CGFloat) -> Void
    var onEnd: (CGFloat, CGFloat) -> Void
    var onCancel: () -> Void
    var onVertical: () -> Void

    @State private var axis: Axis?

    func body(content: Content) -> some View {
        if #available(iOS 18.0, *) {
            content.gesture(SidewaysPan(onChange: onChange, onEnd: onEnd, onCancel: onCancel))
        } else {
            content.simultaneousGesture(
                DragGesture(minimumDistance: 12)
                    .onChanged { value in
                        let t = value.translation
                        if axis == nil {
                            axis = abs(t.width) > abs(t.height) ? .horizontal : .vertical
                            if axis == .vertical { onVertical() }
                        }
                        if axis == .horizontal { onChange(t.width) }
                    }
                    .onEnded { value in
                        if axis == .horizontal {
                            onEnd(value.translation.width, value.velocity.width)
                        }
                        axis = nil
                    }
            )
        }
    }
}

@available(iOS 18.0, *)
private struct SidewaysPan: UIGestureRecognizerRepresentable {
    var onChange: (CGFloat) -> Void
    var onEnd: (CGFloat, CGFloat) -> Void
    var onCancel: () -> Void

    func makeUIGestureRecognizer(context: Context) -> SidewaysPanRecognizer { SidewaysPanRecognizer() }

    func handleUIGestureRecognizerAction(_ recognizer: SidewaysPanRecognizer, context: Context) {
        let x = recognizer.translation(in: recognizer.view).x
        switch recognizer.state {
        case .began, .changed: onChange(x)
        case .ended: onEnd(x, recognizer.velocity(in: recognizer.view).x)
        case .cancelled, .failed: onCancel()
        default: break
        }
    }
}

/// A pan that only starts sideways. It fails as soon as the finger has
/// moved further up or down than across, and the scroll view's own pan
/// waits for that, so a vertical scroll starts after a few points and a
/// swipe never drags the list with it.
final class SidewaysPanRecognizer: UIPanGestureRecognizer {
    private var start: CGPoint?

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        super.touchesBegan(touches, with: event)
        if start == nil { start = touches.first?.location(in: nil) }
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent) {
        if state == .possible, let start, let p = touches.first?.location(in: nil) {
            let dx = abs(p.x - start.x), dy = abs(p.y - start.y)
            if dy > 5, dy >= dx {
                state = .failed
                return
            }
        }
        super.touchesMoved(touches, with: event)
    }

    override func reset() {
        super.reset()
        start = nil
    }

    override func shouldBeRequiredToFail(by other: UIGestureRecognizer) -> Bool {
        other is UIPanGestureRecognizer && other.view is UIScrollView
    }
}
