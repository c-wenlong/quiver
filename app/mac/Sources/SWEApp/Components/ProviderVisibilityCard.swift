import SWECore
import SwiftUI

/// Settings' provider list, after TokenTracker's: drag to reorder, a switch
/// per installed provider, Enable all / Disable all. Providers that aren't
/// installed are greyed out and their switch is off and locked. A click on
/// a row opens its install or uninstall steps.
struct ProviderVisibilityCard: View {
    @Environment(AppModel.self) private var app
    @Bindable var model: ProviderVisibilityModel
    @State private var opened: ProviderVisibilityModel.Row?

    var body: some View {
        SettingsCard {
            SettingRow(title: "Provider visibility",
                       detail: model.hasChecked ? "\(model.enabledCount) / \(model.rows.count) providers enabled"
                           : "Checking which providers are installed…",
                       warning: model.checkError) {
                HStack(spacing: 8) {
                    if model.checkError != nil {
                        Button("Try again") { Task { await model.load() } }
                            .secondaryButton()
                            .disabled(model.isLoading)
                    }
                    HStack(spacing: 2) {
                        Button("Enable all") { model.enableAll() }
                            .focusRing(cornerRadius: Theme.controlRadius)
                        Button("Disable all") { model.disableAll() }
                            .focusRing(cornerRadius: Theme.controlRadius)
                    }
                    .buttonStyle(SegmentActionStyle())
                    .padding(2)
                    .background(Theme.segmentTrack, in: .rect(cornerRadius: Theme.blockRadius))
                    .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
                    .fixedSize()
                }
            }
            SettingsDivider()
            ReorderableRows(model.rows) { id, before in
                if let before {
                    model.move(id, before: before)
                } else {
                    model.moveToEnd(id)
                }
            } row: { row in
                ProviderRow(row: row, checked: model.hasChecked, failed: model.checkError != nil,
                            open: { opened = row }) {
                    model.setEnabled(row.id, $0)
                }
            }
            .padding(.vertical, 8)
        }
        // Closing the sheet rechecks, so an install or uninstall run from
        // it shows up in the switches.
        .quiverSheet(item: $opened, onDismiss: { Task { await model.load() } }) { row in
            ProviderSetupSheet(provider: row.provider, isInstalled: row.isInstalled)
        }
        // Once per swe: this card is rebuilt on every visit and at the
        // 720pt layout flip, and each check starts a swe process.
        .task(id: app.swePath) { await model.loadIfNeeded(for: app.swePath) }
    }
}

private struct ProviderRow: View {
    var row: ProviderVisibilityModel.Row
    var checked: Bool
    var failed = false
    var open: () -> Void
    var toggle: (Bool) -> Void
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 14) {
            GripDots()
            HStack(spacing: 14) {
                HarnessIcon(harness: row.provider.icon, size: 20)
                Text(row.provider.name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                if !checked && failed {
                    Text("Couldn't check").font(Theme.caption).foregroundStyle(Theme.textMuted)
                } else if !checked {
                    Text("Checking…").font(Theme.caption).foregroundStyle(Theme.textMuted)
                } else if !row.isInstalled {
                    Text("Not installed").font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            .opacity(row.isInstalled || !checked ? 1 : 0.4)
            Spacer()
            let onOff = Toggle(row.provider.name, isOn: Binding(get: { row.isEnabled }, set: { toggle($0) }))
                .toggleStyle(AccentSwitchStyle())
                .labelsHidden()
                .disabled(!row.isInstalled)
            // A plain tooltip, and only on a missing provider's locked
            // switch: a hover tip's tap would take the row's click.
            if row.isInstalled {
                onOff
            } else {
                onOff.help("Install \(row.provider.name) to show it")
            }
        }
        .padding(.horizontal, 8)
        .frame(height: 44)
        .background(hovering ? Theme.rowHover : .clear, in: .rect(cornerRadius: Theme.controlRadius))
        .contentShape(.rect)
        // The switch keeps its own click; anywhere else opens the steps.
        .onTapGesture(perform: open)
        .onHover { hovering = $0 }
        .help(row.isInstalled ? "Click for how to uninstall \(row.provider.name)"
                              : "Click for how to install \(row.provider.name)")
        .accessibilityElement(children: .combine)
        .accessibilityHint(row.isInstalled ? "Drag to reorder" : "Not installed")
        .accessibilityAction(named: row.isInstalled ? "Uninstall steps" : "Install steps", open)
    }
}

/// The six-dot drag handle.
struct GripDots: View {
    var body: some View {
        Grid(horizontalSpacing: 3, verticalSpacing: 3) {
            ForEach(0..<3, id: \.self) { _ in
                GridRow {
                    Circle().frame(width: 3, height: 3)
                    Circle().frame(width: 3, height: 3)
                }
            }
        }
        .foregroundStyle(Theme.textMuted)
        .frame(width: 12)
    }
}

/// Actions in a segmented track: 12/500 labels at 28pt that brighten and
/// take the selected fill under the pointer. Nothing stays selected.
struct SegmentActionStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        SegmentAction(configuration: configuration)
    }
}

private struct SegmentAction: View {
    let configuration: ButtonStyleConfiguration
    @State private var hovering = false

    var body: some View {
        configuration.label
            .font(Theme.control)
            .foregroundStyle(hovering ? Theme.textPrimary : Theme.textTab)
            .padding(.horizontal, 12)
            .frame(height: Theme.controlHeight)
            .background(hovering ? Theme.segmentSelected : .clear, in: .rect(cornerRadius: Theme.controlRadius))
            .opacity(configuration.isPressed ? 0.7 : 1)
            .contentShape(.rect)
            .animation(.easeOut(duration: Theme.fadeDuration), value: hovering)
            .onHover { hovering = $0 }
    }
}
