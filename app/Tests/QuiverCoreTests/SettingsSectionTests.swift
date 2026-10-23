import Foundation
import Testing
@testable import QuiverCore

@Suite struct SettingsSectionTests {
    @Test func subNavIsGroupedAsApproved() {
        #expect(SettingsGroup.allCases.map(\.title) == ["App", "Usage", "Advanced"])
        #expect(SettingsGroup.app.sections == [.general, .sidebar, .notifications])
        #expect(SettingsGroup.usage.sections == [.providers, .services])
        #expect(SettingsGroup.advanced.sections == [.integrations, .paths, .doctor, .experimental])
        #expect(SettingsGroup.order == [.general, .sidebar, .notifications, .providers, .services, .integrations, .paths, .doctor, .experimental])
    }

    @Test func everySectionHasATitleSymbolAndADescription() {
        for section in SettingsSection.allCases {
            #expect(!section.title.isEmpty)
            #expect(!section.symbol.isEmpty)
            #expect(section.detail.hasSuffix("."))
            #expect(section.detail.first?.isUppercase == true)
        }
    }

    @Test func arrowKeysWalkTheSubNavAndStopAtTheEnds() {
        #expect(SettingsSection.general.step(1) == .sidebar)
        #expect(SettingsSection.sidebar.step(1) == .notifications)
        #expect(SettingsSection.notifications.step(1) == .providers)
        #expect(SettingsSection.services.step(1) == .integrations)
        #expect(SettingsSection.integrations.step(1) == .paths)
        #expect(SettingsSection.integrations.step(-1) == .services)
        #expect(SettingsSection.general.step(-1) == .general)
        #expect(SettingsSection.paths.step(1) == .doctor)
        #expect(SettingsSection.doctor.step(1) == .experimental)
        #expect(SettingsSection.experimental.step(1) == .experimental)
    }

    @Test func aSavedSectionRestoresAndAnUnknownOneFallsBackToGeneral() {
        #expect(SettingsSection(saved: "paths") == .paths)
        #expect(SettingsSection(saved: "appearance") == .general)
        // Preferences and Updates folded into General.
        #expect(SettingsSection(saved: "preferences") == .general)
        #expect(SettingsSection(saved: "updates") == .general)
        // GitHub and Remote folded into Integrations.
        #expect(SettingsSection(saved: "github") == .integrations)
        #expect(SettingsSection(saved: "remote") == .integrations)
        #expect(SettingsSection(saved: nil) == .general)
    }

    @Test func appearanceDefaultsToSystem() {
        #expect(AppearanceMode(saved: nil) == .system)
        #expect(AppearanceMode(saved: "dark") == .dark)
        #expect(AppearanceMode(saved: "sepia") == .system)
        #expect(AppearanceMode.allCases.map(\.title) == ["System", "Light", "Dark"])
    }
}
