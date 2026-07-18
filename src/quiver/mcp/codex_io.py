"""TOML I/O adapter for codex's ``[mcp_servers.*]`` region.

Codex stores MCP server definitions in TOML, not JSON. This module provides a
pair of helpers — :func:`load_codex_servers` and :func:`save_codex_servers` — so
the generic :func:`quiver.mcp.cli.cmd_sync` loop can treat codex as one more peer
in the any-to-any sync graph instead of a special-cased CLI.

The loader returns ``{server_name: canonical-shape dict}`` (the same shape
:func:`quiver.mcp.cli.get_tool_servers` produces for JSON tools). The saver
takes the same shape back and writes only the contiguous ``[mcp_servers*]``
region of ``~/.codex/config.toml``, leaving every other section (model,
features, plugins, projects, tui, desktop, comments, blank lines) byte-for-byte
intact.

Byte-level preservation:
  * The TOML text is split into ``(pre, region, post)`` around the first
    ``[mcp_servers*]`` header that begins the block.
  * The saver concatenates ``pre`` + ``render_mcp_region(new_servers)`` + ``post``
    with no extra newlines inserted, so existing whitespace survives intact.
  * Writes are atomic (``.tmp`` + ``rename``) so a mid-write crash cannot
    corrupt the user's ``config.toml``.

Canonical server shape used (matches the rest of quiver's MCP layer):
    {
        "command": str,         # optional
        "args": list[str],      # optional
        "env": dict[str, str],  # optional
        "url": str,             # optional
        "headers": dict[str, str],  # optional
        ... plus any codex-specific scalar/bool/numeric fields like
            ``startup_timeout_sec`` (preserved via Standard handler).
    }
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib

CODEX_CONFIG = Path.home() / ".codex" / "config.toml"


# ── TOML region splitter ─────────────────────────────────────────────

# Recognise [mcp_servers], [mcp_servers.<name>], [mcp_servers.<name>.<subkey>],
# plus dotted-quoted forms: [mcp_servers."name"] or [mcp_servers.'name'].
_MCP_HEADER_RE = re.compile(
    r"^\[mcp_servers(?:\.[A-Za-z0-9_-]+|\"[^\\\"]*\"|'[^']*')*\]"
)


def split_codex_toml(text: str) -> tuple[str, str, str]:
    """Split TOML into ``(pre, mcp_region, post)`` preserving everything else."""
    if not text:
        return "", "", ""

    lines = text.splitlines(keepends=True)
    n = len(lines)

    start = None
    for i, ln in enumerate(lines):
        stripped = ln.lstrip()
        if stripped.startswith("[mcp_servers"):
            # Match only if this is a real [mcp_servers*] header
            # (next char is '.' or ']')
            rest = stripped[len("[mcp_servers"):]
            if not rest or rest[0] in (".", "]"):
                start = i
                break

    if start is None:
        return text, "", ""

    end = n
    for j in range(start + 1, n):
        stripped = lines[j].lstrip()
        if stripped.startswith("["):
            # Stop when we hit the next non-mcp_servers section.
            if not (stripped.startswith("[mcp_servers")
                    and (len(stripped) == len("[mcp_servers")
                         or stripped[len("[mcp_servers")] in (".",))):
                end = j
                break

    pre = "".join(lines[:start])
    region = "".join(lines[start:end])
    post = "".join(lines[end:])
    return pre, region, post


def parse_codex_mcp_region(region: str) -> dict[str, dict]:
    """Parse the ``[mcp_servers*]`` TOML region into ``{name: dict}``.

    Returns deep copies of the parsed mappings so callers can freely mutate
    nested ``env`` / ``headers`` / codex-specific dicts without affecting the
    ``tomllib``-owned internals.
    """
    if not region.strip():
        return {}
    try:
        parsed = tomllib.loads(region)
    except tomllib.TOMLDecodeError:
        return {}
    mcp = parsed.get("mcp_servers", {})
    if not isinstance(mcp, dict):
        return {}
    out: dict[str, dict] = {}
    for name, cfg in mcp.items():
        if isinstance(cfg, dict):
            out[name] = copy.deepcopy(cfg)
    return out


# ── TOML writer primitives ───────────────────────────────────────────


def _toml_key(k: str) -> str:
    """Quote keys that are not TOML bare-keys (ASCII letters/digits/underscore/hyphen only)."""
    return k if re.match(r"^[A-Za-z0-9_-]+$", k) else json.dumps(k, ensure_ascii=False)


def _toml_value(v) -> str:
    """Emit a TOML scalar / array / inline-table literal.

    ``bool`` is checked BEFORE ``int`` so Python's ``bool`` (which is an int
    subclass) is rendered as ``true`` / ``false``, not ``1`` / ``0``.
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[ " + ", ".join(_toml_value(item) for item in v) + " ]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        return "{ " + ", ".join(
            f"{_toml_key(k)} = {_toml_value(val)}" for k, val in v.items()
        ) + " }"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    raise TypeError(f"Unsupported TOML value type: {type(v).__name__}")


def render_codex_server(name: str, canonical: dict) -> str:
    """Render one ``[mcp_servers.<name>]`` TOML block from a canonical-shape dict.

    ``<name>`` is run through ``_toml_key`` so server names that contain dots,
    quotes, or spaces produce a single quoted section header
    (e.g. ``[mcp_servers.\"weird.name\"]``) rather than being parsed by TOML as
    a nested sub-table path.
    """
    safe_name = _toml_key(name)
    lines: list[str] = [f"[mcp_servers.{safe_name}]"]
    nested: list[tuple[str, dict]] = []

    for key, val in canonical.items():
        if isinstance(val, dict):
            # Standard-shape env/headers + any other top-level mapping → nested table.
            nested.append((key, val))
            continue
        if isinstance(val, (str, list, bool, int, float)):
            lines.append(f"{_toml_key(key)} = {_toml_value(val)}")

    for sub_name, sub_dict in nested:
        lines.append("")
        lines.append(f"[mcp_servers.{safe_name}.{_toml_key(sub_name)}]")
        for k, v in sub_dict.items():
            lines.append(f"{_toml_key(k)} = {_toml_value(v)}")

    return "\n".join(lines) + "\n"


def render_mcp_region(servers: dict[str, dict]) -> str:
    """Render the contiguous ``[mcp_servers*]`` TOML region.

    If ``servers`` is empty and the file previously had no ``[mcp_servers*]``
    block, returns an empty string. If ``servers`` is empty but the file had a
    block, ``apply_merges`` is responsible for emitting the stub heading.
    """
    if not servers:
        return ""
    blocks: list[str] = []
    for i, (_name, _cfg) in enumerate(sorted(servers.items())):
        if i > 0:
            blocks.append("\n")
        blocks.append(render_codex_server(_name, _cfg))
    return "".join(blocks)


def apply_merges(codex_text: str, to_write: dict[str, dict]) -> str:
    """Replace the ``[mcp_servers*]`` region with ``to_write``, preserving pre/post.

    Returns the entire new codex.toml content. Pre and post bytes outside the
    block are concatenated as-is — no extra newlines inserted, no quote-style
    changes.
    """
    pre, original_region, post = split_codex_toml(codex_text)
    had_mcp_region = bool(original_region.strip())
    new_region = render_mcp_region(to_write)

    parts: list[str] = []
    if pre:
        parts.append(pre)
        if not pre.endswith("\n"):
            parts.append("\n")

    if new_region:
        parts.append(new_region)
    elif had_mcp_region:
        # Original file had a [mcp_servers*] block but everything got pruned
        # by the caller. Emit a stub heading so future syncs keep working.
        parts.append("[mcp_servers]\n")

    if post:
        parts.append(post)

    return "".join(parts)


# ── public IO surface ────────────────────────────────────────────────


def load_codex_servers(path: Path = CODEX_CONFIG) -> dict[str, dict]:
    """Return ``{server_name: dict}`` from codex.toml's ``[mcp_servers*]`` region.

    Compatible with :func:`quiver.mcp.cli.get_tool_servers` for JSON tools: the
    same flat dict shape keyed by server name.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text()
    except OSError:
        return {}
    _, region, _ = split_codex_toml(text)
    return parse_codex_mcp_region(region)


def save_codex_servers(servers: dict[str, dict], path: Path = CODEX_CONFIG) -> bool:
    """Write ``servers`` into codex.toml's ``[mcp_servers*]`` region.

    Preserves every byte outside that contiguous region. Returns ``True`` if the
    file content actually changed.

    Caller is expected to pre-filter ``servers`` to exactly the set they want
    on disk (including any ``--prune`` decisions). This keeps the engine
    cohesive — it just merges; the CLI makes policy.
    """
    text = path.read_text() if path.exists() else ""
    final = apply_merges(text, servers)
    if final == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tmp + rename (mirrors quiver.mcp.cli.save_json).
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(final)
    tmp.rename(path)
    return True
