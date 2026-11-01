"""The per-harness hook contract registry, and the normalized payload spec.

Every harness that fires command hooks does it a little differently: the
settings file, the JSON schema, the event names, the stdin payload fields,
and what stdout must look like. This table codifies the default each
harness ships with, the same way ``HARNESS_ROOTS`` and ``PLUGIN_FALLBACK``
codify skills and plugins: code holds fallbacks, and a
``capabilities.hooks`` entry in harness.json overrides or extends them.
``init/hooks.py::hook_root`` reads ``root``; ``hooks/settings.py`` reads
``settings`` and ``schema``; the shared-hook deploy in ``hooks/shared.py``
reads ``shim``, ``events`` and ``event_map``.

Fields a ``capabilities.hooks`` entry may carry beyond the ones
``hook_root`` and ``settings_files`` already honour (``supported``,
``root``, ``settings``):

  contract      another row's key, used as the base this entry overrides
                (``"claude"`` for the common grouped schema, ``"cursor"``
                for the flat one) — how an unknown harness declares which
                language its hooks speak
  events        the native event names, replacing the base row's
  event_map     native -> normalized overrides, merged into the base row's
  shim          whether shared-hook wrappers may deploy (true/false)
  must_print_json   whether stdout must be valid JSON on every path
  timeout_unit  ``"s"`` or ``"ms"`` (gemini counts timeout in ms)

Mechanisms, in descending order of what swe can do with them:

  scripts     JSON-declared command hooks. Linkable into ``root`` and
              shim-able: a generated wrapper translates the payload.
  eventfiles  extensionless executables named by event (cline). The files
              are inventory/CRUD-visible but not shim-able — deployment
              would need one generated file per event name, and cline's
              stdin shape is undocumented.
  plugin      in-process plugins/extensions (opencode, pi, kilo). Not
              deployable; ``plugin_files`` are listed read-only.
  worktree    per-repo files, not a home dir (copilot's .github/hooks).
              List-only: nothing links or deploys there.
  none        no hook mechanism at all.

The normalized payload every shared hook reads (design doc for the
shims; ``shim_runtime.py`` implements it): a JSON object with ``event``
(the native ``hook_event_name`` mapped to Claude-style PascalCase via
``event_map`` — unknown names pass through), ``harness``, ``session_id``,
``transcript_path``, ``cwd``, ``tool_name``, ``tool_input``,
``tool_output``, ``prompt``, ``stop_hook_active``, ``session_source``,
``agent_id``, ``agent_type``, ``agent_transcript_path``,
``last_assistant_message``, ``error``, ``permission_mode``,
``tool_use_id``, ``timestamp`` (set by the shim), and ``raw`` — the
harness's own payload, untouched. The aliases the shim resolves:
cursor's ``conversation_id`` becomes ``session_id`` and
``workspace_roots[0]`` becomes ``cwd``; ``tool_response`` becomes
``tool_output`` (codex and cursor); ``source`` becomes
``session_source``. A shared hook answers with exit 0 to pass, exit 2 +
stderr to block, or a JSON object on stdout — Claude's shape
(``hookSpecificOutput``, ``decision``, ``continue``, ``reason``), which
the shim maps back into the harness's contract, including a mandatory
valid default on any failure path for the contracts that stall without
one (cursor, gemini, qwen, copilot).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path


@dataclass(frozen=True)
class HookContract:
    """How one harness declares, feeds and reads a command hook.

    ``mechanism`` is the coarse capability (see the module docstring);
    ``schema`` is the shape of the settings ``hooks`` map: ``grouped``
    (Claude's ``{Event: [{matcher, hooks: [{type, command}]}]}``) or
    ``flat`` (cursor's ``{event: [{command, timeout, matcher}]}``).
    ``events`` are native spellings; ``event_map`` holds only the
    renames, native -> normalized, so unlisted names pass through.
    ``fields`` maps a normalized payload field to extra native keys the
    shim should try after the field's own name.
    """

    mechanism: str = "none"
    schema: str = ""
    root: Path | None = None
    settings: tuple[Path, ...] = ()
    must_print_json: bool = False
    timeout_unit: str = "s"
    shim: bool = False
    events: tuple[str, ...] = ()
    event_map: dict[str, str] = field(default_factory=dict)
    fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    plugin_files: tuple[str, ...] = ()
    # When set, the settings file names no hooks itself — it points at a
    # managed file through this key (muse's managed_hooks_path).
    managed_hooks_key: str = ""
    note: str = ""

    @property
    def supported(self) -> bool:
        """Whether the harness has any hook mechanism swe understands."""
        return self.mechanism not in ("none",)

    @property
    def linkable(self) -> bool:
        """Whether a hub script can be symlinked into ``root``."""
        return self.mechanism in ("scripts", "eventfiles") and self.root is not None

    @property
    def shim_able(self) -> bool:
        """Whether shared-hook wrappers may deploy to this harness."""
        return self.mechanism == "scripts" and self.shim and self.root is not None

    def normalized_events(self) -> list[str]:
        """The normalized event vocabulary this harness can fire."""
        return [self.event_map.get(e, e) for e in self.events]


# Claude Code's event vocabulary is the normalized one, so its map is
# empty. The other grouped-schema harnesses rename from this list.
_CLAUDE_EVENTS = (
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
    "UserPromptSubmit", "SessionStart", "SessionEnd", "Stop", "StopFailure",
    "SubagentStart", "SubagentStop", "PostCompact", "TeammateIdle",
)

# Codex speaks the same schema and payload plus its own events.
_CODEX_EVENTS = _CLAUDE_EVENTS + (
    "Interrupt", "PreModelRequest", "PostModelRequest", "PreCompact",
)

# Cursor's flat schema and camelCase events; the map covers the events
# with a clear Claude-style equivalent, the rest pass through PascalCase
# as themselves (beforeShellExecution -> "beforeShellExecution" would be
# wrong, so every native name is listed and unmapped ones keep native).
_CURSOR_EVENTS = (
    "preToolUse", "postToolUse", "sessionStart", "sessionEnd",
    "beforeSubmitPrompt", "stop", "afterFileEdit", "beforeShellExecution",
    "afterShellExecution", "beforeMCPExecution", "afterMCPExecution",
    "beforeReadFile", "beforeTabRead", "subagentStart", "subagentStop",
    "afterAgentResponse", "afterAgentThought", "workspaceOpen",
)

_CURSOR_EVENT_MAP = {
    "preToolUse": "PreToolUse",
    "postToolUse": "PostToolUse",
    "sessionStart": "SessionStart",
    "sessionEnd": "SessionEnd",
    "beforeSubmitPrompt": "UserPromptSubmit",
    "stop": "Stop",
    "subagentStart": "SubagentStart",
    "subagentStop": "SubagentStop",
}

_GEMINI_EVENTS = (
    "BeforeAgent", "AfterAgent", "BeforeTool", "AfterTool",
    "BeforeModel", "AfterModel", "BeforeToolSelection", "PreCompress",
    "SessionStart", "SessionEnd", "Notification",
)

_GEMINI_EVENT_MAP = {
    # BeforeAgent fires once per prompt, before the agent loop — the
    # closest Claude event is UserPromptSubmit. AfterAgent is the turn's
    # end, which is Stop. The tool/model/compaction pairs line up
    # one-to-one with the Claude names.
    "BeforeAgent": "UserPromptSubmit",
    "AfterAgent": "Stop",
    "BeforeTool": "PreToolUse",
    "AfterTool": "PostToolUse",
    "BeforeModel": "PreModelRequest",
    "AfterModel": "PostModelRequest",
    "PreCompress": "PreCompact",
}

# Devin's config.json speaks the grouped schema; the only renamed event
# is PostCompaction, which is Claude's PostCompact.
_DEVIN_EVENT_MAP = {"PostCompaction": "PostCompact"}

_DROID_EVENTS = (
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
    "UserPromptSubmit", "SessionStart", "SessionEnd", "Stop", "Notification",
)


CONTRACTS: dict[str, HookContract] = {
    "claude": HookContract(
        mechanism="scripts", schema="grouped",
        root=Path(".claude/hooks"),
        settings=(Path(".claude/settings.json"),
                  Path(".claude/settings.local.json")),
        shim=True, events=_CLAUDE_EVENTS,
        note="exit 2 + stderr blocks; stdout JSON hookSpecificOutput",
    ),
    "codex": HookContract(
        mechanism="scripts", schema="grouped",
        root=Path(".codex/hooks"),
        settings=(Path(".codex/hooks.json"),),
        shim=True, events=_CODEX_EVENTS,
        note="[hooks.state] in config.toml keys commands by trusted_hash: "
             "editing a declared command invalidates trust and re-prompts. "
             "Also needs [features] hooks = true in config.toml.",
    ),
    "cursor": HookContract(
        mechanism="scripts", schema="flat",
        root=Path(".cursor/hooks"),
        settings=(Path(".cursor/hooks.json"),),
        must_print_json=True, shim=True,
        events=_CURSOR_EVENTS, event_map=_CURSOR_EVENT_MAP,
        note="flat schema {version: 1, hooks: {event: [{command}]}}; a "
             "hook that prints no valid JSON stalls the run — pre*/before* "
             "events want {\"permission\": \"allow\"}, the rest "
             "{\"continue\": true}",
    ),
    "gemini": HookContract(
        mechanism="scripts", schema="grouped",
        root=Path(".gemini/hooks"),
        settings=(Path(".gemini/settings.json"),),
        must_print_json=True, shim=True, timeout_unit="ms",
        events=_GEMINI_EVENTS, event_map=_GEMINI_EVENT_MAP,
        note="timeout is in MILLISECONDS; stdout must be JSON only",
    ),
    "qwen": HookContract(
        mechanism="scripts", schema="grouped",
        root=Path(".qwen/hooks"),
        settings=(Path(".qwen/settings.json"),),
        must_print_json=True, shim=True, events=_CLAUDE_EVENTS,
        note="Claude schema; a hook must print {} rather than nothing",
    ),
    "devin": HookContract(
        mechanism="scripts", schema="grouped",
        root=Path(".config/devin/hooks"),
        settings=(Path(".config/devin/config.json"),),
        shim=True,
        events=tuple(e for e in _CLAUDE_EVENTS if e != "PostCompact")
               + ("PostCompaction",),
        event_map=_DEVIN_EVENT_MAP,
        note="PostCompaction is Claude's PostCompact",
    ),
    "droid": HookContract(
        mechanism="scripts", schema="grouped",
        root=Path(".factory/hooks"),
        settings=(Path(".factory/hooks.json"), Path(".factory/settings.json")),
        shim=True, events=_DROID_EVENTS,
        note="~/.factory/hooks.json is preferred when it exists; the "
             "matcher field also accepts commandRegex",
    ),
    "cline": HookContract(
        mechanism="eventfiles",
        root=Path("Documents/Cline/Hooks"),
        events=_CLAUDE_EVENTS,
        note="extensionless executables named by event — no JSON "
             "declaration; deploy skips it (one file per event name "
             "would be needed, and cline's stdin shape is undocumented)",
    ),
    "muse": HookContract(
        mechanism="scripts", schema="grouped",
        settings=(Path(".muse/settings.json"),),
        managed_hooks_key="managed_hooks_path",
        note="its settings file points at an external file via "
             "managed_hooks_path; managed_hooks_env_vars allowlists "
             "which env vars hooks see",
    ),
    "copilot": HookContract(
        mechanism="worktree", schema="flat",
        must_print_json=True,
        note="worktree-scoped .github/hooks, camelCase events — "
             "list-only, out of scope for deploy",
    ),
    "opencode": HookContract(
        mechanism="plugin",
        plugin_files=(".config/opencode/*.js", ".config/opencode/*.ts"),
        note="in-process plugin model — no command hooks to deploy",
    ),
    "pi": HookContract(
        mechanism="plugin",
        plugin_files=(".pi/agent/extensions/*.ts",),
        note="in-process extension model — no command hooks to deploy",
    ),
    "kilo": HookContract(
        mechanism="plugin",
        plugin_files=(".config/kilo/extensions/*.ts",
                      ".config/kilo/extensions/*.js"),
        note="in-process extension model — no command hooks to deploy",
    ),
    "freebuff": HookContract(),
    "artemis": HookContract(),
    "grok": HookContract(),
    "hermes": HookContract(),
}


def _expand(root: str, home: Path) -> Path:
    raw = str(root)
    if raw == "~":
        return home
    if raw.startswith("~/"):
        return home / raw[2:]
    return Path(raw)


def hooks_caps(registry: dict, name: str) -> dict:
    """The ``capabilities.hooks`` dict for ``name``, or {}."""
    entry = registry.get(name)
    caps = ((entry if isinstance(entry, dict) else {})
            .get("capabilities") or {}).get("hooks")
    return caps if isinstance(caps, dict) else {}


def contract_for(registry: dict, name: str, home: Path) -> HookContract:
    """The contract ``name`` hooks obey here: table row + registry overrides.

    A ``capabilities.hooks.contract`` naming another table row re-bases
    the merge, so an unknown harness can declare it speaks Claude's
    schema (``{"hooks": {"supported": true, "contract": "claude",
    "root": "~/.x/hooks"}}``) without duplicating the survey. Scalar
    fields override; ``event_map`` merges; ``supported: false`` yields
    the empty contract, matching ``hook_root``'s UNSUPPORTED answer.
    """
    caps = hooks_caps(registry, name)
    if "supported" in caps and not caps["supported"]:
        return HookContract()
    base = CONTRACTS.get(name)
    archetype = caps.get("contract")
    if isinstance(archetype, str) and archetype in CONTRACTS:
        base = CONTRACTS[archetype]
    if base is None:
        base = HookContract(mechanism="scripts", schema="grouped")
    out = base
    if caps.get("root"):
        out = replace(out, root=_expand(str(caps["root"]), home))
    if "settings" in caps:
        raw = caps["settings"]
        items = raw if isinstance(raw, list) else [raw]
        out = replace(out, settings=tuple(
            _expand(v, home) for v in items if isinstance(v, str)))
    if "events" in caps and isinstance(caps["events"], list):
        out = replace(out, events=tuple(
            str(e) for e in caps["events"] if isinstance(e, str)))
    if isinstance(caps.get("event_map"), dict):
        merged = dict(out.event_map)
        merged.update({str(k): str(v) for k, v in
                       caps["event_map"].items()})
        out = replace(out, event_map=merged)
    if "shim" in caps:
        out = replace(out, shim=bool(caps["shim"]))
    if "must_print_json" in caps:
        out = replace(out, must_print_json=bool(caps["must_print_json"]))
    if isinstance(caps.get("timeout_unit"), str):
        out = replace(out, timeout_unit=caps["timeout_unit"])
    return out


# The normalized output contract: what a shared hook may put on stdout.
# It is Claude's shape — the shims translate it per contract. Field
# meanings: hookSpecificOutput.permissionDecision allow|deny|ask for
# permission-shaped events; decision approve|block and reason for the
# grouped schema's verdict; continue false + stopReason to end the turn.
NORMALIZED_OUTPUT_FIELDS = (
    "hookSpecificOutput", "decision", "continue", "reason", "stopReason",
    "suppressOutput", "systemMessage",
)


def normalized_event(contract: HookContract, native: str) -> str:
    """The PascalCase name ``native`` normalizes to, or itself."""
    return contract.event_map.get(native, native)
