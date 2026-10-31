import Foundation
import Testing
@testable import QuiverCore

@Suite struct InferenceCatalogueTests {
    @Test func plansThenProvidersInTheirOwnOrders() {
        let entries = InferenceCatalogue.entries(keys: [])
        #expect(entries.count == InferenceCatalog.all.count + APIKeyProviders.all.count)
        #expect(Set(entries.map(\.id)).count == entries.count)
        #expect(entries.prefix(InferenceCatalog.all.count).allSatisfy { $0.kind == .plan })
        #expect(entries.suffix(APIKeyProviders.all.count).allSatisfy { $0.kind == .apiKey })
        #expect(entries.map(\.name).prefix(3).elementsEqual(InferenceCatalog.all.map(\.name).prefix(3)))
        #expect(entries.suffix(APIKeyProviders.all.count).map(\.name)
            .elementsEqual(APIKeyProviders.all.map(\.name)))
        #expect(entries.allSatisfy { !$0.hasKey })
    }

    @Test func aSavedKeyMarksItsEntry() {
        let entries = InferenceCatalogue.entries(
            keys: [inferenceKey("GLM_CODING_PLAN_KEY"), inferenceKey("TOGETHER_API_KEY"), inferenceKey("OPENAI_API_KEY", "")])
        #expect(entries.first { $0.id == "plan:glm-coding-plan" }?.hasKey == true)
        // Any of a provider's variables marks it, not only the first.
        #expect(entries.first { $0.id == "provider:together_ai" }?.hasKey == true)
        // An empty value reads as no key, like the page's own rows.
        #expect(entries.first { $0.id == "provider:openai" }?.hasKey == false)
    }

    @Test func filtersByKindAndQuery() {
        let entries = InferenceCatalogue.entries(keys: [])
        let plans = InferenceCatalogue.filter(entries, kind: .plan, query: "")
        #expect(plans.count == InferenceCatalog.all.count)
        #expect(plans.allSatisfy { $0.kind == .plan })
        let providers = InferenceCatalogue.filter(entries, kind: .apiKey, query: "")
        #expect(providers.count == APIKeyProviders.all.count)
        // The query reads the name, the vendor and the variable it writes.
        #expect(InferenceCatalogue.filter(entries, kind: nil, query: " z.ai ")
            .contains { $0.id == "plan:glm-coding-plan" })
        #expect(InferenceCatalogue.filter(entries, kind: nil, query: "glm_coding")
            .map(\.id) == ["plan:glm-coding-plan"])
        #expect(InferenceCatalogue.filter(entries, kind: nil, query: "together_api_key")
            .map(\.id) == ["provider:together_ai"])
        #expect(InferenceCatalogue.filter(entries, kind: nil, query: "no such thing").isEmpty)
    }

    /// The sheet's mark column: every row's logo names a bundled
    /// harness-*.svg. A new plan or provider with no mark fails here
    /// rather than shipping a blank cell.
    @Test func everyEntryResolvesABundledMark() {
        for entry in InferenceCatalogue.entries(keys: []) {
            let logo = entry.logo
            #expect(logo != nil, "\(entry.id) has no bundled mark")
            #expect(logo.flatMap { HarnessIcons.icon(for: $0) } != nil,
                    "\(entry.id) names \(logo ?? "?") with no harness-*.svg")
        }
    }
}
