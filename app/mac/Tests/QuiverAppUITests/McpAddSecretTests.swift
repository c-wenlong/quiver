import QuiverCore
import Testing
@testable import QuiverApp

/// The add sheet refuses, before any secret is stored, a secret name that
/// another server's values already live under.
@Suite struct McpAddSecretTests {
    @Test func aSecretAnotherServerKeepsIsRefused() {
        let existing = [McpServer(name: "foo_bar", envKeys: ["API_KEY"], harnesses: ["claude"])]
        let owner = McpAddServerSheet.secretOwner(
            of: [mcpSecretName(server: "foo-bar", key: "API_KEY")], adding: "foo-bar", existing: existing)
        #expect(owner?.server == "foo_bar")
        #expect(owner?.secret == "FOO_BAR_API_KEY")
    }

    @Test func aBearerTokenCountsAsTheServersSecret() {
        let existing = [McpServer(name: "docs.v2", transport: "http", headerKeys: ["Authorization"],
                                  harnesses: [])]
        let owner = McpAddServerSheet.secretOwner(
            of: [mcpSecretName(server: "docs-v2", key: "TOKEN")], adding: "docs-v2", existing: existing)
        #expect(owner?.server == "docs.v2")
    }

    @Test func replacingTheSameServerIsNotAClash() {
        let existing = [McpServer(name: "exa", envKeys: ["EXA_API_KEY"], harnesses: ["claude"])]
        #expect(McpAddServerSheet.secretOwner(
            of: [mcpSecretName(server: "exa", key: "EXA_API_KEY")], adding: "exa", existing: existing) == nil)
        #expect(McpAddServerSheet.secretOwner(
            of: [mcpSecretName(server: "github", key: "TOKEN")], adding: "github", existing: existing) == nil)
    }
}
