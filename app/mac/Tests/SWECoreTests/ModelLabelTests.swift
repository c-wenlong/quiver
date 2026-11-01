import Testing
@testable import SWECore

/// The rules the iPhone's `ModelLabel` and the web app's `Fmt.modelName`
/// share: the same cases are in each client's tests.
struct ModelLabelTests {
    @Test func shortDropsProviderPathAndDateStamp() {
        #expect(ModelLabel.short("claude-opus-4-5-20251101") == "claude-opus-4-5")
        #expect(ModelLabel.short("anthropic/claude-opus-4-5") == "claude-opus-4-5")
        #expect(ModelLabel.short("openrouter/nvidia/nemotron-3:free") == "nemotron-3:free")
        #expect(ModelLabel.short("gpt-5.1-codex") == "gpt-5.1-codex", "a version is not a date stamp")
        #expect(ModelLabel.short("swe-2-max") == "swe-2-max")
        #expect(ModelLabel.short("model-2025110") == "model-2025110", "seven digits are kept")
        #expect(ModelLabel.short("-20251101") == "-20251101", "never shortened to nothing")
        #expect(ModelLabel.short("  claude-opus-4-5  ") == "claude-opus-4-5")
        #expect(ModelLabel.short(nil) == nil)
        #expect(ModelLabel.short("") == nil)
        #expect(ModelLabel.short("  ") == nil)
    }

    @Test func aRowNamesTheNewestModelFirstAndOnlyOnce() {
        #expect(ModelLabel.rowModels(current: nil, used: []) == [])
        #expect(ModelLabel.rowModels(current: "", used: ["claude-sonnet-4-5"]) == ["claude-sonnet-4-5"])
        #expect(ModelLabel.rowModels(current: "claude-opus-4-5-20251101", used: []) == ["claude-opus-4-5"])
        #expect(ModelLabel.rowModels(current: "claude-opus-4-5-20251101", used: ["claude-opus-4-5-20251101"])
                == ["claude-opus-4-5"])
        #expect(ModelLabel.rowModels(current: "anthropic/claude-opus-4-5",
                                     used: ["claude-sonnet-4-5", "claude-opus-4-5"])
                == ["claude-opus-4-5", "claude-sonnet-4-5"])
    }
}
