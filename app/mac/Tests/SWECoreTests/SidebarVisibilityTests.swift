import Testing
@testable import SWECore

@MainActor
@Suite struct SidebarVisibilityTests {
    @Test func everyPageStartsShownInTheDefaultOrder() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        #expect(model.enabledCount == AppSection.allCases.count)
        #expect(model.totalCount == AppSection.allCases.count)
        #expect(model.sections(in: .usage) == [.overview, .sessions, .calendar, .pricing])
        #expect(model.visibleGroups == SidebarGroup.allCases)
        #expect(model.rows(in: .github).map(\.section) == [.repositories, .tasks, .inbox])
    }

    @Test func overviewAccountsAndSettingsAreCore() {
        #expect(AppSection.allCases.filter(\.isCore) == [.overview, .accounts, .settings])
    }

    @Test func corePagesCannotBeSwitchedOff() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        for section in AppSection.allCases.filter(\.isCore) {
            model.setEnabled(section, false)
            #expect(model.isVisible(section), "\(section)")
        }
        #expect(model.enabledCount == model.totalCount)
    }

    @Test func hiddenPagesDropOutOfTheSidebarButStayInTheCard() {
        let store = MemoryStore()
        let model = SidebarVisibilityModel(store: store)
        model.setEnabled(.sessions, false)
        #expect(model.sections(in: .usage) == [.overview, .calendar, .pricing])
        #expect(model.rows(in: .usage).map(\.section) == [.overview, .sessions, .calendar, .pricing])
        #expect(model.rows(in: .usage).first { $0.section == .sessions }?.isEnabled == false)
        #expect(model.enabledCount == AppSection.allCases.count - 1)

        let reopened = SidebarVisibilityModel(store: store)
        #expect(reopened.isHidden(.sessions))
        #expect(reopened.isVisible(.calendar))
    }

    @Test func aGroupWithEveryPageOffLeavesNoHeading() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        model.setEnabled(.repositories, false)
        model.setEnabled(.tasks, false)
        model.setEnabled(.inbox, false)
        #expect(model.sections(in: .github).isEmpty)
        #expect(!model.visibleGroups.contains(.github))
        model.setEnabled(.inbox, true)
        #expect(model.visibleGroups.contains(.github))
    }

    @Test func disableAllLeavesOnlyTheCorePages() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        model.disableAll()
        #expect(model.enabledCount == 3)
        #expect(model.sections(in: .usage) == [.overview])
        #expect(model.sections(in: .manage) == [.accounts])
        #expect(model.sections(in: .system) == [.settings])
        #expect(model.visibleGroups == [.usage, .manage, .system])
        model.enableAll()
        #expect(model.enabledCount == model.totalCount)
    }

    @Test func onHideFiresOnceForEachPageSwitchedOff() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        var hid: [AppSection] = []
        model.onHide = { hid.append($0) }
        model.setEnabled(.inbox, false)
        model.setEnabled(.inbox, false)
        model.setEnabled(.inbox, true)
        model.setEnabled(.overview, false)
        #expect(hid == [.inbox])

        hid = []
        model.setEnabled(.sessions, false)
        hid = []
        model.disableAll()
        #expect(Set(hid) == Set(AppSection.allCases.filter { !$0.isCore && $0 != .sessions }))
    }

    @Test func dragsStayInsideTheirGroup() {
        let store = MemoryStore()
        let model = SidebarVisibilityModel(store: store)
        model.move("sessions", before: "overview")
        #expect(model.sections(in: .usage) == [.sessions, .overview, .calendar, .pricing])
        #expect(model.rows(in: .usage).map(\.id) == ["sessions", "overview", "calendar", "pricing"])

        let reopened = SidebarVisibilityModel(store: store)
        #expect(reopened.sections(in: .usage) == [.sessions, .overview, .calendar, .pricing])
    }

    @Test func aNilTargetLandsAtTheEndOfTheGroupNotTheList() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        model.move("overview", before: nil)
        #expect(model.sections(in: .usage) == [.sessions, .calendar, .pricing, .overview])
        // The rest of the sidebar is untouched.
        #expect(model.sections(in: .github) == [.repositories, .tasks, .inbox])
        #expect(model.sections(in: .manage) == [.accounts, .harnesses, .compute])
    }

    @Test func hiddenPagesKeepTheirPlaceInTheOrder() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        model.setEnabled(.sessions, false)
        model.move("sessions", before: "overview")
        model.setEnabled(.sessions, true)
        #expect(model.sections(in: .usage) == [.sessions, .overview, .calendar, .pricing])
    }

    @Test func aPageTheSavePredatesSlotsBackIntoItsGroup() {
        let store = MemoryStore()
        // The save knows every page but .calendar and .models.
        let saved = SidebarGroup.order.map(\.rawValue).filter { $0 != "calendar" && $0 != "models" }
        store.set(saved, forKey: "sidebarOrder")
        let model = SidebarVisibilityModel(store: store)
        // Each one goes back where the default layout puts it, inside its
        // own group — not at the end of the list.
        #expect(model.sections(in: .usage) == [.overview, .sessions, .calendar, .pricing])
        #expect(model.sections(in: .primitives) == [.plugins, .skills, .agentsMd, .mcpServers, .hooks, .models])
    }

    @Test func aMissingPageJoinsItsGroupNotItsNeighbour() {
        let store = MemoryStore()
        // Manage was reordered and the save predates .plugins. Its
        // default neighbour Compute sits mid-Manage; joining there would
        // interleave the groups.
        var saved = SidebarGroup.order.map(\.rawValue).filter { $0 != "plugins" }
        saved.removeAll { ["accounts", "harnesses", "compute"].contains($0) }
        saved.insert(contentsOf: ["compute", "accounts", "harnesses"],
                     at: saved.firstIndex(of: "inbox")! + 1)
        store.set(saved, forKey: "sidebarOrder")
        let model = SidebarVisibilityModel(store: store)
        #expect(model.sections(in: .manage) == [.compute, .accounts, .harnesses])
        #expect(model.sections(in: .primitives) == [.plugins, .skills, .agentsMd, .mcpServers, .hooks, .models])
        // Arrow keys never cross a group boundary.
        #expect(model.step(.compute, by: 1) == .accounts)
        #expect(model.step(.harnesses, by: 1) == .plugins)
    }

    @Test func aSavedOrderWithDeadIdsIsCleanedUp() {
        let store = MemoryStore()
        store.set(["sessions", "gone", "overview", "calendar", "pricing", "accounts", "settings"],
                  forKey: "sidebarOrder")
        let model = SidebarVisibilityModel(store: store)
        #expect(model.totalCount == AppSection.allCases.count)
        #expect(model.sections(in: .usage) == [.sessions, .overview, .calendar, .pricing])
    }

    @Test func arrowKeysSkipHiddenPages() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        model.setEnabled(.calendar, false)
        #expect(model.step(.sessions, by: 1) == .pricing)
        #expect(model.step(.pricing, by: -1) == .sessions)
        #expect(model.step(.overview, by: -1) == .overview)
    }

    @Test func aStepOutOfAHiddenPageLandsOnTheDirectionalNeighbour() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        model.setEnabled(.calendar, false)
        #expect(model.step(.calendar, by: 1) == .pricing)
        #expect(model.step(.calendar, by: -1) == .sessions)
        // Two away counts shown pages only.
        model.setEnabled(.pricing, false)
        #expect(model.step(.calendar, by: 2) == .tasks)
    }

    @Test func aHiddenPageOpenedDirectlyStepsFromItsOwnSlot() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        model.setEnabled(.serviceStatus, false)
        // Service status sits between Widgets and Settings.
        #expect(model.step(.serviceStatus, by: 1) == .settings)
        #expect(model.step(.serviceStatus, by: -1) == .widgets)
    }
    @Test func commandNumbersCoverTheFirstNineShownPages() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        #expect(model.shortcutSections == [.overview, .sessions, .calendar, .pricing,
                                           .repositories, .tasks, .inbox, .accounts, .harnesses])
        #expect(model.shortcutNumber(of: .overview) == 1)
        #expect(model.shortcutNumber(of: .harnesses) == 9)
        #expect(model.shortcutNumber(of: .compute) == nil)
        #expect(model.shortcutNumber(of: .plugins) == nil)
    }

    @Test func hiddenPagesGiveTheirNumberToTheNextShownPage() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        model.setEnabled(.sessions, false)
        model.setEnabled(.inbox, false)
        #expect(model.shortcutNumber(of: .sessions) == nil)
        #expect(model.shortcutNumber(of: .calendar) == 2)
        #expect(model.shortcutNumber(of: .compute) == 8)
        #expect(model.shortcutNumber(of: model.rows(in: .primitives)[0].section) == 9)
    }

    @Test func fewerThanNineShownPagesLeaveTheHighDigitsUnused() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        model.disableAll()
        #expect(model.shortcutSections == [.overview, .accounts, .settings])
        #expect(model.shortcutNumber(of: .settings) == 3)
    }

    @Test func reorderingMovesTheNumbers() {
        let model = SidebarVisibilityModel(store: MemoryStore())
        model.move(AppSection.calendar.rawValue, before: AppSection.overview.rawValue)
        #expect(model.shortcutNumber(of: .calendar) == 1)
        #expect(model.shortcutNumber(of: .overview) == 2)
    }
}
