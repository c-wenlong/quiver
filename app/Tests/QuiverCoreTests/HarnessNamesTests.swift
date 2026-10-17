import Foundation
import Testing
@testable import QuiverCore

/// Every name Quiver shows for a harness, account, provider or model lab
/// comes from one table each, and starts with a capital letter.
@Suite struct HarnessNamesTests {
    private var everyHarnessId: [String] {
        Array(Set(HarnessIcons.available + ProviderCatalog.all.map(\.id) + Array(HarnessMark.names.keys)
                  + ["claude-desktop", "grok-bot", DevinCloud.harness, "codex-acc2"])).sorted()
    }

    @Test func everyNameStartsWithACapital() {
        func capitalised(_ name: String) -> Bool { name.first.map { $0.isUppercase || $0.isNumber } ?? false }
        for id in everyHarnessId {
            #expect(capitalised(HarnessMark(id).name), "harness \(id): \(HarnessMark(id).name)")
            #expect(capitalised(HarnessMark.accountName(id)), "account \(id)")
        }
        for provider in ProviderCatalog.all {
            #expect(capitalised(provider.name), "provider \(provider.id): \(provider.name)")
        }
        for brand in ModelBrands.all + [ModelBrands.other] {
            #expect(capitalised(brand.name), "brand \(brand.id): \(brand.name)")
        }
        for vendor in Vendor.allCases {
            #expect(capitalised(vendor.title), "vendor \(vendor)")
        }
    }

    /// The tables that used to disagree now give one answer per harness.
    @Test func everyPageReadsTheSameName() {
        for id in everyHarnessId {
            #expect(UsageFormat.harness(id) == HarnessMark(id).name, "\(id)")
        }
        #expect(HarnessMark("droid").name == "Factory Droid")
        #expect(HarnessMark("copilot").name == "GitHub Copilot")
        #expect(HarnessMark("grok").name == "Grok Build")
        #expect(HarnessMark("goose").name == "Goose")
        #expect(HarnessMark("aider").name == "Aider")
        #expect(HarnessMark("kilo").name == "Kilo Code")
        #expect(HarnessMark("codex-acc2").name == "Codex (acc2)")
    }

    /// A Claude account is the Anthropic subscription, so it reads
    /// "Claude"; every other account reads its harness name.
    @Test func accountNames() {
        #expect(Vendor.claude.title == "Claude")
        #expect(Vendor.codex.title == "Codex")
        #expect(ProviderCatalog.provider("claude")?.name == "Claude")
        #expect(ProviderCatalog.provider("droid")?.name == "Factory Droid")
        #expect(ProviderCatalog.provider("ark-coding-plan")?.icon == "ark-coding-plan")
        #expect(HarnessIcons.icon(for: ProviderCatalog.provider("ark-coding-plan")!.icon) != nil)
    }

    @Test func accountLabelsUseTheTable() {
        let directory = AccountDirectory(profiles: [], paths: { _ in fatalError() },
                                         home: URL(fileURLWithPath: "/Users/k"))
        #expect(directory.label(for: "/Users/k/.local/share/opencode") == HarnessMark("opencode").name)
        #expect(directory.label(for: DevinCloud.accountHome) == HarnessMark(DevinCloud.harness).name)
    }
}
