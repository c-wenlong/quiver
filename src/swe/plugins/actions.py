"""Install, uninstall, enable and disable one plugin in one harness.

Each action reads the harness's state first and does nothing when the plugin
is already where it was asked to be. Otherwise it runs the harness's own CLI
without a shell (argv lists, the same environment ``swe find plugins --fix``
uses: ``HOME`` pinned, ``CLAUDE_CONFIG_DIR`` and ``CODEX_HOME`` removed, so
the CLI acts on the files that were just read):

  claude  claude plugin install|uninstall|enable|disable <ref>
  codex   codex plugin add|remove <ref>

Codex has no enable verb: the flag lives only in ``[plugins."<ref>"]
enabled`` in ``~/.codex/config.toml``. The stdlib reads TOML but cannot write
it, so the edit is a one-line text change that is then proven safe: the
edited file must parse to exactly the original document with that one flag
changed, or nothing is written and the error says what to edit by hand.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import stat
import subprocess
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path

from swe.find.plugin_drift import _harness_env
from swe.paths import cache_dir_for, expand_user
from swe.plugins.models import ActionResult
from swe.plugins.catalog import ACTIONABLE, READERS, _public

ACTIONS = ("install", "uninstall", "enable", "disable")

# Plugin and marketplace names as both CLIs accept them. Anything else is
# refused before a command is built, a leading "-" above all, which a CLI
# would read as a flag.
_NAME = r"[A-Za-z0-9_.][A-Za-z0-9_.+-]*"
_REF = re.compile(f"({_NAME})@({_NAME})")

# A marketplace source the CLIs share: owner/repo(@ref), an https:// or
# git@ URL, or an existing local directory. Anything else is a typo.
_REPO_REF = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(@[A-Za-z0-9_./-]+)?$")
_SSH_URL = re.compile(r"^git@[A-Za-z0-9_.:-]+:[A-Za-z0-9_./-]+$")
_SOURCE_FORMS = ("owner/repo, owner/repo@ref, https://…, git@host:…, "
                 "or an existing folder")

_VERBS = {
    "claude": {"install": "install", "uninstall": "uninstall",
               "enable": "enable", "disable": "disable"},
    "codex": {"install": "add", "uninstall": "remove"},
}

# How much of a failing command's output the error carries.
_TAIL_LINES = 5


class ActionError(Exception):
    """An action that was refused, or ran and failed; the message says which."""


def split_ref(ref: str) -> tuple[str, str]:
    match = _REF.fullmatch(ref or "")
    if match is None:
        raise ActionError(f"not a plugin ref: {ref!r}; write it as <plugin>@<marketplace>")
    return match.group(1), match.group(2)


def plugin_state(home: Path, harness: str, ref: str) -> dict:
    """registered/installed/enabled/version for one ref in one harness."""
    errors: list = []
    view = READERS[harness](home, errors)
    if view is None:
        detail = errors[0]["detail"] if errors else f"{harness} is not set up in {home}"
        raise ActionError(detail)
    _name, market = split_ref(ref)
    inst = view.installs.get(ref)
    return {
        "registered": market in view.markets,
        "installed": inst is not None,
        "enabled": view.enabled.get(ref) if inst is not None else None,
        "version": inst.version if inst is not None else None,
    }


def _tail(*streams: str | None) -> str:
    for text in streams:
        lines = [line for line in (text or "").strip().splitlines() if line.strip()]
        if lines:
            return "\n".join(lines[-_TAIL_LINES:])
    return ""


def _run(home: Path, argv: list[str], run) -> subprocess.CompletedProcess:
    try:
        done = run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                   timeout=300, env=_harness_env(home))
    except FileNotFoundError:
        raise ActionError(f"{argv[0]} is not on PATH") from None
    except subprocess.TimeoutExpired:
        raise ActionError(f"{shlex.join(argv)} did not finish in 5 minutes") from None
    except OSError as exc:
        raise ActionError(f"couldn't run {argv[0]}: {exc.strerror or exc}") from None
    if done.returncode != 0:
        said = _tail(done.stderr, done.stdout) or "no output"
        raise ActionError(f"{shlex.join(argv)} failed with exit status "
                          f"{done.returncode}:\n{said}")
    return done


def _noop_reason(action: str, before: dict, harness: str) -> str | None:
    """Why nothing needs doing, or None. Raises when the action cannot apply.

    An install from a marketplace this harness has not registered still
    runs: a harness can know marketplaces swe cannot see (codex's built-in
    ones, a project's claude settings), and its own error says it better.
    """
    if action == "install":
        return "already installed" if before["installed"] else None
    if action == "uninstall":
        return None if before["installed"] else "not installed"
    if not before["installed"]:
        raise ActionError(f"not installed in {harness}, so it cannot be {action}d")
    want = action == "enable"
    return f"already {action}d" if before["enabled"] is want else None


def plugin_action(home: Path, action: str, ref: str, harness: str, run=None) -> dict:
    """Do ``action`` for ``ref`` in ``harness``; return what happened."""
    if action not in ACTIONS:
        raise ActionError(f"unknown action {action!r}; use one of {', '.join(ACTIONS)}")
    if harness not in ACTIONABLE:
        raise ActionError(f"--harness must be one of {', '.join(ACTIONABLE)}, not {harness!r}")
    split_ref(ref)
    run = run or subprocess.run
    before = plugin_state(home, harness, ref)
    reason = _noop_reason(action, before, harness)
    ran: list[list[str]] = []
    edited = None
    output = ""
    if reason is None:
        verb = _VERBS[harness].get(action)
        if verb is not None:
            argv = [harness, "plugin", verb, ref]
            done = _run(home, argv, run)
            ran.append(argv)
            output = _tail(done.stdout, done.stderr)
        else:
            edited = str(set_codex_enabled(home, ref, action == "enable"))
    after = plugin_state(home, harness, ref)
    changed = (after["installed"], after["enabled"]) != (before["installed"], before["enabled"])
    if reason is None and not changed:
        reason = "the command finished but the state did not change"
    return ActionResult(
        action=action,
        harness=harness,
        ref=ref,
        changed=changed,
        detail=reason,
        ran=ran,
        edited=edited,
        output=output or None,
        state={k: after[k] for k in ("installed", "enabled", "version")},
    ).to_json()


def marketplace_source(source: str, home: Path) -> str:
    """The source as the CLIs get it. Returns a validated form; raises
    ActionError for anything that is not a repo ref, URL, or real dir."""
    source = source.strip()
    if source.startswith("-") or not source:
        raise ActionError(f"not a marketplace source: {source!r}; use {_SOURCE_FORMS}")
    folder = expand_user(source)
    # A path-looking source is a folder or nothing: `./plugins` must not
    # pass as the GitHub repo "./plugins".
    looks_like_path = source.startswith(("/", "~", "."))
    if not looks_like_path and (_REPO_REF.fullmatch(source) or source.startswith("https://")
                                or _SSH_URL.fullmatch(source)):
        return source
    if folder.is_dir():
        return str(folder.resolve())
    raise ActionError(f"not a marketplace source: {source!r}; use {_SOURCE_FORMS}")


def marketplace_add(home: Path, source: str, harnesses: list[str], run=None) -> dict:
    """`swe plugins marketplace add`: one harness CLI call per harness."""
    run = run or subprocess.run
    argv_source = marketplace_source(source, home)
    # Every harness is checked before any CLI runs, so a typo in the second
    # one can't leave the first registered and the command reporting failure.
    for harness in harnesses:
        if harness not in ACTIONABLE:
            raise ActionError(
                f"--harness must be one of {', '.join(ACTIONABLE)}, not {harness!r}")
    results = []
    for harness in harnesses:
        if harness == "claude":
            argv = ["claude", "plugin", "marketplace", "add",
                    "--scope", "user", argv_source]
        else:
            argv = ["codex", "plugin", "marketplace", "add", argv_source]
        try:
            done = run(argv, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120,
                       env=_harness_env(home))
            if done.returncode == 0:
                results.append({"harness": harness, "ok": True,
                                "detail": _tail(done.stdout, done.stderr)})
            else:
                results.append({"harness": harness, "ok": False,
                                "detail": _tail(done.stderr, done.stdout)
                                or "no output"})
        except FileNotFoundError:
            results.append({"harness": harness, "ok": False,
                            "detail": f"{argv[0]} is not on PATH"})
        except subprocess.TimeoutExpired:
            results.append({"harness": harness, "ok": False,
                            "detail": f"{argv[0]} did not finish in 2 minutes"})
        except OSError as exc:
            results.append({"harness": harness, "ok": False,
                            "detail": f"couldn't run {argv[0]}: "
                                      f"{exc.strerror or exc}"})
    # Keep credentials a private https source carries out of every detail:
    # scrub each URL inside the message, not the whole line.
    for result in results:
        result["detail"] = re.sub(
            r"https?://[^\s\"']+",
            lambda m: _public(m.group(0)) or m.group(0).split("//", 1)[0],
            result["detail"])
    # The CLI needed the real URL; what swe prints never carries its token.
    return {"source": _public(argv_source), "results": results}


# --------------------------------------------------------------------------
# codex enable/disable: a proven one-line edit of config.toml


def manual_edit(ref: str, value: bool) -> str:
    """The edit by hand, as plugin_drift words it. json.dumps quotes any name
    as a valid TOML basic-string key."""
    return (f"set [plugins.{json.dumps(ref)}] enabled = {str(value).lower()} "
            "in ~/.codex/config.toml")


_HEADER = re.compile(r"\s*\[")
_ENABLED = re.compile(r"(\s*enabled\s*=\s*)(true|false)(\s*(?:#.*)?)")


def _header_key(line: str) -> tuple | None:
    """The key path a ``[table]`` header line names, or None."""
    try:
        doc = tomllib.loads(line.strip())
    except tomllib.TOMLDecodeError:
        return None
    path = []
    while isinstance(doc, dict) and len(doc) == 1:
        key, doc = next(iter(doc.items()))
        path.append(key)
    return tuple(path) if doc == {} else None


def _edit_enabled(text: str, ref: str, value: bool) -> str | None:
    """``text`` with the ref's enabled flag set, or None when its shape is
    not the plain ``[plugins."<ref>"]`` table this can edit."""
    lines = text.splitlines(keepends=True)
    start = end = None
    for i, line in enumerate(lines):
        if not _HEADER.match(line):
            continue
        if start is not None:
            end = i
            break
        if _header_key(line) == ("plugins", ref):
            start = i
    if start is None:
        return None
    end = len(lines) if end is None else end
    word = "true" if value else "false"
    hits = []
    for i in range(start + 1, end):
        body = lines[i].rstrip("\r\n")
        match = _ENABLED.fullmatch(body)
        if match:
            hits.append((i, match, lines[i][len(body):]))
    if len(hits) > 1:
        return None
    if hits:
        i, match, newline = hits[0]
        lines[i] = match.group(1) + word + match.group(3) + newline
    else:
        newline = lines[start][len(lines[start].rstrip("\r\n")):] or "\n"
        if not lines[start].endswith(("\n", "\r")):
            lines[start] += newline
        lines.insert(start + 1, f"enabled = {word}{newline}")
    return "".join(lines)


@contextmanager
def _config_lock(home: Path, by_hand: str):
    """Serialise config.toml edits across swe processes.

    ``os.replace`` stops a reader seeing half a file, not two writers each
    replacing the file with their own edit of the same original: the second
    would silently undo the first. The lock lives in ~/.quiver/cache, not
    beside config.toml, so nothing of swe's is left in ~/.codex.
    """
    try:
        # Imported here, not at the top: Windows has no fcntl, and every
        # other swe command loads this module and must keep working there.
        import fcntl
    except ImportError:
        raise ActionError(f"this platform has no file locking for a safe edit; {by_hand}") from None
    lock = cache_dir_for(home) / "codex-config.lock"
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock, "a")
    except OSError as exc:
        raise ActionError(f"couldn't open {lock}: {exc.strerror or exc}") from None
    with handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def set_codex_enabled(home: Path, ref: str, value: bool) -> Path:
    """Set ``[plugins."<ref>"] enabled`` in config.toml; return the file.

    The file is read, checked and written under ``_config_lock``, through a
    temporary sibling and ``os.replace``, so a reader sees the old file or
    the new one, never half of each. A symlinked config.toml (dotfiles) is
    edited at its target, and keeps its mode.
    """
    with _config_lock(home, manual_edit(ref, value)):
        return _set_codex_enabled(home, ref, value)


def _set_codex_enabled(home: Path, ref: str, value: bool) -> Path:
    target = Path(os.path.realpath(home / ".codex" / "config.toml"))
    by_hand = manual_edit(ref, value)
    try:
        raw = target.read_bytes()
        text = raw.decode("utf-8")
        before = tomllib.loads(text)
    except (OSError, ValueError) as exc:
        raise ActionError(f"can't read {target} ({exc}); {by_hand}") from None
    plugins = before.get("plugins")
    if not isinstance(plugins, dict) or not isinstance(plugins.get(ref), dict):
        raise ActionError(f"{ref} has no [plugins] table in {target}; is it installed?")
    if plugins[ref].get("enabled") is value:
        return target   # another swe got here first while this one waited

    edited = _edit_enabled(text, ref, value)
    expected = copy.deepcopy(before)
    expected["plugins"][ref]["enabled"] = value
    try:
        proven = edited is not None and tomllib.loads(edited) == expected
    except tomllib.TOMLDecodeError:
        proven = False
    if not proven:
        raise ActionError(f"{target} is not laid out in a way swe can edit safely; {by_hand}")

    try:
        # Codex may have rewritten the file since it was read; never clobber that.
        if target.read_bytes() != raw:
            raise ActionError(f"{target} changed while it was being edited; try again")
        mode = stat.S_IMODE(target.stat().st_mode)
        fd, tmp = tempfile.mkstemp(prefix=".config.toml.", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(edited)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise ActionError(f"couldn't write {target}: {exc.strerror or exc}; {by_hand}") from None
    return target
