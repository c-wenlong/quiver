import Foundation
import Testing
@testable import QuiverCore

/// The Accounts card -> `usageResets` mapping the Calendar page's Usage
/// legend and grid markers read.
@Suite struct UsageResetTests {
    private func card(vendor: Vendor = .claude, name: String = "Work",
                      usage: AccountUsage?) -> AccountsModel.Card {
        AccountsModel.Card(
            profile: Profile(id: "\(vendor.rawValue)-work", vendor: vendor, name: name,
                             isDefault: false, color: "#d97757"),
            usage: usage)
    }

    @Test func resetsKeepOnlyWindowsWithATime() {
        let at = utc("2026-10-30T15:00:00Z")
        let card = card(usage: AccountUsage(status: .ok, windows: [
            UsageWindow(label: "5h", usedPercent: 63, resetsAt: at),
            UsageWindow(label: "7d", usedPercent: 20, resetsAt: utc("2026-11-02T09:00:00Z")),
            UsageWindow(label: "30d", usedPercent: 10, resetsAt: nil),
        ]))
        let resets = card.usageResets
        #expect(resets.map(\.window) == ["5h", "7d"])
        let five = resets[0]
        #expect(five.account == "claude-work")
        #expect(five.accountName == "Work")
        #expect(five.title == "Claude: Work")
        #expect(five.harness == "claude")
        #expect(five.at == at)
        #expect(!five.isStale)
        #expect(five.id == "claude-work|5h|\(at.timeIntervalSince1970)")
    }

    @Test func aStaleReadingMarksItsResets() {
        let card = card(usage: AccountUsage(status: .error("down"), windows: [
            UsageWindow(label: "7d", usedPercent: 80, resetsAt: utc("2026-11-02T09:00:00Z")),
        ], isStale: true))
        #expect(card.usageResets.map(\.isStale) == [true])
    }

    @Test func noUsageLeavesNoResets() {
        #expect(card(usage: nil).usageResets.isEmpty)
        #expect(card(usage: AccountUsage(status: .signedOut)).usageResets.isEmpty)
    }

    @Test func usageTitleJoinsVendorAndName() {
        #expect(card(vendor: .codex, name: "Default", usage: nil).usageTitle == "Codex: Default")
    }
}
