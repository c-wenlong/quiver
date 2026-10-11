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

func catalogRunner(_ extra: [([String], CommandResult)] = []) -> RoutingRunner {
    RoutingRunner(extra + [
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

    @Test func groupsPutTheHubFirstAndTheUnregisteredLast() async throws {
        let model = PluginCatalogModel()
        await model.load(swe: SweClient(runner: catalogRunner()))
        let index = try #require(model.index)
        #expect(index.total == 9)
        let groups = index.groups()
        #expect(groups.map(\.kind) == [.hub, .other, .unregistered])
        #expect(groups[0].rows.map(\.id) == ["drawer@kit", "toolkit@kit"])
        #expect(groups[1].rows.map(\.id) == ["bundle@official", "linter@official", "nested@official",
                                             "tool@official", "vercel@official", "cursor-thing@cursor-public"])
        #expect(groups[2].rows.map(\.id) == ["canva@openai-curated"])
        #expect(ExploreGroup.Kind.unregistered.note != nil)
        #expect(index.otherMarketplaces == ["official", "openai-curated", "cursor-public"])
    }

    @Test func filtersBySourceHarnessAndText() async throws {
        let model = PluginCatalogModel()
        await model.load(swe: SweClient(runner: catalogRunner()))
        model.skills = try decodedInventory()
        let index = try #require(model.index)
        func refs(_ source: ExploreSource = .all, _ harness: String? = nil, _ query: String = "") -> [String] {
            index.groups(source: source, harness: harness, query: query).flatMap(\.rows).map(\.id)
        }
        #expect(refs(.hub) == ["drawer@kit", "toolkit@kit"])
        #expect(refs(.marketplace("openai-curated")) == ["canva@openai-curated"])
        #expect(refs(.all, "cursor") == ["cursor-thing@cursor-public"])
        #expect(refs(.hub, "codex") == ["drawer@kit", "toolkit@kit"])
        // A skill's name finds the plugin that ships it, an installed copy's too.
        #expect(refs(.all, nil, " BRAND ") == ["canva@openai-curated"])
        #expect(refs(.all, nil, "release") == ["vercel@official"])
        #expect(refs(.all, nil, "deploy") == ["vercel@official"])
        #expect(refs(.all, nil, "tool kit") == ["toolkit@kit"])
        #expect(refs(.hub, nil, "deploy").isEmpty)
        // A marketplace's name finds its plugins, so typing what a ref or a
        // chip says works too.
        #expect(refs(.all, nil, "openai-curated") == ["canva@openai-curated"])
        #expect(refs(.all, nil, "canva@openai") == ["canva@openai-curated"])
    }

    @Test func aPluginListsItsSkillsAndEveryInstalledCopyOncePerName() async throws {
        let model = PluginCatalogModel()
        await model.load(swe: SweClient(runner: catalogRunner()))
        model.skills = try decodedInventory()
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
        let runner = RoutingRunner([(["plugins", "catalog", "--json"], CommandResult(
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
        #expect(model.index?.rows.first { $0.id == "vercel@official" }?.skills.map(\.name) == ["ai-sdk", "eve", "release"])
        #expect(model.revision == revision)
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
        let drift = RoutingRunner([(["find", "plugins", "--json"], json(#"""
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

/// A RoutingRunner that also runs a hook before answering, outside any lock.
final class HookRunner: CommandRunning, @unchecked Sendable {
    private let inner: RoutingRunner
    var onCall: (@Sendable ([String]) async -> Void)?

    init(_ inner: RoutingRunner) { self.inner = inner }

    var calls: [[String]] { inner.calls }

    func run(_ arguments: [String]) async throws -> CommandResult {
        await onCall?(arguments)
        return try await inner.run(arguments)
    }
}
