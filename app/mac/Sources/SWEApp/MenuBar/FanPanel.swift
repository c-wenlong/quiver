import SWECore
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
            .font(Theme.font(size: 14))
            .foregroundStyle(hovering ? Theme.textPrimary : Theme.textSecondary)
            .frame(width: 22, height: 22)
            .contentShape(.rect)
            .plainButton(cornerRadius: Theme.smallRadius)
            .onHover { hovering = $0 }
            .help(fans.isHolding ? "Fans: manual" : "Fans")
            .popover(isPresented: $showing, arrowEdge: .bottom) {
                PanelFanPopover()
                    .padding(12)
                    .frame(width: 240)
            }
            // One read when the tab's header shows, so the glyph and the
            // popover's rows aren't a stale sample.
            .task { await fans.refresh() }
    }
}

/// FanPopover compressed for the panel: one line per fan, the same
/// Automatic threshold and Manual speed controls, and the temperatures
/// as icons. Only the helper's removal stays on the page's popover.
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

    /// The threshold's slider applies at once; Manual's waits for its button.
    private var thresholdLevel: Binding<Double> {
        Binding(get: { fans.level }, set: { fans.setThresholdLevel($0) })
    }

    /// The off temperature's dropdown only moves the line; the rule is
    /// already on when it shows.
    private var releasePick: Binding<Double> {
        Binding(get: { fans.releaseBelow }, set: { fans.releaseBelow = $0 })
    }

    /// What the dropdown last picked, so an enable still installing
    /// when Off is picked cannot switch the rule back on.
    @State private var wantedPick: Double = 0

    /// The dropdown doubles as the rule's switch: picking a degree
    /// while off enables it, which may install the helper (the one
    /// password prompt); picking one while on just moves the line.
    /// Off is immediate.
    private var thresholdPick: Binding<Double> {
        Binding(get: { fans.thresholdEnabled ? fans.threshold : 0 },
                set: { pick in
                    wantedPick = pick
                    if pick == 0 {
                        fans.thresholdEnabled = false
                    } else {
                        fans.threshold = pick
                        if !fans.thresholdEnabled {
                            Task {
                                await fans.enableThreshold()
                                // An Off picked while the install waited
                                // must win over this call's enable.
                                if wantedPick == 0 { fans.thresholdEnabled = false }
                            }
                        }
                    }
                })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Fans").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                Spacer()
                Text(status).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            if !fans.loaded {
                Text("Reading the fans…").font(Theme.caption).foregroundStyle(Theme.textSecondary)
            } else if fans.fans.isEmpty {
                Text("This Mac has no fans SWE can read.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            } else {
                VStack(spacing: 6) {
                    ForEach(fans.fans) { PanelFanRow(fan: $0) }
                }
                temperatureChips
                Divider().overlay(Theme.border)
                if fans.helperOutdated { FanHelperUpdateNotice() }
                Tabs(options: [(false, "Automatic"), (true, "Manual")], selection: mode,
                     style: .segmented, fillWidth: true)
                if manual { manualControls } else { thresholdControls }
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

    /// The speed slider, shared by Manual and the threshold.
    private func speedSlider(_ title: String, value: Binding<Double>) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(title).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                Spacer()
                if let rpm = fans.targetRPM {
                    Text("\(Self.rpm(rpm)) · \(Int((fans.level * 100).rounded()))%")
                        .font(Theme.bodySmall.monospacedDigit()).foregroundStyle(Theme.textSecondary)
                }
            }
            Slider(value: value, in: 0...1)
                .tint(Theme.interactive)
        }
    }

    /// The page's checkbox and stepper folded into one dropdown: Off, or
    /// the CPU temperature that holds the fans at the picked speed.
    @ViewBuilder private var thresholdControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Fans up above").font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                Spacer()
                SelectMenu(options: [(0.0, "Off")]
                               + Self.thresholdSteps.map { (Double($0), "\($0) °C") },
                           selection: thresholdPick, width: nil)
                    .disabled(fans.busy)
                Image(systemName: "info.circle")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .hoverTip(thresholdCaption)
            }
            if fans.thresholdEnabled {
                HStack {
                    Text("Fan off below").font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                    Spacer()
                    SelectMenu(options: Self.releaseSteps(below: fans.threshold)
                                   .map { (Double($0), "\($0) °C") },
                               selection: releasePick, width: nil)
                }
                speedSlider("Speed above it", value: thresholdLevel)
            }
        }
    }

    private var thresholdCaption: String {
        if !fans.thresholdEnabled {
            return "Optional: past the picked temperature, SWE holds the fans at a speed you pick."
                + (fans.helperInstalled ? ""
                   : " Turning it on installs the fan helper, which asks for your password once.")
        }
        return "Holds this speed until the CPU drops to \(Int(fans.releaseBelow.rounded())) °C. "
            + "Works while SWE is open."
    }

    /// One degree steps, like the page's stepper, so a temperature set
    /// there always has a row here.
    private static let thresholdSteps =
        Array(Int(FanControl.thresholdRange.lowerBound)...Int(FanControl.thresholdRange.upperBound))

    /// The off temperature's steps: every degree under the on line, so
    /// no pick can land the fans' drop-out at or over it.
    private static func releaseSteps(below threshold: Double) -> [Int] {
        Array(Int(FanControl.releaseRange.lowerBound)...Int(threshold) - 1)
    }

    @ViewBuilder private var manualControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            speedSlider("Speed", value: level)
            HStack {
                Button(buttonTitle) { Task { await fans.applyManual() } }
                    .primaryButton()
                    .disabled(fans.phase == .starting || fans.busy)
                Spacer()
                Image(systemName: "info.circle")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .hoverTip(manualCaption)
            }
        }
    }

    private var manualCaption: String {
        "A manual speed holds while SWE is open. macOS takes the fans back when you pick "
            + "Automatic or quit SWE."
            + (fans.helperInstalled ? ""
               : " The first time, SWE installs a small helper, which asks for your password once.")
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

    /// CPU, GPU and battery as icon chips: the component's chart colour
    /// on an icon, its reading beside it. The names only fit as words on
    /// the page; here the icon carries them, with the full name on hover.
    @ViewBuilder private var temperatureChips: some View {
        let temps = temperatures
        if !temps.isEmpty {
            HStack(spacing: 10) {
                ForEach(temps, id: \.0) { component, value in
                    HStack(spacing: 3) {
                        Image(systemName: component.panelIcon)
                            .foregroundStyle(component.colour)
                            .accessibilityHidden(true)
                        Text(TemperatureChart.formatted(value))
                            .monospacedDigit()
                            .foregroundStyle(Theme.textSecondary)
                    }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("\(component.title) \(TemperatureChart.formatted(value))")
                    .help(component.title)
                }
                Spacer()
            }
            .font(Theme.caption)
        }
    }

    private var temperatures: [(ThermalComponent, Double)] {
        guard let point = app.compute.history.temperatures.last else { return [] }
        return [ThermalComponent.cpu, .gpu, .battery].compactMap { component in
            point[component].map { (component, $0) }
        }
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
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule().fill(Theme.usageTrack)
                    Capsule().fill(Theme.accentFill).frame(width: geometry.size.width * share)
                }
            }
            .frame(height: 4)
            .accessibilityElement()
            .accessibilityValue("\(Int((share * 100).rounded())) percent")
            Text("\(Int(fan.actual.rounded()).formatted()) rpm")
                .font(Theme.caption.monospacedDigit()).foregroundStyle(Theme.textSecondary)
        }
    }
}

private extension ThermalComponent {
    /// The icon that stands in for the name on the panel's temperature
    /// chips. Covers every case so a new component can't slip through.
    var panelIcon: String {
        switch self {
        case .cpu: "cpu"
        case .gpu: "rectangle.3.offgrid.fill"
        case .memory: "memorychip"
        case .ssd: "internaldrive"
        case .battery: "battery.100"
        case .airflow: "wind"
        case .display: "display"
        }
    }
}
