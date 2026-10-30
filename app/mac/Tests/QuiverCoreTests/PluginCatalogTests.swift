import Foundation
import Testing
@testable import QuiverCore

/// `swe plugins catalog --json` as swe writes it: one hub marketplace, one
/// registered elsewhere, one no harness registers any more, and Cursor's
/// cache.
let catalogJSON = #"""
{"harnesses": ["claude", "codex", "cursor"], "actions": ["claude", "codex"],
 "errors": [{"harness": "codex", "marketplace": "broken", "detail": "manifest is not JSON"}],
 "marketplaces": [
  {"name": "kit", "hub": true, "path": "/h/.quiver/plugins/kit", "description": "My kit",
   "harnesses": {"claude": {"source_type": "directory", "location": "/h/.quiver/plugins/kit", "manifest": "/m", "readable": true},
                 "codex": {"source_type": "directory", "location": "/h/.quiver/plugins/kit", "manifest": "/m", "readable": true}},
   "plugins": [
    {"name": "drawer", "ref": "drawer@kit", "marketplace": "kit", "display_name": null, "description": "Holds things",
     "version": "2.0.0", "category": null, "tags": [], "author": null, "homepage": null, "listed": true,
     "source_path": "/h/.quiver/plugins/kit/drawer", "components": {"commands": 1}, "skills": [],
     "harnesses": {
      "claude": {"available": true, "installed": true, "enabled": false, "version": "1.0.0", "scopes": ["user"],
                 "path": "/h/.quiver/plugins/kit/drawer", "copies": []},
      "codex": {"available": true, "installed": true, "enabled": true, "version": "2.0.0", "scopes": [],
                "path": "/h/.codex/plugins/cache/kit/drawer/2.0.0", "copies": ["/h/.codex/plugins/cache/kit/drawer/2.0.0"]},
      "cursor": {"available": false, "installed": null, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []}},
     "drift": {"claude": {"state": "stale", "fix": "claude plugin update drawer@kit", "id": "claude:stale:drawer@kit"},
               "codex": {"state": "installed", "fix": null, "id": null}}},
    {"name": "toolkit", "ref": "toolkit@kit", "marketplace": "kit", "display_name": "Tool kit", "description": "Tools for testing",
     "version": "1.2.0", "category": "dev", "tags": ["test"], "author": "Me", "homepage": null, "listed": true,
     "source_path": "/h/.quiver/plugins/kit/toolkit",
     "components": {"skills": 2, "mcp_servers": 1},
     "skills": [{"name": "hammer", "description": "Hits things", "path": "/h/.quiver/plugins/kit/toolkit/skills/hammer", "harnesses": ["claude"]},
                {"name": "saw", "description": "", "path": "/h/.quiver/plugins/kit/toolkit/skills/saw", "harnesses": ["claude"]}],
     "harnesses": {
      "claude": {"available": true, "installed": true, "enabled": true, "version": "1.2.0", "scopes": ["user"],
                 "path": "/h/.quiver/plugins/kit/toolkit", "copies": ["/h/.claude/plugins/cache/kit/toolkit/1.0"]},
      "codex": {"available": true, "installed": false, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []},
      "cursor": {"available": false, "installed": null, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []}},
     "drift": {"claude": {"state": "installed", "fix": null, "id": null},
               "codex": {"state": "not-installed", "fix": "codex plugin add toolkit@kit", "id": "codex:not-installed:toolkit@kit"}}}
   ]},
  {"name": "official", "hub": false, "path": null, "description": null,
   "harnesses": {"claude": {"source_type": "github", "location": "o/official", "manifest": "/m", "readable": true},
                 "codex": {"source_type": "git", "location": "https://example.com/o.git", "manifest": "/m", "readable": true}},
   "plugins": [
    {"name": "bundle", "ref": "bundle@official", "marketplace": "official", "description": "The whole marketplace",
     "listed": true, "source_path": "/h/.claude/plugins/marketplaces/official", "components": {}, "skills": [],
     "harnesses": {
      "claude": {"available": true, "installed": false, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []},
      "codex": {"available": false, "installed": false, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []},
      "cursor": {"available": false, "installed": null, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []}},
     "drift": null},
    {"name": "linter", "ref": "linter@official", "marketplace": "official", "description": "Finds lint", "version": null,
     "listed": true, "source_path": null, "components": {"skills": 1},
     "skills": [{"name": "lint", "description": "Lints", "path": "/h/.claude/plugins/marketplaces/official/x/lint", "harnesses": []}],
     "harnesses": {
      "claude": {"available": true, "installed": false, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []},
      "codex": {"available": true, "installed": false, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []},
      "cursor": {"available": false, "installed": null, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []}},
     "drift": null},
    {"name": "nested", "ref": "nested@official", "marketplace": "official", "description": null,
     "listed": true, "source_path": "/h/.claude/plugins/marketplaces/official/plugins/nested", "components": {}, "skills": [],
     "harnesses": {
      "claude": {"available": true, "installed": false, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []},
      "codex": {"available": false, "installed": false, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []},
      "cursor": {"available": false, "installed": null, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []}},
     "drift": null},
    {"name": "tool", "ref": "tool@official", "marketplace": "official", "description": "A tool",
     "listed": true, "source_path": null, "components": {}, "skills": [],
     "harnesses": {
      "claude": {"available": false, "installed": false, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []},
      "codex": {"available": true, "installed": true, "enabled": true, "version": "1", "scopes": [], "path": "/h/x/tool", "copies": ["/h/x/tool"]},
      "cursor": {"available": false, "installed": null, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []}},
     "drift": null},
    {"name": "vercel", "ref": "vercel@official", "marketplace": "official", "description": "Deploy things",
     "version": "0.50.0", "listed": true, "source_path": null, "components": {"skills": 2},
     "skills": [{"name": "ai-sdk", "description": "The AI SDK", "path": "/h/.claude/plugins/cache/official/vercel/0.50.0/skills/ai-sdk", "harnesses": ["claude"]},
                {"name": "eve", "description": "Eve", "path": "/h/.claude/plugins/cache/official/vercel/0.50.0/skills/eve", "harnesses": ["claude"]}],
     "harnesses": {
      "claude": {"available": true, "installed": true, "enabled": true, "version": "0.50.0", "scopes": ["user"],
                 "path": "/h/.claude/plugins/cache/official/vercel/0.50.0", "copies": ["/h/.claude/plugins/cache/official/vercel/0.50.0"]},
      "codex": {"available": true, "installed": false, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []},
      "cursor": {"available": false, "installed": null, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []}},
     "drift": null}
   ]},
  {"name": "openai-curated", "hub": false, "path": null, "description": null, "harnesses": {},
   "plugins": [
    {"name": "canva", "ref": "canva@openai-curated", "marketplace": "openai-curated", "description": null,
     "listed": false, "source_path": null, "components": {"skills": 1},
     "skills": [{"name": "canva-brand-check", "description": "Checks a design against a brand kit",
                 "path": "/h/.codex/plugins/cache/openai-curated/canva/5fd/skills/canva-brand-check", "harnesses": ["codex"]}],
     "harnesses": {
      "claude": {"available": false, "installed": false, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []},
      "codex": {"available": false, "installed": true, "enabled": true, "version": "5fd", "scopes": [],
                "path": "/h/.codex/plugins/cache/openai-curated/canva/5fd", "copies": ["/h/.codex/plugins/cache/openai-curated/canva/5fd"]},
      "cursor": {"available": false, "installed": null, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []}},
     "drift": null}
   ]},
  {"name": "cursor-public", "hub": false, "path": null, "description": null,
   "harnesses": {"cursor": {"source_type": "cache", "location": "/h/.cursor/plugins/cache/cursor-public", "manifest": null, "readable": false}},
   "plugins": [
    {"name": "cursor-thing", "ref": "cursor-thing@cursor-public", "marketplace": "cursor-public", "description": "Cursor only",
     "listed": false, "source_path": null, "components": {"skills": 1},
     "skills": [{"name": "cthing", "description": "", "path": "/h/.cursor/plugins/cache/cursor-public/cursor-thing/1/skills/cthing", "harnesses": ["cursor"]}],
     "harnesses": {
      "claude": {"available": false, "installed": false, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []},
      "codex": {"available": false, "installed": false, "enabled": null, "version": null, "scopes": [], "path": null, "copies": []},
      "cursor": {"available": false, "installed": null, "enabled": null, "version": "1", "scopes": [],
                 "path": "/h/.cursor/plugins/cache/cursor-public/cursor-thing/1", "copies": ["/h/.cursor/plugins/cache/cursor-public/cursor-thing/1"]}},
     "drift": null}
   ]}
 ]}
"""#

/// `swe skills --json` on the same machine: every shape a skill can take
/// against the catalog above.
let catalogInventoryJSON = #"""
{"shared_dir": "/h/.quiver/skills", "harnesses": [],
 "skills": [
  {"name": "alpha", "description": "Mine", "path": "/h/.quiver/skills/alpha", "root": "shared", "source": "shared", "harnesses": ["claude"]},
  {"name": "only-codex", "description": "", "path": "/h/.codex/skills/only-codex", "root": "codex", "source": "harness", "harnesses": ["codex"]},
  {"name": "hammer", "description": "Hits things", "path": "/h/.quiver/plugins/kit/toolkit/skills/hammer",
   "root": "claude-plugin", "source": "plugin", "harnesses": ["claude"]},
  {"name": "hammer", "description": "Hits things", "path": "/h/.claude/plugins/cache/kit/toolkit/1.0/skills/hammer",
   "root": "claude-plugin", "source": "plugin", "harnesses": ["claude"]},
  {"name": "ai-sdk", "description": "The AI SDK", "path": "/h/.claude/plugins/cache/official/vercel/0.50.0/skills/ai-sdk",
   "root": "claude-plugin", "source": "plugin", "harnesses": ["claude"]},
  {"name": "ai-sdk", "description": "Upstream copy", "path": "/h/.claude/plugins/cache/official/vercel/0.50.0/skills/ai-sdk/upstream",
   "root": "claude-plugin", "source": "plugin", "harnesses": ["claude"]},
  {"name": "release", "description": "Ships a release", "path": "/h/.claude/plugins/cache/official/vercel/0.50.0/.claude/skills/release",
   "root": "claude-plugin", "source": "plugin", "harnesses": ["claude"]},
  {"name": "canva-brand-check", "description": "", "path": "/h/.codex/plugins/cache/openai-curated/canva/5fd/skills/canva-brand-check",
   "root": "codex-plugin", "source": "plugin", "harnesses": ["codex"]},
  {"name": "cthing", "description": "", "path": "/h/.cursor/plugins/cache/cursor-public/cursor-thing/1/skills/cthing",
   "root": "cursor-plugin", "source": "plugin", "harnesses": ["cursor"]},
  {"name": "n1", "description": "", "path": "/h/.claude/plugins/marketplaces/official/plugins/nested/skills/n1/",
   "root": "claude-plugin", "source": "plugin", "harnesses": ["claude"]},
  {"name": "orphan", "description": "Under no plugin", "path": "/h/x/toolbox/skills/orphan",
   "root": "claude-plugin", "source": "plugin", "harnesses": ["claude"]},
  {"name": "built", "description": "", "path": "/h/.claude/plugins/marketplaces/official/builtin/built",
   "root": "claude", "source": "builtin", "harnesses": ["claude"]}
 ]}
"""#

func catalogRunner(_ extra: [([String], CommandResult)] = []) -> ScriptedRunner {
    ScriptedRunner(extra + [
        (["plugins", "catalog", "--json"], json(catalogJSON)),
        (["skills", "--json"], json(catalogInventoryJSON)),
    ])
}

func decodedCatalog() throws -> PluginCatalog {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(PluginCatalog.self, from: Data(catalogJSON.utf8))
}

func decodedInventory() throws -> [Skill] {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(SkillInventory.self, from: Data(catalogInventoryJSON.utf8)).skills
}

@MainActor
@Suite struct PluginCatalogTests {
    // MARK: Decoding

    @Test func decodesTheCatalog() throws {
        let catalog = try decodedCatalog()
        #expect(catalog.harnesses == ["claude", "codex", "cursor"])
        #expect(catalog.actions == ["claude", "codex"])
        #expect(catalog.errors == [.init(harness: "codex", marketplace: "broken", detail: "manifest is not JSON")])
        #expect(catalog.marketplaces.map(\.name) == ["kit", "official", "openai-curated", "cursor-public"])
        #expect(catalog.plugins.count == 9)
        let toolkit = try #require(catalog.plugins.first { $0.ref == "toolkit@kit" })
        #expect(toolkit.displayName == "Tool kit")
        #expect(toolkit.sourcePath == "/h/.quiver/plugins/kit/toolkit")
        // swe's snake_case component names survive the decoder's key conversion.
        #expect(toolkit.components == ["skills": 2, "mcp_servers": 1])
        #expect(toolkit.harnesses["claude"]?.scopes == ["user"])
        #expect(toolkit.drift?["codex"]?.state == .notInstalled)
        #expect(catalog.marketplaces.map(\.isRegistered) == [true, true, false, true])
    }

    @Test func aSavedCatalogReadsBackTheSame() throws {
        // SnapshotFile writes with a plain encoder and reads with a plain decoder.
        let value = try decodedCatalog()
        let data = try JSONEncoder().encode(value)
        #expect(try JSONDecoder().decode(PluginCatalog.self, from: data) == value)
    }

    @Test func anObjectWithNoMarketplacesIsNotAnEmptyCatalog() {
        // Read as empty, every plugin skill would look unowned.
        #expect(throws: DecodingError.self) {
            try JSONDecoder().decode(PluginCatalog.self, from: Data(#"{"catalog": {}, "skills": []}"#.utf8))
        }
    }

    @Test func anUnknownDriftStateStillDecodes() throws {
        let text = catalogJSON.replacingOccurrences(of: #""state": "stale""#, with: #""state": "quarantined""#)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let catalog = try decoder.decode(PluginCatalog.self, from: Data(text.utf8))
        let drawer = try #require(catalog.plugins.first { $0.name == "drawer" })
        #expect(drawer.drift?["claude"]?.state == .unknown)
        #expect(drawer.worstDrift?.state == .unknown)
    }

    @Test func eachHarnessStateHasOneStatus() throws {
        let catalog = try decodedCatalog()
        func status(_ ref: String, _ harness: String) -> PluginHarnessState.Status? {
            catalog.plugins.first { $0.ref == ref }?.harnesses[harness]?.status
        }
        #expect(status("toolkit@kit", "claude") == .enabled)
        #expect(status("drawer@kit", "claude") == .disabled)
        #expect(status("toolkit@kit", "codex") == .available)
        #expect(status("cursor-thing@cursor-public", "cursor") == .cached)
        #expect(status("toolkit@kit", "cursor") == .absent)
        #expect(status("bundle@official", "codex") == .absent)
    }

    @Test func aHubPluginShowsItsWorstGap() throws {
        let catalog = try decodedCatalog()
        let drawer = try #require(catalog.plugins.first { $0.name == "drawer" })
        #expect(drawer.worstDrift?.state == .stale)
        #expect(drawer.worstDrift?.harnesses == ["claude"])
        #expect(drawer.worstDrift?.state.title == "Out of date")
        let toolkit = try #require(catalog.plugins.first { $0.name == "toolkit" })
        #expect(toolkit.worstDrift?.state == .notInstalled)
        #expect(catalog.plugins.first { $0.name == "vercel" }?.worstDrift == nil)
    }

    // MARK: Actions

    @Test func eachStateOffersOnlyWhatCanBeDone() throws {
        let catalog = try decodedCatalog()
        func verbs(_ ref: String, _ harness: String) throws -> [PluginAction.Verb] {
            catalog.actions(for: try #require(catalog.plugins.first { $0.ref == ref }), in: harness).map(\.verb)
        }
        #expect(try verbs("toolkit@kit", "claude") == [.disable, .uninstall])
        #expect(try verbs("drawer@kit", "claude") == [.enable, .uninstall])
        #expect(try verbs("toolkit@kit", "codex") == [.install])
        // Not listed by any marketplace codex has: an install would fail there.
        #expect(try verbs("bundle@official", "codex").isEmpty)
        // Its marketplace is gone, but what is installed can still go.
        #expect(try verbs("canva@openai-curated", "codex") == [.disable, .uninstall])
        // Cursor is read-only.
        #expect(try verbs("cursor-thing@cursor-public", "cursor").isEmpty)
    }

    @Test func theConfirmationShowsTheExactCommandOrEdit() {
        #expect(PluginAction(verb: .install, ref: "tool@kit", harness: "claude").command
            == "claude plugin install tool@kit")
        #expect(PluginAction(verb: .disable, ref: "tool@kit", harness: "claude").command
            == "claude plugin disable tool@kit")
        #expect(PluginAction(verb: .install, ref: "tool@kit", harness: "codex").command == "codex plugin add tool@kit")
        #expect(PluginAction(verb: .uninstall, ref: "tool@kit", harness: "codex").command
            == "codex plugin remove tool@kit")
        let enable = PluginAction(verb: .enable, ref: "tool@kit", harness: "codex")
        #expect(enable.isEdit)
        #expect(enable.command == #"set [plugins."tool@kit"] enabled = true in ~/.codex/config.toml"#)
        #expect(PluginAction(verb: .disable, ref: "tool@kit", harness: "codex").command
            == #"set [plugins."tool@kit"] enabled = false in ~/.codex/config.toml"#)
        #expect(enable.question == "Enable tool@kit in Codex?")
        #expect(PluginAction(verb: .uninstall, ref: "tool@kit", harness: "claude").menuTitle
            == "Uninstall from Claude Code")
    }

    @Test func addMarketplaceArgvAndDecodesAPartialFailure() async throws {
        // swe exits 1 when any harness failed but the JSON still lands.
        let replyJSON = #"""
        {"schema": 1, "data": {"source": "owner/repo", "results": [
         {"harness": "claude", "ok": true, "detail": "added"},
         {"harness": "codex", "ok": false, "detail": "not on PATH"}]}}
        """#
        let runner = catalogRunner([(["plugins", "marketplace", "add"],
                                     CommandResult(status: 1,
                                                   stdout: Data(replyJSON.utf8),
                                                   stderr: ""))])
        let swe = SweClient(runner: runner)
        let reply = try await swe.addMarketplace(source: "owner/repo",
                                                 harnesses: ["claude", "codex"])
        #expect(runner.calls.last ==
            ["plugins", "marketplace", "add", "owner/repo",
             "--harness=claude,codex", "--json"])
        #expect(reply.results.map { $0.ok } == [true, false])
        #expect(reply.results[1].detail == "not on PATH")
    }

    @Test func anActionAsksSweByRefThenReadsTheCatalogAgain() async throws {
        let runner = catalogRunner([(["plugins", "install"], json(#"""
            {"action": "install", "harness": "codex", "ref": "linter@official", "changed": true, "detail": null,
             "ran": [["codex", "plugin", "add", "linter@official"]], "edited": null, "output": "ok",
             "state": {"installed": true, "enabled": true, "version": "1"}}
            """#))])
        let model = PluginCatalogModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        await model.run(PluginAction(verb: .install, ref: "linter@official", harness: "codex"), swe: swe)
        #expect(runner.calls == [
            ["plugins", "catalog", "--json"],
            ["plugins", "install", "linter@official", "--harness=codex", "--json"],
            ["plugins", "catalog", "--json"],
        ])
        #expect(model.lastResult == "Installed linter@official in Codex")
        #expect(model.actionError == nil)
        #expect(model.running == nil)
    }

    @Test func aNoOpSaysWhyNothingChanged() async throws {
        let runner = catalogRunner([(["plugins", "disable"], json(#"""
            {"action": "disable", "harness": "claude", "ref": "toolkit@kit", "changed": false,
             "detail": "already disabled", "ran": [], "edited": null, "output": null,
             "state": {"installed": true, "enabled": false, "version": "1.2.0"}}
            """#))])
        let model = PluginCatalogModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        await model.run(PluginAction(verb: .disable, ref: "toolkit@kit", harness: "claude"), swe: swe)
        #expect(model.lastResult == "toolkit@kit in Claude Code: already disabled")
    }

    @Test func aFailedActionShowsSwesReasonAndStillReloads() async throws {
        let runner = catalogRunner([(["plugins", "uninstall"], CommandResult(
            status: 1, stdout: Data(), stderr: "codex plugin remove canva@openai-curated failed with exit status 1:\nno such plugin\n"))])
        let model = PluginCatalogModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        await model.run(PluginAction(verb: .uninstall, ref: "canva@openai-curated", harness: "codex"), swe: swe)
        #expect(model.actionError == "codex plugin remove canva@openai-curated failed with exit status 1:\nno such plugin")
        #expect(model.lastResult == nil)
        #expect(runner.calls.filter { $0 == ["plugins", "catalog", "--json"] }.count == 2)
    }

    @Test func anActionTheCatalogDoesNotOfferIsNeverSent() async throws {
        let runner = catalogRunner()
        let model = PluginCatalogModel()
        let swe = SweClient(runner: runner)
        // Nothing on show yet: nothing to act on.
        await model.run(PluginAction(verb: .install, ref: "linter@official", harness: "codex"), swe: swe)
        await model.load(swe: swe)
        for action in [
            PluginAction(verb: .install, ref: "cursor-thing@cursor-public", harness: "cursor"),
            PluginAction(verb: .install, ref: "toolkit@kit", harness: "claude"),
            PluginAction(verb: .enable, ref: "toolkit@kit", harness: "claude"),
            PluginAction(verb: .install, ref: "missing@kit", harness: "claude"),
        ] {
            await model.run(action, swe: swe)
        }
        #expect(!runner.calls.contains { $0.count > 1 && $0[0] == "plugins" && $0[1] != "catalog" })
    }

    @Test func aReloadForASweNoLongerPickedIsDropped() async throws {
        let model = PluginCatalogModel()
        let other = SweClient(runner: catalogRunner())
        let runner = HookRunner(catalogRunner([(["plugins", "install"], json(#"""
            {"action": "install", "harness": "codex", "ref": "linter@official", "changed": true,
             "state": {"installed": true, "enabled": true, "version": "1"}}
            """#))]))
        // Settings switches to another swe while the install runs.
        runner.onCall = { args in
            if args.starts(with: ["plugins", "install"]) { await model.loadIfStale(swe: other, for: "/other/swe") }
        }
        let swe = SweClient(runner: runner)
        await model.load(swe: swe, for: "/this/swe")
        await model.run(PluginAction(verb: .install, ref: "linter@official", harness: "codex"), swe: swe, for: "/this/swe")
        // This swe's catalog is not read again over the other's.
        #expect(runner.calls.filter { $0 == ["plugins", "catalog", "--json"] }.count == 1)
        #expect(model.lastResult == "Installed linter@official in Codex")
    }

    // MARK: The Explore tab

    /// Source id to its marketplaces' ids, and each marketplace's rows.
    private func tree(_ sources: [ExploreSource]) -> [String: [String: [String]]] {
        Dictionary(uniqueKeysWithValues: sources.map { source in
            (source.id, Dictionary(uniqueKeysWithValues: source.marketplaces.map { ($0.id, $0.plugins.map(\.row.id)) }))
        })
    }

    @Test func eachMarketplaceSitsUnderItsSourceAndEachRowShowsThatSourcesChips() async throws {
        let model = PluginCatalogModel()
        await model.load(swe: SweClient(runner: catalogRunner()))
        let index = try #require(model.index)
        #expect(index.total == 9)
        let sources = index.sources
        #expect(sources.map(\.id) == ["local", "claude", "codex", "cursor", "unregistered"])
        #expect(sources.map(\.title) == ["Local", "Claude Code", "Codex", "Cursor", "Not registered"])
        #expect(tree(sources) == [
            "local": ["local/kit": ["drawer@kit", "toolkit@kit"]],
            // Registered in two harnesses: under each, with the plugins
            // that harness lists or has.
            "claude": ["claude/official": ["bundle@official", "linter@official", "nested@official", "vercel@official"]],
            "codex": ["codex/official": ["linter@official", "tool@official", "vercel@official"]],
            "cursor": ["cursor/cursor-public": ["cursor-thing@cursor-public"]],
            "unregistered": ["unregistered/openai-curated": ["canva@openai-curated"]],
        ])
        // Every plugin is somewhere.
        #expect(PluginExploreIndex.distinctPlugins(in: sources) == index.total)

        func chips(_ source: String, _ ref: String) -> [String]? {
            sources.first { $0.id == source }?.marketplaces.flatMap(\.plugins).first { $0.row.id == ref }?.harnesses
        }
        // The hub shows both harnesses swe acts in, a missing one included.
        #expect(chips("local", "toolkit@kit") == ["claude", "codex"])
        #expect(chips("claude", "vercel@official") == ["claude"])
        #expect(chips("codex", "vercel@official") == ["codex"])
        #expect(chips("cursor", "cursor-thing@cursor-public") == ["cursor"])
        // Unregistered: wherever it is installed.
        #expect(chips("unregistered", "canva@openai-curated") == ["codex"])
        // Two rows of one plugin keep apart.
        #expect(sources[1].marketplaces[0].plugins.last?.id == "claude/vercel@official")

        // What the catalog calls each marketplace's source; a folder is local.
        #expect(sources.flatMap(\.marketplaces).map(\.tag) == ["local", "github", "git", "cache", nil])
        #expect(sources[3].note == "Read only")
        #expect(sources[1].note == "Marketplaces Claude Code registers")
        let rows = try #require(index.rows.first { $0.id == "drawer@kit" })
        #expect(rows.isHub)
    }

    @Test func installedCountsAreForTheSourcesHarness() async throws {
        let model = PluginCatalogModel()
        await model.load(swe: SweClient(runner: catalogRunner()))
        let sources = try #require(model.index).sources
        func market(_ id: String) -> ExploreMarketplace? { sources.flatMap(\.marketplaces).first { $0.id == id } }
        // official: vercel is installed in Claude Code, tool in Codex.
        #expect(market("claude/official")?.installed == 1)
        #expect(market("codex/official")?.installed == 1)
        #expect(market("claude/official")?.countLine(searching: false) == "4 plugins · 1 installed")
        // The hub counts a plugin installed in either harness.
        #expect(market("local/kit")?.installed == 2)
        // Cursor's cached copy is how Cursor has a plugin.
        #expect(market("cursor/cursor-public")?.installed == 1)
        #expect(market("unregistered/openai-curated")?.installed == 1)
        #expect(sources[0].countLine(searching: false) == "1 marketplace · 2 plugins")
        #expect(sources[1].countLine(searching: false) == "1 marketplace · 4 plugins")
    }

    @Test func anOpenedPluginFollowsItsRefAcrossAReload() async throws {
        let runner = catalogRunner()
        let model = PluginCatalogModel()
        await model.load(swe: SweClient(runner: runner))
        let index = try #require(model.index)
        // drawer lives under the hub source; the lookup a detail view
        // makes after a reload finds it by ref.
        #expect(index.entry(for: "drawer@kit")?.row.plugin.version == "2.0.0")
        #expect(index.entry(for: "nobody@kit") == nil)
        // A plugin under two sources resolves to the first in order.
        #expect(index.entry(for: "vercel@official")?.id == "claude/vercel@official")

        // A reload swaps the index wholesale: the ref resolves to the
        // new catalog's entry, not the snapshot a view was holding.
        let bumped = catalogJSON.replacingOccurrences(of: #""version": "2.0.0", "category""#,
                                                    with: #""version": "3.0.0", "category""#)
        runner.set(["plugins", "catalog", "--json"], json(bumped))
        await model.load(swe: SweClient(runner: runner))
        #expect(try #require(model.index).entry(for: "drawer@kit")?.row.plugin.version == "3.0.0")
        // And a ref the reload dropped resolves to nothing, which is
        // what sends the detail back to the list.
        runner.set(["plugins", "catalog", "--json"],
                   json(bumped.replacingOccurrences(of: #""ref": "drawer@kit""#, with: #""ref": "moved@kit""#)))
        await model.load(swe: SweClient(runner: runner))
        #expect(try #require(model.index).entry(for: "drawer@kit") == nil)
    }

    @Test func sourcesComeInOrderAndNoPluginIsLost() {
        func plugin(_ name: String, _ market: String, _ states: [String: PluginHarnessState]) -> CatalogPlugin {
            CatalogPlugin(name: name, marketplace: market, harnesses: states)
        }
        let absent = PluginHarnessState(available: false, installed: false)
        let listed = PluginHarnessState(available: true, installed: false)
        let catalog = PluginCatalog(harnesses: ["claude", "codex"], actions: ["claude", "codex"], marketplaces: [
            PluginMarketplace(name: "zeta", harnesses: ["gemini": .init(sourceType: "url")],
                              plugins: [plugin("g", "zeta", ["gemini": listed])]),
            PluginMarketplace(name: "beta", harnesses: ["codex": .init(sourceType: "directory"),
                                                        "claude": .init(sourceType: "github")],
                              // No harness it is registered in lists it.
                              plugins: [plugin("lost", "beta", ["claude": absent, "codex": absent])]),
            PluginMarketplace(name: "alpha", harnesses: ["claude": .init(sourceType: "github")],
                              plugins: [plugin("a", "alpha", ["claude": listed])]),
            PluginMarketplace(name: "old", harnesses: [:],
                              plugins: [plugin("o", "old", ["codex": PluginHarnessState(available: false, installed: true)])]),
            // The hub comes first wherever the catalog lists it.
            PluginMarketplace(name: "hub", hub: true, harnesses: [:],
                              plugins: [plugin("h", "hub", ["claude": listed, "codex": absent])]),
        ])
        let index = PluginExploreIndex(catalog: catalog, skills: [])
        // swe's harnesses in its order, then any other by name, then the
        // unregistered. Codex lists nothing of beta, so it has no source.
        #expect(index.sources.map(\.id) == ["local", "claude", "gemini", "unregistered"])
        // Within a source, the catalog's order.
        #expect(index.sources[1].marketplaces.map(\.id) == ["claude/beta", "claude/alpha"])
        #expect(tree(index.sources)["claude"]?["claude/beta"] == ["lost@beta"])
        // swe cannot act in it.
        #expect(index.sources[2].note == "Read only")
        #expect(PluginExploreIndex.distinctPlugins(in: index.sources) == index.total)
        // A hub registered nowhere is still a folder.
        #expect(index.sources[0].marketplaces[0].tag == "local")
        #expect(index.sources[0].marketplaces[0].plugins[0].harnesses == ["claude", "codex"])
    }

    @Test func aCopyInstalledWhereTheMarketplaceIsNoLongerRegisteredKeepsItsControls() {
        let catalog = PluginCatalog(harnesses: ["claude", "codex"], actions: ["claude", "codex"], marketplaces: [
            // Claude Code still registers it; Codex dropped it, keeping the install.
            PluginMarketplace(name: "split", harnesses: ["claude": .init(sourceType: "github")], plugins: [
                CatalogPlugin(name: "p", marketplace: "split", harnesses: [
                    "claude": PluginHarnessState(available: true, installed: false),
                    "codex": PluginHarnessState(available: false, installed: true, enabled: true)]),
                // Only the Codex copy is left of this one.
                CatalogPlugin(name: "q", marketplace: "split", harnesses: [
                    "claude": PluginHarnessState(available: false, installed: false),
                    "codex": PluginHarnessState(available: false, installed: true, enabled: false)]),
            ]),
        ])
        let index = PluginExploreIndex(catalog: catalog, skills: [])
        #expect(tree(index.sources) == [
            "claude": ["claude/split": ["p@split"]],
            "unregistered": ["unregistered/split": ["p@split", "q@split"]],
        ])
        let orphan = index.sources[1].marketplaces[0]
        #expect(orphan.plugins.map(\.harnesses) == [["codex"], ["codex"]])
        #expect(orphan.installed == 2)
        // The Codex copy can still be turned off or removed from here.
        #expect(catalog.actions(for: orphan.plugins[0].row.plugin, in: "codex").map(\.verb) == [.disable, .uninstall])
        #expect(index.sources(harness: "codex").flatMap(\.marketplaces).flatMap(\.plugins).map(\.id)
                == ["unregistered/p@split", "unregistered/q@split"])
        #expect(PluginExploreIndex.distinctPlugins(in: index.sources) == index.total)
    }

    @Test func filtersByHarnessAndText() async throws {
        let model = PluginCatalogModel()
        await model.load(swe: SweClient(runner: catalogRunner()))
        model.skills = try decodedInventory()
        await model.indexed()
        let index = try #require(model.index)
        func refs(_ harness: String? = nil, _ query: String = "") -> [String] {
            index.sources(harness: harness, query: query).flatMap(\.marketplaces).flatMap(\.plugins).map(\.id)
        }
        #expect(refs() == index.sources.flatMap(\.marketplaces).flatMap(\.plugins).map(\.id))
        // A harness keeps the rows showing its chip: the hub's, its own
        // source's and the unregistered ones installed there.
        #expect(refs("codex") == ["local/drawer@kit", "local/toolkit@kit", "codex/linter@official",
                                  "codex/tool@official", "codex/vercel@official", "unregistered/canva@openai-curated"])
        #expect(refs("cursor") == ["cursor/cursor-thing@cursor-public"])
        // A skill's name finds the plugin that ships it, an installed copy's too.
        #expect(refs(nil, " BRAND ") == ["unregistered/canva@openai-curated"])
        #expect(refs(nil, "release") == ["claude/vercel@official", "codex/vercel@official"])
        #expect(refs(nil, "tool kit") == ["local/toolkit@kit"])
        #expect(refs("codex", "deploy") == ["codex/vercel@official"])
        // A marketplace's name finds its plugins, so typing what a ref says works too.
        #expect(refs(nil, "canva@openai") == ["unregistered/canva@openai-curated"])
        #expect(refs(nil, "nothing like it").isEmpty)

        let found = index.sources(query: "deploy")
        #expect(found.map(\.id) == ["claude", "codex"])
        // One plugin under two sources is one plugin in the total.
        #expect(PluginExploreIndex.distinctPlugins(in: found) == 1)
        #expect(found[0].countLine(searching: true) == "1 match")
        #expect(found[0].marketplaces[0].countLine(searching: true) == "1 match")
        // Counts are of what the filter kept.
        #expect(index.sources(harness: "codex").first { $0.id == "local" }?.marketplaces[0].installed == 2)
    }

    @Test func marketplacesStartClosedAndOpenPerToggle() async throws {
        let model = PluginCatalogModel()
        await model.load(swe: SweClient(runner: catalogRunner()))
        #expect(model.index != nil)
        // Every marketplace starts closed, hub or not.
        #expect(!model.isOpen("local/kit"))
        #expect(!model.isOpen("claude/official"))
        model.toggle("local/kit")
        #expect(model.isOpen("local/kit"))
        #expect(model.expanded == ["local/kit"])
        model.toggle("local/kit")
        #expect(!model.isOpen("local/kit"))
    }

    @Test func openStateIsSessionOnly() {
        let model = PluginCatalogModel()
        model.toggle("claude/official")
        #expect(model.isOpen("claude/official"))
        // Nothing is kept between runs: a fresh model starts closed.
        let next = PluginCatalogModel()
        #expect(!next.isOpen("claude/official"))
        #expect(next.expanded.isEmpty)
    }

    @Test func aSearchOpensWhatItFindsAndLeavesTheOwnStateAlone() async throws {
        let model = PluginCatalogModel()
        await model.load(swe: SweClient(runner: catalogRunner()))
        model.toggle("local/kit")

        model.query = "deploy"
        #expect(model.isSearching)
        // Matching marketplaces show open without touching the user's set.
        #expect(model.isOpen("claude/official"))
        #expect(model.isOpen("codex/official"))
        #expect(model.expanded == ["local/kit"])
        // Closing one during the search lasts only as long as the search.
        model.toggle("claude/official")
        #expect(!model.isOpen("claude/official"))
        model.query = "deploy "
        #expect(!model.isOpen("claude/official"))
        model.query = "deplo"
        #expect(model.isOpen("claude/official"))

        // Cleared: what was open before the search is open again.
        model.query = ""
        #expect(!model.isSearching)
        #expect(model.isOpen("local/kit"))
        #expect(!model.isOpen("claude/official"))
        #expect(!model.isOpen("codex/official"))
    }

    @Test func aPluginListsItsSkillsAndEveryInstalledCopyOncePerName() async throws {
        let model = PluginCatalogModel()
        await model.load(swe: SweClient(runner: catalogRunner()))
        model.skills = try decodedInventory()
        await model.indexed()
        let index = try #require(model.index)
        let vercel = try #require(index.rows.first { $0.id == "vercel@official" })
        #expect(vercel.skills.map(\.name) == ["ai-sdk", "eve", "release"])
        #expect(vercel.skills[0].paths == ["/h/.claude/plugins/cache/official/vercel/0.50.0/skills/ai-sdk",
                                           "/h/.claude/plugins/cache/official/vercel/0.50.0/skills/ai-sdk/upstream"])
        let toolkit = try #require(index.rows.first { $0.id == "toolkit@kit" })
        #expect(toolkit.skills.map(\.name) == ["hammer", "saw"])
        #expect(toolkit.skills[0].paths.count == 2)
    }

    @Test func theLastCatalogShowsAtOnceOnTheNextLaunch() async throws {
        let store = MemorySnapshotStore<Snapshot<PluginCatalog>>()
        let runner = catalogRunner()
        let first = PluginCatalogModel(snapshots: store)
        await first.loadIfStale(swe: SweClient(runner: runner), for: "/bin/swe")
        #expect(await store.saves == 1)

        let next = PluginCatalogModel(snapshots: store)
        #expect(next.lookup == .pending)
        let before = next.revision
        await next.restore()
        #expect(next.index == first.index)
        #expect(next.revision > before)
        #expect(next.lookup.catalog?.plugins.count == 9)
        // Still fresh: opening the tab asks swe nothing.
        await next.loadIfStale(swe: SweClient(runner: runner), for: "/bin/swe")
        #expect(runner.calls.filter { $0 == ["plugins", "catalog", "--json"] }.count == 1)
    }

    @Test func aCatalogSweCannotReadIsUnavailableNotEmpty() async {
        let runner = ScriptedRunner([(["plugins", "catalog", "--json"], CommandResult(
            status: 1, stdout: Data(), stderr: "Unknown subcommand: catalog\n"))])
        let model = PluginCatalogModel()
        await model.load(swe: SweClient(runner: runner))
        #expect(model.lookup == .unavailable("Unknown subcommand: catalog"))
        #expect(model.index == nil)
    }

    @Test func withoutAnInventoryEachPluginListsTheCatalogsOwnSkills() async throws {
        let model = PluginCatalogModel()
        await model.load(swe: SweClient(runner: catalogRunner()))
        let vercel = try #require(model.index?.rows.first { $0.id == "vercel@official" })
        #expect(vercel.skills.map(\.name) == ["ai-sdk", "eve"])
        // The inventory arrives: the rows are worked out again, the catalog
        // is the same, so the skills model is not told of a new one.
        let revision = model.revision
        model.skills = try decodedInventory()
        // Built off the main actor: the last rows stay on show meanwhile.
        #expect(model.index?.rows.first { $0.id == "vercel@official" }?.skills.map(\.name) == ["ai-sdk", "eve"])
        await model.indexed()
        #expect(model.index?.rows.first { $0.id == "vercel@official" }?.skills.map(\.name) == ["ai-sdk", "eve", "release"])
        #expect(model.revision == revision)
    }

    @Test func anIndexOvertakenByNewerSkillsIsDropped() async throws {
        let model = PluginCatalogModel()
        await model.load(swe: SweClient(runner: catalogRunner()))
        let inventory = try decodedInventory()
        // Two inventories in a row, before either index lands: only the
        // second is shown, however the two builds finish.
        model.skills = inventory
        model.skills = Array(inventory.prefix(1))
        await model.indexed()
        #expect(model.index?.skills == Array(inventory.prefix(1)))
    }

    // MARK: Every skill shown exactly once

    @Test func placementKeepsStandaloneSkillsAndMovesOwnedPluginSkills() throws {
        let placement = SkillPlacement(skills: try decodedInventory(), catalog: try decodedCatalog())
        #expect(placement.standalone.map(\.name) == ["alpha", "only-codex", "orphan", "built"])
        #expect(placement.inPlugins == 8)
        #expect(placement.byPlugin["toolkit@kit"]?.count == 2)
        #expect(placement.byPlugin["vercel@official"]?.map(\.name) == ["ai-sdk", "ai-sdk", "release"])
        // The deepest folder wins: the marketplace-root plugin does not take it.
        #expect(placement.byPlugin["nested@official"]?.map(\.name) == ["n1"])
        #expect(placement.byPlugin["bundle@official"] == nil)
        #expect(placement.byPlugin["canva@openai-curated"]?.count == 1)
        #expect(placement.byPlugin["cursor-thing@cursor-public"]?.count == 1)
        // /h/x/tool is not a folder holding /h/x/toolbox.
        #expect(placement.byPlugin["tool@official"] == nil)
    }

    @Test func withoutACatalogEverySkillStaysOnTheSkillsPage() throws {
        let skills = try decodedInventory()
        let placement = SkillPlacement(skills: skills, catalog: nil)
        #expect(placement.standalone == skills)
        #expect(placement.inPlugins == 0)
    }

    @Test func aSkillFoundThroughASymlinkStillFindsItsPlugin() throws {
        let root = FileManager.default.temporaryDirectory.appending(path: "quiver-symlink-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let real = root.appending(path: "real/kit/toolkit/skills/hammer")
        try FileManager.default.createDirectory(at: real, withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(at: root.appending(path: "plugins"),
                                                   withDestinationURL: root.appending(path: "real"))
        // The catalog names the plugin through the symlink; swe skills
        // reports the folder it points at.
        let linked = root.appending(path: "plugins/kit/toolkit").path
        let plugin = CatalogPlugin(name: "toolkit", marketplace: "kit", sourcePath: linked,
                                   skills: [CatalogSkill(name: "hammer", path: linked + "/skills/hammer")],
                                   harnesses: ["claude": PluginHarnessState(available: true, installed: true, path: linked)])
        let catalog = PluginCatalog(harnesses: ["claude"], actions: ["claude"], marketplaces: [
            PluginMarketplace(name: "kit", hub: true, harnesses: ["claude": .init()], plugins: [plugin]),
        ])
        let skill = Skill(name: "hammer", path: FolderOwners.realPath(real.path), root: "claude-plugin",
                          source: .plugin, harnesses: ["claude"])
        #expect(skill.path != linked + "/skills/hammer")
        let placement = SkillPlacement(skills: [skill], catalog: catalog)
        // Off the Skills page, and once in the plugin's row, under its name.
        #expect(placement.standalone.isEmpty)
        let shown = placement.skills(of: plugin)
        #expect(shown.map(\.name) == ["hammer"])
        #expect(shown[0].paths == [linked + "/skills/hammer", skill.path])
    }

    @Test func twoPluginsSharingAFolderLeaveItsSkillWhereItWas() {
        let state = PluginHarnessState(available: true, installed: true, enabled: true, path: "/p/shared")
        let catalog = PluginCatalog(harnesses: ["claude"], actions: ["claude"], marketplaces: [
            PluginMarketplace(name: "m", harnesses: ["claude": .init()], plugins: [
                CatalogPlugin(name: "a", marketplace: "m", harnesses: ["claude": state]),
                CatalogPlugin(name: "b", marketplace: "m", harnesses: ["claude": state]),
            ]),
        ])
        let skill = Skill(name: "s", path: "/p/shared/skills/s", root: "claude-plugin", source: .plugin, harnesses: ["claude"])
        let placement = SkillPlacement(skills: [skill], catalog: catalog)
        #expect(placement.standalone == [skill])
    }

    /// The catalog lists a skill under each plugin that ships it. A skill on
    /// the Mac shown on Skills, or in another plugin's row, is left out of
    /// the rest; one not on the Mac stays as a preview.
    @Test func aCatalogEntryForASkillShownElsewhereIsLeftOut() {
        let state = PluginHarnessState(available: true, installed: true, enabled: true, path: "/p/shared")
        let shared = [CatalogSkill(name: "s", path: "/p/shared/skills/s"),
                      CatalogSkill(name: "preview", path: "/p/shared/skills/preview")]
        let a = CatalogPlugin(name: "a", marketplace: "m", skills: shared, harnesses: ["claude": state])
        let b = CatalogPlugin(name: "b", marketplace: "m", skills: shared, harnesses: ["claude": state])
        // c lists a skill that d's installed copy holds.
        let c = CatalogPlugin(name: "c", marketplace: "m", skills: [CatalogSkill(name: "t", path: "/p/d/skills/t")],
                              harnesses: ["claude": PluginHarnessState(available: true, installed: false)])
        let d = CatalogPlugin(name: "d", marketplace: "m", skills: [CatalogSkill(name: "t", path: "/p/d/skills/t")],
                              harnesses: ["claude": PluginHarnessState(available: true, installed: true, path: "/p/d")])
        let catalog = PluginCatalog(harnesses: ["claude"], actions: ["claude"], marketplaces: [
            PluginMarketplace(name: "m", harnesses: ["claude": .init()], plugins: [a, b, c, d]),
        ])
        let skills = [
            Skill(name: "s", path: "/p/shared/skills/s", root: "claude-plugin", source: .plugin, harnesses: ["claude"]),
            Skill(name: "t", path: "/p/d/skills/t", root: "claude-plugin", source: .plugin, harnesses: ["claude"]),
        ]
        // Across both pages, as each lists them.
        let index = PluginExploreIndex(catalog: catalog, skills: skills)
        let onSkills = index.placement.standalone.map(\.path)
        let inRows = index.rows.flatMap { $0.skills.flatMap(\.paths) }
        for skill in skills {
            #expect((onSkills + inRows).filter { $0 == skill.path }.count == 1, "\(skill.path)")
        }
        #expect(onSkills == ["/p/shared/skills/s"])
        #expect(index.rows.first { $0.id == "a@m" }?.skills.map(\.name) == ["preview"])
        #expect(index.rows.first { $0.id == "b@m" }?.skills.map(\.name) == ["preview"])
        #expect(index.rows.first { $0.id == "c@m" }?.skills.isEmpty == true)
        #expect(index.rows.first { $0.id == "d@m" }?.skills.map(\.name) == ["t"])
    }

    // MARK: One change at a time

    @Test func aFixWaitsWhileAnActionRuns() async throws {
        let work = PluginWork()
        let fixes = PluginsModel(work: work)
        let drift = ScriptedRunner([(["find", "plugins", "--json"], json(#"""
            {"sources": 1, "drift": [
              {"id": "codex:stale:canva@openai-curated", "harness": "codex", "marketplace": "openai-curated",
               "plugin": "canva", "ref": "canva@openai-curated", "kind": "stale", "detail": "d",
               "fix": "codex plugin remove canva@openai-curated && codex plugin add canva@openai-curated",
               "runnable": true, "paths": []}]}
            """#))])
        let fixSwe = SweClient(runner: drift)
        await fixes.load(swe: fixSwe)
        guard case .loaded(let report) = fixes.state else { Issue.record("\(fixes.state)"); return }
        let finding = try #require(report.drift.first)

        let model = PluginCatalogModel(work: work)
        let runner = HookRunner(catalogRunner([(["plugins", "uninstall"], json(#"""
            {"action": "uninstall", "harness": "codex", "ref": "canva@openai-curated", "changed": true,
             "state": {"installed": false, "enabled": null, "version": null}}
            """#))]))
        // While the uninstall runs, the Health tab tries its fix and Fix all.
        runner.onCall = { args in
            guard args.starts(with: ["plugins", "uninstall"]) else { return }
            await fixes.fix(finding, swe: fixSwe)
            await fixes.fixAll(confirmed: [finding.id], swe: fixSwe)
        }
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        await model.run(PluginAction(verb: .uninstall, ref: "canva@openai-curated", harness: "codex"), swe: swe)
        #expect(model.lastResult == "Uninstalled canva@openai-curated from Codex")
        #expect(!drift.calls.contains { $0.contains { $0.hasPrefix("--fix=") } })
        #expect(!work.isBusy)

        // And the other way round: an action waits for a fix.
        let held = HookRunner(drift)
        held.onCall = { args in
            guard args.contains(where: { $0.hasPrefix("--fix=") }) else { return }
            await model.run(PluginAction(verb: .disable, ref: "toolkit@kit", harness: "claude"), swe: swe)
        }
        await fixes.fix(finding, swe: SweClient(runner: held))
        #expect(!runner.calls.contains { $0.starts(with: ["plugins", "disable"]) })
        #expect(!work.isBusy)
    }

    /// The user's requirement: moving plugin skills to the Plugins page
    /// loses none. Through both pages' own models, every skill on the Mac
    /// is listed exactly once: on the Skills page, or in one plugin's row.
    @Test func everySkillIsShownExactlyOnceAcrossBothPages() async throws {
        let runner = catalogRunner()
        let swe = SweClient(runner: runner)
        let explore = PluginCatalogModel()
        await explore.load(swe: swe)
        let skillsPage = SkillsModel(swe: swe)
        await skillsPage.load()
        // As the app hands them over: one inventory, one catalog.
        skillsPage.plugins = explore.lookup
        explore.skills = skillsPage.allSkills
        await skillsPage.placed()
        await explore.indexed()

        let inventory = try decodedInventory()
        let counts = shownCounts(inventory: inventory, skillsPage: skillsPage, explore: try #require(explore.index))
        for skill in inventory {
            #expect(counts[skill.path] == 1, "\(skill.path) shown \(counts[skill.path] ?? 0) times")
        }
        #expect(skillsPage.visibleSkills.count + skillsPage.pluginSkillCount == inventory.count)
        #expect(skillsPage.pluginSkillCount == 8)
    }

    /// Opt-in: the same check against this Mac's swe, which is too slow and
    /// machine-specific for every run. Set QUIVER_COVERAGE_SWE to its path.
    @Test(.enabled(if: ProcessInfo.processInfo.environment["QUIVER_COVERAGE_SWE"] != nil))
    func everySkillOnThisMacIsShownExactlyOnce() async throws {
        let path = try #require(ProcessInfo.processInfo.environment["QUIVER_COVERAGE_SWE"])
        let swe = SweClient(runner: ProcessRunner(executable: path))
        let explore = PluginCatalogModel(timeout: .seconds(120))
        await explore.load(swe: swe)
        let skillsPage = SkillsModel(swe: swe)
        await skillsPage.load()
        skillsPage.plugins = explore.lookup
        explore.skills = skillsPage.allSkills
        await skillsPage.placed()
        await explore.indexed()
        let index = try #require(explore.index, "\(explore.state)")
        let inventory = skillsPage.allSkills
        let counts = shownCounts(inventory: inventory, skillsPage: skillsPage, explore: index)
        let wrong = inventory.filter { counts[$0.path] != 1 }
        #expect(wrong.isEmpty, "\(wrong.map(\.path))")
        let pluginSource = inventory.filter { $0.source == .plugin }
        let unplaced = skillsPage.listedSkills.filter { $0.source == .plugin }
        #expect(unplaced.isEmpty, "plugin skills no catalog plugin claims: \(unplaced.map(\.path))")
        print("""
            coverage: \(inventory.count) skills = \(skillsPage.listedSkills.count) on Skills \
            + \(skillsPage.pluginSkillCount) in plugins; \(pluginSource.count) with source plugin, \
            \(pluginSource.count - unplaced.count) mapped; \(index.total) plugins in \
            \(index.catalog.marketplaces.count) marketplaces
            """)
    }

    /// How many times each skill path is shown: on the Skills page's list
    /// with no filter set, and in every plugin row on the Explore tab.
    private func shownCounts(inventory: [Skill], skillsPage: SkillsModel,
                             explore: PluginExploreIndex) -> [String: Int] {
        let wanted = Set(inventory.map(\.path))
        var counts: [String: Int] = [:]
        for skill in skillsPage.visibleSkills where wanted.contains(skill.path) { counts[skill.path, default: 0] += 1 }
        for row in explore.rows {
            for path in row.skills.flatMap(\.paths) where wanted.contains(path) { counts[path, default: 0] += 1 }
        }
        return counts
    }
}

/// A ScriptedRunner that also runs a hook before answering, outside any lock.
final class HookRunner: CommandRunning, @unchecked Sendable {
    private let inner: ScriptedRunner
    var onCall: (@Sendable ([String]) async -> Void)?

    init(_ inner: ScriptedRunner) { self.inner = inner }

    var calls: [[String]] { inner.calls }

    func run(_ arguments: [String]) async throws -> CommandResult {
        await onCall?(arguments)
        return try await inner.run(arguments)
    }
}
