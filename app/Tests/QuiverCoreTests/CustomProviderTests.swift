import Foundation
import Testing
@testable import QuiverCore

/// The naming, URL and parsing rules behind the Add-key sheet's
/// "Custom provider" path.
@Suite struct CustomProviderTests {
    @Test func envPrefixFoldsTheNameIntoAVariablePrefix() {
        #expect(CustomProvider.envPrefix(for: "SoCLaaS") == "SOCLAAS")
        #expect(CustomProvider.envPrefix(for: "NUS SoC") == "NUS_SOC")
        #expect(CustomProvider.envPrefix(for: "foo-bar.baz") == "FOO_BAR_BAZ")
        #expect(CustomProvider.envPrefix(for: "  spaced  out ") == "SPACED_OUT")
        // Accents fold to ASCII so the variables still parse in a shell;
        // a name with nothing ASCII left has no prefix to give.
        #expect(CustomProvider.envPrefix(for: "Café") == "CAFE")
        #expect(CustomProvider.envPrefix(for: "學") == "")
        #expect(CustomProvider.envPrefix(for: "!!!") == "")
        #expect(CustomProvider.envPrefix(for: "") == "")
    }

    @Test func modelsURLJoinsTheBaseToModels() {
        #expect(CustomProvider.modelsURL("https://soclaas-api.comp.nus.edu.sg/v1")?.absoluteString
                == "https://soclaas-api.comp.nus.edu.sg/v1/models")
        // A trailing slash folds, so the two spellings agree.
        #expect(CustomProvider.modelsURL("https://host/v1/")?.absoluteString
                == "https://host/v1/models")
        // Plain http stays legal for local endpoints.
        #expect(CustomProvider.modelsURL("http://localhost:11434/v1")?.absoluteString
                == "http://localhost:11434/v1/models")
        // The append lands on the path, so a base carrying a query or
        // fragment keeps it after /models rather than swallowing it.
        #expect(CustomProvider.modelsURL("https://host/v1?api-version=2024")?.absoluteString
                == "https://host/v1/models?api-version=2024")
        #expect(CustomProvider.modelsURL("https://host")?.absoluteString == "https://host/models")
    }

    @Test func modelsURLRefusesNonURLs() {
        #expect(CustomProvider.modelsURL("") == nil)
        #expect(CustomProvider.modelsURL("not a url") == nil)
        #expect(CustomProvider.modelsURL("ftp://host/v1") == nil)
        #expect(CustomProvider.modelsURL("host/v1") == nil)
    }

    @Test func entriesWritesBaseKeyAndModelInFileOrder() {
        let entries = CustomProvider.entries(prefix: "SOCLAAS", baseURL: "https://h/v1",
                                             key: "k", model: "m1")
        #expect(entries.map { $0.name } == ["SOCLAAS_BASE_URL", "SOCLAAS_API_KEY", "SOCLAAS_MODEL"])
        #expect(entries.map { $0.value } == ["https://h/v1", "k", "m1"])
        // No model: the variable is skipped, not written empty.
        #expect(CustomProvider.entries(prefix: "P", baseURL: "https://h", key: "k", model: "")
                .map { $0.name } == ["P_BASE_URL", "P_API_KEY"])
    }

    @Test func modelsParsesTheOpenAIShape() {
        let body = Data(#"{"object":"list","data":[{"id":"a","object":"model"},{"id":"b"}]}"#.utf8)
        #expect(CustomProvider.models(in: body).map { $0.id } == ["a", "b"])
        #expect(CustomProvider.models(in: body).map { $0.label } == ["a", "b"])
    }

    @Test func modelsReadsADisplayNameFromTheEntryOrOneLevelDown() {
        let body = Data(#"{"data":[{"id":"x","display_name":"X Large"},{"id":"y","name":"Y Small"},{"id":"z","soclaas":{"display_name":"Zed"}}]}"#.utf8)
        let models = CustomProvider.models(in: body)
        #expect(models.map { $0.id } == ["x", "y", "z"])
        #expect(models.map { $0.label } == ["X Large", "Y Small", "Zed"])
    }

    @Test func modelsAcceptsABareListAndSkipsJunk() {
        #expect(CustomProvider.models(in: Data(#"[{"id":"a"},{"noid":1},42]"#.utf8)).map { $0.id } == ["a"])
        #expect(CustomProvider.models(in: Data("junk".utf8)).isEmpty)
        #expect(CustomProvider.models(in: Data(#"{"data":42}"#.utf8)).isEmpty)
        #expect(CustomProvider.models(in: Data(#"{"data":[{"id":""}]}"#.utf8)).isEmpty)
    }

    @Test func probeHitsTheModelsURLAsBearer() {
        let probe = CustomProvider.probe(baseURL: "https://h.test/v1")
        #expect(probe?.hosts == [URL(string: "https://h.test/v1/models")!])
        #expect(probe?.method == "GET")
        #expect(probe?.auth == .bearer)
        #expect(CustomProvider.probe(baseURL: "junk") == nil)
    }
}
