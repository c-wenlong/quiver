"""How a fresh launch starts a harness with its approval checks off.

``POST /v1/sessions``'s ``yolo: true`` asks the new session to run
autonomously: every permission prompt answered yes at launch, so the
agent never waits on an approval. How each harness takes that is
declared in ``harness.json`` as ``capabilities.yolo``:

  supported   false: the harness has no autonomy switch, and the
              launch routes refuse a ``yolo`` for it
  argv        tokens appended to the launch argv (claude's
              ``--dangerously-skip-permissions``, devin's
              ``--permission-mode dangerous``)

``YOLO_FALLBACK`` is the read-only floor for a harness whose registry
entry declares nothing, the same role ``MODEL_FALLBACK``,
``HARNESS_ROOTS`` and ``PLUGIN_FALLBACK`` play: a registry
``capabilities.yolo`` entry's ``argv`` replaces it, and
``supported: false`` switches it off. Every flag here was read off the
harness's own ``--help`` on the owner's Mac on 2026-10-31 (claude,
codex, cursor-agent, devin, opencode, droid, cline, kilo, qwen).
"""

from __future__ import annotations

YOLO_FALLBACK: dict[str, list[str]] = {
    "claude": ["--dangerously-skip-permissions"],
    "codex": ["--dangerously-bypass-approvals-and-sandbox"],
    "cursor": ["--yolo"],
    "devin": ["--permission-mode", "dangerous"],
    "opencode": ["--auto"],
    "droid": ["--auto", "high"],
    "cline": ["--yolo"],
    "kilo": ["--auto"],
    "qwen-code": ["--yolo"],
}


def capability(name: str, reg: dict | None = None) -> list[str] | None:
    """``name``'s yolo argv tokens: the registry's
    ``capabilities.yolo.argv`` over ``YOLO_FALLBACK``, or None when the
    harness has no autonomy switch a launch can set. ``reg`` is the
    loaded registry; None reads it (read-only)."""
    if reg is None:
        from quiver.harness import registry

        try:
            reg = registry.load_registry_readonly()
        except (OSError, ValueError):
            reg = {}
    entry = reg.get(name) if isinstance(reg, dict) else None
    # A hand-edited registry can hold anything here; a malformed
    # declaration is ignored rather than taking the hub down with it.
    caps = (entry if isinstance(entry, dict) else {}).get("capabilities")
    caps = caps.get("yolo") if isinstance(caps, dict) else None
    if isinstance(caps, dict):
        if "supported" in caps and not caps["supported"]:
            return None
        argv = caps.get("argv")
        if isinstance(argv, list) and argv and all(
                isinstance(token, str) and token.strip() for token in argv):
            return [str(token) for token in argv]
    base = YOLO_FALLBACK.get(name)
    return list(base) if base else None


def yolo_error(name: str, yolo: bool | None,
               reg: dict | None = None) -> str:
    """The refusal note when ``name``'s launch can't honour ``yolo``,
    else "" (always "" for no pick)."""
    if not yolo:
        return ""
    if capability(name, reg) is None:
        return (f"{name} launches can't run autonomously: harness.json "
                f"declares no capabilities.yolo for it")
    return ""


def yolo_argv(name: str, reg: dict | None = None) -> list[str]:
    """The argv tokens appended to the binary to start ``name`` with
    approvals off. Raises ValueError when the harness can't take it
    (see ``yolo_error``)."""
    argv = capability(name, reg)
    if argv is None:
        raise ValueError(yolo_error(name, True))
    return argv
