"""Supervision: sessions running under our tmux (or headless) control.

An ``observed`` session belongs to a human terminal; an ``attached`` one
runs under swe's supervisor and the phone can write to it. Modes:

- ``tmux``: ``tmux new-session -d`` relaunches the harness with its
  resume flags. The session keeps a real TUI — ``tmux attach -t`` still
  works locally — and writes go in through ``send-keys``.
- ``headless``: no persistent process. Each prompt spawns a one-shot
  harness invocation (``claude -p --resume``); ``inject.py`` owns it.
- ``serve`` (opencode): the tier-A channel — ``inject.py`` lazily runs
  ``opencode serve`` for the project and writes over REST/SSE.
- ``appserver`` (codex): ``inject.py`` keeps a ``codex app-server``
  JSON-RPC proc per session and writes over ``turn/start``.

``auto`` resolves to the tier-A mode when the harness has one, else
tmux; ``--mode=tmux`` stays the explicit escape hatch.

The registry is ``data/remote/supervised.json``: ``{key: {mode, tmux,
tool_name, session_id, cwd, attached_at}}``. Catalog reads the same file
to flag ownership; a dead tmux entry is pruned on read so a crashed
supervisor cleanly reverts a session to ``observed``.

Detach has three modes (``DETACH_MODES``): ``now`` kills the tmux
session, ``keep`` stops supervising but leaves tmux running, and
``when_idle`` (``detach.py``) waits for the turn to end, then does
``now``. A kept session is recorded in ``data/remote/kept.json``
(``{key: {...the supervised row, pane_pid, kept_at}}``), under the same
lock as the supervised registry: re-attach adopts it instead of
launching a second run, and ``reap_kept`` closes one nobody uses.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import threading
import time
import weakref
from pathlib import Path

from swe.paths import atomic_write_text, remote_dir_for
from swe.remote.protocol import key_slug
from swe.sessions.models import Session
from swe.sessions.resume import account_env, resume_flags, launch_tool_name

# Attach is offered only for idle sessions; the hub re-checks at command
# time. "Idle" = no transcript append in this window.
ATTACH_IDLE_S = 5.0

# How long a fresh tmux pane must survive before launch_tmux trusts it.
LAUNCH_SETTLE_S = 1.0

# A launch marker whose transcript never shows is given up on after a
# day: adopt_pending deletes it, and pending_launch stops counting it
# (or the abandoned marker would keep scans uncached for a day).
PENDING_MAX_AGE_S = 86400.0

# How long attach waits for a resumed TUI to boot past its trust gate.
# The phone's attach request times out at 15 s and the launch settle
# comes first, so this stays well under it (a real resume boots in ~2 s).
ATTACH_BOOT_S = 8.0

# Harness -> tier-A channel mode for ``attach(mode="auto")``. Harnesses
# not listed keep the tmux channel.
_AUTO_MODE = {"opencode": "serve", "codex": "appserver"}

# Which binary each API mode needs; checked before the registry write so
# an attached session never points at a channel that cannot exist.
_MODE_BINARY = {"serve": "opencode", "appserver": "codex",
                "headless": "claude"}

# ``attach`` accepts exactly these; anything else is a 422 at the route.
ATTACH_MODES = frozenset({"auto", "tmux", "headless", "serve", "appserver"})

# A kept tmux session with no tmux client attached and no transcript
# activity for this long is closed by the hub's (or satellite's)
# periodic loop.
KEPT_IDLE_REAP_S = 12 * 3600

# Tests point every tmux call at a private server (``tmux -L <name>``)
# so they never touch the user's own.
_TMUX_SOCKET: str | None = None


def resolve_mode(session: Session, mode: str) -> str:
    """The concrete channel for ``mode`` on this session.

    ``auto`` picks the tier-A channel when the harness has one *and* its
    binary is on PATH; otherwise tmux. Explicit modes pass through.
    """
    if mode != "auto":
        return mode
    if session.tool_name == "claude":
        # Claude's tier-A channel is headless print mode — no tmux pane
        # to own. Prefer tmux when it's here (steer mid-turn works);
        # headless is the fallback for tmux-less boxes, and tmux is the
        # fallback when neither binary exists so attach can 422 plainly.
        if tmux_bin() is not None:
            return "tmux"
        return "headless" if shutil.which("claude") else "tmux"
    want = _AUTO_MODE.get(session.tool_name)
    if want and shutil.which(_MODE_BINARY[want]):
        return want
    return "tmux"

# The supervised registry is read-change-save; the hub, satellite agents
# and `swe remote attach` CLI calls can all touch it. In-process lock
# plus an flock'd sidecar make the read-write atomic across both.
_REGISTRY_LOCK = threading.RLock()

# Keystrokes into a tmux pane are multi-call (text, then Enter): per-pane
# locks keep two concurrent writes from interleaving into one prompt.
_PANE_LOCKS: dict[str, threading.Lock] = {}
_PANE_LOCKS_LOCK = threading.Lock()


@contextlib.contextmanager
def _registry_lock(home: Path | None):
    """Exclusive section around supervised.json read-modify-write."""
    path = remote_dir_for(home)
    path.mkdir(parents=True, exist_ok=True)
    with _REGISTRY_LOCK:
        with open(path / "supervised.lock", "w") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# One lock per session key around attach, detach and keep, taken before
# the registry lock: an attach holds it across its pane's boot, so a
# second attach (or a detach) of the same key waits for that one
# instead of the registry lock being held for the whole boot. Held
# weakly, like the journal's: a lock lives while a caller holds it.
_ATTACH_LOCKS: "weakref.WeakValueDictionary[str, threading.RLock]" = \
    weakref.WeakValueDictionary()
_ATTACH_LOCKS_LOCK = threading.Lock()


def _attach_lock(session_key: str) -> threading.RLock:
    with _ATTACH_LOCKS_LOCK:
        lock = _ATTACH_LOCKS.get(session_key)
        if lock is None:
            lock = _ATTACH_LOCKS[session_key] = threading.RLock()
        return lock


def _pane_lock(name: str) -> threading.Lock:
    with _PANE_LOCKS_LOCK:
        return _PANE_LOCKS.setdefault(name, threading.Lock())


def tmux_bin() -> str | None:
    return shutil.which("tmux")


def tmux_name(session_key: str) -> str:
    return f"qv-{key_slug(session_key)[:16]}"


def _registry_file(home: Path | None) -> Path:
    return remote_dir_for(home) / "supervised.json"


def _load_registry(home: Path | None) -> dict[str, dict]:
    try:
        data = json.loads(_registry_file(home).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def _save_registry(
    home: Path | None, entries: dict[str, dict], strict: bool = False
) -> None:
    """Write the registry; ``strict`` raises a failed write instead of
    swallowing it (moves between it and ``kept.json`` need to know)."""
    path = _registry_file(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(entries, indent=1))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        if strict:
            raise


def _tmux(*args: str) -> subprocess.CompletedProcess:
    server = ["-L", _TMUX_SOCKET] if _TMUX_SOCKET else []
    return subprocess.run(
        [tmux_bin() or "tmux", *server, *args],
        capture_output=True, text=True, timeout=10,
    )


def tmux_alive(name: str) -> bool:
    if not tmux_bin():
        return False
    try:
        return _tmux("has-session", "-t", name).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def supervised(home: Path | str | None) -> dict[str, dict]:
    """Live supervised entries; dead tmux rows pruned on read."""
    home_path = Path(home) if home is not None else None
    with _registry_lock(home_path):
        entries = _load_registry(home_path)
        dead = [
            key for key, meta in entries.items()
            if meta.get("mode") == "tmux"
            and not tmux_alive(str(meta.get("tmux") or ""))
        ]
        if dead:
            for key in dead:
                entries.pop(key, None)
            _save_registry(home_path, entries)
    return entries


def mark(home: Path | None, session_key: str, meta: dict) -> dict:
    with _registry_lock(home):
        _mark_locked(home, session_key, meta)
    return meta


def _mark_locked(home: Path | None, session_key: str, meta: dict) -> None:
    """mark() with the registry lock already held (attach's handoff)."""
    entries = _load_registry(home)
    entries[session_key] = meta
    _save_registry(home, entries)


def unmark(home: Path | None, session_key: str) -> None:
    with _registry_lock(home):
        _unmark_locked(home, session_key)


def _unmark_locked(home: Path | None, session_key: str) -> None:
    entries = _load_registry(home)
    if entries.pop(session_key, None) is not None:
        _save_registry(home, entries)


def meta_for(home: Path | None, session_key: str) -> dict | None:
    return _load_registry(home).get(session_key)


def set_meta(home: Path | None, session_key: str, field: str,
             value: str) -> bool:
    """Set one field on an attached session's row; False when the
    session is no longer attached (a detach won the race) or the write
    failed. The API channels read ``model`` off the row on every turn,
    so this is how a mid-session switch reaches the next one."""
    with _registry_lock(home):
        entries = _load_registry(home)
        meta = entries.get(session_key)
        if meta is None:
            return False
        meta[field] = value
        try:
            _save_registry(home, entries, strict=True)
        except OSError:
            return False
    return True


def registry(home: Path | None) -> dict[str, dict]:
    """Every supervised entry as stored, read once: ``meta_for`` for a
    caller looking up many keys (no tmux liveness check)."""
    return _load_registry(home)


def launch_tmux(
    session_key: str, argv: list[str], cwd: str | None,
    env: dict | None = None,
) -> str | None:
    """Start ``argv`` in a detached tmux session; returns the tmux name.

    The harness is exec'd directly, with this process's PATH, rather
    than through tmux's default shell: under launchd that shell re-reads
    zsh startup files, and nix-darwin's /etc/zshenv rebuilds PATH
    without Homebrew, so ``claude`` exited 127 and took the pane with it.
    ``env`` carries per-session overrides like an account's config home.
    """
    if not tmux_bin():
        return None
    name = tmux_name(session_key)
    if tmux_alive(name):
        # Callers only launch for keys with no supervised or adoptable
        # kept row, so a live session under this name is not ours (a
        # kept pane now running something else, a leftover): binding
        # to it would hand the phone someone else's process.
        return None
    binary = shutil.which(argv[0]) or argv[0]
    args = ["new-session", "-d", "-s", name,
            "-e", f"PATH={os.environ.get('PATH', '')}"]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    if cwd:
        args += ["-c", cwd]
    command = [binary, *argv[1:]]
    if len(command) == 1:
        # tmux hands a lone command argument to default-shell -c, which
        # is the PATH rebuild above, and splits a binary path with a
        # space in it. Two or more arguments are exec'd as given, so a
        # bare launch goes through env.
        command = ["/usr/bin/env", binary]
    args += command
    try:
        if _tmux(*args).returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    # A harness that dies on launch leaves nothing to write to; report
    # it now instead of "attached" and a prune a few seconds later.
    time.sleep(LAUNCH_SETTLE_S)
    if not tmux_alive(name):
        return None
    return name


def send_text(name: str, text: str) -> bool:
    """Type ``text`` into tmux session ``name`` and press Enter.

    Serialized per pane: the literal-text and Enter send-keys are two
    tmux calls, and an interleaved second writer would merge prompts.
    ``--`` ends tmux's flags, so text that starts with "-" (a pasted
    list, "-v") is typed as text instead of refused as an unknown flag.
    """
    with _pane_lock(name):
        try:
            if _tmux("send-keys", "-t", name, "-l", "--",
                     text).returncode != 0:
                return False
            return _tmux("send-keys", "-t", name, "Enter").returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False


def send_key(name: str, key: str) -> bool:
    """One named tmux key (``Escape``, ``C-c``, digits for menus)."""
    try:
        return _tmux("send-keys", "-t", name, key).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# Markers a permission menu prints on screen; a pane without one is not
# waiting on an approval. Bare words like "allow" are deliberately not
# here — scrollback mentions them constantly.
APPROVAL_MARKERS = (
    "do you want to",
    "don't ask again",
    "esc to cancel",
    # Devin's menu footer is "↑↓ select · ↵ confirm · esc cancel".
    "esc cancel",
    "permission to proceed",
    # Devin's ask-question panel always ends its option list with an
    # "Other (type your own)" row — the surest tell of that prompt.
    "type your own",
)

# Marker lines that are key-hint furniture, not option text: a bare row
# containing one is a footer, never a choice. "type your own" stays out
# of this list on purpose — the Other row it names is a real option.
_FOOTER_MARKERS = tuple(
    m for m in APPROVAL_MARKERS if m != "type your own"
)

# Short stacked option lines ("1. Yes", "❯ Allow once", "Deny") are the
# other shape a permission menu takes — only 2+ in a row counts, so one
# "allow" in normal output can't fake a prompt. Devin numbers rows with
# a bare space ("1 Yes", "8 No"), so whitespace counts as a delimiter
# alongside "." and ")".
_APPROVAL_ITEM = re.compile(
    r"^\s*(?:[❯>•*)\-]\s*)?(?:\d{1,2}[.)]\s*|\d{1,2}\s+)?"
    r"(?:yes|no|allow|deny|approve|reject)\b",
    re.IGNORECASE,
)


# A numbered row in a permission menu: "1. Yes", "2) No", devin's
# cursor row ") 3 Yes", "4 Yes, allow `git` commands". Labels decide
# answers — positions alone cannot, since devin puts "No" at 8 while
# claude puts it at 3. The cursor class covers every glyph a TUI draws
# on the selected row — devin's select widget uses "❭" (U+276D), not
# the "❯" (U+276F) claude draws — or the selected option never parses.
_MENU_OPTION = re.compile(
    r"^\s*(?:[❭❯›>•*)\-]\s*)?(\d{1,2})[.)]?\s+(\S.*?)\s*$"
)
_YES_LABEL = re.compile(r"(?:yes|approve|allow|proceed)\b", re.IGNORECASE)
_DENY_LABEL = re.compile(r"(?:no|deny|reject|cancel)\b", re.IGNORECASE)
# A label that is just the yes-word ("Yes", "approve!") reads as a
# one-shot grant; anything longer ("Yes, allow `git` commands") is a
# standing grant. Same test for the deny word — "No" is a menu row,
# "No errors found" is transcript noise.
_BARE_YES = re.compile(
    r"^(?:yes|approve|allow|proceed)\b[.,!]?\s*$", re.IGNORECASE
)
_BARE_DENY = re.compile(
    r"^(?:no|deny|reject|cancel)\b[.,!]?\s*$", re.IGNORECASE
)


def menu_block(pane: str) -> tuple[list[tuple[str, str]], int]:
    """The menu's ``(digit, label)`` rows and its first line index.

    ``([], -1)`` when the pane shows none. Only the bottom-most numbered
    block counts — numbered lines that scrolled up from earlier output
    ("1. No errors found") are transcript text, not the menu, and a
    stray match must not answer it. Unnumbered non-blank lines between
    two rows are a wrapped label's continuation and fold back into the
    row above; a blank line or a menu footer ends the block instead.
    """
    lines = pane.splitlines()
    run: list[tuple[str, str]] = []
    last: list[tuple[str, str]] = []
    run_start = last_start = -1
    gap: list[str] = []
    for i, ln in enumerate(lines):
        m = _MENU_OPTION.match(ln)
        if m:
            if run and any(
                not g.strip()
                or any(mk in g.lower() for mk in APPROVAL_MARKERS)
                for g in gap
            ):
                # A blank or footer line ended the block — the gap was
                # not a wrap, and this row starts a new menu.
                last, last_start = run, run_start
                run = []
            elif gap and run:
                n, label = run[-1]
                run[-1] = (n, label + " " + " ".join(
                    g.strip() for g in gap))
            if not run:
                run_start = i
            gap = []
            run.append((m.group(1), m.group(2)))
        else:
            gap.append(ln)
    if run:
        return run, run_start
    return last, last_start


def menu_options(pane: str) -> list[tuple[str, str]]:
    """The ``(digit, label)`` rows a numbered menu prints, in order."""
    rows, _ = menu_block(pane)
    return rows


# An unnumbered option row in an arrow-select list — devin's ask panel
# and other TUIs draw the selected row with a cursor glyph ("❭ Option
# A") and siblings as bare lines at the same label column. No digit
# reaches these: answering walks the cursor with arrow keys, so a
# row's positional id is a key distance, not a keystroke.
_CHOICE_CURSOR = re.compile(r"^\s*([❭❯›])\s+(\S.*?)\s*$")
_CHOICE_LABEL_MAX = 80
# A cursor glyph decorating a line — for fingerprint normalization,
# where the same row must hash identically selected or not.
_CURSOR_ROW = re.compile(r"^[❭❯›]\s+")


def _choice_row(line: str) -> tuple[str, int, bool, bool] | None:
    """(label, label column, has cursor, usable) for a row, else None.

    A cursor-glyph row is always a usable option — the glyph is what
    separates a live menu's selected row from stacked output. A bare
    line must be indented and carry text. Furniture — a key-hint
    footer, a marker row, a wrapped line — stays inside the zone but
    is never an option, so it cannot split the list in two. A "?" or
    ":" ending keeps a bare row usable: the cursor's label column, not
    punctuation, separates options from the question line above. Only
    an empty, flush-left, or numbered line is a zone boundary.
    """
    if _MENU_OPTION.match(line):
        return None
    m = _CHOICE_CURSOR.match(line)
    if m:
        return m.group(2), m.start(2), True, True
    text = line.strip()
    col = len(line) - len(line.lstrip())
    if not col or not any(ch.isalnum() for ch in text):
        return None
    usable = not (
        len(text) > _CHOICE_LABEL_MAX
        or "·" in text
        or any(mk in text.lower() for mk in _FOOTER_MARKERS)
    )
    return text, col, False, usable


def choice_block(pane: str) -> tuple[list[tuple[str, str]], int, int | None]:
    """The unnumbered arrow-select list, or ``([], -1, None)``.

    Returns ``(rows, first line index, cursor index)`` where rows are
    ``("1".."k", label)`` numbered top to bottom — positional ids the
    injector turns into arrow keys. A block needs a marker somewhere on
    the pane, a cursor row anchoring the list's label column, and a
    second row at that column; deeper-indented descriptions and the
    question line above fall outside the column and stay out. The
    bottom-most candidate wins, like ``menu_block``.
    """
    if not any(mk in pane.lower() for mk in APPROVAL_MARKERS):
        return [], -1, None
    zones: list[list[tuple[int, str, int, bool, bool]]] = []
    zone: list[tuple[int, str, int, bool, bool]] = []
    for i, ln in enumerate(pane.splitlines()):
        row = _choice_row(ln)
        if row is None:
            if zone:
                zones.append(zone)
                zone = []
            continue
        label, col, is_cursor, usable = row
        zone.append((i, label, col, is_cursor, usable))
    if zone:
        zones.append(zone)
    for zone in reversed(zones):
        cols = [col for _i, _l, col, cur, _u in zone if cur]
        if not cols:
            continue
        col = cols[0]
        rows = [
            (i, l, cur) for i, l, c, cur, u in zone if c == col and u
        ]
        if len(rows) < 2:
            continue
        start = rows[0][0]
        cursor = next(i for i, r in enumerate(rows) if r[2])
        return (
            [(str(n), l) for n, (_i, l, _c) in enumerate(rows, 1)],
            start,
            cursor,
        )
    return [], -1, None


def option_menu(pane: str) -> tuple[list[tuple[str, str]], str, int | None]:
    """The answerable menu rows: ``(rows, kind, cursor index)``.

    ``"numbered"`` rows are keyed by their own digit and answered with
    a keystroke; ``"choices"`` rows are an unnumbered arrow-select list
    whose positional ids the injector walks with Up/Down. Whichever
    block sits lowest is the live one — a numbered run scrolled up from
    an earlier prompt must not outrank the question waiting now.
    """
    numbered, mstart = menu_block(pane)
    choices, cstart, cursor = choice_block(pane)
    if choices and cstart > mstart:
        return choices, "choices", cursor
    if numbered:
        return numbered, "numbered", None
    return choices, "choices", cursor


def menu_key_for(pane: str, decision: str) -> str | None:
    """The digit a ``decision`` maps to on this pane, or None.

    ``allow`` wants the one-shot grant ("Approve once", a bare "Yes") —
    never a standing grant, so a clipped pane whose first row is gone
    can't escalate the answer. ``always`` wants the first standing
    grant that is not a session-wide bypass. ``deny`` prefers a bare
    "No"; menus order deny last, so a deny-word row that reads like
    output text only wins when nothing plainer is there. Rows that
    match nothing leave the fixed fallback map in charge.
    """
    opts = menu_options(pane)
    if not opts:
        return None
    if decision == "deny":
        den = [(n, l) for n, l in opts if _DENY_LABEL.match(l)]
        for n, l in den:
            if _BARE_DENY.match(l):
                return n
        return den[-1][0] if den else None
    yes = [(n, l) for n, l in opts if _YES_LABEL.match(l)]
    once = [
        n for n, l in yes
        if "bypass" not in l.lower()
        and ("once" in l.lower() or _BARE_YES.match(l))
    ]
    standing = [
        n for n, l in yes
        if "bypass" not in l.lower()
        and "once" not in l.lower()
        and not _BARE_YES.match(l)
    ]
    if decision == "allow":
        return once[0] if once else None
    if decision == "always":
        return standing[0] if standing else None
    return None


# Words that mark a menu row as a grant reaching past this one prompt:
# claude's "don't ask again" / "allow all edits during this session",
# codex's "don't ask again for this command", devin's "always allow"
# and "switch to bypass mode", gemini's "allow for this session" /
# "allow always", and the yolo / auto-accept / trust modes.
_STANDING_WORDS = re.compile(
    r"bypass|yolo|always|forever|permanent|dangerous|"
    r"\b(?:don['’]?t|do\s+not|never|stop|without)\s+ask|ask\s+again|"
    r"\bsession\b|\ball\b|\bauto|\btrust|\bremember|\bskip|"
    r"\bfrom\s+now\b|\bfuture\b|\bevery|\baccept\s+edits|"
    r"\bfull\s+access\b",
    re.IGNORECASE,
)
# A one-shot grant once its key hints are gone: "Yes", "Allow once",
# "Yes, proceed" (codex's "Yes, proceed (y)"), devin's "Yes  (Approve
# once)".
_ONE_SHOT = re.compile(
    r"^(?:(?:yes|approve|allow|proceed|once|ok(?:ay)?)\b[.,!]?\s*)+$",
    re.IGNORECASE,
)
_PAREN_HINT = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")


def standing_grant(label: str) -> bool:
    """True when answering this menu row would grant more than the one
    prompt on screen: an always-allow, a don't-ask-again, a session-wide
    allow or a bypass/yolo mode.

    Fails closed: a row that starts with a yes-word and is not plainly
    one-shot ("Yes, allow `git` commands") counts as standing, the same
    split ``menu_key_for`` draws. Rows that are not grants at all — a
    bare "No", "No, and tell Claude what to do differently", a question's
    "SQLite" — are not standing unless a standing word shows up.
    """
    text = str(label or "").strip()
    if _STANDING_WORDS.search(text):
        return True
    if _YES_LABEL.match(text):
        return not _ONE_SHOT.match(_PAREN_HINT.sub(" ", text).strip())
    return False


def decides(label: str) -> bool:
    """True for a row that answers yes or no ("Yes", "No, and tell
    Claude…"), as opposed to "Edit command" or "Describe change"."""
    text = str(label or "").strip()
    return bool(_YES_LABEL.match(text) or _DENY_LABEL.match(text))


def running_context(pane: str) -> str:
    """Devin asks under a "Running command" banner; that plus the
    command block is the prompt's identity — same text the card shows
    and the fingerprint binds a request id to. A wrapped command keeps
    going past its first row, so collection stops only at a menu row,
    a menu marker or a gap — never just at the newline.
    """
    lines = pane.splitlines()
    for i, ln in enumerate(lines):
        text = ln.strip()
        if not text:
            continue
        if text.lower().lstrip("●>•*- ").startswith("running "):
            head = text.lstrip("●>•*- ")
            parts = []
            for nxt in lines[i + 1:]:
                t = nxt.strip()
                low = t.lower()
                if (
                    not t
                    or _MENU_OPTION.match(nxt)
                    or any(m in low for m in APPROVAL_MARKERS)
                    or len(parts) >= 6
                ):
                    break
                parts.append(t.lstrip("$ "))
            return f"{head}: {' '.join(parts)}" if parts else head
    return ""


def pane_shows_approval(pane: str) -> bool:
    text = pane.lower()
    if any(marker in text for marker in APPROVAL_MARKERS):
        return True
    tail = [ln.strip() for ln in pane.splitlines()[-15:]]
    # Menu choices must be a stacked block — "No errors found" and
    # "No files changed" separated by real output are not a menu.
    run = best = 0
    for ln in tail:
        if 0 < len(ln) <= 40 and _APPROVAL_ITEM.match(ln):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best >= 2


# The pane cannot report which prompt an approval id belongs to, so the
# tailer records the id it issued for the currently-visible prompt here.
# Injectors reject a request_id that does not match — a stale or
# superseded id then cannot answer a newer menu.
_PENDING_APPROVALS: dict[str, tuple[str, str | None]] = {}


def set_pending_approval(
    key: str, request_id: str | None, fingerprint: str | None = None
) -> None:
    if request_id:
        _PENDING_APPROVALS[key] = (request_id, fingerprint)
    else:
        _PENDING_APPROVALS.pop(key, None)


def pending_approval(key: str) -> str | None:
    entry = _PENDING_APPROVALS.get(key)
    return entry[0] if entry else None


def pending_approval_fp(key: str) -> str | None:
    entry = _PENDING_APPROVALS.get(key)
    return entry[1] if entry else None


def approval_fingerprint(pane: str) -> str:
    """Hash the prompt-looking lines so a swapped menu gets a fresh id.

    Only marker/question/option lines count — a spinner or clock ticking
    elsewhere on screen must not rotate the outstanding request id. The
    running-command banner does count: two devin prompts can share the
    menu rows while asking about different commands.
    """
    lines = []
    ctx = running_context(pane)
    if ctx:
        lines.append(ctx)
    for ln in pane.splitlines():
        # Cursor decoration never counts: "❭ Other (type your own)" and
        # "Other (type your own)" are the same row at different cursor
        # positions, and a local arrow move must not rotate the id.
        text = _CURSOR_ROW.sub("", ln.strip())
        if not text:
            continue
        low = text.lower()
        if (
            any(m in low for m in APPROVAL_MARKERS)
            or _APPROVAL_ITEM.match(text)
            or text.endswith("?")
        ):
            lines.append(text)
    # The option labels bind too: a question that swaps its choices is a
    # new prompt, and a stale id must not answer it. Cursor position is
    # deliberately not hashed — arrows moving over the same rows leave
    # the outstanding request alone.
    rows, _kind, _cursor = option_menu(pane)
    lines.extend(label for _n, label in rows)
    return hashlib.sha1("\n".join(lines).encode()).hexdigest()[:16]


def capture_pane(name: str, lines: int = 0) -> str:
    """The visible pane; ``lines`` > 0 also pulls that much scrollback.

    Menu detection (``pane_shows_approval``, ``prime_pane``) must see only
    what is on screen — a prompt that scrolled into history is already
    answered, so scrollback stays opt-in.
    """
    try:
        args = ["capture-pane", "-p", "-t", name]
        if lines > 0:
            args += ["-S", f"-{lines}"]
        out = _tmux(*args)
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def kill(name: str) -> bool:
    try:
        return _tmux("kill-session", "-t", name).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def channel_name(home: Path | None, session_key: str) -> str:
    """The tmux session a key's writes go to (registry first, else derive)."""
    meta = _load_registry(home).get(session_key) or {}
    return str(meta.get("tmux") or "") or tmux_name(session_key)


def attach(
    home: Path | None,
    session_key: str,
    session: Session,
    *,
    last_append_at: float | None,
    status: str,
    mode: str = "auto",
    model: str | None = None,
    effort: str | None = None,
) -> tuple[bool, str]:
    """observed → attached: relaunch under the supervisor.

    The phone offers Attach only when the session looks idle; this
    server-side re-check is the one that actually prevents the race —
    a session can go active between list and tap. The checks run under
    the registry lock, the tmux launch and boot outside it, and the
    registry write under it again; the key's attach lock spans all
    three, so concurrent attaches of one key cannot split the handoff.

    ``model``/``effort`` are the same pick a launch takes: checked
    against the resolved channel (``channel_tuning_error``), typed into
    a tmux pane after boot, recorded on the supervised entry either way
    so the channel's own mechanism — headless ``--model``, appserver's
    ``turn/start`` overrides, serve's model object — can carry it.
    """
    flags = resume_flags(session)
    # One attach per key at a time: a second tap waits here and then
    # sees "already attached". The registry lock is held only for the
    # checks and the final write, not across the pane's boot (up to
    # ATTACH_BOOT_S), which held every other attach, detach, keep and
    # the catalog's registry read meanwhile.
    with _attach_lock(session_key):
        with _registry_lock(home):
            ready = _attach_checks_locked(
                home, session_key, session, flags,
                last_append_at=last_append_at, status=status, mode=mode,
                model=model, effort=effort)
        if isinstance(ready[0], bool):
            return ready
        meta, writers = ready
        return _attach_tmux(home, session_key, session, flags, meta,
                            writers, model=model, effort=effort)


def _already_attached(
    current: dict, model: str | None, effort: str | None,
) -> tuple[bool, str]:
    """The answer to an attach of a key that already has an entry.

    Idempotent — but a pick is a request for the session to run that
    value, and a live pane's launch-time apply can't be redone: a pick
    differing from the recorded one is refused rather than silently
    dropped.
    """
    changed = [op for op, want in (("model", model), ("effort", effort))
               if want and want != str(current.get(op) or "")]
    if changed:
        ops = " and ".join(f"/{op}" for op in changed)
        return False, (
            f"already attached; {ops} can only be picked on a "
            "fresh attach — detach the session and attach again")
    return True, "already attached"


def _attach_checks_locked(
    home: Path | None,
    session_key: str,
    session: Session,
    flags: list[str],
    *,
    last_append_at: float | None,
    status: str,
    mode: str,
    model: str | None,
    effort: str | None,
):
    """``attach``'s checks, under the registry lock: ``(ok, note)``
    when the attach is settled here (refused, idempotent, adopted, or
    an API channel marked), else ``(meta, writers)`` for a tmux launch.
    """
    current = _load_registry(home).get(session_key)
    if current is not None:
        return _already_attached(current, model, effort)
    # A tmux session kept running by an earlier detach is taken back
    # as it is, mid-turn or not: launching here would start a second
    # run of the same session next to it.
    adopted = _adopt_kept_locked(home, session_key, session,
                                 model=model, effort=effort)
    if adopted is not None:
        return adopted
    if status == "active":
        return False, "session is mid-turn; try again when it is idle"
    if last_append_at is not None and \
            time.time() - last_append_at < ATTACH_IDLE_S:
        return False, (
            "transcript is still moving; try again when it is idle"
        )
    # The cached status/append time is seconds-stale; the
    # transcript's own mtime is not. Stat it so a turn that started
    # after the last poll still blocks the attach.
    from swe.remote import tail

    transcript = tail.resolve_jsonl_path(session)
    try:
        if transcript is not None and \
                time.time() - transcript.stat().st_mtime < ATTACH_IDLE_S:
            return False, (
                "transcript is still moving; try again when it is idle"
            )
    except OSError:
        pass
    if not flags:
        return False, f"{session.tool_name} cannot be resumed"
    # Tier-A only when the harness binary is actually here —
    # an opencode session on a box without `opencode` still
    # gets the tmux channel.
    mode = resolve_mode(session, mode)
    if mode == "headless" and session.tool_name != "claude":
        return False, "headless attach only supports claude"
    if mode == "serve" and session.tool_name != "opencode":
        return False, "serve attach only supports opencode"
    if mode == "appserver" and session.tool_name != "codex":
        return False, "appserver attach only supports codex"
    if mode not in ATTACH_MODES:
        return False, f"unknown attach mode {mode}"
    binary = _MODE_BINARY.get(mode)
    if binary and shutil.which(binary) is None:
        return False, f"{binary} is not installed"
    blocked = channel_tuning_error(mode, session.tool_name,
                                   model, effort)
    if blocked:
        return False, blocked

    meta = {
        "mode": mode,
        "tool_name": session.tool_name,
        "session_id": session.session_id,
        "cwd": session.path or "",
        "attached_at": time.time(),
    }
    for field, value in (("model", model), ("effort", effort)):
        if value:
            meta[field] = value
    if mode in {"headless", "serve", "appserver"}:
        # The original terminal still owns a writer process — stop
        # it before claiming the session or both sides write. The
        # API channel itself is spawned lazily by the injector on
        # first write, so nothing else to start here.
        _stop_local_writers(session)
        _mark_locked(home, session_key, meta)
        return True, f"attached ({mode})"

    # tmux mode: stand the replacement up BEFORE killing the original,
    # so a failed handoff leaves the human's session running rather
    # than stopped with nothing to show for it. List the original's
    # pids first: the replacement carries the same binary and resume
    # flags, so a scan after launch would SIGTERM it too.
    return meta, _local_writer_pids(session)


def _attach_tmux(
    home: Path | None,
    session_key: str,
    session: Session,
    flags: list[str],
    meta: dict,
    writers: list[int],
    *,
    model: str | None,
    effort: str | None,
) -> tuple[bool, str]:
    """``attach``'s tmux launch, outside the registry lock (the caller
    holds the key's attach lock). The entry is written under the lock
    at the end, unless another process attached the key meanwhile.
    """
    binary = launch_tool_name(session.tool_name)
    name = launch_tmux(
        session_key, [binary, *flags], session.path or None,
        env=account_env(session),
    )
    if name is None:
        taken = tmux_name(session_key)
        if tmux_alive(taken):
            # launch_tmux never binds to a session it did not start.
            return False, (
                f"tmux session {taken} already exists and is not "
                f"supervised; close it (tmux kill-session -t {taken}) "
                "and attach again"
            )
        return False, (
            f"tmux is not available, or {binary} exited right "
            "after launch"
        )
    # A resume in a folder claude has not trusted opens on the trust
    # gate with "No, exit" selected: the tailer read it as an
    # approval and the phone's next Enter quit claude. Get past it
    # before anyone can write to the pane.
    settled = boot_pane(name, timeout=ATTACH_BOOT_S)
    if settled is None:
        kill(name)
        return False, f"{binary} did not finish starting under tmux"
    if not tmux_alive(name):
        return False, f"{binary} exited while starting under tmux"
    if (model or effort) and not _send_launch_controls(
            name, model, effort, before=settled):
        kill(name)
        return False, (
            "attached under tmux but the launch controls could not "
            "be delivered — attach again or send them by hand")
    meta["tmux"] = name
    with _registry_lock(home):
        current = _load_registry(home).get(session_key)
        if current is not None:
            # Another process attached the key while this pane booted
            # (in-process attaches wait on the key's lock): theirs
            # stands, this one goes, answered as any attach of an
            # attached key is (a different pick is refused).
            kill(name)
            return _already_attached(current, model, effort)
        _stop_local_writers(session, writers)
        _mark_locked(home, session_key, meta)
    return True, f"attached under tmux {name}"


def _writer_targets(session: Session) -> list[str]:
    """Arg values a resumed writer's command line must carry.

    Taken from ``resume_flags`` rather than ``session_id`` itself:
    ``codex resume`` takes the thread uuid, which lives inside the
    rollout filename stem — searching for ``session_id`` never finds
    the running ``codex resume <uuid>``.
    """
    return [
        tok for tok in resume_flags(session)
        if tok and not tok.startswith("-") and tok != "resume"
    ]


def _stop_local_writers(
    session: Session, pids: list[int] | None = None
) -> None:
    """SIGTERM ``pids``, or every process found to own ``session``."""
    for pid in _local_writer_pids(session) if pids is None else pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue


_FORK_FLAGS = frozenset({"--fork-session", "--fork"})


def _flag_value(argv: list[str], flag: str) -> str | None:
    for i, tok in enumerate(argv[:-1]):
        if tok == flag:
            return argv[i + 1]
    prefix = flag + "="
    for tok in argv:
        if tok.startswith(prefix):
            return tok[len(prefix):]
    return None


def _local_writer_pids(session: Session) -> list[int]:
    """Harness processes that still own ``session``.

    A process qualifies only when its executable's basename is the
    harness binary AND a resume-flag value appears as a whole argv
    token — so `curl .../claude:<sid>/control` (wrong binary, id buried
    in a URL) never gets killed. A session started bare (``claude``
    with no id flag) is not identifiable this way; its terminal keeps
    write access and the doc's "don't type in both places" guidance
    applies.
    """
    targets = _writer_targets(session)
    binary = os.path.basename(
        launch_tool_name(session.tool_name) or ""
    )
    if not targets or not binary:
        return []
    try:
        out = subprocess.run(
            ["pgrep", "-f", "|".join(
                re.escape(t) for t in targets
            )],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for pid_s in out.stdout.split():
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        try:
            cmdline = subprocess.run(
                ["ps", "-p", pid_s, "-o", "command="],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        try:
            argv = shlex.split(cmdline)
        except ValueError:
            continue
        if not argv or os.path.basename(argv[0]) != binary:
            continue
        # A fork names its source too but writes its own session:
        # attaching the source must not stop it. A fork that names its
        # output (claude --session-id) still owns that one.
        if argv[1:2] == ["fork"] or _FORK_FLAGS.intersection(argv[1:]):
            if _flag_value(argv, "--session-id") not in targets:
                continue
        if not any(t in argv[1:] for t in targets):
            continue
        pids.append(pid)
    return pids


def detach(home: Path | None, session_key: str) -> bool:
    """attached → observed: kill the tmux session, drop the registry row.

    The whole read-kill-write sequence holds the registry lock so a
    concurrent attach cannot interleave and resurrect the entry, and
    the key's attach lock first, so a detach sent while an attach is
    still booting its pane acts on the finished attach.
    """
    with _attach_lock(session_key), _registry_lock(home):
        meta = _load_registry(home).get(session_key)
        killed = True
        if meta and meta.get("mode") == "tmux":
            killed = kill(channel_name(home, session_key))
        _unmark_locked(home, session_key)
        return killed


# -- kept tmux sessions ----------------------------------------------------


def _kept_file(home: Path | None) -> Path:
    return remote_dir_for(home) / "kept.json"


def _load_kept(home: Path | None) -> dict[str, dict]:
    try:
        data = json.loads(_kept_file(home).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def _save_kept(
    home: Path | None, entries: dict[str, dict], strict: bool = False
) -> None:
    path = _kept_file(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(entries, indent=1))
    except OSError:
        if strict:
            raise


def _reconcile_locked(home: Path | None) -> None:
    """Settle a key left in both registries by an interrupted move.

    Both moves (keep: supervised → kept, adopt: kept → supervised)
    write the destination before removing the source, so a crash or a
    failed write in between leaves the key in both, never in neither.
    The supervised row wins either way: that tmux session is still
    running and still ours, and a keep that never finished was never
    reported as done.
    """
    kept_rows = _load_kept(home)
    both = set(kept_rows) & set(_load_registry(home))
    if both:
        for key in both:
            kept_rows.pop(key, None)
        _save_kept(home, kept_rows)


def reconcile_kept(home: Path | str | None) -> None:
    """``_reconcile_locked`` for start-up (the hub and satellite agent)."""
    home_path = Path(home) if home is not None else None
    with _registry_lock(home_path):
        _reconcile_locked(home_path)


def kept(home: Path | str | None) -> dict[str, dict]:
    """Kept tmux sessions by key, as recorded (not checked for life)."""
    home_path = Path(home) if home is not None else None
    with _registry_lock(home_path):
        return _load_kept(home_path)


def pane_pid(name: str) -> int | None:
    """The pid of tmux session ``name``'s live pane, else None.

    A pane whose process exited (``remain-on-exit``) counts as gone. The
    pid names one run of the harness: tmux keeps it for the pane's life,
    so the same pid later means the same process.
    """
    try:
        out = _tmux("list-panes", "-s", "-t", name,
                    "-F", "#{pane_dead} #{pane_pid}")
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        dead, _, pid = line.strip().partition(" ")
        if dead == "0" and pid.isdigit():
            return int(pid)
    return None


def tmux_clients(name: str) -> int | None:
    """How many tmux clients are attached to ``name``; None if unknown."""
    try:
        out = _tmux("display-message", "-p", "-t", name,
                    "#{session_attached}")
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    text = out.stdout.strip()
    return int(text) if text.isdigit() else None


def keep(home: Path | None, session_key: str) -> tuple[bool, str]:
    """attached → observed, leaving the tmux session (and harness) running.

    Only a tmux-mode session has something to leave running. The row
    moves from the supervised registry to ``kept.json`` under the lock,
    with the pane's pid so a later attach can tell the same run from a
    different one under a reused name. The kept row is written first;
    if removing the supervised row then fails, the kept one is taken
    back and the keep fails, and ``_reconcile_locked`` settles whatever
    a crash in between leaves. Waits out an attach of the key still
    booting its pane, as ``detach`` does.
    """
    with _attach_lock(session_key), _registry_lock(home):
        _reconcile_locked(home)
        entries = _load_registry(home)
        meta = entries.get(session_key)
        if meta is None:
            return False, "session is not attached"
        if meta.get("mode") != "tmux":
            return False, (
                f"{meta.get('mode')} sessions have no tmux session to "
                "keep; detach now or when idle instead"
            )
        name = channel_name(home, session_key)
        pid = pane_pid(name)
        if pid is None:
            entries.pop(session_key, None)
            try:
                _save_registry(home, entries, strict=True)
            except OSError as exc:
                return False, f"could not update the registry: {exc}"
            return True, (
                "the tmux session had already exited; session is "
                "observed again"
            )
        kept_rows = _load_kept(home)
        kept_rows[session_key] = {
            **meta, "tmux": name, "pane_pid": pid, "kept_at": time.time(),
        }
        try:
            _save_kept(home, kept_rows, strict=True)
        except OSError as exc:
            return False, f"could not record the kept session: {exc}"
        entries.pop(session_key, None)
        try:
            _save_registry(home, entries, strict=True)
        except OSError as exc:
            kept_rows.pop(session_key, None)
            _save_kept(home, kept_rows)
            return False, f"could not update the registry: {exc}"
        return True, (
            f"stopped supervising; tmux session {name} keeps running "
            f"(tmux attach -t {name})"
        )


def _kept_state(row: dict) -> str:
    """``ours``: the recorded pane still runs. ``gone``: the tmux session
    no longer exists. ``foreign``: it exists, but its pane is not the
    process we left there (a new run under the same name, or a dead
    pane), so it must be neither adopted nor launched over."""
    name = str(row.get("tmux") or "")
    if not name or not tmux_alive(name):
        return "gone"
    pid = pane_pid(name)
    if pid is not None and pid == row.get("pane_pid"):
        return "ours"
    return "foreign"


def _adopt_kept_locked(
    home: Path | None, session_key: str, session: Session,
    *, model: str | None = None, effort: str | None = None,
) -> tuple[bool, str] | None:
    """Take a kept tmux session back under supervision, registry lock held.

    Adopted only when the recorded tmux session is still up with the
    same pane process for the same harness. None means there is nothing
    to adopt and attach should launch as usual; a kept tmux session that
    now runs something else is refused rather than launched over. The
    supervised row is written before the kept one is removed (see
    ``keep``).

    A ``model``/``effort`` pick is honoured only when it matches what
    the kept run was launched with: typing ``/model`` into a live,
    possibly mid-turn pane is not the launch-time apply the pick asks
    for, so a different value is refused rather than silently dropped.
    """
    _reconcile_locked(home)
    kept_rows = _load_kept(home)
    row = kept_rows.get(session_key)
    if row is None:
        return None
    name = str(row.get("tmux") or "")
    state = _kept_state(row)
    if state == "gone":
        kept_rows.pop(session_key, None)
        _save_kept(home, kept_rows)
        return None
    if state == "foreign" or row.get("tool_name") != session.tool_name:
        return False, (
            f"tmux session {name} no longer runs this session; close it "
            f"(tmux kill-session -t {name}) and attach again"
        )
    changed = [op for op, want in (("model", model), ("effort", effort))
               if want and want != str(row.get(op) or "")]
    if changed:
        ops = " and ".join(f"/{op}" for op in changed)
        return False, (
            f"the kept tmux session {name} is still running; {ops} can "
            "only be picked on a fresh attach — detach the session "
            "and attach again"
        )
    meta = {k: v for k, v in row.items()
            if k not in {"pane_pid", "kept_at"}}
    meta["attached_at"] = time.time()
    entries = _load_registry(home)
    entries[session_key] = meta
    try:
        _save_registry(home, entries, strict=True)
    except OSError as exc:
        return False, f"could not update the registry: {exc}"
    kept_rows.pop(session_key, None)
    # A failure here leaves the key in both; the supervised row wins.
    _save_kept(home, kept_rows)
    return True, f"adopted kept tmux session {name}"


def _last_activity(session: Session | None) -> float:
    """Seconds since the epoch of the session's last transcript write."""
    if session is None:
        return 0.0
    from swe.remote import tail

    transcript = tail.resolve_jsonl_path(session)
    if transcript is not None:
        try:
            return transcript.stat().st_mtime
        except OSError:
            pass
    return (session.timestamp or 0) / 1000


def reap_kept(
    home: Path | None,
    session_for,
    now: float | None = None,
) -> list[str]:
    """Close kept tmux sessions nobody uses; returns the reaped keys.

    One is closed when no tmux client is attached to it and neither the
    keep nor its transcript has moved for ``KEPT_IDLE_REAP_S``. A row is
    dropped only once its tmux session is confirmed gone: a failed kill
    stays for the next loop, and a session whose pane now runs another
    process stays recorded (and untouched) so attach keeps refusing it.
    ``session_for(key)`` gives the catalog's Session (or None). Runs
    under the registry lock so it cannot race an attach adopting the
    same session.
    """
    now = time.time() if now is None else now
    reaped: list[str] = []
    with _registry_lock(home):
        _reconcile_locked(home)
        entries = _load_kept(home)
        if not entries:
            return []
        changed = False
        for key, row in list(entries.items()):
            name = str(row.get("tmux") or "")
            state = _kept_state(row)
            if state == "gone":
                entries.pop(key, None)
                changed = True
                continue
            if state == "foreign":
                continue  # not ours to kill; attach refuses it
            if tmux_clients(name) != 0:
                continue  # someone is looking, or tmux would not say
            try:
                session = session_for(key)
            except Exception:
                session = None
            last = max(float(row.get("kept_at") or 0),
                       _last_activity(session))
            if now - last < KEPT_IDLE_REAP_S:
                continue
            if not kill(name) and tmux_alive(name):
                continue  # try again next loop
            entries.pop(key, None)
            changed = True
            reaped.append(key)
        if changed:
            _save_kept(home, entries)
    return reaped


def _pending_dir(home: Path | None) -> Path:
    return remote_dir_for(home) / "pending"


def pending_launch(home: Path | None) -> bool:
    """Whether an unadopted launch marker still counts. While one does,
    a session scan must not come from the aggregator's 60 s disk cache —
    the new transcript would stay invisible to ``adopt_pending`` for
    the rest of the TTL. The marker is deleted on adoption and
    ``adopt_pending`` abandons one after a day, so the window closes
    itself."""
    try:
        markers = list(_pending_dir(home).glob("*.json"))
    except OSError:
        return False
    now = time.time()
    for marker in markers:
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Mid-write or corrupt: adopt_pending drops it once it has
            # stayed unreadable past its grace period.
            return True
        launched = float(data.get("launched_at") or 0) \
            if isinstance(data, dict) else 0.0
        if now - launched <= PENDING_MAX_AGE_S:
            return True
    return False


def launch_tuning_error(
    tool_name: str, model: str | None, effort: str | None
) -> str:
    """The refusal note when ``tool_name``'s launch can't honour
    ``model``/``effort``, else "".

    For a fork or a ``mode: tmux`` attach, whose command line is a
    resume, the mechanism is typing ``/op <v>`` into the pane (a fresh
    launch puts the model on its argv instead: ``fresh_launch_error``),
    allowed only where ``slash_builtins`` verifies
    the TUI runs the command with an argument (claude and codex for
    ``model``, claude for ``effort``). Anything else would send the text
    to the model as a prompt, so the field is refused rather than
    silently dropped — the same honesty an injector answers with
    ``unsupported_control``.
    """
    from swe.remote import slash_builtins

    missing = [
        op for op, value in (("model", model), ("effort", effort))
        if value and not slash_builtins.launch_op_ok(tool_name, op)
    ]
    if not missing:
        return ""
    ops = " and ".join(f"/{op}" for op in missing)
    return (
        f"{tool_name} launches can't take {ops}: its TUI has no "
        f"verified {ops} command"
    )


def fresh_launch_error(
    tool_name: str, model: str | None, effort: str | None,
    reg: dict | None = None, yolo: bool = False,
) -> str:
    """The refusal note when a fresh launch of ``tool_name``
    (``launch_new``) can't honour ``model``/``effort``/``yolo``, else "".

    A fresh process takes the model on its command line, as
    ``capabilities.model`` in harness.json declares
    (``launch_model``); ``effort`` has no launch flag anywhere yet, so
    it keeps the typed ``/effort <v>`` rule of ``launch_tuning_error``;
    ``yolo`` appends the harness's bypass argv, as
    ``capabilities.yolo`` declares (``launch_yolo``).
    """
    from swe.remote import launch_model, launch_yolo

    note = launch_model.model_error(tool_name, model, reg)
    if note:
        return note
    note = launch_tuning_error(tool_name, None, effort)
    if note:
        return note
    return launch_yolo.yolo_error(tool_name, yolo, reg)


def _launch_settings_dir(home: Path | None) -> Path:
    return remote_dir_for(home) / "launch-settings"


def channel_tuning_error(
    mode: str, tool_name: str, model: str | None, effort: str | None
) -> str:
    """The refusal note when a resolved attach mode can't honour
    ``model``/``effort``, else "" — the attach-side twin of
    ``launch_tuning_error``.

    ``tmux`` is a launch pane, so the TUI-builtin rule applies.
    ``headless`` is claude print mode: ``--model`` rides every
    invocation's argv, but no effort flag exists. ``appserver`` and
    ``serve`` take the pick on their own wire fields — ``turn/start``'s
    ``model``/``effort`` overrides, and ``prompt_async``'s
    ``{providerID, modelID}`` plus ``variant``. Serve's model object is
    the one shape constraint a client can't guess, so a ``model``
    without its ``provider/model`` form is refused rather than
    silently dropped — the same honesty an injector answers with
    ``unsupported_control``.
    """
    if mode == "tmux":
        return launch_tuning_error(tool_name, model, effort)
    if mode == "headless":
        if effort:
            return ("claude headless can't take /effort: print-mode "
                    "turns have no effort flag")
        return ""
    if mode == "serve":
        from swe.remote.launch_model import provider_split

        if model and provider_split(model) is None:
            return ("opencode serve can't take /model "
                    f"{model!r}: the channel needs a provider/model "
                    "value")
        return ""
    if mode == "appserver":
        return ""
    if model or effort:
        missing = [op for op, value in (("model", model), ("effort", effort))
                   if value]
        ops = " and ".join(f"/{op}" for op in missing)
        return (f"{tool_name} on the {mode} channel can't take {ops}: "
                f"the channel has no verified control for it")
    return ""


def _send_launch_controls(
    name: str,
    model: str | None,
    effort: str | None,
    *,
    before: str,
) -> bool:
    """Type each picked ``/model``/``/effort`` into a settled pane,
    checking each one actually left the input box.

    A bare ``send_text`` only proves tmux took the keys — a TUI can
    swallow the Enter behind fast-typed text and leave ``/model opus``
    sitting in the input, where the next send would merge into it. So
    each control goes through the same checked delivery the first
    prompt gets (``_deliver``), and the pane re-settles between
    controls so a TUI still applying one is never typed over.
    ``before`` is the settled frame the first control is checked
    against.
    """
    from swe.remote import inject

    frame = before
    for op, value in (("model", model), ("effort", effort)):
        if not value:
            continue
        if not _deliver(
                name, inject.control_text(op, value), before=frame):
            return False
        frame = boot_pane(name)
        if frame is None:
            return False
    return True


def _prime_launch(
    name: str,
    prompt: str,
    model: str | None,
    effort: str | None,
) -> bool:
    """Boot the pane, apply launch-time ``/model``/``/effort``, type the
    prompt — in that order, so the first turn runs on the picked model.

    The command text is the injector's own (``inject.control_text``), so
    a launch types exactly what the control op would. ``prime_pane``
    then re-waits for a settled frame — real settle time for the TUI to
    apply the switch — before delivering the prompt. False when the
    pane never settled or a send could not be delivered.
    """
    if model or effort:
        settled = boot_pane(name)
        if settled is None:
            return False
        if not _send_launch_controls(
                name, model, effort, before=settled):
            return False
    if not prompt:
        return True
    return prime_pane(name, prompt)


def launch_new(
    home: Path | None,
    tool_name: str,
    cwd: str,
    prompt: str,
    *,
    known_ids: list[str] | None = None,
    launch_id: str = "",
    binary: str,
    model: str | None = None,
    effort: str | None = None,
    yolo: bool = False,
    provider: str | None = None,
) -> tuple[bool, str, str | None]:
    """``POST /v1/sessions``: start a fresh harness run under tmux.

    The harness picks its own session id, so the placeholder key cannot
    be marked supervised directly — a pending marker lets the catalog
    adopt the new transcript on its next refresh. ``known_ids`` is the
    catalog's session ids at launch time: adoption refuses any of them,
    so a pre-existing same-cwd session can never be claimed as ours.
    ``launch_id`` rides the marker into the supervised entry, so the hub
    can tell the phone which session its launch became. ``binary`` is the
    harness's registry command (``cursor`` runs as ``cursor-agent``),
    from ``catalog.launch_command``; it is required so no caller falls
    back to guessing it from the tool name (#510). ``model`` goes on
    the harness's command line (``launch_model.model_argv``, per
    harness.json ``capabilities.model``); ``effort`` is typed into the
    pane once it is up. ``yolo`` appends the harness's approval-bypass
    argv (``launch_yolo.yolo_argv``, per ``capabilities.yolo``), so the
    session never waits on a permission prompt. ``provider`` (a slug)
    starts the harness behind ``provider_exec.py``, which reads this
    machine's keys file and execs it with only that provider's
    variables (``launch_provider``, per ``capabilities.provider``); no
    key goes on argv or into the marker. A provider whose key is
    missing here is refused before anything starts.
    ``fresh_launch_error`` names what a harness can't take. All three
    ride the marker into the supervised entry as launch bookkeeping.
    Returns ``(ok, note, tmux_name)``.
    """
    from swe.remote import launch_model, launch_provider, launch_yolo

    blocked = fresh_launch_error(tool_name, model, effort, yolo=yolo)
    if blocked:
        return False, blocked, None
    keys_file = launch_provider.keys_path(home)
    if provider:
        try:
            blocked = launch_provider.provider_error(
                tool_name, provider,
                keys=launch_provider.read_keys(keys_file))
        except ValueError as exc:
            blocked = f"can't read the keys file: {exc}"
        if blocked:
            return False, blocked, None
    if not binary or not shutil.which(binary):
        return False, f"{tool_name} is not installed on this machine", None
    if not cwd or not os.path.isdir(cwd):
        return False, f"no such folder on this machine: {cwd}", None
    # The nonce matters: pid+second collide for two same-harness launches
    # inside one second, and the tmux name derives from the key.
    key = f"new:{tool_name}:{int(time.time())}-{os.getpid()}-{secrets.token_hex(3)}"
    argv = [binary]
    if model:
        try:
            argv += launch_model.model_argv(
                tool_name, model, settings_dir=_launch_settings_dir(home),
                stem=tmux_name(key))
        except (OSError, ValueError) as exc:
            return False, f"can't start {tool_name} on {model}: {exc}", None
    if yolo:
        try:
            argv += launch_yolo.yolo_argv(tool_name)
        except ValueError as exc:
            return False, f"can't start {tool_name} autonomously: {exc}", None
    if provider:
        try:
            argv = launch_provider.wrap_argv(
                tool_name, provider, argv, keys_file=keys_file)
        except ValueError as exc:
            return False, f"can't start {tool_name} on {provider}: {exc}", None
    name = launch_tmux(key, argv, cwd or None)
    if name is None:
        return False, (
            f"tmux is not available, or {binary} exited right after launch"
        ), None
    try:
        folder = _pending_dir(home)
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write_text(folder / f"{name}.json", json.dumps({
            "tmux": name,
            "tool_name": tool_name,
            "cwd": cwd or "",
            "exclude": list(known_ids or []),
            "launched_at": time.time(),
            "launch_id": launch_id,
            "model": model or "",
            "effort": effort or "",
            "yolo": bool(yolo),
            "provider": provider or "",
        }))
    except OSError:
        pass
    # The model is already on the command line; only effort is typed.
    if not _prime_launch(name, prompt, None, effort):
        what = "the launch controls or prompt" if effort else "the prompt"
        # Nothing was delivered, so no turn will ever start: the record
        # says failed rather than leaving the phone polling a pending
        # launch. The pane itself stays up for a terminal attach.
        return False, (
            f"launched under tmux but {what} could not be "
            "delivered — the pane is still up; attach from a "
            "terminal or retry"
        ), name
    return True, "launched under tmux; the session appears on refresh", name


def launch_fork(
    home: Path | None,
    source: Session,
    argv: list[str],
    prompt: str,
    *,
    known_ids: list[str] | None = None,
    launch_id: str = "",
    expect_id: str = "",
    model: str | None = None,
    effort: str | None = None,
) -> tuple[bool, str, str | None]:
    """``POST /v1/sessions/{key}/fork``: run ``argv`` (``fork.fork_argv``)
    under tmux in the source's folder, tracked like ``launch_new``.

    The source is never written to or stopped. Its id joins ``known_ids``
    in the marker's exclude list, and ``fork_of`` keeps a codex resume of
    the source (a new rollout under the same thread) out of adoption too.
    ``expect_id`` is the new session's id when the command line set it
    (claude ``--session-id``): adoption then waits for exactly that id.
    ``model``/``effort`` are typed into the pane once it is up, as in
    ``launch_new``.
    Returns ``(ok, note, tmux_name)``.
    """
    blocked = launch_tuning_error(source.tool_name, model, effort)
    if blocked:
        return False, blocked, None
    binary = argv[0] if argv else ""
    if not binary or not shutil.which(binary):
        return False, f"{source.tool_name} is not installed on this machine", None
    cwd = source.path or ""
    if not cwd or not os.path.isdir(cwd):
        return False, f"no such folder on this machine: {cwd}", None
    key = (f"fork:{source.tool_name}:{int(time.time())}-{os.getpid()}-"
           f"{secrets.token_hex(3)}")
    name = tmux_name(key)
    exclude = set(known_ids or [])
    if source.session_id:
        exclude.add(source.session_id)
    marker = _pending_dir(home) / f"{name}.json"
    # The marker is the reservation: written before the pane exists, and
    # under the lock, so a second fork in this folder sees it at once.
    with _registry_lock(home):
        if not expect_id and fork_blocker(home, source.tool_name, cwd):
            return False, fork_busy_note(source.tool_name), None
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(marker, json.dumps({
                "tmux": name,
                "tool_name": source.tool_name,
                "cwd": cwd,
                "exclude": sorted(exclude),
                "fork_of": source.session_id or "",
                "expect_id": expect_id,
                "launched_at": time.time(),
                "launch_id": launch_id,
                "model": model or "",
                "effort": effort or "",
            }))
        except OSError:
            pass
    if launch_tmux(key, argv, cwd) is None:
        marker.unlink(missing_ok=True)
        return False, (
            f"tmux is not available, or {binary} exited right after launch"
        ), None
    if prompt or model or effort:
        if not _prime_launch(name, prompt, model, effort):
            what = "the launch controls or prompt" if (model or effort) \
                else "the prompt"
            # Same failed-launch reporting as launch_new: nothing was
            # delivered, so the phone must not poll a pending launch
            # for a prompt that never landed.
            return False, (
                f"forked under tmux, but {what} could not be "
                "delivered; the pane is still up — attach from a "
                "terminal or retry"
            ), name
    elif boot_pane(name) is None:
        # No prompt to type, but the pane must still get past a trust
        # gate before the phone's first Enter can land on "No, exit".
        return True, "forked under tmux; it has not finished starting", name
    return True, "forked under tmux; the session appears on refresh", name


# A marker this fresh holds its folder even before its pane is up.
_RESERVE_S = 60.0


def fork_busy_note(tool: str) -> str:
    return (f"another {tool} session started in this folder is still "
            f"waiting to show up; fork again once it has")


def fork_blocker(home: Path | None, tool: str, cwd: str) -> str | None:
    """The pending launch a new id-less fork would race, if any.

    A harness that picks its own id leaves adoption only the folder and
    the creation time to go on, so two launches waiting in one folder
    could claim each other's transcripts. Forks of such harnesses wait
    until the earlier one is adopted or its pane is gone. Markers that
    name their id (claude) never block. Returns the blocker's tmux name.
    """
    real = os.path.realpath(cwd)
    now = time.time()
    try:
        markers = list(_pending_dir(home).glob("*.json"))
    except OSError:
        return None
    for path in markers:
        try:
            pending = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if pending.get("expect_id") or pending.get("tool_name") != tool:
            continue
        if os.path.realpath(str(pending.get("cwd") or "")) != real:
            continue
        name = str(pending.get("tmux") or "")
        fresh = now - float(pending.get("launched_at") or 0) < _RESERVE_S
        if fresh or tmux_alive(name):
            return name
    return None


def prime_pane(name: str, prompt: str, *, timeout: float = 30.0) -> bool:
    """Boot the TUI past its trust dialog, then type ``prompt``.

    Sends nothing into an unknown screen; returns False on the deadline
    so the caller can tell the phone the prompt never landed.
    """
    settled = boot_pane(name, timeout=timeout)
    if settled is None:
        return False
    return _deliver(name, prompt, before=settled)


def boot_pane(name: str, *, timeout: float = 30.0) -> str | None:
    """Wait for a usable screen, confirming the trust gate on the way.

    Polls the pane until a usable screen is up — a fresh launch can sit
    on a blank frame, a trust gate, or a confirm prompt for a while.
    Returns the settled frame, or None at the deadline.
    """
    deadline = time.time() + timeout
    confirmed = False
    # The last frame seen: a TUI still drawing its splash (opencode)
    # drops whatever is typed, so typing waits for one unchanged frame.
    last = None
    while time.time() < deadline:
        raw = capture_pane(name)
        pane = raw.lower()
        if not pane.strip():
            time.sleep(0.5)
            continue
        if "trust this folder" in pane and not confirmed:
            # Claude's trust menu defaults to "No, exit": move to
            # "Yes, I trust this folder" before confirming.
            send_key(name, "Down")
            send_key(name, "Enter")
            confirmed = True
            last = None
            time.sleep(0.5)
            continue
        if "enter to confirm" in pane and not confirmed:
            send_key(name, "Enter")
            confirmed = True
            last = None
            time.sleep(0.5)
            continue
        if raw != last:
            last = raw
            time.sleep(1.0)
            continue
        return raw
    return None


def _deliver(name: str, prompt: str, *, before: str) -> bool:
    """Type ``prompt`` into a settled pane and make sure it was sent."""
    if not send_text(name, prompt):
        return False
    retyped = False
    # Claude's TUI can take fast-typed text as a paste and swallow the
    # Enter right behind it, leaving the prompt in the input box and no
    # transcript for adoption to find: press Enter again while it sits
    # there. A pane exactly as it was before typing took nothing at all:
    # the prompt is typed once more, and a pane still unchanged after
    # that is a failed delivery, never a sent one.
    pane = before
    for _ in range(3):
        time.sleep(1.0)
        pane = capture_pane(name)
        if pane == before:
            if not retyped:
                retyped = True
                if not send_text(name, prompt):
                    return False
            continue
        if not _prompt_unsent(pane, prompt):
            return True
        send_key(name, "Enter")
    pane = capture_pane(name)
    return pane != before and not _prompt_unsent(pane, prompt)


def _prompt_unsent(pane: str, prompt: str) -> bool:
    """True when ``prompt`` still sits in the TUI's input box: the lines
    between the last two horizontal rules, the way Claude draws it. A
    pane with no such box (other harnesses) reads as sent."""
    lines = pane.splitlines()
    rules = [i for i, line in enumerate(lines)
             if line.strip() and set(line.strip()) <= {"─"}]
    if len(rules) < 2:
        return False
    head = (prompt.strip().splitlines() or [""])[0][:40]
    box = "\n".join(lines[rules[-2] + 1:rules[-1]])
    return bool(head) and head in box


# An unreadable pending marker younger than this may still be mid-write.
_PENDING_UNREADABLE_GRACE_S = 60.0


def _drop_if_stale(marker: Path, now: float) -> None:
    try:
        if now - marker.stat().st_mtime > _PENDING_UNREADABLE_GRACE_S:
            marker.unlink(missing_ok=True)
    except OSError:
        pass


def adopt_pending(
    home: Path | None,
    rows: list[tuple[str, Session, str]],
) -> list[str]:
    """Mark newly discovered sessions that came from ``launch_new``.

    A row is adopted when a pending launch's harness and cwd match and
    the session's transcript was written after the launch went up (with
    slack for clock/mtime ordering). Returns the adopted keys.
    """
    folder = _pending_dir(home)
    try:
        markers = list(folder.glob("*.json"))
    except OSError:
        return []
    if not markers:
        return []
    adopted: list[str] = []
    now = time.time()
    loaded: list[tuple[Path, dict]] = []
    for marker in markers:
        try:
            loaded.append(
                (marker, json.loads(marker.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            # Markers are written atomically now, but one from an older
            # build may be caught mid-write: only a marker that has
            # stayed unreadable a while is corrupt and safe to drop.
            _drop_if_stale(marker, now)
    # Ids a fork named up front belong to that fork alone: no other
    # same-folder launch may claim them by creation time.
    claimed = {str(p.get("expect_id")) for _m, p in loaded
               if isinstance(p, dict) and p.get("expect_id")}
    for marker, pending in loaded:
        if not isinstance(pending, dict):
            marker.unlink(missing_ok=True)
            continue
        # Give up on markers older than a day: whatever launched either
        # never wrote a transcript or was missed by the parser.
        if now - float(pending.get("launched_at") or 0) > PENDING_MAX_AGE_S:
            marker.unlink(missing_ok=True)
            continue
        hit = _pending_match(home, pending, rows, claimed)
        if hit is None:
            continue
        key = hit
        meta = {
            "mode": "tmux",
            "tmux": str(pending.get("tmux") or ""),
            "tool_name": str(pending.get("tool_name") or ""),
            "session_id": "",
            "cwd": str(pending.get("cwd") or ""),
            "attached_at": now,
            "launch_id": str(pending.get("launch_id") or ""),
        }
        # The launch-time picks stay on the record as bookkeeping; a
        # session's *current* model is a transcript/ledger question.
        for field in ("model", "effort", "provider"):
            if pending.get(field):
                meta[field] = str(pending[field])
        if pending.get("yolo"):
            meta["yolo"] = True
        mark(home, key, meta)
        marker.unlink(missing_ok=True)
        adopted.append(key)
    return adopted


def _same_thread(tool: str, session_id: str, other: str) -> bool:
    """Whether two session ids are one conversation: a codex resume
    writes a new rollout under the same thread uuid."""
    if session_id == other:
        return True
    if tool != "codex":
        return False
    from swe.sessions.parsers import codex_thread_id

    thread = codex_thread_id(session_id)
    return bool(thread) and thread == codex_thread_id(other)


def _pending_match(
    home: Path | None, pending: dict, rows: list[tuple[str, Session, str]],
    claimed: set[str] = frozenset(),
) -> str | None:
    from swe.remote import tail

    tool = str(pending.get("tool_name") or "")
    cwd = os.path.realpath(str(pending.get("cwd") or ""))
    launched = float(pending.get("launched_at") or 0)
    # Session ids the catalog already knew at launch — ours can only be
    # one the catalog did NOT have, so these never qualify regardless of
    # what the filesystem timestamps say.
    exclude = {str(s) for s in pending.get("exclude") or ()}
    # A fork whose command line named the new id: that id and no other.
    expect = str(pending.get("expect_id") or "")
    fork_of = str(pending.get("fork_of") or "")
    candidates: list[tuple[str, float]] = []
    # A kept session still runs in its own tmux pane: binding its key to
    # this launch's (or fork's) pane would leave two panes on one key and
    # strand the kept one.
    owned = set(_load_registry(home)) | set(_load_kept(home))
    for key, session, _status in rows:
        if key in owned:
            continue
        if session.tool_name != tool:
            continue
        if expect:
            if session.session_id == expect:
                candidates.append((key, launched))
            continue
        if session.session_id in exclude or session.session_id in claimed:
            continue
        if fork_of and _same_thread(tool, session.session_id, fork_of):
            continue
        if os.path.realpath(session.path or "") != cwd:
            continue
        transcript = tail.resolve_jsonl_path(session)
        try:
            st = transcript.stat() if transcript else None
        except OSError:
            st = None
        if st is not None:
            # Creation time, not mtime: a pre-existing session that was
            # written to after launch has a new mtime but an old birth
            # time — adopting it would bind someone else's session to our
            # pane. st_birthtime exists on macOS and statx-capable Linux;
            # ctime is the fallback (still older than mtime for a
            # recycled file).
            created = getattr(st, "st_birthtime", None)
            if created is None:
                created = min(st.st_mtime, st.st_ctime)
        elif session.tool_name in tail._JSONL_ROOTS:
            continue
        else:
            # No JSONL transcript (opencode, kilo, devin, cline keep
            # sessions in databases): the session's own activity time
            # stands in. Every session the catalog knew at launch is
            # already excluded, so only a new one can qualify.
            created = (session.timestamp or 0) / 1000
        # 30s slack for clock/timestamp skew on the same machine.
        if created >= launched - 30:
            candidates.append((key, created))
    if len(candidates) != 1:
        # Zero matches: nothing to adopt yet. Two or more: a human may
        # have started a same-cwd session in the window — adopting would
        # bind their transcript to our pane, so stay pending until the
        # marker ages out instead of guessing.
        return None
    return candidates[0][0]
