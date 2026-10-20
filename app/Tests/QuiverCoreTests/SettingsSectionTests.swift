import Foundation
import Testing
@testable import QuiverCore

@Suite struct SettingsSectionTests {
    @Test func subNavIsGroupedAsApproved() {
        #expect(SettingsGroup.allCases.map(\.title) == ["App", "Usage", "Advanced"])
        #expect(SettingsGroup.app.sections == [.general, .preferences, .notifications, .updates])
        #expect(SettingsGroup.usage.sections == [.providers, .services])
        #expect(SettingsGroup.advanced.sections == [.paths, .doctor])
        #expect(SettingsGroup.order == [.general, .preferences, .notifications, .updates, .providers, .services, .paths, .doctor])
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
        #expect(SettingsSection.general.step(1) == .preferences)
        #expect(SettingsSection.preferences.step(1) == .notifications)
        #expect(SettingsSection.notifications.step(1) == .updates)
        #expect(SettingsSection.updates.step(1) == .providers)
        #expect(SettingsSection.providers.step(-1) == .updates)
        #expect(SettingsSection.general.step(-1) == .general)
        #expect(SettingsSection.paths.step(1) == .doctor)
        #expect(SettingsSection.doctor.step(1) == .doctor)
    }

    @Test func aSavedSectionRestoresAndAnUnknownOneFallsBackToGeneral() {
        #expect(SettingsSection(saved: "paths") == .paths)
        #expect(SettingsSection(saved: "appearance") == .general)
        #expect(SettingsSection(saved: nil) == .general)
    }

    @Test func appearanceDefaultsToSystem() {
        #expect(AppearanceMode(saved: nil) == .system)
        #expect(AppearanceMode(saved: "dark") == .dark)
        #expect(AppearanceMode(saved: "sepia") == .system)
        #expect(AppearanceMode.allCases.map(\.title) == ["System", "Light", "Dark"])
    }
}
