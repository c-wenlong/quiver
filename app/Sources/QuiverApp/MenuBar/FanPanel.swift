import QuiverCore
import SwiftUI

/// The Compute tab's header button in the panel: the fan popover stands
/// where Usage's Refresh does, since the charts tick on their own. The
/// glyph fills while a manual speed is held, so a forgotten override
/// shows here too.
struct PanelFanButton: View {
    @Environment(AppModel.self) private var app
    @State private var showing = false
    @State private var hovering = false

    private var fans: FanControl { app.fans }

    var body: some View {
        Button(fans.isHolding ? "Fans: manual" : "Fans",
               systemImage: fans.isHolding ? "fan.fill" : "fan") { showing = true }
            .labelStyle(.iconOnly)
            .font(.system(size: 14))
            .foregroundStyle(hovering ? Theme.textPrimary : Theme.textSecondary)
            .frame(width: 22, height: 22)
            .contentShape(.rect)
            .plainButton(cornerRadius: Theme.smallRadius)
            .onHover { hovering = $0 }
            .help(fans.isHolding ? "Fans: manual" : "Fans")
            .popover(isPresented: $showing, arrowEdge: .bottom) {
                PanelFanPopover()
                    .padding(14)
                    .frame(width: 280)
            }
            // One read when the tab's header shows, so the glyph and the
            // popover's rows aren't a stale sample.
            .task { await fans.refresh() }
    }
}

/// FanPopover compressed for the panel: one line per fan, then the
/// manual speed alone. The threshold rule and the helper's removal stay
/// on the Compute page's popover.
private struct PanelFanPopover: View {
    @Environment(AppModel.self) private var app
    /// Shown mode, apart from the phase: picking Manual only opens the
    /// slider, nothing changes until Set speed.
    @State private var manual = false

    private var fans: FanControl { app.fans }

    private var mode: Binding<Bool> {
        Binding(get: { manual },
                set: { wantsManual in
                    manual = wantsManual
                    if !wantsManual && fans.phase != .automatic { fans.restoreAutomatic() }
                })
    }

    private var level: Binding<Double> {
        Binding(get: { fans.level }, set: { fans.level = $0 })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Fans").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                Spacer()
                Text(status).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            if !fans.loaded {
                Text("Reading the fans…").font(Theme.caption).foregroundStyle(Theme.textSecondary)
            } else if fans.fans.isEmpty {
                Text("This Mac has no fans Quiver can read.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            } else {
                VStack(spacing: 6) {
                    ForEach(fans.fans) { PanelFanRow(fan: $0) }
                }
                if let line = temperatureLine {
                    Text(line).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                Divider().overlay(Theme.border)
                Tabs(options: [(false, "Automatic"), (true, "Manual")], selection: mode, style: .segmented)
                if manual { manualControls }
                if case .failed(let message) = fans.phase {
                    Text(message).font(Theme.caption).foregroundStyle(Theme.dangerText)
                }
            }
        }
        // The threshold's own hold is an automatic behaviour, so this
        // opens on the Automatic side when it is the one holding.
        .onAppear { manual = fans.isHolding && !fans.heldByThreshold }
        .task {
            while !Task.isCancelled {
                await fans.refresh()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    @ViewBuilder private var manualControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Speed").font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                Spacer()
                if let rpm = fans.targetRPM {
                    Text("\(Self.rpm(rpm)) · \(Int((fans.level * 100).rounded()))%")
                        .font(Theme.bodySmall.monospacedDigit()).foregroundStyle(Theme.textSecondary)
                }
            }
            Slider(value: level, in: 0...1)
                .tint(Theme.accent)
            HStack {
                Button(buttonTitle) { Task { await fans.applyManual() } }
                    .primaryButton()
                    .disabled(fans.phase == .starting || fans.busy)
                Spacer()
            }
            Text("A manual speed holds while Quiver is open. macOS takes the fans back when you pick "
                 + "Automatic or quit Quiver."
                 + (fans.helperInstalled ? ""
                    : " The first time, Quiver installs a small helper, which asks for your password once."))
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var buttonTitle: String {
        if fans.busy { return "Installing…" }
        return switch fans.phase {
        case .manual: "Update speed"
        case .starting: "Starting…"
        case .automatic, .failed: "Set speed"
        }
    }

    private var status: String {
        if fans.heldByThreshold { return "Above \(Int(fans.threshold.rounded())) °C" }
        return switch fans.phase {
        case .automatic, .failed: "Automatic"
        case .starting: "Taking over…"
        case .manual: "Manual"
        }
    }

    private var temperatureLine: String? {
        guard let point = app.compute.history.temperatures.last else { return nil }
        let parts = [ThermalComponent.cpu, .gpu, .battery].compactMap { component in
            point[component].map { "\(component.title) \(TemperatureChart.formatted($0))" }
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private static func rpm(_ value: Double) -> String {
        "\(Int(value.rounded()).formatted()) rpm"
    }
}

/// One fan on a single line: its number, a short bar for how far into
/// its range it runs, and the rpm — the page row's progress bar folded
/// into the row.
private struct PanelFanRow: View {
    var fan: FanReading

    private var share: Double {
        fan.maximum > 0 ? min(max(fan.actual / fan.maximum, 0), 1) : 0
    }

    var body: some View {
        HStack(spacing: 8) {
            Text("Fan \(fan.index + 1)").font(Theme.caption).foregroundStyle(Theme.textPrimary)
            Spacer(minLength: 8)
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule().fill(Theme.usageTrack)
                    Capsule().fill(Theme.accentFill).frame(width: geometry.size.width * share)
                }
            }
            .frame(width: 44, height: 4)
            .accessibilityElement()
            .accessibilityValue("\(Int((share * 100).rounded())) percent")
            Text("\(Int(fan.actual.rounded()).formatted()) rpm")
                .font(Theme.caption.monospacedDigit()).foregroundStyle(Theme.textSecondary)
        }
    }
}
