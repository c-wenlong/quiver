import Foundation
import Testing
@testable import QuiverCore

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
