import Foundation
import Testing
@testable import SWECore

@MainActor
@Suite struct HarnessEditTests {
    func loaded(_ rules: [(prefix: [String], reply: CommandResult)]) async -> (HarnessesModel, ScriptedRunner) {
        let runner = ScriptedRunner(rules + [(["list"], ok(harnessesJSON))])
        let model = HarnessesModel(swe: SweClient(runner: runner))
        await model.load()
        return (model, runner)
    }

    func codex(_ model: HarnessesModel) throws -> Harness {
        try #require(model.all.first { $0.name == "codex" })
    }

    @Test func aDraftStartsFromTheHarnessAndSendsNothingUntilChanged() async throws {
        let (model, _) = await loaded([])
        let harness = try codex(model)
        let draft = HarnessEdit(harness)
        #expect(draft.displayName == "")
        #expect(draft.command == "codex")
        #expect(draft.description == "OpenAI's")
        #expect(draft.version == "0.144.1")
        #expect(draft.aliases == "cx")
        #expect(draft.tags == "agentic")
        #expect(draft.notes == "")
        #expect(draft.flags(from: HarnessEdit(harness)).isEmpty)
    }

    @Test func onlyChangedFieldsBecomeFlagsWithListsNormalised() async throws {
        let (model, _) = await loaded([])
        let original = HarnessEdit(try codex(model))
        var draft = original
        draft.description = "  OpenAI's CLI  "
        draft.tags = "agentic, coding,, agentic "
        draft.aliases = " cx "
        draft.version = ""
        draft.notes = "Pinned for CI"
        draft.displayName = " Codex CLI "
        #expect(draft.flags(from: original) == [
            "--display-name=Codex CLI", "--description=OpenAI's CLI", "--version=", "--tags=agentic,coding", "--notes=Pinned for CI",
        ])
    }

    @Test func anEmptyCommandIsAProblem() async throws {
        let (model, _) = await loaded([])
        var draft = HarnessEdit(try codex(model))
        #expect(draft.commandProblem == nil)
        draft.command = "  "
        #expect(draft.commandProblem != nil)
    }

    @Test func savingRunsSweEditThenReloads() async throws {
        let reply = #"{"name": "codex", "changes": ["description"], "harness": {"name": "codex", "command": "codex", "description": "New", "version": "0.144.1", "aliases": ["cx"], "tags": ["agentic"], "state": "active", "sessions_100d": 60, "rate_limit": null, "installed": true, "archived": null, "notes": null}}"#
        let (model, runner) = await loaded([(["edit"], ok(reply))])
        let harness = try codex(model)
        var draft = HarnessEdit(harness)
        draft.description = "New"
        let error = await model.save(harness, draft)
        #expect(error == nil)
        #expect(runner.calls.filter { $0.first == "edit" } == [["edit", "codex", "--description=New", "--json"]])
        #expect(runner.calls.last == ["list", "--scope=all", "--json"])
    }

    @Test func savingWithNoChangesRunsNothing() async throws {
        let (model, runner) = await loaded([])
        let harness = try codex(model)
        #expect(await model.save(harness, HarnessEdit(harness)) == nil)
        #expect(runner.calls.count == 1)
    }

    @Test func aRefusedEditComesBackAsTheMessage() async throws {
        let (model, _) = await loaded([
            (["edit"], CommandResult(status: 1, stdout: Data(), stderr: "  Alias 'cc' already used by 'claude'")),
        ])
        let harness = try codex(model)
        var draft = HarnessEdit(harness)
        draft.aliases = "cc"
        let error = await model.save(harness, draft)
        #expect(error?.contains("already used by 'claude'") == true)
        #expect(model.all.count == 4)
    }

    @Test func aDisplayNameIsTheTitleAndTheBrandNameIsTheFallback() async throws {
        let json = harnessesJSON.replacingOccurrences(of: #""name": "codex","#, with: #""name": "codex", "display_name": "Codex CLI","#)
            .replacingOccurrences(of: #""name": "opencode","#, with: #""name": "opencode", "display_name": "  ","#)
        let runner = ScriptedRunner([(["list"], ok(json))])
        let model = HarnessesModel(swe: SweClient(runner: runner))
        await model.load()
        func title(_ name: String) -> String? { model.all.first { $0.name == name }?.title }
        #expect(title("codex") == "Codex CLI")
        // Blank or missing: the brand name SWE already knows, else the id capitalised.
        #expect(title("opencode") == "OpenCode")
        #expect(title("goose") == "Goose")
        #expect(HarnessEdit(try #require(model.all.first { $0.name == "codex" })).displayName == "Codex CLI")
    }

    @Test func notesDecodeWhenSweSendsThem() async throws {
        let json = harnessesJSON.replacingOccurrences(of: #""archived": null}"#, with: #""archived": null, "notes": "hi"}"#)
        let runner = ScriptedRunner([(["list"], ok(json))])
        let model = HarnessesModel(swe: SweClient(runner: runner))
        await model.load()
        #expect(model.all.first { $0.name == "codex" }?.notes == "hi")
        #expect(model.all.first { $0.name == "goose" }?.notes == nil)
    }
}
