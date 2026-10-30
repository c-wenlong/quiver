import Foundation
import Testing
@testable import QuiverCore

@Suite struct APIKeyProvidersTests {
    /// The (slug, url, env_vars) of every DEFAULT_PROVIDERS entry, in file
    /// order, read straight out of defaults.py so the Swift table can't
    /// drift from the Python one without failing this test.
    private static func pythonProviders() throws -> [(slug: String, url: String, envVars: [String])] {
        let file = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // QuiverCoreTests
            .deletingLastPathComponent() // Tests
            .deletingLastPathComponent() // app/mac
            .appending(path: "../../src/quiver/providers/defaults.py")
            .standardizedFileURL
        let text = try String(contentsOf: file, encoding: .utf8)
        let entryPattern = #/(?m)^    "([a-z0-9_]+)": \{\n(?:(?!^    \},)[\s\S])*^    \},?$/#
        let urlPattern = #/"url": "([^"]+)"/#
        let envPattern = #/"env_vars": \[([^\]]*)\]/#
        let quoted = #/"([^"]*)"/#
        var parsed: [(String, String, [String])] = []
        for entry in text.matches(of: entryPattern) {
            let block = entry.output.0
            let slug = String(entry.output.1)
            let url = try #require(block.firstMatch(of: urlPattern)).1
            let list = try #require(block.firstMatch(of: envPattern)).1
            // # comments inside the list can hold quoted words; cut them first.
            let envVars = list.components(separatedBy: "\n").flatMap { line in
                let code = line.prefix { $0 != "#" }
                return code.matches(of: quoted).map { String($0.1) }
            }
            parsed.append((slug, String(url), envVars))
        }
        return parsed
    }

    @Test func theTableMatchesDefaultsPy() throws {
        let python = try Self.pythonProviders()
        #expect(python.count > 20, "defaults.py parsed almost empty; the sync regex broke")
        let swiftIDs = APIKeyProviders.all.map(\.id), pythonIDs = python.map(\.slug)
        #expect(swiftIDs == pythonIDs, """
            The Swift table and defaults.py list different providers. Only in Swift: \
            \(Set(swiftIDs).subtracting(pythonIDs).sorted()); only in defaults.py: \
            \(Set(pythonIDs).subtracting(swiftIDs).sorted()). If both look the same, \
            defaults.py was likely reformatted and pythonProviders() needs its new layout.
            """)
        for (provider, expected) in zip(APIKeyProviders.all, python) {
            #expect(provider.keyURL.absoluteString == expected.url,
                    "\(provider.id) url differs from defaults.py")
            #expect(provider.envVars == expected.envVars,
                    "\(provider.id) env vars differ from defaults.py")
        }
    }

    /// Hand-picked answers: a loop comparing `canCheck` with the probe
    /// lookup would only restate `canCheck`'s own body.
    @Test func canCheckFollowsTheProbeTable() {
        #expect(APIKeyProviders.provider(forID: "openai")?.canCheck == true)
        #expect(APIKeyProviders.provider(forID: "anthropic")?.canCheck == true)
        // Cohere has no probe, so no check.
        #expect(APIKeyProviders.provider(forID: "cohere")?.canCheck == false)
    }

    @Test func aVariableFindsItsProviderByAnyEnvVar() {
        #expect(APIKeyProviders.provider(forVariable: "TOGETHER_API_KEY")?.id == "together_ai")
        #expect(APIKeyProviders.provider(forVariable: "TOGETHER_AI_API_KEY")?.id == "together_ai")
        #expect(APIKeyProviders.provider(forVariable: "GOOGLE_API_KEY")?.id == "gemini")
        #expect(APIKeyProviders.provider(forVariable: "GH_TOKEN")?.id == "github")
        #expect(APIKeyProviders.provider(forVariable: "NOPE_API_KEY") == nil)
    }

    @Suite @MainActor struct HeadingTests {
        private func rows(_ pairs: [(provider: String, name: String)]) -> [APIKeysModel.Row] {
            pairs.map { APIKeysModel.Row(provider: $0.provider, name: $0.name,
                                         value: "v", status: .loaded) }
        }

        @Test func aGroupHoldingAnotherOfItsVariablesWins() {
            let rows = rows([("GOOGLE", "GOOGLE_API_KEY")])
            let gemini = APIKeyProviders.provider(forID: "gemini")!
            #expect(APIKeyProviders.heading(for: gemini, in: rows) == "GOOGLE")
        }

        @Test func aHeadingMatchingTheNameIgnoringCaseWins() {
            let anthropic = APIKeyProviders.provider(forID: "anthropic")!
            #expect(APIKeyProviders.heading(for: anthropic,
                                          in: rows([("ANTHROPIC", "OTHER_KEY")])) == "ANTHROPIC")
        }

        @Test func aHeadingMatchingIgnoringSpacesAndUnderscoresWins() {
            let together = APIKeyProviders.provider(forID: "together_ai")!
            #expect(APIKeyProviders.heading(for: together,
                                          in: rows([("Together AI", "OTHER_KEY")])) == "Together AI")
            #expect(APIKeyProviders.heading(for: together,
                                          in: rows([("TOGETHER_AI", "OTHER_KEY")])) == "TOGETHER_AI")
        }

        @Test func noMatchMeansTheProviderName() {
            let openai = APIKeyProviders.provider(forID: "openai")!
            #expect(APIKeyProviders.heading(for: openai,
                                          in: rows([("ANTHROPIC", "ANTHROPIC_API_KEY")])) == "OpenAI")
            #expect(APIKeyProviders.heading(for: openai, in: []) == "OpenAI")
        }
    }
}
