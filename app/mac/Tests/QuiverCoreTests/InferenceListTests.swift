import Foundation
import Testing
@testable import QuiverCore

/// A saved key row as the inference lists see it; shared with
/// InferenceCatalogueTests.
func inferenceKey(_ name: String, _ value: String = "sk-1234567890", provider: String = "OpenAI") -> APIKeysModel.Row {
    APIKeysModel.Row(provider: provider, name: name, value: value, status: .loaded)
}

@Suite struct InferenceCatalogTests {
    @Test func plansAreUniqueAndKeptApartFromPayAsYouGoNames() {
        let plans = InferenceCatalog.all
        #expect(Set(plans.map(\.id)).count == plans.count)
        #expect(Set(plans.map(\.keyName)).count == plans.count)
        // A plan key under a pay-as-you-go name would be checked, and picked
        // up by harnesses, against the wrong endpoint.
        for name in ["ZAI_API_KEY", "ZHIPU_API_KEY", "KIMI_API_KEY", "MOONSHOT_API_KEY", "MINIMAX_API_KEY",
                     "DASHSCOPE_API_KEY", "QWEN_API_KEY", "STEPFUN_API_KEY", "MIMO_API_KEY"] {
            #expect(InferenceCatalog.plan(forKey: name) == nil, "\(name)")
        }
        #expect(plans.allSatisfy { !$0.endpoints.isEmpty })
        // A key filed under the vendor's name finds the same logo.
        for plan in plans {
            #expect(APIKeyProviderLogo.logoID(for: plan.vendor) != nil, "\(plan.vendor) has no brand")
        }
        #expect(APIKeyProviderLogo.logoID(for: "Moore Threads") == "mthreads")
        // Every plan draws its vendor's logo; none falls back to a blank square.
        for plan in plans {
            #expect(plan.logo.flatMap { HarnessIcons.icon(for: $0) } != nil, "\(plan.name) has no logo")
        }
        #expect(plans.allSatisfy { ProviderCatalog.provider($0.id) == nil })
    }

    /// Every plan's key can be written under the shared heading; the keys
    /// file refuses a heading with a word of 16 letters or more.
    @Test func everyPlanKeySavesUnderTheSharedHeading() throws {
        #expect(APIKeysFile.isLabel(InferenceCatalog.heading))
        var file = APIKeysFile(text: "")
        for plan in InferenceCatalog.all {
            try file.set(name: plan.keyName, value: "sk-test-1234567890", provider: InferenceCatalog.heading)
        }
        #expect(Set(file.keys.map(\.name)) == Set(InferenceCatalog.all.map(\.keyName)))
        #expect(Set(file.keys.map(\.provider)) == [InferenceCatalog.heading])
    }

    @Test func messagesURLJoinsWithOrWithoutATrailingSlash() {
        let kimi = InferenceCatalog.all.first { $0.id == "kimi-code" }!
        #expect(kimi.endpoints[0].messagesURL.absoluteString == "https://api.kimi.ai/coding/v1/messages")
        let glm = InferenceCatalog.all.first { $0.id == "glm-coding-plan" }!
        #expect(glm.endpoints[0].messagesURL.absoluteString == "https://api.z.ai/api/anthropic/v1/messages")
    }

    /// Each checkable plan gets an empty Messages request to every site,
    /// read the way routing.run's is: 400 or 422 means the key got in.
    @Test func checkablePlansHaveAProbeOverEverySite() throws {
        let mimo = try #require(APIKeyProbe.probe(forName: "MIMO_TOKEN_PLAN_KEY"))
        #expect(mimo.hosts.count == 3)
        #expect(mimo.method == "POST")
        #expect(mimo.worksOnBadRequest)
        #expect(APIKeyProbe.probe(forName: "KIMI_CODE_KEY")?.auth == .xApiKey)
        #expect(APIKeyProbe.probe(forName: "GLM_CODING_PLAN_KEY")?.auth == .bearer)
        // These read the body before the key, so an empty request proves nothing.
        #expect(APIKeyProbe.probe(forName: "DEVPASS_KEY") == nil)
        #expect(APIKeyProbe.probe(forName: "QIANFAN_TOKEN_PLAN_KEY") == nil)
        // TaoToken refuses a fake key with a 400, which a bad-request probe
        // would count as working.
        #expect(APIKeyProbe.probe(forName: "TAOTOKEN_CODING_PLAN_KEY") == nil)
        #expect(APIKeyProbe.noCheckReason(forName: "DEVPASS_KEY").contains("DevPass"))
        let body = Data(#"{"type":"error","error":{"message":"max_tokens: Field required"}}"#.utf8)
        #expect(APIKeyProbe.classify(status: 400, body: body, probe: mimo, key: "tp-x") == .works)
        #expect(APIKeyProbe.classify(status: 401, body: Data(), probe: mimo, key: "tp-x")
            == .rejected(status: 401, message: nil))
    }
}

@Suite struct InferenceListTests {
    @Test func keysInFileOrderThenPlansWithNoKey() {
        let items = InferenceList.items(keys: [inferenceKey("OPENAI_API_KEY"), inferenceKey("KIMI_CODE_KEY", provider: "Kimi Code")])
        #expect(items[0].id == "OPENAI_API_KEY")
        #expect(items[0].kind == .apiKey)
        #expect(items[1].id == "KIMI_CODE_KEY")
        #expect(items[1].kind == .plan)
        #expect(items[1].title == "Kimi Code")
        #expect(items.count == 2 + InferenceCatalog.all.count - 1)
        let unset = items.dropFirst(2)
        #expect(unset.allSatisfy { $0.key == nil && $0.kind == .plan && !$0.hasKey })
        #expect(!unset.contains { $0.id == "KIMI_CODE_KEY" })
    }

    @Test func stateFollowsTheCheck() {
        #expect(InferenceKeyState(hasKey: false, check: .works) == .noKey)
        #expect(InferenceKeyState(hasKey: true, check: .works) == .valid)
        #expect(InferenceKeyState(hasKey: true, check: .noCredit) == .valid)
        #expect(InferenceKeyState(hasKey: true, check: .rejected(status: 401, message: nil)) == .invalid)
        #expect(InferenceKeyState(hasKey: true, check: .couldNotCheck("HTTP 500")) == .unchecked)
        #expect(InferenceKeyState(hasKey: true, check: nil) == .unchecked)
    }

    @Test func filtersByStateKindAndQuery() {
        let items = InferenceList.items(keys: [inferenceKey("OPENAI_API_KEY")])
        let open = items[0]
        let glm = items.first { $0.id == "GLM_CODING_PLAN_KEY" }!
        let state: (InferenceItem) -> InferenceKeyState = { $0.hasKey ? .valid : .noKey }
        #expect(InferenceList.matches(open, state: .valid, kind: nil, query: "", stateOf: state))
        #expect(!InferenceList.matches(glm, state: .valid, kind: nil, query: "", stateOf: state))
        #expect(InferenceList.matches(glm, state: .noKey, kind: .plan, query: "", stateOf: state))
        #expect(!InferenceList.matches(open, state: nil, kind: .plan, query: "", stateOf: state))
        #expect(InferenceList.matches(glm, state: nil, kind: nil, query: "z.ai", stateOf: state))   // vendor
        #expect(InferenceList.matches(glm, state: nil, kind: nil, query: "glm_coding", stateOf: state))
        #expect(!InferenceList.matches(open, state: nil, kind: nil, query: "glm", stateOf: state))
        // A plan key filed under a heading of your own is found by it.
        let filed = InferenceList.items(keys: [inferenceKey("GLM_CODING_PLAN_KEY", provider: "Work")])[0]
        #expect(filed.title == "GLM Coding Plan")
        #expect(InferenceList.matches(filed, state: nil, kind: nil, query: "work", stateOf: state))
    }

    @MainActor @Test func dragOrderPersistsAndNewRowsGoLast() {
        let store = MemoryStore()
        let order = InferenceOrder(store: store)
        let items = InferenceList.items(keys: [inferenceKey("A_KEY"), inferenceKey("B_KEY")], plans: [])
        order.move("B_KEY", before: "A_KEY", in: items)
        #expect(order.sorted(items).map(\.id) == ["B_KEY", "A_KEY"])
        #expect(store.values["inferenceOrder"] == ["B_KEY", "A_KEY"])
        // A new key lands at the end; a fresh model reads the same order.
        let more = InferenceList.items(keys: [inferenceKey("A_KEY"), inferenceKey("B_KEY"), inferenceKey("C_KEY")], plans: [])
        #expect(InferenceOrder(store: store).sorted(more).map(\.id) == ["B_KEY", "A_KEY", "C_KEY"])
        order.move("B_KEY", before: nil, in: more)
        #expect(order.sorted(more).map(\.id) == ["A_KEY", "C_KEY", "B_KEY"])
    }
}
