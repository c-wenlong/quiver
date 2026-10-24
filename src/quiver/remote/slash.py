"""The slash commands a session's harness will run, for the phone's "/" menu.

``GET /v1/sessions/{key}/commands`` answers from here. The hub only lists
commands: the phone inserts ``/name `` into the message, and the harness
expands it when the message arrives. So the list must hold only what
that harness, reached the way the phone reaches it, will actually run.

Three sources, merged per harness:

1. Built-ins, from the curated tables in ``slash_builtins.py`` (no file
   on disk says what a TUI has built in).
2. Custom commands and skills from the user's and the project's folders.
   Skills roots come from the registry (``capabilities.skills.root``)
   with ``skills.layout.HARNESS_ROOTS`` as the fallback, the same join
   ``swe skills`` uses; descriptions come from frontmatter through
   ``skills.discovery.parse_skill_md``.
3. Plugin commands and skills, for harnesses whose enabled plugins the
   repo already knows how to find (Claude Code's ``installed_plugins.json``
   and live directory marketplaces, the same reading as
   ``skills.inventory``).

Then one filter, by channel (see ``for_channel``): what reaches a real TUI
can use built-ins, what goes through print mode or a server API mostly
cannot.

A satellite's files are not readable from the hub, so the satellite agent
pushes its own custom entries (``satellite_payload``) beside its catalog,
and the hub merges them here (``for_remote_row``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, NamedTuple

from quiver.paths import atomic_write_text, remote_dir_for
from quiver.remote import slash_builtins
from quiver.remote.slash_builtins import Builtin

# How long a (harness, folder) scan is reused. Opening the menu should be
# instant; a command added on disk shows up within this long.
CACHE_TTL_S = 30.0

# A description is one line in a phone menu.
MAX_DESCRIPTION = 160

SOURCES = ("builtin", "user", "project", "skill", "plugin")

# Where the hub keeps each satellite's pushed command lists. Not the
# catalog's ``satellite/`` folder: that one's ``*.json`` are all rows.
_SAT_DIR = "slash"

_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------
# Frontmatter


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Single-line ``key: value`` fields of a YAML header, and the body.

    Only the scalar keys a menu needs are read here (``name``,
    ``argument-hint``, ``user-invocable``); ``description`` goes through
    ``parse_skill_md``, which also handles folded and literal blocks.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fields: dict[str, str] = {}
    for line in text[3:end].splitlines():
        match = re.match(r"^([\w][\w-]*):\s*(.*)$", line)
        if match:
            val = match.group(2).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]
            fields[match.group(1).lower()] = val
    body_start = text.find("\n", end + 4)
    return fields, (text[body_start + 1:] if body_start != -1 else "")


def _one_line(text: str) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > MAX_DESCRIPTION:
        text = text[:MAX_DESCRIPTION - 1].rstrip() + "…"
    return text


def read_meta(path: Path) -> dict | None:
    """``{name, description, argument_hint, hidden}`` from one markdown file.

    ``name`` is the frontmatter ``name`` or "" (the caller knows the
    default: a skill's folder, a command's file stem). A missing
    description falls back to the body's first non-empty line, which is
    what Claude Code and Codex both show. None when the file is unreadable.
    """
    from quiver.skills.discovery import parse_skill_md

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields, body = _frontmatter(text)
    _name, description = parse_skill_md(path)
    if not description:
        for line in body.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                description = line
                break
    return {
        "name": fields.get("name", ""),
        "description": _one_line(description),
        "argument_hint": _one_line(fields.get("argument-hint", "")),
        "hidden": fields.get("user-invocable", "").lower() == "false",
    }


def _entry(name: str, meta: dict, source: str) -> dict:
    row = {
        "name": name,
        "description": meta.get("description") or "",
        "source": source,
    }
    if meta.get("argument_hint"):
        row["argument_hint"] = meta["argument_hint"]
    return row


# ---------------------------------------------------------------------
# Folders


def _md_files(root: Path, recursive: bool = True) -> list[Path]:
    """Every ``*.md`` under ``root``, sorted, hidden folders skipped."""
    out: list[Path] = []
    try:
        if not root.is_dir():
            return out
    except OSError:
        return out
    if not recursive:
        try:
            return sorted(p for p in root.glob("*.md") if p.is_file())
        except OSError:
            return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.endswith(".md") and not name.startswith("."):
                out.append(Path(dirpath) / name)
    return out


def command_files(root: Path, source: str, sep: str = ":",
                  prefix: str = "", recursive: bool = True) -> list[dict]:
    """A command folder's entries: one per ``*.md``, named by its path.

    ``commands/frontend/component.md`` is ``frontend:component`` (Claude
    Code turns each folder into a ``:`` segment); ``prefix`` namespaces a
    plugin's commands (``my-plugin:``).
    """
    out = []
    for path in _md_files(root, recursive):
        meta = read_meta(path)
        if meta is None or meta["hidden"]:
            continue
        rel = path.relative_to(root).with_suffix("")
        name = prefix + sep.join(rel.parts)
        out.append(_entry(name, meta, source))
    return out


def skill_folders(root: Path, source: str = "skill", prefix: str = "",
                  recursive: bool = False) -> list[dict]:
    """A skills root's entries: one per folder holding a ``SKILL.md``.

    Claude Code reads one level (``<root>/<name>/SKILL.md``); Codex walks
    the tree, so ``recursive`` follows it down. The frontmatter ``name``
    wins over the folder name, as both harnesses do.
    """
    out = []
    try:
        if not root.is_dir():
            return out
    except OSError:
        return out
    if recursive:
        found = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            # Codex installs its bundled skills under ``.system``.
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith(".") or d == ".system"
            )
            if "SKILL.md" in filenames:
                found.append(Path(dirpath))
    else:
        try:
            found = sorted(
                p for p in root.iterdir()
                if not p.name.startswith(".") and (p / "SKILL.md").is_file()
            )
        except OSError:
            found = []
    for folder in found:
        meta = read_meta(folder / "SKILL.md")
        if meta is None or meta["hidden"]:
            continue
        name = prefix + (meta["name"] or folder.name)
        out.append(_entry(name, meta, source))
    return out


def project_dirs(cwd: str, home: Path) -> list[Path]:
    """``cwd`` and its parents up to the repository root, nearest first.

    Claude Code loads project skills from the start folder and every
    parent up to the repo root. Without a ``.git`` above, only ``cwd``
    counts. ``home`` itself is never a project: its ``.claude`` is the
    user level.
    """
    if not cwd:
        return []
    start = Path(cwd)
    chain: list[Path] = []
    here = start
    while True:
        if here == home:
            break
        chain.append(here)
        if (here / ".git").exists():
            return chain
        if here.parent == here:
            break
        here = here.parent
    return [start] if start != home else []


def _skills_root(harness: str, home: Path) -> Path | None:
    """The harness's user skills folder: registry first, then the
    ``HARNESS_ROOTS`` fallback; None when the registry says it has none."""
    from quiver.init.layout import load_registry
    from quiver.skills.layout import HARNESS_ROOTS

    try:
        registry = load_registry(home)
    except Exception:
        registry = {}
    entry = registry.get(harness) if isinstance(registry, dict) else None
    skills = ((entry or {}).get("capabilities") or {}).get("skills") \
        if isinstance(entry, dict) else None
    if isinstance(skills, dict):
        if skills.get("supported") is False:
            return None
        root = skills.get("root")
        if isinstance(root, str) and root.startswith("~/"):
            rel = Path(root[2:])
            if rel.parts and ".." not in rel.parts:
                return home / rel
    for label, rel in HARNESS_ROOTS:
        if label == harness:
            return home / rel
    return None


# ---------------------------------------------------------------------
# Per-harness custom entries


def _claude_plugins(home: Path, cwd: str | None) -> list[dict]:
    """Enabled Claude Code plugins' commands and skills, namespaced.

    The plugin folder is where claude runs it from: the marketplace's own
    folder for a directory marketplace, else the install record's cache
    path (the reading ``skills.inventory`` uses). ``cwd=None`` lists the
    user-scoped installs; a folder lists the installs scoped to it.
    """
    from quiver.find.plugins import _load_json
    from quiver.paths import expand_user
    from quiver.skills.inventory import (
        _claude_directory_marketplaces,
        _claude_live_source,
    )

    base = home / ".claude" / "plugins"
    record = _load_json(base / "installed_plugins.json")
    plugins = record.get("plugins") if isinstance(record, dict) else None
    if not isinstance(plugins, dict):
        return []
    settings = _load_json(home / ".claude" / "settings.json")
    flags = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    enabled = flags if isinstance(flags, dict) else {}
    markets = None
    want = os.path.realpath(cwd) if cwd else None
    out: list[dict] = []
    for ref in sorted(plugins):
        if enabled.get(ref) is False:
            continue
        entries = plugins[ref]
        entries = entries if isinstance(entries, list) else [entries]
        folder = None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            project = entry.get("projectPath")
            scoped = entry.get("scope") in {"project", "local"} and project
            here = os.path.realpath(str(project)) if scoped else None
            if here != want:
                continue
            path = entry.get("installPath")
            if isinstance(path, str) and path:
                folder = expand_user(path)
                break
        if folder is None:
            continue
        if markets is None:
            markets = _claude_directory_marketplaces(home, settings)
        live = _claude_live_source(markets, ref)
        if live is not None:
            folder = live
        prefix = ref.partition("@")[0] + ":"
        out += command_files(folder / "commands", "plugin", prefix=prefix)
        out += skill_folders(folder / "skills", "plugin", prefix=prefix)
    return out


def _claude_user(home: Path) -> dict:
    root = _skills_root("claude", home)
    return {
        "skills": skill_folders(root, "skill") if root else [],
        "commands": command_files(home / ".claude" / "commands", "user"),
        "plugins": _claude_plugins(home, None),
    }


def _claude_project(cwd: str, home: Path) -> dict:
    skills: list[dict] = []
    commands: list[dict] = []
    for folder in project_dirs(cwd, home):
        skills += skill_folders(folder / ".claude" / "skills", "skill")
        commands += command_files(folder / ".claude" / "commands", "project")
    return {
        "skills": skills,
        "commands": commands,
        "plugins": _claude_plugins(home, cwd) if cwd else [],
    }


def _codex_home(home: Path) -> Path:
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env and home == Path.home() else home / ".codex"


def _mentions(rows: list[dict]) -> list[dict]:
    for row in rows:
        row["insert"] = f"${row['name']} "
    return rows


def _codex_user(home: Path) -> dict:
    root = _skills_root("codex", home) or (_codex_home(home) / "skills")
    skills = skill_folders(root, "skill", recursive=True)
    skills += skill_folders(home / ".agents" / "skills", "skill",
                            recursive=True)
    return {"skills": _mentions(skills)}


def _codex_project(cwd: str, home: Path) -> dict:
    skills: list[dict] = []
    for folder in project_dirs(cwd, home):
        skills += skill_folders(folder / ".agents" / "skills", "skill",
                                recursive=True)
    return {"skills": _mentions(skills)}


def _opencode_user(home: Path) -> dict:
    commands: list[dict] = []
    for sub in ("command", "commands"):
        commands += command_files(
            home / ".config" / "opencode" / sub, "user", sep="/")
    return {"commands": commands}


def _opencode_project(cwd: str, home: Path) -> dict:
    commands: list[dict] = []
    for folder in project_dirs(cwd, home):
        for sub in ("command", "commands"):
            commands += command_files(
                folder / ".opencode" / sub, "project", sep="/")
    return {"commands": commands}


class _Harness(NamedTuple):
    user: Callable[[Path], dict]
    project: Callable[[str, Path], dict]
    # Precedence, first wins on a name clash: (scope, layer) pairs.
    order: tuple[tuple[str, str], ...]


_HARNESSES: dict[str, _Harness] = {
    # Claude Code: a skill beats a command file of the same name, and a
    # personal entry beats a project one ("Enterprise over personal, and
    # personal over project", code.claude.com/docs/en/skills). Plugin
    # entries are namespaced (``plugin:name``), so they never clash.
    "claude": _Harness(_claude_user, _claude_project, (
        ("user", "skills"), ("project", "skills"),
        ("user", "commands"), ("project", "commands"),
        ("user", "plugins"), ("project", "plugins"),
    )),
    # Codex: skills only, mentioned as ``$name`` rather than run as
    # ``/name`` ("run /skills or type $ to mention a skill"), so each row
    # carries ``insert``. Roots: the registry's skills root (else
    # ``$CODEX_HOME/skills``), ``~/.agents/skills``, and ``.agents/skills``
    # in the folder and its parents up to the repo root; nearest wins.
    # File prompts (``/prompts:<name>``) are not listed: the docs call
    # them deprecated and codex-cli 0.163.0 no longer reads
    # ``~/.codex/prompts``.
    "codex": _Harness(_codex_user, _codex_project, (
        ("project", "skills"), ("user", "skills"),
    )),
    # opencode: markdown commands in ``~/.config/opencode/command(s)`` and
    # ``<project>/.opencode/command(s)``, named by their path; project
    # config merges over global, so a project command wins.
    "opencode": _Harness(_opencode_user, _opencode_project, (
        ("project", "commands"), ("user", "commands"),
    )),
}


def supported(harness: str) -> bool:
    return harness in _HARNESSES or harness in slash_builtins.BUILTINS


def _cached(key: tuple, scan: Callable[[], dict], now: float) -> dict:
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < CACHE_TTL_S:
            return hit[1]
    try:
        layers = scan()
    except Exception:
        layers = {}
    with _cache_lock:
        _cache[key] = (now, layers)
    return layers


def user_layers(harness: str, home: Path | None = None,
                now: float | None = None) -> dict:
    """The harness's user-level entries by layer, cached per harness."""
    spec = _HARNESSES.get(harness)
    if spec is None:
        return {}
    home = Path(home) if home is not None else Path.home()
    now = time.time() if now is None else now
    return _cached(("user", harness, str(home)),
                   lambda: spec.user(home), now)


def project_layers(harness: str, cwd: str, home: Path | None = None,
                   now: float | None = None) -> dict:
    """The harness's entries for one folder by layer, cached per folder."""
    spec = _HARNESSES.get(harness)
    if spec is None or not cwd:
        return {}
    home = Path(home) if home is not None else Path.home()
    now = time.time() if now is None else now
    return _cached(("project", harness, cwd, str(home)),
                   lambda: spec.project(cwd, home), now)


def combine(harness: str, user: dict, project: dict) -> list[dict]:
    """Merge layers in the harness's precedence; copies, never the cache."""
    spec = _HARNESSES.get(harness)
    if spec is None:
        return []
    scopes = {"user": user or {}, "project": project or {}}
    out: dict[str, dict] = {}
    for scope, layer in spec.order:
        rows = scopes[scope].get(layer) or []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("name"):
                out.setdefault(str(row["name"]), dict(row))
    return list(out.values())


def custom_entries(harness: str, cwd: str, home: Path | None = None,
                   now: float | None = None) -> list[dict]:
    """The harness's custom commands and skills for ``cwd``: user level
    cached per harness, project level per folder, ``CACHE_TTL_S`` each."""
    return combine(
        harness,
        user_layers(harness, home, now),
        project_layers(harness, cwd, home, now),
    )


# ---------------------------------------------------------------------
# Channels


def channel_of(home: Path | None, key: str, session) -> tuple[str, bool]:
    """``(channel, attached)`` for a session on this machine.

    An attached session uses the channel its supervised entry names. An
    observed one is read-only until attached, and the phone attaches with
    ``auto``, so the menu shows what that attach would give.
    """
    from quiver.remote import supervise

    meta = supervise.meta_for(home, key)
    if meta and meta.get("mode"):
        return str(meta["mode"]), True
    if session is None:
        return "tmux", False
    try:
        return supervise.resolve_mode(session, "auto"), False
    except Exception:
        return "tmux", False


def for_channel(harness: str, channel: str,
                custom: list[dict]) -> tuple[list[dict], bool]:
    """Built-ins plus custom entries that run through ``channel``.

    The rule:

    - ``tmux``: the message is typed into the real TUI, so built-ins work.
      A built-in whose bare form opens a picker or dialog would leave the
      pane waiting on keys the phone can't send, and the next message
      would land in that picker. Those are listed only when an argument
      skips the picker (``/model sonnet``), marked
      ``argument_required``; the rest are left out of the tables. Custom
      commands and skills work.
    - ``headless`` (``claude -p --resume``): print mode expands skills
      and custom commands, but only the few built-ins the docs list for
      ``-p`` run there; the others are dropped.
    - ``serve`` / ``appserver`` (opencode ``prompt_async``, codex
      ``turn/start``): the text goes to the model as a prompt, with no TUI
      to expand a built-in. Built-ins are dropped; custom entries stay
      only where ``slash_builtins.CUSTOM_CHANNELS`` says that harness
      expands them on that channel.

    Returns ``(entries, custom_ok)``; ``custom_ok`` is False when this
    channel can't run custom commands at all.
    """
    rows: list[dict] = []
    for item in slash_builtins.BUILTINS.get(harness, ()):
        if channel not in item.channels:
            continue
        rows.append(_builtin_row(item))
    custom_ok = channel in slash_builtins.CUSTOM_CHANNELS.get(harness, ())
    if custom_ok:
        taken = {row["name"] for row in rows}
        for row in custom:
            # A custom entry named like a built-in replaces it in Claude
            # Code's terminal; either way the menu shows the name once.
            # A ``$skill`` mention is a different token, so it never
            # shadows ``/compact``.
            if row["name"] in taken and "insert" not in row:
                rows = [r for r in rows if r["name"] != row["name"]]
            rows.append(row)
    rows.sort(key=lambda r: (r["source"] != "builtin", r["name"].lower()))
    return rows, custom_ok


def _builtin_row(item: Builtin) -> dict:
    row = {
        "name": item.name,
        "description": item.description,
        "source": "builtin",
    }
    if item.argument_hint:
        row["argument_hint"] = item.argument_hint
    if item.argument_required:
        row["argument_required"] = True
    return row


# ---------------------------------------------------------------------
# The route's answer


def for_local_session(home: Path | None, key: str, row: dict,
                      session) -> dict:
    harness = str(row.get("harness") or getattr(session, "tool_name", "")
                  or "")
    cwd = str(getattr(session, "path", "") or row.get("cwd") or "")
    channel, attached = channel_of(home, key, session)
    custom = custom_entries(harness, cwd) if supported(harness) else []
    rows, custom_ok = for_channel(harness, channel, custom)
    return _answer(key, harness, channel, attached, rows,
                   custom_available=custom_ok, note="")


def _answer(key, harness, channel, attached, rows, custom_available, note):
    body = {
        "key": key,
        "harness": harness,
        "channel": channel,
        "attached": attached,
        "supported": supported(harness),
        "custom_available": custom_available,
        "commands": rows,
    }
    if note:
        body["note"] = note
    return body


# ---------------------------------------------------------------------
# Satellites


def satellite_payload(rows: list[dict], home: Path | None = None) -> dict:
    """What a satellite pushes beside its catalog.

    Per harness its sessions use: the user-level layers once, the
    project layers of each folder that has any, and the channel an
    ``auto`` attach would pick; plus the channel of each attached
    session. ``home`` is the quiver home (for the supervised registry);
    harness folders are read under the user's own home. Scans go through
    the same caches, so the push every few seconds rescans at most once
    per ``CACHE_TTL_S``.
    """
    from quiver.remote import supervise

    out: dict = {"harnesses": {}, "attached": {}}
    for row in rows:
        if not isinstance(row, dict):
            continue
        harness = str(row.get("harness") or "")
        if harness not in _HARNESSES:
            continue
        slot = out["harnesses"].get(harness)
        if slot is None:
            slot = out["harnesses"][harness] = {
                "user": user_layers(harness),
                "folders": {},
                "auto": _auto_channel(harness),
            }
        cwd = str(row.get("cwd") or "")
        if cwd and cwd not in slot["folders"]:
            layers = project_layers(harness, cwd)
            if any(layers.values()):
                slot["folders"][cwd] = layers
        key = str(row.get("key") or "")
        meta = supervise.meta_for(home, key) if key else None
        if meta and meta.get("mode"):
            out["attached"][key] = str(meta["mode"])
    return out


def _auto_channel(harness: str) -> str:
    from quiver.remote import supervise
    from quiver.sessions.models import Session

    probe = Session(timestamp=0, agent="", path="", title="",
                    session_id="", tool_name=harness)
    try:
        return supervise.resolve_mode(probe, "auto")
    except Exception:
        return "tmux"


def payload_digest(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def _sat_file(home: Path | None, machine_id: str) -> Path:
    from quiver.remote.catalog import _safe_mid

    return remote_dir_for(home) / _SAT_DIR / f"{_safe_mid(machine_id)}.json"


def save_satellite(home: Path | None, machine_id: str, payload) -> bool:
    """Persist a satellite's pushed commands; False when malformed."""
    if not isinstance(payload, dict) or \
            not isinstance(payload.get("harnesses"), dict):
        return False
    path = _sat_file(home, machine_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(payload))
    except OSError:
        return False
    return True


def load_satellite(home: Path | None, machine_id: str) -> dict | None:
    try:
        data = json.loads(_sat_file(home, machine_id).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def for_remote_row(home: Path | None, key: str, row: dict) -> dict:
    """A satellite session's menu, from what its agent last pushed.

    A satellite that never pushed commands (an agent older than this
    route) gets built-ins only, for the channel its attached session
    reports or tmux, with ``custom_available: false`` and a note.
    """
    from quiver.remote import protocol

    harness = str(row.get("harness") or "")
    cwd = str(row.get("cwd") or "")
    mid = str(row.get("machine_id") or "")
    local_key = protocol.split_remote_key(key)[0]
    pushed = load_satellite(home, mid) or {}
    slot = (pushed.get("harnesses") or {}).get(harness)
    modes = pushed.get("attached") or {}
    attached = row.get("ownership") == protocol.OWNERSHIP_ATTACHED
    mode = modes.get(local_key) if isinstance(modes, dict) else None
    if isinstance(slot, dict):
        channel = str(mode or slot.get("auto") or "tmux")
        folders = slot.get("folders") or {}
        custom = combine(harness, slot.get("user") or {},
                         folders.get(cwd) or {})
        rows, custom_ok = for_channel(harness, channel, custom)
        return _answer(key, harness, channel, attached, rows,
                       custom_available=custom_ok, note="")
    channel = str(mode or "tmux")
    rows, _custom_ok = for_channel(harness, channel, [])
    note = ("custom commands aren't available for this machine yet"
            if harness in _HARNESSES else "")
    return _answer(key, harness, channel, attached, rows,
                   custom_available=False, note=note)
