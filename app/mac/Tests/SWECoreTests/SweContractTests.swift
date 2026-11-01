import Foundation
import Testing
@testable import SWECore

/// Every `swe ... --json` reply the app decodes, checked against output
/// captured from a real swe run against a throwaway HOME (the made-up
/// `fixturetool` and `oldtool` registry entries, the fixture-market hub
/// marketplace and the fixture session). `swe-find-plugins-fix` and
/// `swe-plugins-action` are built to the shape src/swe emits, because
/// running them needs a harness CLI; `swe-list-usage` carries a rate-limit
/// reading added by hand, since a fake HOME has no providers to poll.
@Suite struct SweContractTests {
    private func client(_ fixtureName: String) throws -> (SweClient, FakeRunner) {
        let runner = FakeRunner(.init(status: 0, stdout: try fixture(fixtureName), stderr: ""))
        return (SweClient(runner: runner), runner)
    }

    @Test func listScopeAllDecodesArchivedAndOptionalFields() async throws {
        let (swe, _) = try client("swe-list-scope-all")
        let all = try await swe.allHarnesses()
        #expect(all.map(\.name) == ["claude", "codex", "fixturetool", "oldtool"])

        let claude = try #require(all.first)
        #expect(claude.state == .starred)
        #expect(claude.displayName == "Claude Code")
        #expect(claude.installed == true)
        #expect(claude.notes == "Fixture notes for the contract test")
        #expect(claude.primaryColor == "#D97757")
        #expect(claude.sessions100d == 1)

        let fixture = try #require(all.first { $0.name == "fixturetool" })
        #expect(fixture.installed == false)
        #expect(fixture.sessions100d == nil)
        #expect(fixture.archived == nil)

        let old = try #require(all.last)
        #expect(old.state == .archived)
        #expect(old.archived == .init(reason: "Switched to fixturetool",
                                      archivedAt: "2026-01-02T03:04:05", usage: "trial"))
    }

    @Test func listUsageDecodesWindowsOnDemandAndExtras() async throws {
        let (swe, _) = try client("swe-list-usage")
        let claude = try #require(await swe.harnesses(withUsage: true).first)
        let limit = try #require(claude.rateLimit)
        #expect(limit.status == .ok)
        #expect(limit.usedPercent == 62)
        #expect(limit.planType == "teams")
        #expect(limit.windowSeconds == 86400)
        #expect(limit.fetchedAt != nil)
        #expect(limit.extraUsageUSD == 12.5)
        #expect(limit.windows?.map(\.window) == ["1d", "7d"])
        #expect(limit.allWindows.count == 2)
        let onDemand = try #require(limit.onDemand)
        #expect(onDemand.state == "unlimited")
        #expect(onDemand.scope == "team-member")
        #expect(onDemand.usedUSD == 0)
        #expect(onDemand.limitUSD == nil)
    }

    @Test func sessionsDecodeRowsAndFailures() async throws {
        let (swe, _) = try client("swe-session")
        let list = try await swe.sessions(days: nil, limit: 5)
        #expect(list.failures == [])
        let session = try #require(list.sessions.first)
        #expect(session.id == "fixture-session")
        #expect(session.harness == "claude")
        #expect(session.agent == "Claude Code")
        #expect(session.cwd == "/tmp/quiver-fixture")
        #expect(session.title == "first fixture message")
        #expect(session.titleSource == nil)
        #expect(session.status == "done")
        #expect(session.resume?.argv == ["claude", "--resume", "fixture-session"])
        #expect(session.resume?.cwd == "/tmp/quiver-fixture")
    }

    @Test func skillsDecodeRootsAndSkills() async throws {
        let (swe, _) = try client("swe-skills")
        let inventory = try await swe.skills()
        #expect(inventory.sharedDir == "/tmp/quiver-fixture-home/.swe/skills")
        let claude = try #require(inventory.harnesses.first)
        #expect(claude.name == "claude")
        #expect(claude.state == "linked")
        #expect(claude.archived == false)
        let codex = try #require(inventory.harnesses.last)
        #expect(codex.state == "create")
        let skill = try #require(inventory.skills.first)
        #expect(skill.name == "fixture-skill")
        #expect(skill.root == "shared")
        #expect(skill.source == .shared)
        #expect(skill.harnesses == ["claude"])
    }

    @Test func skillsLinkRepliesDecode() async throws {
        let (swe, _) = try client("swe-skills-link")
        try await swe.link(SkillRoot(name: "claude", path: "/tmp/quiver-fixture-home/.claude/skills",
                                   state: "create"))
        let (swe2, _) = try client("swe-skills-unlink")
        try await swe2.unlink(SkillRoot(name: "claude", path: "/tmp/quiver-fixture-home/.claude/skills",
                                      state: "linked"))
    }

    @Test func mcpListDecodesServersWithoutHealth() async throws {
        let (swe, _) = try client("swe-mcp-list")
        let inventory = try await swe.mcpServers(health: false)
        #expect(inventory.harnesses == ["claude", "claude-desktop", "codex"])
        let stdio = try #require(inventory.servers.first { $0.name == "fixture-stdio" })
        #expect(stdio.transport == "stdio")
        #expect(stdio.command == "fixture-mcp")
        #expect(stdio.args == ["serve", "--port", "9999"])
        #expect(stdio.envKeys == ["FIXTURE_KEY", "SECRET_TOKEN"])
        #expect(stdio.harnesses == ["claude", "codex"])
        #expect(stdio.conflict == true)
        #expect(stdio.variants == [["claude"], ["codex"]])
        #expect(stdio.health == nil)
        #expect(stdio.detail == nil)
        #expect(stdio.secretsMissing == ["SECRET_TOKEN"])
        let http = try #require(inventory.servers.first { $0.name == "fixture-http" })
        #expect(http.transport == "http")
        #expect(http.url == "https://fixture.example/mcp")
        #expect(http.headerKeys == ["Authorization"])
        #expect(http.secretsMissing == [])
        #expect(http.inHub)
        let hubOnly = try #require(inventory.servers.first { $0.name == "fixture-hub-only" })
        #expect(hubOnly.inHub)
        #expect(hubOnly.harnesses == [])
        #expect(hubOnly.command == "hub-only-bin")
        #expect(hubOnly.envKeys == ["HUB_TOKEN"])
        #expect(hubOnly.secretsMissing == ["HUB_TOKEN"])
        #expect(stdio.inHub)
    }

    @Test func mcpStatusAddsHealthAndDetail() async throws {
        let (swe, _) = try client("swe-mcp-status")
        let inventory = try await swe.mcpServers(health: true)
        let stdio = try #require(inventory.servers.first { $0.name == "fixture-stdio" })
        #expect(stdio.health == "error")
        #expect(stdio.detail == "'fixture-mcp' not in PATH")
    }

    @Test func pluginCatalogDecodesMarketplacesAndStates() async throws {
        let (swe, _) = try client("swe-plugins-catalog")
        let catalog = try await swe.pluginCatalog()
        #expect(catalog.harnesses == ["claude", "codex"])
        #expect(catalog.actions == ["claude", "codex"])
        #expect(catalog.errors == [])

        let market = try #require(catalog.marketplaces.first)
        #expect(market.name == "fixture-market")
        #expect(market.hub == true)
        #expect(market.path == "/tmp/quiver-fixture-home/.swe/plugins/fixture-market")

        let plugin = try #require(market.plugins.first)
        #expect(plugin.name == "fixture-plugin")
        #expect(plugin.ref == "fixture-plugin@fixture-market")
        #expect(plugin.version == "0.1.0")
        #expect(plugin.author == "Fixture")
        #expect(plugin.homepage == "https://fixture.example/plugin")
        #expect(plugin.components == ["skills": 1])
        #expect(plugin.skills.map(\.name) == ["fixture-plugin-skill"])
        #expect(plugin.harnesses["claude"]?.installed == false)
        #expect(plugin.drift?["claude"]?.state == .unregistered)
        #expect(plugin.drift?["claude"]?.id == "claude:unregistered:/tmp/quiver-fixture-home/.swe/plugins/fixture-market")
    }

    @Test func pluginActionReplyDecodes() async throws {
        let (swe, _) = try client("swe-plugins-action")
        let result = try await swe.run(PluginAction(verb: .install,
                                                  ref: "fixture-plugin@fixture-market",
                                                  harness: "claude"))
        #expect(result.action == "install")
        #expect(result.changed == true)
        #expect(result.detail == nil)
    }

    @Test func pluginDriftDecodesFindings() async throws {
        let (swe, _) = try client("swe-find-plugins")
        let report = try await swe.pluginDrift()
        #expect(report.sources == 1)
        #expect(report.drift.count == 2)
        let finding = try #require(report.drift.first)
        #expect(finding.id == "claude:unregistered:/tmp/quiver-fixture-home/.swe/plugins/fixture-market")
        #expect(finding.harness == "claude")
        #expect(finding.marketplace == "fixture-market")
        #expect(finding.plugin == nil)
        #expect(finding.kind == "unregistered")
        #expect(finding.runnable == true)
        #expect(finding.paths == [])
    }

    @Test func pluginFixReplyDecodes() async throws {
        let (swe, _) = try client("swe-find-plugins-fix")
        try await swe.fixPluginDrift(id: "claude:unregistered:/tmp/quiver-fixture-home/.swe/plugins/fixture-market")
    }

    @Test func doctorDecodesFindings() async throws {
        let (swe, _) = try client("swe-doctor")
        let report = try await swe.doctor()
        #expect(report.ok == false)
        #expect(report.findings.count == 4)
        let off = report.findings[2]
        #expect(off.check == "off-path")
        #expect(off.severity == .error)
        #expect(off.fix == "swe install fixturetool")
        #expect(off.harness == "fixturetool")
        #expect(report.findings[0].harness == nil)
        let linked = try #require(report.findings.last)
        #expect(linked.action == .`init`)
    }

    @Test func initCheckDecodesLinkStates() async throws {
        let (swe, _) = try client("swe-init-check")
        let check = try await swe.initCheck()
        #expect(check.agentsFile == "/tmp/quiver-fixture-home/.swe/AGENTS.md")
        #expect(check.agentsFileExists == true)
        let claude = try #require(check.harnesses.first)
        #expect(claude.instructions?.state == .linked)
        #expect(claude.skills?.state == .linked)
        let codex = try #require(check.harnesses.last)
        #expect(codex.instructions?.state == .create)
        #expect(codex.skills?.state == .create)
    }

    @Test func starReplyDecodes() async throws {
        let (swe, runner) = try client("swe-harness-star")
        let change = try await swe.toggleStar("fixturetool")
        #expect(change == HarnessStateChange(name: "fixturetool", state: .starred))
        #expect(runner.calls == [["harness", "star", "fixturetool", "--json"]])
    }

    @Test func archiveAndRestoreRepliesDecode() async throws {
        let (swe, _) = try client("swe-harness-archive")
        let change = try await swe.archive("fixturetool", reason: "contract fixture")
        #expect(change.state == .archived)

        let (restoring, runner) = try client("swe-harness-restore")
        let restored = try await restoring.restore("fixturetool")
        #expect(restored.state == .active)
        #expect(runner.calls.last == ["harness", "archive", "fixturetool", "--json"])
    }

    /// Captured from `swe mcp sync claude codex --only=fixture-server
    /// --no-interactive --json` against a throwaway HOME.
    @Test func mcpSyncReplyDecodesThroughTheClient() async throws {
        let (swe, runner) = try client("swe-mcp-sync")
        let server = McpServer(name: "fixture-server", harnesses: ["claude"])
        let report = try await swe.mcpSync(server: server, from: "claude", to: ["codex"],
                                           overwrite: false, dryRun: false)
        #expect(report.source == "claude")
        #expect(report.dryRun == false)
        #expect(report.selected == ["fixture-server"])
        #expect(report.targets == [McpSyncReport.Target(name: "codex", added: ["fixture-server"])])
        #expect(runner.calls.last?.prefix(4) == ["mcp", "sync", "claude", "codex"])
    }

    @Test func editReplyDecodesTheChangedHarness() async throws {
        let (swe, _) = try client("swe-edit")
        let result = try await swe.editHarness("codex", flags: ["--notes=fixture-edit"])
        #expect(result.name == "codex")
        #expect(result.changes == ["notes"])
        #expect(result.harness.notes == "fixture-edit")
        #expect(result.harness.displayName == "Codex")
    }

    /// Captured from `swe hooks list --json` against a throwaway HOME. The
    /// row shape since #787: `name`, `source_path`, `dest_path`,
    /// `link_status`, declarations naming their `file` with no command.
    @Test func hooksListDecodesStatesAndDeclarations() async throws {
        let (swe, runner) = try client("swe-hooks")
        let inventory = try await swe.hooksList()
        #expect(runner.calls.last == ["hooks", "list", "--json"])
        #expect(inventory.hooks.count == 7)
        let beacon = try #require(inventory.hooks.first)
        #expect(beacon.script == "beacon.py")
        #expect(beacon.harness == "claude")
        #expect(beacon.source == "/tmp/quiver-fixture-home/.swe/hooks/claude/beacon.py")
        #expect(beacon.destination == "/tmp/quiver-fixture-home/.claude/hooks/beacon.py")
        #expect(beacon.state == .linked)
        #expect(beacon.tier == .harness)
        #expect(beacon.enabled == true)
        #expect(beacon.eventsSupported?.contains("PreToolUse") == true)
        #expect(beacon.isUndeclared)

        let guard_ = try #require(inventory.hooks.first { $0.script == "prose-guard.py" })
        let declared = try #require(guard_.declared.first)
        #expect(declared.label == "PreToolUse:Bash")
        #expect(declared.timeout == nil)
        #expect(declared.command == "")
        #expect(declared.settings == "/tmp/quiver-fixture-home/.claude/settings.json")

        let block = try #require(inventory.hooks.first { $0.script == "block-rm.py" })
        #expect(block.declared.first?.label == "SessionStart")
        #expect(block.state == .create)

        let probe = try #require(inventory.hooks.first { $0.script == "probe.py" })
        #expect(probe.state == .skipped)
        #expect(probe.harness == "fixturetool")
    }

    /// The row shape before #787 (`script`, `source`, `state`, declarations
    /// with `command` and `settings`), from an older swe on PATH or a
    /// saved snapshot: it still decodes, and the new fields read nil.
    @Test func hooksListDecodesTheLegacyShape() async throws {
        let (swe, _) = try client("swe-hooks-legacy")
        let inventory = try await swe.hooksList()
        #expect(inventory.hooks.count == 7)
        let beacon = try #require(inventory.hooks.first)
        #expect(beacon.script == "beacon.py")
        #expect(beacon.harness == "claude")
        #expect(beacon.state == .linked)
        #expect(beacon.tier == nil)
        #expect(beacon.enabled == nil)
        #expect(beacon.eventsSupported == nil)
        #expect(beacon.isUndeclared)

        let guard_ = try #require(inventory.hooks.first { $0.script == "prose-guard.py" })
        let declared = try #require(guard_.declared.first)
        #expect(declared.label == "PreToolUse:Bash")
        #expect(declared.timeout == 10)
        #expect(declared.command == "~/.claude/hooks/prose-guard.py")
        #expect(declared.settings == "/tmp/quiver-fixture-home/.claude/settings.json")

        let block = try #require(inventory.hooks.first { $0.script == "block-rm.py" })
        #expect(block.declared.first?.label == "SessionStart")
        #expect(block.declared.first?.timeout == nil)
        #expect(block.state == .create)

        let probe = try #require(inventory.hooks.first { $0.script == "probe.py" })
        #expect(probe.state == .skipped)
        #expect(probe.detail.contains("no hooks root"))
        let legacy = try #require(inventory.hooks.first { $0.script == "legacy.sh" })
        #expect(legacy.state == .ignored)
    }

    /// `swe hooks check --json` exits 1 while findings remain; the payload
    /// still decodes through `allowFailure`.
    @Test func hooksCheckDecodesThroughItsNonzeroExit() async throws {
        let runner = FakeRunner(.init(status: 1, stdout: try fixture("swe-hooks-check"), stderr: ""))
        let swe = SweClient(runner: runner)
        let report = try await swe.hooksCheck()
        #expect(runner.calls.last == ["hooks", "check", "--json"])
        #expect(report.findings.map(\.kind) == ["declared-missing", "undeclared",
                                                "unlinked", "unlinked", "unlinked"])
        let ghost = try #require(report.findings.first)
        #expect(ghost.harness == "claude")
        #expect(ghost.kindTitle == "Declared, missing")
        #expect(report.findings[1].kindTitle == "Undeclared")
        #expect(report.findings[2].kindTitle == "Unlinked")
    }

    /// Captured from `swe hooks info prose-guard --json`.
    @Test func hooksInfoDecodesPlacementsAndSummary() async throws {
        let (swe, runner) = try client("swe-hooks-info")
        let info = try await swe.hookInfo(name: "prose-guard")
        #expect(runner.calls.last == ["hooks", "info", "prose-guard", "--json"])
        #expect(info.script == "prose-guard")
        let claude = try #require(info.harnesses.first)
        #expect(claude.harness == "claude")
        #expect(claude.state == .linked)
        #expect(claude.source == "/tmp/quiver-fixture-home/.swe/hooks/claude/prose-guard.py")
        #expect(claude.destination == "/tmp/quiver-fixture-home/.claude/hooks/prose-guard.py")
        #expect(claude.summary == "Blocks prompt submissions that ask for edits to prose files.")
        let declared = try #require(claude.declared.first)
        #expect(declared.label == "PreToolUse:Bash")
        #expect(declared.timeout == 10)
        #expect(declared.command == "~/.claude/hooks/prose-guard.py")
        #expect(declared.settings == "/tmp/quiver-fixture-home/.claude/settings.json")
    }

    /// `swe hooks info` before #787 still decodes.
    @Test func hooksInfoDecodesTheLegacyShape() async throws {
        let (swe, _) = try client("swe-hooks-info-legacy")
        let info = try await swe.hookInfo(name: "prose-guard")
        let claude = try #require(info.harnesses.first)
        #expect(claude.state == .linked)
        #expect(claude.destination == "/tmp/quiver-fixture-home/.claude/hooks/prose-guard.py")
        #expect(claude.declared.first?.settings == "/tmp/quiver-fixture-home/.claude/settings.json")
    }

    /// Captured from `swe hooks new claude guard --json`: the script, the
    /// command a declaration runs, and the stanza to paste.
    @Test func hooksNewReplyDecodesTheStanza() async throws {
        let (swe, runner) = try client("swe-hooks-new")
        let reply = try await swe.newHook(harness: "claude", name: "guard")
        #expect(runner.calls.last == ["hooks", "new", "claude", "guard", "--json"])
        #expect(reply.path.hasSuffix(".swe/hooks/claude/guard.py"))
        #expect(reply.command == "~/.claude/hooks/guard.py")
        #expect(reply.settings == ["/tmp/quiver-fixture-home/.claude/settings.json"])
        #expect(reply.stanza.contains("PreToolUse"))
    }

    /// A newer swe's `tier`, `enabled` and `events_supported` decode when
    /// present; an unlisted tier word reads as nil, never a failed row.
    @Test func hooksListDecodesTheFutureMetadata() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: Data(#"""
            {"schema": 1, "data": {"hooks": [
              {"script": "a.py", "harness": "shared",
               "source": "/h/.swe/hooks/shared/a.py",
               "destination": "/h/.claude/hooks/a.py",
               "state": "linked", "detail": "", "declared": [],
               "tier": "shared", "enabled": true,
               "events_supported": ["PreToolUse"]},
              {"script": "b.py", "harness": "claude",
               "source": "/h/.swe/hooks/claude/b.py",
               "destination": "/h/.claude/hooks/b.py",
               "state": "create", "detail": "", "declared": [],
               "tier": "unlisted-word"}
            ]}}
            """#.utf8), stderr: ""))
        let inventory = try await SweClient(runner: runner).hooksList()
        let a = try #require(inventory.hooks.first)
        #expect(a.tier == .shared)
        #expect(a.enabled == true)
        #expect(a.eventsSupported == ["PreToolUse"])
        #expect(inventory.hooks.last?.tier == nil)
    }

    /// Captured from `swe install --json` against a throwaway HOME.
    @Test func installCatalogDecodesMethodsAndRegistration() async throws {
        let (swe, runner) = try client("swe-install")
        let all = try await swe.installCatalog()
        #expect(runner.calls.last == ["install", "--json"])
        let aider = try #require(all.first { $0.name == "aider" })
        #expect(aider.installed == false)
        #expect(aider.registered == false)
        #expect(aider.description == "Git-native pair programmer, commits every edit as it goes")
        #expect(aider.methods.map(\.via) == ["brew", "uv", "nix", "script"])
        #expect(aider.uninstall == nil)
        let claude = try #require(all.first { $0.name == "claude" })
        #expect(claude.uninstall?.command == "brew uninstall --cask claude-code@latest")
        // The audited vendors ship their download page for the sheet's link.
        #expect(all.first { $0.name == "antigravity" }?.website == "https://antigravity.google")
    }
}
