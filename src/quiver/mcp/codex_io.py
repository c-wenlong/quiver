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
from datetime import date, datetime, time
from pathlib import Path

from quiver.paths import atomic_write_text

import tomllib

CODEX_CONFIG = Path.home() / ".codex" / "config.toml"


# ── TOML region splitter ─────────────────────────────────────────────

# Recognise [mcp_servers], [mcp_servers.<name>], [mcp_servers.<name>.<subkey>],
# plus dotted-quoted forms: [mcp_servers."name"] or [mcp_servers.'name'].
_MCP_HEADER_RE = re.compile(
    r"^\[mcp_servers(?:\.[A-Za-z0-9_-]+|\"[^\\\"]*\"|'[^']*')*\]"
)


def _is_mcp_header(stripped_line: str) -> bool:
    """True for a ``[mcp_servers…]`` table header (``.``/``]`` next char)."""
    if not stripped_line.startswith("[mcp_servers"):
        return False
    rest = stripped_line[len("[mcp_servers"):]
    return not rest or rest[0] in (".", "]")


def _table_headers(lines: list[str]) -> list[int]:
    """Line indices that begin real TOML table headers.

    Tracks strings and comments while scanning: a ``[mcp_servers.x]``
    line inside a ``'''…'''``/``\"\"\"…\"\"\"`` multiline string is text,
    not a table, and a delimiter inside a ``#`` comment (or inside an
    ordinary quoted value, e.g. ``x = '\"\"\"'``) cannot fake an open
    multiline string. Basic strings honor ``\\`` escapes.
    """
    headers: list[int] = []
    in_ml: str | None = None
    for i, ln in enumerate(lines):
        s = ln.lstrip()
        if in_ml is not None:
            if in_ml in s:
                in_ml = None
            continue
        is_header = s.startswith("[")
        pos = 0
        while pos < len(s):
            if s.startswith(('"""', "'''"), pos):
                delim = s[pos:pos + 3]
                close = s.find(delim, pos + 3)
                if close == -1:
                    in_ml = delim
                    break
                pos = close + 3
                continue
            ch = s[pos]
            if ch == "#":
                break
            if ch == "'":
                end = s.find("'", pos + 1)
                if end == -1:
                    break
                pos = end + 1
                continue
            if ch == '"':
                pos += 1
                while pos < len(s):
                    if s[pos] == "\\":
                        pos += 2
                        continue
                    if s[pos] == '"':
                        pos += 1
                        break
                    pos += 1
                continue
            pos += 1
        if is_header:
            headers.append(i)
    return headers


def split_codex_toml(text: str) -> tuple[str, str, str]:
    """Split TOML into ``(pre, mcp_region, post)`` preserving everything else.

    ``[mcp_servers*]`` tables are legal anywhere in a TOML file, not just
    one contiguous run. A second block living past other sections used to
    survive in ``post`` and be written a second time on save (invalid
    TOML: duplicate table). Every later ``[mcp_servers*]`` block is folded
    into ``region`` so the loader sees it and the saver writes it once.
    """
    if not text:
        return "", "", ""

    lines = text.splitlines(keepends=True)
    n = len(lines)
    headers = _table_headers(lines)
    mcp_headers = [h for h in headers if _is_mcp_header(lines[h].lstrip())]

    if not mcp_headers:
        return text, "", ""

    start = mcp_headers[0]
    # The contiguous run ends at the first non-mcp table header after it.
    end = next((h for h in headers if h > start and not _is_mcp_header(lines[h].lstrip())), n)

    pre = "".join(lines[:start])
    region = "".join(lines[start:end])

    # Stray mcp sections past the contiguous run move into the region;
    # everything around them stays in ``post``.
    extra_ranges = []
    for h in mcp_headers:
        if h < end:
            continue
        nxt = next((x for x in headers if x > h), n)
        extra_ranges.append((h, nxt))
    post_lines: list[str] = []
    extra_at = {i: (h, nxt) for h, nxt in extra_ranges for i in range(h, nxt)}
    for k in range(end, n):
        if k in extra_at:
            continue
        post_lines.append(lines[k])
    region += "".join("".join(lines[h:nxt]) for h, nxt in extra_ranges)
    return pre, region, "".join(post_lines)


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
    # TOML date/time literals are bare ISO strings — tomllib hands them
    # back as datetime/date/time objects, which must round-trip.
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
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
        if isinstance(val, (str, list, bool, int, float, datetime, date, time)):
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
    # Atomic, and keeps the file's mode: this holds resolved tokens and is
    # 0600 for a reason.
    atomic_write_text(path, final, private=True)
    return True
