import QuiverCore
import SwiftUI

/// Settings' provider list, after TokenTracker's: drag to reorder, a switch
/// per installed provider, Enable all / Disable all. Providers that aren't
/// installed are greyed out and their switch is off and locked.
struct ProviderVisibilityCard: View {
    @Environment(AppModel.self) private var app
    @Bindable var model: ProviderVisibilityModel
    @State private var dragging: String?

    var body: some View {
        Card {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Provider visibility").font(.system(size: 15, weight: .medium)).foregroundStyle(Theme.textPrimary)
                    Text(model.hasChecked ? "\(model.enabledCount) / \(model.rows.count) providers enabled"
                         : model.checkError != nil ? "Couldn't check which providers are installed"
                         : "Checking which providers are installed…")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary).monospacedDigit()
                }
                Spacer()
                HStack(spacing: 0) {
                    Button("Enable all") { model.enableAll() }
                    Button("Disable all") { model.disableAll() }
                }
                .buttonStyle(QuietTextButtonStyle())
                .focusRing(cornerRadius: Theme.controlRadius)
                .padding(.horizontal, 4)
                .frame(height: 36)
                .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Theme.border, lineWidth: 1))
            }
            if let error = model.checkError {
                HStack(spacing: 10) {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(Theme.caption).foregroundStyle(Theme.warning)
                        .lineLimit(2)
                    Spacer()
                    Button("Try again") { Task { await model.load() } }
                        .secondaryButton()
                        .disabled(model.isLoading)
                }
            }
            Divider().overlay(Theme.border)
            VStack(spacing: 0) {
                ForEach(model.rows) { row in
                    ProviderRow(row: row, checked: model.hasChecked, failed: model.checkError != nil,
                                isDragging: dragging == row.id) {
                        model.setEnabled(row.id, $0)
                    }
                        .draggable(row.id) {
                            ProviderRow(row: row, checked: model.hasChecked, isDragging: false, toggle: { _ in })
                                .frame(width: 320)
                                .background(Theme.card, in: .rect(cornerRadius: Theme.controlRadius))
                                .onAppear { dragging = row.id }
                        }
                        .dropDestination(for: String.self) { ids, _ in
                            guard let id = ids.first else { return false }
                            withAnimation(.easeOut(duration: 0.15)) { model.move(id, before: row.id) }
                            dragging = nil
                            return true
                        } isTargeted: { targeted in
                            if !targeted, dragging == row.id { dragging = nil }
                        }
                }
                // Dropping below the last row moves a provider to the end.
                Color.clear
                    .frame(height: 16)
                    .contentShape(.rect)
                    .dropDestination(for: String.self) { ids, _ in
                        guard let id = ids.first else { return false }
                        withAnimation(.easeOut(duration: 0.15)) { model.moveToEnd(id) }
                        dragging = nil
                        return true
                    }
            }
        }
        // Again whenever a different swe is chosen.
        .task(id: app.swePath) { await model.load() }
    }
}

private struct ProviderRow: View {
    var row: ProviderVisibilityModel.Row
    var checked: Bool
    var failed = false
    var isDragging: Bool
    var toggle: (Bool) -> Void
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 14) {
            GripDots()
            HStack(spacing: 14) {
                HarnessIcon(harness: row.provider.icon, size: 20)
                Text(row.provider.name).font(.system(size: 15)).foregroundStyle(Theme.textPrimary)
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
            Toggle(row.provider.name, isOn: Binding(get: { row.isEnabled }, set: toggle))
                .toggleStyle(GreenSwitchStyle())
                .labelsHidden()
                .disabled(!row.isInstalled)
                .help(row.isInstalled ? "" : "Install \(row.provider.name) to show it")
        }
        .padding(.horizontal, 8)
        .frame(height: 44)
        .background(hovering ? Theme.rowHover : .clear, in: .rect(cornerRadius: Theme.controlRadius))
        .opacity(isDragging ? 0.4 : 1)
        .contentShape(.rect)
        .onHover { hovering = $0 }
        .accessibilityElement(children: .combine)
        .accessibilityHint(row.isInstalled ? "Drag to reorder" : "Not installed")
    }
}

/// The six-dot drag handle.
private struct GripDots: View {
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

/// Plain 13pt text buttons that brighten on hover, for grouped actions.
struct QuietTextButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        QuietTextButton(configuration: configuration)
    }
}

private struct QuietTextButton: View {
    let configuration: ButtonStyleConfiguration
    @State private var hovering = false

    var body: some View {
        configuration.label
            .font(.system(size: 13, weight: .medium))
            .foregroundStyle(hovering ? Theme.textPrimary : Theme.textSecondary)
            .padding(.horizontal, 12)
            .frame(height: 30)
            .background(hovering ? Theme.hover : .clear, in: .rect(cornerRadius: Theme.controlRadius))
            .opacity(configuration.isPressed ? 0.7 : 1)
            .contentShape(.rect)
            .onHover { hovering = $0 }
    }
}
