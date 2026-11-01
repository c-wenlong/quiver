"""Curated built-in slash commands per harness, for the phone's "/" menu.

No file on disk says what a TUI has built in, so this is a hand-kept
table. Last checked 2026-10-24 against:

- Claude Code: code.claude.com/docs/en/commands (the built-in table) and
  code.claude.com/docs/en/headless ("Command support differs in -p
  mode").
- Codex: codex-rs/tui/src/slash_command.rs and slash_dispatch.rs on
  github.com/openai/codex main, and the descriptions in the installed
  codex-cli 0.163.0 binary.
- opencode: opencode.ai/docs/tui and opencode.ai/docs/server.

Only commands that make sense from a phone are listed. Left out on
purpose: anything whose bare form opens a picker, dialog or full-screen
view with no argument to skip it (/resume, /permissions, /memory,
/status, /skills, /help, /theme...), anything that leaves or replaces
the TUI the supervisor owns (/exit, /background, /branch, /stop,
/editor), sign-in and terminal setup (/login, /logout, /ide,
/terminal-setup), and commands that only copy to the host's clipboard
or open a browser there. ``argument_required`` marks a command whose
bare form opens a picker but whose argument skips it (``/model
sonnet``): the phone should insist on the argument.

``channels`` says where each one runs (see ``slash.for_channel``):
``tmux`` is the real TUI; ``headless`` is ``claude -p --resume``, which
runs only the built-ins the headless docs list.
"""

from __future__ import annotations

from typing import NamedTuple

TMUX = frozenset({"tmux"})
TMUX_AND_PRINT = frozenset({"tmux", "headless"})


class Builtin(NamedTuple):
    name: str
    description: str
    argument_hint: str = ""
    argument_required: bool = False
    channels: frozenset = TMUX


BUILTINS: dict[str, tuple[Builtin, ...]] = {
    "claude": (
        Builtin("clear", "Start a new conversation with empty context",
                "[name]"),
        Builtin("compact",
                "Summarize the conversation so far to free up context",
                "[instructions]"),
        Builtin("config", "Set a setting, for example thinking=false",
                "<key=value>", True, TMUX_AND_PRINT),
        Builtin("context", "Show current context usage", "[all]"),
        Builtin("effort", "Set the effort level", "<low|medium|high|xhigh|max|auto>",
                True, TMUX_AND_PRINT),
        Builtin("fast", "Turn fast mode on or off", "[on|off]",
                channels=TMUX_AND_PRINT),
        Builtin("goal",
                "Keep working across turns until a condition is met",
                "[condition|clear]"),
        Builtin("init", "Initialize the project with a CLAUDE.md guide"),
        Builtin("mcp", "Reconnect, enable or disable an MCP server",
                "<reconnect|enable|disable> <server|all>", True,
                TMUX_AND_PRINT),
        Builtin("model", "Switch the model, for example sonnet",
                "<model>", True, TMUX_AND_PRINT),
        Builtin("output-style", "List output styles or switch to one",
                "[style]", channels=TMUX_AND_PRINT),
        Builtin("plan", "Enter plan mode, optionally starting on a task",
                "[description]"),
        Builtin("recap", "One-line summary of the session so far"),
        Builtin("reload-plugins", "Reload active plugins without restarting",
                channels=TMUX_AND_PRINT),
        Builtin("reload-skills",
                "Re-scan skill and command folders for changes"),
        Builtin("rename", "Rename the session; no name generates one",
                "[name]", channels=TMUX_AND_PRINT),
        Builtin("subtask",
                "Spawn a forked subagent that works on a task in the background",
                "<task>"),
        # Bundled skills: prompts Claude runs, so print mode expands them
        # like any other user-invoked skill.
        Builtin("code-review", "Review the current diff, a PR, branch or path for bugs",
                "[low|medium|high|xhigh|max] [pr#|branch|path]",
                channels=TMUX_AND_PRINT),
        Builtin("debug", "Turn on debug logging and troubleshoot this session",
                "[description]", channels=TMUX_AND_PRINT),
        Builtin("loop", "Run a prompt repeatedly while the session stays open",
                "[interval] [prompt]", channels=TMUX_AND_PRINT),
        Builtin("security-review",
                "Review the branch's changes for security vulnerabilities"),
        Builtin("simplify", "Review changed code for cleanups and apply them",
                "[target]", channels=TMUX_AND_PRINT),
    ),
    "codex": (
        Builtin("compact",
                "Summarize conversation to prevent hitting the context limit"),
        Builtin("goal", "Set or view the goal for a long-running task",
                "[goal]"),
        Builtin("init", "Create an AGENTS.md file with instructions for Codex"),
        Builtin("model", "Choose the model (and reasoning effort)",
                "<model>", True),
        Builtin("plan", "Switch to Plan mode"),
        Builtin("review", "Review current changes with custom instructions",
                "<instructions>", True),
    ),
    "opencode": (
        Builtin("compact", "Compact the current session"),
        Builtin("init", "Create or update the AGENTS.md file"),
        Builtin("redo", "Redo a previously undone message"),
        Builtin("undo",
                "Undo the last message, its responses and file changes"),
    ),
}

# The channels on which a harness expands custom commands and skills
# typed as message text. Claude Code's print mode expands them ("User-
# invoked skills and custom commands work", headless docs). Codex's core
# expands ``$skill`` mentions in plain text, so its app-server does too.
# opencode's server takes a prompt literally: a command needs its own
# ``POST /session/:id/command`` call, which the hub doesn't make.
CUSTOM_CHANNELS: dict[str, frozenset] = {
    "claude": frozenset({"tmux", "headless"}),
    "codex": frozenset({"tmux", "appserver"}),
    "opencode": frozenset({"tmux"}),
}


def launch_op_ok(harness: str, op: str) -> bool:
    """Whether a fresh tmux pane of ``harness`` takes ``/<op> <value>``.

    Only a verified built-in whose argument skips its picker counts:
    typing ``/model sonnet`` into a TUI that does not know the command
    sends it to the model as prompt text, which is worse than refusing
    the pick. ``/compact``-style arg-less commands are never a launch
    control either way.
    """
    return any(
        item.name == op and item.argument_required
        and "tmux" in item.channels
        for item in BUILTINS.get(harness, ())
    )
