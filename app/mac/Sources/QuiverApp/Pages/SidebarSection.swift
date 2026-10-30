import QuiverCore
import SwiftUI

/// Settings › Sidebar, after the provider list: every page in a draggable
/// order inside its group, a switch per optional page, and Enable all /
/// Disable all. Core pages keep their switch on and locked.
struct SidebarSection: View {
    @Environment(AppModel.self) private var app

    private var model: SidebarVisibilityModel { app.sidebar }

    var body: some View {
        SettingsCard {
            SettingRow(title: "Sidebar pages",
                       detail: "\(model.enabledCount) / \(model.totalCount) pages shown") {
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
            SettingsDivider()
            VStack(alignment: .leading, spacing: 14) {
                ForEach(SidebarGroup.allCases) { group in
                    VStack(alignment: .leading, spacing: 0) {
                        Text(group.title)
                            .font(Theme.font(size: 11, weight: .medium))
                            .foregroundStyle(Theme.textMuted)
                            .padding(.horizontal, 8)
                            .padding(.bottom, 4)
                        ReorderableRows(model.rows(in: group)) { id, before in
                            model.move(id, before: before)
                        } row: { row in
                            SidebarRow(row: row) {
                                model.setEnabled(row.section, $0)
                            }
                        }
                    }
                }
            }
            .padding(.vertical, 8)
        }
    }
}

private struct SidebarRow: View {
    var row: SidebarVisibilityModel.Row
    var toggle: (Bool) -> Void
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 14) {
            GripDots()
            HStack(spacing: 14) {
                Image(systemName: row.section.symbol)
                    .font(Theme.font(size: 15))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 18)
                Text(row.section.title).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                if row.isCore {
                    Text("Always on").font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            Spacer()
            let onOff = Toggle(row.section.title, isOn: Binding(get: { row.isEnabled }, set: { toggle($0) }))
                .toggleStyle(AccentSwitchStyle())
                .labelsHidden()
                .disabled(row.isCore)
            // A plain tooltip, and only on a core page's locked switch.
            if row.isCore {
                onOff.help("Core pages always show")
            } else {
                onOff
            }
        }
        .padding(.horizontal, 8)
        .frame(height: 44)
        .background(hovering ? Theme.rowHover : .clear, in: .rect(cornerRadius: Theme.controlRadius))
        .onHover { hovering = $0 }
        .accessibilityElement(children: .combine)
        .accessibilityHint(row.isCore ? "Always shown" : "Drag to reorder")
    }
}
