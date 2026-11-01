import Foundation
import Testing
@testable import SWECore

/// The by-event layout of the Hooks page, built from rows shaped like this
/// Mac's real `swe hooks list`: five Claude Code hooks of your own, herdr's
/// and Agent Relay's scripts beside them, and a hub folder nothing declares.
@Suite struct HookTimelineTests {
    static let claudeEvents = ["PreToolUse", "PostToolUse", "PermissionRequest", "UserPromptSubmit",
                               "SessionStart", "SessionEnd", "Stop", "SubagentStart"]

    static func hook(_ script: String, harness: String = "claude", tier: Hook.Tier = .harness,
                     state: LinkState = .linked, _ declared: [(String, String)],
                     summary: String = "") -> Hook {
        Hook(script: script, harness: harness, source: "/h/\(harness)/\(script)",
             destination: "/h/.\(harness)/hooks/\(script)", state: state,
             declared: declared.map {
                 HookDeclaration(event: $0.0, matcher: $0.1, timeout: 10,
                                 command: script, settings: "/h/.\(harness)/settings.json")
             },
             tier: tier, enabled: true, eventsSupported: claudeEvents, summary: summary)
    }

    static let hooks: [Hook] = [
        hook("no-sleep-poll.py", [("PreToolUse", "Bash")]),
        hook("pr-behind-main.py", [("PreToolUse", "Bash")]),
        hook("prose-guard.py", [("Stop", "")]),
        hook("session-size-warn.py", [("PreToolUse", "")]),
        hook("task-model-nudge.py", [("PreToolUse", "Task")]),
        hook("herdr-agent-state.sh", tier: .foreign, state: LinkState(rawValue: "foreign"),
             [("SessionStart", "*")]),
        hook("claude-hook.sh", tier: .foreign, state: LinkState(rawValue: "foreign"),
             [("SessionStart", ""), ("Stop", "")]),
        hook("codex-hook.sh", harness: "codex", tier: .foreign, state: LinkState(rawValue: "foreign"),
             [("Stop", "")]),
        hook("command-guard", harness: "command-guard", state: .skipped, []),
    ]

    @Test func lanesFollowTheSessionOrderAndHoldOnlyYoursByDefault() {
        let timeline = HookTimeline(harness: "claude", hooks: Self.hooks, includeForeign: false)
        #expect(timeline.lanes.map(\.event) == ["PreToolUse", "Stop"])
        #expect(timeline.foreignCount == 2)
        let pre = timeline.lanes[0].entries
        // No matcher first, then by matcher.
        #expect(pre.map(\.hook.script) == ["session-size-warn.py", "no-sleep-poll.py",
                                           "pr-behind-main.py", "task-model-nudge.py"])
        #expect(pre.map(\.matcherLabel) == ["any tool", "Bash", "Bash", "Task"])
        #expect(timeline.lanes[1].entries.first?.matcherLabel == nil)
        #expect(timeline.lanes[0].caption == "before a tool runs")
    }

    @Test func quietEventsAreTheSupportedOnesNothingFiresOn() {
        let timeline = HookTimeline(harness: "claude", hooks: Self.hooks, includeForeign: false)
        #expect(timeline.quietEvents == ["SessionStart", "UserPromptSubmit", "PermissionRequest",
                                         "PostToolUse", "SubagentStart", "SessionEnd"])
    }

    @Test func otherInstallersHooksJoinTheirLanesAfterYours() {
        let timeline = HookTimeline(harness: "claude", hooks: Self.hooks, includeForeign: true)
        #expect(timeline.lanes.map(\.event) == ["SessionStart", "PreToolUse", "Stop"])
        let start = timeline.lanes[0].entries
        // "*" on SessionStart gates nothing, so it shows no matcher.
        #expect(start.map(\.matcherLabel) == [nil, nil])
        #expect(start.allSatisfy { $0.isForeign })
        let stop = timeline.lanes[2].entries
        #expect(stop.map(\.hook.script) == ["prose-guard.py", "claude-hook.sh"])
        #expect(!timeline.quietEvents.contains("SessionStart"))
    }

    @Test func aScriptDeclaredTwiceOnOneEventIsOneEntry() {
        let twice = [Self.hook("cursor-hook.sh", harness: "cursor", tier: .foreign,
                               [("stop", ""), ("stop", "")])]
        let timeline = HookTimeline(harness: "cursor", hooks: twice + twice, includeForeign: true)
        #expect(timeline.lanes.map { $0.entries.count } == [1])
    }

    @Test func cursorsLowercaseEventsSortWithTheRest() {
        #expect(HookEvents.sorted(["stop", "beforeShellExecution", "sessionStart", "zzCustom"])
                == ["sessionStart", "beforeShellExecution", "stop", "zzCustom"])
        #expect(HookEvents.caption(for: "preToolUse") == "before a tool runs")
        #expect(HookEvents.caption(for: "zzCustom") == nil)
    }

    @Test func tabsPutHarnessesWithYourHooksFirst() {
        #expect(HookTimeline.harnesses(in: Self.hooks) == ["claude", "codex"])
    }

    @Test func notRunningIsYourUndeclaredScriptsOnly() {
        let foreignIdle = Self.hook("state", harness: "cursor", tier: .foreign, [])
        #expect(HookTimeline.notRunning(in: Self.hooks + [foreignIdle]).map(\.script) == ["command-guard"])
    }

    @Test func shortSummaryDropsTheEventPrefix() {
        #expect(HookTimeline.shortSummary("PreToolUse hook (Bash): block synchronous sleep-polling of PR/CI state.")
                == "Block synchronous sleep-polling of PR/CI state.")
        #expect(HookTimeline.shortSummary("Stop hook: block replies that break the rules.")
                == "Block replies that break the rules.")
        #expect(HookTimeline.shortSummary("Warns when the disk fills.") == "Warns when the disk fills.")
        #expect(HookTimeline.shortSummary("") == "")
    }

    @Test func aListRowCarriesItsSummary() throws {
        let row = #"{"name": "a.py", "harness": "claude", "source_path": "/a", "dest_path": "/b", "link_status": "linked", "declared": [], "summary": "Stop hook: hi."}"#
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let hook = try decoder.decode(Hook.self, from: Data(row.utf8))
        #expect(hook.summary == "Stop hook: hi.")
    }
}
