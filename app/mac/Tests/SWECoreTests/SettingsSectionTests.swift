import Foundation
import Testing
@testable import SWECore

@Suite struct SettingsSectionTests {
    @Test func subNavIsGroupedAsApproved() {
        #expect(SettingsGroup.allCases.map(\.title) == ["App", "Usage", "Advanced"])
        #expect(SettingsGroup.app.sections == [.general, .sidebar, .notifications])
        #expect(SettingsGroup.usage.sections == [.providers])
        #expect(SettingsGroup.advanced.sections == [.integrations, .files, .experimental])
        #expect(SettingsGroup.order == [.general, .sidebar, .notifications, .providers, .integrations, .files, .experimental])
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
        #expect(SettingsSection.providers.step(1) == .integrations)
        #expect(SettingsSection.integrations.step(1) == .files)
        #expect(SettingsSection.integrations.step(-1) == .providers)
        #expect(SettingsSection.general.step(-1) == .general)
        #expect(SettingsSection.files.step(1) == .experimental)
        #expect(SettingsSection.experimental.step(1) == .experimental)
    }

    @Test func aSavedSectionRestoresAndAnUnknownOneFallsBackToGeneral() {
        #expect(SettingsSection(saved: "files") == .files)
        #expect(SettingsSection(saved: "appearance") == .general)
        // Preferences and Updates folded into General.
        #expect(SettingsSection(saved: "preferences") == .general)
        #expect(SettingsSection(saved: "updates") == .general)
        // GitHub, Remote and Services folded into Integrations.
        #expect(SettingsSection(saved: "github") == .integrations)
        #expect(SettingsSection(saved: "remote") == .integrations)
        #expect(SettingsSection(saved: "services") == .integrations)
        // Doctor folded into General.
        #expect(SettingsSection(saved: "doctor") == .general)
        // Paths was renamed Files.
        #expect(SettingsSection(saved: "paths") == .files)
        #expect(SettingsSection(saved: nil) == .general)
    }

    @Test func appearanceDefaultsToSystem() {
        #expect(AppearanceMode(saved: nil) == .system)
        #expect(AppearanceMode(saved: "dark") == .dark)
        #expect(AppearanceMode(saved: "sepia") == .system)
        #expect(AppearanceMode.allCases.map(\.title) == ["System", "Light", "Dark"])
    }
}
