import Foundation
import Testing
@testable import SWECore

@Suite struct ModelBrandsTests {
    @Test func everyBrandLogoIsBundled() {
        for brand in ModelBrands.all {
            guard let icon = brand.icon else { continue }
            #expect(HarnessIcons.icon(for: icon) != nil, "\(brand.id) → \(icon)")
        }
    }

    @Test func noKeywordNamesTwoBrands() {
        var owner: [String: String] = [:]
        for brand in ModelBrands.all {
            for word in brand.keywords {
                #expect(owner[word] == nil, "\(word) is \(owner[word] ?? "") and \(brand.id)")
                owner[word] = brand.id
            }
        }
    }

    /// Two labs side by side on the chart must not share a bar colour;
    /// Grok Bot is xAI's, so it may.
    @Test func chartColoursAreDistinct() {
        var seen: [String: String] = [:]
        for brand in ModelBrands.all where brand.id != "grok-bot" {
            guard let color = brand.color else { continue }
            let key = "\(color)"
            #expect(seen[key] == nil, "\(brand.id) repeats \(seen[key] ?? "")'s colour")
            seen[key] = brand.id
        }
    }

    /// "Other" must never look like a named brand: Cognition once drew in
    /// exactly Other's grey.
    @Test func otherHasItsOwnColour() {
        for brand in ModelBrands.all {
            #expect(brand.color != ModelBrands.other.color, "\(brand.id) draws in Other's colour")
        }
    }

    /// Artificial Analysis's creator slugs, as its API sends them.
    @Test func creatorSlugsResolve() {
        let cases = [("anthropic", "anthropic"), ("openai", "openai"), ("google", "google"),
                     ("xai", "xai"), ("meta", "meta"), ("zai", "zai"), ("kimi", "moonshot"),
                     ("alibaba", "alibaba"), ("xiaomi", "xiaomi"), ("stepfun", "stepfun"),
                     ("inclusionai", "inclusionai"), ("nvidia", "nvidia"), ("deepseek", "deepseek"),
                     ("mistral", "mistral"), ("minimax", "minimax"), ("x-ai", "xai")]
        for (slug, id) in cases {
            #expect(ModelBrands.forKeyword(slug)?.id == id, "\(slug)")
        }
        #expect(ModelBrands.forKeyword("aws") == nil)
    }

    /// The bars that drew without a logo: Muse, MiMo, Step and Ling.
    @Test func modelNamesResolve() {
        let cases = [("Muse Wonder Full (Max)", "meta"), ("muse-spark-1.3", "meta"),
                     ("MiMo-V2.6-Pro", "xiaomi"), ("Step 5 Preview", "stepfun"),
                     ("step-3.5-flash", "stepfun"), ("Ling 3.1 Flash", "inclusionai"),
                     ("llama-4-maverick", "meta"), ("o3", "openai"), ("grok-4", "xai"),
                     ("grok-bot-default", "grok-bot"), ("openrouter/qwen3-coder", "alibaba"),
                     ("nemotron-3-super", "nvidia"), ("stepping-stone", "other")]
        for (model, id) in cases {
            #expect(ModelBrands.forModel(model).id == id, "\(model)")
        }
    }

    /// Devin logs Claude Haiku 4.5 as "MODEL_PRIVATE_11"; the bundled
    /// supplement aliases it, so the row takes Anthropic's brand.
    @Test func anAliasNamesAndBrandsAnOpaqueID() {
        #expect(ModelBrands.resolvedName("MODEL_PRIVATE_11") == "claude-haiku-4-5")
        #expect(ModelBrands.resolvedName("model_private_11") == "claude-haiku-4-5")
        #expect(ModelBrands.resolvedName("claude-opus-5") == nil)
        #expect(ModelBrands.forModel("MODEL_PRIVATE_11").id == "anthropic")
        #expect(ModelBrands.forModel("MODEL_PRIVATE_99").id == "other")
        let row = ModelsModel.Row(logged: "MODEL_PRIVATE_11")
        #expect((row.alias, row.displayName, row.vendor.id) == ("claude-haiku-4-5", "claude-haiku-4-5", "anthropic"))
    }

    @Test func aliasTargetsFollowChainsAndForms() {
        let supplement = PricingSupplement(aliases: ["a": "b", "b": "c", "x": "X"])
        #expect(supplement.aliasTarget(of: "a") == "c")
        #expect(supplement.aliasTarget(of: "openrouter/b-20260101") == "c")
        #expect(supplement.aliasTarget(of: "x") == nil)
    }

    @Test func vendorsCarryTheNewLogos() {
        #expect(ModelVendor.of(logged: "muse-wonder").icon == "meta")
        #expect(ModelVendor.of(logged: "mimo-v2.6-pro").icon == "xiaomimimo")
        #expect(ModelVendor.of(logged: "step-5").icon == "stepfun")
        #expect(ModelVendor.of(logged: "ling-3.1-flash").icon == "antgroup")
        // A brand with no chart colour draws in Other's grey.
        #expect(ModelVendor.of(logged: "composer-2").color == ModelBrands.other.color)
    }

    @Test func headingsMatchWholeWords() {
        #expect(ModelBrands.inText("MiMo (Xiaomi)")?.id == "xiaomi")
        #expect(ModelBrands.inText("x.ai")?.id == "xai")
        #expect(ModelBrands.inText("Stepping up") == nil)
        #expect(ModelBrands.inText("") == nil)
    }
}
