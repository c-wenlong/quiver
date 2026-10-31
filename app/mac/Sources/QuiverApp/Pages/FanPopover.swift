import QuiverCore
import SwiftUI

/// The Compute header's fan button. The glyph fills while a manual speed
/// is held, so a forgotten override shows from across the page.
struct FanButton: View {
    @Environment(AppModel.self) private var app
    @State private var showing = false

    var body: some View {
        Button("Fans", systemImage: app.fans.isHolding ? "fan.fill" : "fan") { showing = true }
            .iconButton()
            .help(app.fans.isHolding ? "Fans: manual" : "Fans")
            .popover(isPresented: $showing, arrowEdge: .bottom) {
                FanPopover()
                    .padding(16)
                    .frame(width: 360)
            }
    }
}

/// What the fans are doing, the Mac's temperatures, and a manual speed.
/// Choosing Automatic hands the fans straight back to macOS; a manual
/// speed asks for the password once, then holds while Quiver is open.
private struct FanPopover: View {
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

    /// Switching on may install the helper (the one password prompt), so
    /// it goes through `enableThreshold`; switching off is immediate.
    private var thresholdOn: Binding<Bool> {
        Binding(get: { fans.thresholdEnabled },
                set: { on in
                    if on {
                        Task { await fans.enableThreshold() }
                    } else {
                        fans.thresholdEnabled = false
                    }
                })
    }

    private var threshold: Binding<Double> {
        Binding(get: { fans.threshold }, set: { fans.threshold = $0 })
    }

    private var release: Binding<Double> {
        Binding(get: { fans.releaseBelow }, set: { fans.releaseBelow = $0 })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
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
                VStack(spacing: 8) {
                    ForEach(fans.fans) { FanRow(fan: $0) }
                }
                if let line = temperatureLine {
                    Text(line).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                Divider().overlay(Theme.border)
                if fans.helperOutdated { FanHelperUpdateNotice() }
                Tabs(options: [(false, "Automatic"), (true, "Manual")], selection: mode, style: .segmented)
                if manual { manualControls } else { thresholdControls }
                if case .failed(let message) = fans.phase {
                    Text(message).font(Theme.caption).foregroundStyle(Theme.dangerText)
                }
                if fans.helperInstalled || fans.helperOutdated {
                    Button("Remove fan helper", role: .destructive) { Task { await fans.removeHelper() } }
                        .plainButton()
                        .font(Theme.caption)
                        .disabled(fans.busy)
                        .hoverTip("Hands the fans back to macOS and uninstalls the helper. Asks for your password.", replacesLabel: false)
                }
            }
        }
        // The threshold's own hold is an automatic behaviour, so it opens
        // on the Automatic side, where its switch lives.
        .onAppear { manual = fans.isHolding && !fans.heldByThreshold }
        .task {
            while !Task.isCancelled {
                await fans.refresh()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    /// The threshold's slider applies at once; Manual's waits for its button.
    private var thresholdLevel: Binding<Double> {
        Binding(get: { fans.level }, set: { fans.setThresholdLevel($0) })
    }

    /// The speed slider, shared by Manual and the threshold.
    private func speedSlider(_ title: String, value: Binding<Double>) -> some View {
        VStack(alignment: .leading, spacing: 8) {
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

    @ViewBuilder private var thresholdControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Toggle("Fan on temperature", isOn: thresholdOn)
                    .toggleStyle(.checkbox)
                    .font(Theme.bodySmall)
                    .disabled(fans.busy)
                Spacer()
                Text("\(Int(fans.threshold.rounded())) °C")
                    .font(Theme.bodySmall.monospacedDigit())
                    .foregroundStyle(fans.thresholdEnabled ? Theme.textPrimary : Theme.textSecondary)
                Stepper("Fan on temperature", value: threshold, in: FanControl.thresholdRange, step: 1)
                    .labelsHidden()
            }
            HStack(spacing: 8) {
                Text("Fan off temperature")
                    .font(Theme.bodySmall)
                    .foregroundStyle(fans.thresholdEnabled ? Theme.textPrimary : Theme.textSecondary)
                Spacer()
                Text("\(Int(fans.releaseBelow.rounded())) °C")
                    .font(Theme.bodySmall.monospacedDigit())
                    .foregroundStyle(fans.thresholdEnabled ? Theme.textPrimary : Theme.textSecondary)
                // Its ceiling is a degree under the on line, so stepping
                // can never put the fans down at or over it.
                Stepper("Fan off temperature", value: release,
                        in: FanControl.releaseRange.lowerBound...fans.threshold - 1, step: 1)
                    .labelsHidden()
            }
            if fans.thresholdEnabled {
                speedSlider("Speed above it", value: thresholdLevel)
            }
            Text(thresholdCaption)
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var thresholdCaption: String {
        let line = "\(Int(fans.threshold.rounded())) °C"
        let back = "\(Int(fans.releaseBelow.rounded())) °C"
        if !fans.thresholdEnabled {
            return "Optional: when the CPU passes \(line), Quiver holds the fans at a speed you pick, "
                + "instead of waiting for macOS."
                + (fans.helperInstalled ? "" : " Switching it on installs the fan helper, which asks for "
                   + "your password once.")
        }
        return "When the CPU passes \(line), Quiver holds the fans at this speed until it drops to \(back). "
            + "Works while Quiver is open."
    }

    @ViewBuilder private var manualControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            speedSlider("Speed", value: level)
            HStack {
                Button(buttonTitle) { Task { await fans.applyManual() } }
                    .primaryButton()
                    .disabled(fans.phase == .starting || fans.busy)
                Spacer()
            }
            Text("A manual speed holds while Quiver is open. macOS takes the fans back when you pick "
                 + "Automatic or quit Quiver, and the fans run flat out if the Mac gets seriously hot."
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

    static func rpm(_ value: Double) -> String {
        "\(Int(value.rounded()).formatted()) rpm"
    }
}

/// An older fan helper is installed: why Quiver won't steer it, and the
/// update (one password prompt). Shared with the menu bar's fan panel.
struct FanHelperUpdateNotice: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(FanControl.outdatedNotice)
                .font(Theme.caption).foregroundStyle(Theme.warningText)
                .fixedSize(horizontal: false, vertical: true)
            Button(app.fans.busy ? "Updating…" : "Update fan helper") { Task { await app.fans.updateHelper() } }
                .secondaryButton()
                .disabled(app.fans.busy)
        }
    }
}

/// One fan: its number, its speed, and how far into its range it runs.
private struct FanRow: View {
    var fan: FanReading

    private var share: Double {
        fan.maximum > 0 ? min(max(fan.actual / fan.maximum, 0), 1) : 0
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Fan \(fan.index + 1)").font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                Spacer()
                Text(FanPopover.rpm(fan.actual))
                    .font(Theme.bodySmall.monospacedDigit()).foregroundStyle(Theme.textSecondary)
            }
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule().fill(Theme.usageTrack)
                    Capsule().fill(Theme.accentFill).frame(width: geometry.size.width * share)
                }
            }
            .frame(height: 4)
        }
    }
}
