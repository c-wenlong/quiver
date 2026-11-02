"""First-run setup: the Mac app's setup sheet (``FirstRunSetupSheet``)
for the hub machine, at ``#/setup``.

``GET /v1/app/setup`` answers both steps the browser can do:

- ``agents``: the coding agents SWE knows (``ProviderCatalog`` in the
  Swift, in its order), each marked found on this machine or not, the
  way ``ProviderDetection.installed`` decides it (swe's installed
  harnesses, plus each extra provider's own commands and paths), the
  harnesses starred now (the sheet's first ticks), and for each one not
  found, swe's install commands (``swe install --json``'s ``methods``).
- ``preview``: ``swe setup --json``'s data, built in process
  (``swe.setup.commands.setup_preview``): harnesses and MCP servers
  setup would add, skills hints, and what linking AGENTS.md would do.

``first_run`` is the Mac app's launch check, ``FirstRun.shouldOffer``:
no shared ``~/.swe/AGENTS.md`` yet. (The Mac app also remembers a
per-Mac "finished or skipped" flag. The web app keeps its own per browser:
a bare ``/`` opens ``#/setup`` once, and Overview shows a card linking
here for as long as ``first_run`` holds; see ``app.js``.)

``POST /v1/app/setup/apply`` with ``{"starred": [ids], "adopt": path or
null}`` is ``swe setup --apply --json --starred=a,b [--adopt=<file>]``
in process (``apply_setup``, which Doctor's setup fix uses too).
Every starred id must be one the page lists (a catalog agent or a
registered harness), and ``adopt`` one of the preview's
``adopt_candidates`` right now; anything else is refused.
The reply is ``{"applied", "first_run", "starred"}``.

``POST /v1/app/setup/hub`` with ``{"url"}`` clones the user's hub repo
as ``~/.swe``, ``swe setup --hub``'s own clone, on a thread: it
answers at once with ``{"hub"}`` (``hub_status``), and the page polls
``GET /v1/app/setup?hub=1`` until ``hub.job.state`` leaves ``cloning``.
409 ``has_hub`` when ~/.swe already holds a hub (or is a file or a
symlink), 409 ``busy`` while a clone or setup runs. The URL is only ever
kept or answered with its credentials removed (``public_url``).

``POST /v1/app/setup/install`` with ``{"id", "via"}`` is ``swe install
<id> --via=<via>`` in process with its output captured, for an agent the
page lists as not found: one of the agent's own install commands, never
a package the request names. It runs as a Doctor fix does (one at a time,
409 while one runs) and answers ``{"error", "setup"}``: what went wrong
with the end of the installer's output (``null`` once the command is on
PATH) and this page's data read again.
"""

from __future__ import annotations

import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path

from swe.webapp.pages import CONFIG_LOCK, PageError, home_path

# ProviderCatalog.all in SWECore/Providers/ProviderVisibility.swift,
# row for row: (id, commands on PATH, paths that exist). Only ids swe
# has no registry entry for carry signs of their own. Keep in step.
PROVIDERS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("claude", (), ()),
    ("codex", (), ()),
    ("devin", (), ()),
    ("cursor", (), ()),
    ("antigravity", ("agy",), ("/Applications/Antigravity.app", "~/Applications/Antigravity.app",
                              "~/.gemini/antigravity", "~/.gemini/antigravity-ide")),
    ("copilot", (), ()),
    ("kimi", ("kimi",), ("~/.kimi",)),
    ("kiro", (), ()),
    ("grok", ("grok",), ()),
    ("zcode", (), ("~/.zcode",)),
    ("opencode", (), ()),
    ("commandcode", ("command-code",), ("~/.commandcode",)),
    ("qoder", ("qodercli", "qoder"), ("/Applications/Qoder.app", "~/Applications/Qoder.app",
                                     "~/.qoder")),
    ("qoder-cn", ("qoderclicn",), ("/Applications/Qoder.app", "~/Applications/Qoder.app",
                                  "~/.qoder")),
    ("gemini", (), ()),
    ("qwen-code", (), ()),
    ("mistral-vibe", (), ()),
    ("crush", (), ()),
    ("cline", (), ()),
    ("kilo", (), ()),
    ("goose", (), ()),
    ("aider", (), ()),
    ("continue", (), ()),
    ("pi", (), ()),
    ("forge", (), ()),
    ("droid", (), ()),
    ("augment", (), ()),
    ("blackbox", (), ()),
    ("freebuff", (), ()),
)
PROVIDER_IDS = tuple(p[0] for p in PROVIDERS)
NAME_MAX = 200
PATH_MAX = 4096

# One setup at a time: it registers harnesses, writes mcp.json, links
# files and rewrites the stars, and two at once could lose one another's
# writes. The Mac app's sheet runs its one swe call the same way.
_LOCK = threading.Lock()


def get(ctx, args) -> dict:
    from swe.init.layout import agents_file
    from swe.setup.commands import setup_preview

    home = home_path(ctx)
    if args.get("hub") == "1":
        # What the page polls while a clone runs: no discovery, no preview.
        return {"hub": hub_status(home)}
    preview = setup_preview(home)
    agents = _agents(home, {h["name"] for h in preview.get("harness", [])})
    return {
        "machine": ctx.machine.get("name") if ctx is not None else None,
        "home": str(home),
        "agents_file": str(agents_file(home)),
        "first_run": _first_run(home),
        "agents": agents,
        "preview": preview,
        "hub": hub_status(home),
    }


def post(ctx, action: str, body: dict) -> dict:
    if action == "install":
        return _install(ctx, body)
    if action == "hub":
        return _start_clone(ctx, body)
    if action != "apply":
        raise PageError(404, "unknown_action", f"unknown setup action: {action}")
    starred = _starred(body.get("starred"))
    adopt = body.get("adopt")
    if adopt is not None and (not isinstance(adopt, str) or not adopt or len(adopt) > PATH_MAX):
        raise PageError(422, "bad_adopt", "adopt must be the path of an instruction file, or null")

    from swe.harness.registry import load_registry_readonly, resolve, starred_names
    from swe.init.commands import agents_file_customised, check_adoptable
    from swe.setup.commands import _instructions_preview, apply_setup
    from swe.webapp.pages.harness import REGISTRY_LOCK

    home = home_path(ctx)
    # The Harnesses page's writes and Doctor's fixes rewrite harness.json
    # too, under the same registry lock, so setup waits for them; it also
    # writes mcp.json and relinks skills, under the MCP and Skills pages'
    # CONFIG_LOCK. A hub clone holds _LOCK for minutes, so that one is a
    # 409 instead of a wait.
    with _apply_lock(), REGISTRY_LOCK, CONFIG_LOCK:
        registry = load_registry_readonly()
        for name in starred:
            if name not in PROVIDER_IDS and resolve(registry, name) is None:
                raise PageError(404, "unknown_harness", f"no agent named {name!r}")
        source = None
        if adopt is not None:
            # Only a file the preview offers right now, so no other path on
            # the hub is ever opened. AGENTS.md holding your own text empties
            # that list, and is said as such: a second adopt is a conflict.
            if agents_file_customised(home):
                raise PageError(409, "not_adoptable", check_adoptable(home, Path(adopt)))
            offered = {c["path"] for c in _instructions_preview(home).get("adopt_candidates", [])}
            if adopt not in offered:
                raise PageError(404, "unknown_file",
                                "That file isn't one setup offers to start AGENTS.md from.")
            source = Path(adopt)
        try:
            applied = apply_setup(home, adopt=source, starred=starred)
        except (EOFError, KeyboardInterrupt):
            raise
        except Exception as exc:
            # Discovery failed before anything was written; the steps
            # after it report their own failures inside ``applied``.
            raise PageError(503, "setup_failed", f"Setup couldn't look at this machine: {exc}") from exc
        return {
            "applied": applied,
            "first_run": _first_run(home),
            "starred": starred_names(load_registry_readonly()),
        }


# --------------------------------------------------------------- hub clone
# One clone per home, on its own thread: ls-remote can take 30 s and the
# clone 10 minutes, far past what a page request should hold a handler
# for. The thread holds _LOCK, so setup's apply can't write ~/.swe
# underneath it; the page polls ``GET /v1/app/setup?hub=1``.

URL_MAX = 2048
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
# Under _JOBS_LOCK: a clone has claimed _LOCK (it may not hold it yet).
_clone_claimed = False
# How often an apply waiting on _LOCK checks whether a clone holds it.
_APPLY_POLL_S = 0.1


def _idle() -> dict:
    return {"state": "idle", "url": None, "error": None, "cloned": None}


def hub_status(home: Path) -> dict:
    """``{can_clone, demo_url, job}``: whether ~/.swe can take a clone
    now (the page shows its hub field only then), the demo hub to fork
    (null when unset), and this hub's last clone. ``job.state`` is
    ``idle``, ``cloning``, ``done`` or ``failed``; ``job.url`` never
    carries credentials."""
    from swe.init.hub import DEMO_HUB_URL, can_clone

    with _JOBS_LOCK:
        job = dict(_JOBS.get(str(home)) or _idle())
    return {"can_clone": can_clone(home), "demo_url": DEMO_HUB_URL or None, "job": job}


def _bad_url(url) -> bool:
    return (not isinstance(url, str) or not url.strip() or len(url) > URL_MAX
            or any(ord(ch) < 32 for ch in url))


def _start_clone(ctx, body: dict) -> dict:
    from swe.init.hub import HubError, check_target, public_url

    url = body.get("url")
    if _bad_url(url):
        raise PageError(422, "bad_url", "url must be a git URL on one line")
    url = url.strip()
    if url.startswith("-"):
        raise PageError(422, "bad_url", public_url(f"not a git URL: {url}"))
    home = home_path(ctx)
    busy = PageError(409, "busy", "Setup or a hub clone is already running. Wait for it, then try again.")
    # Claim first, then take the lock: an apply waiting on _LOCK sees the
    # claim and answers 409 rather than sit behind the clone.
    if not _claim_clone():
        raise busy
    if not _LOCK.acquire(blocking=False):
        _release_claim()
        raise busy
    try:
        check_target(home)
    except HubError as exc:
        _LOCK.release()
        _release_claim()
        raise PageError(409, "has_hub", str(exc)) from None
    with _JOBS_LOCK:
        _JOBS[str(home)] = {"state": "cloning", "url": public_url(url), "error": None, "cloned": None}
    try:
        threading.Thread(target=_clone, args=(home, url), name="setup-hub-clone", daemon=True).start()
    except RuntimeError as exc:
        _finish(home, {"state": "failed", "error": f"couldn't start the clone: {exc}"})
        _LOCK.release()
        _release_claim()
        raise PageError(503, "clone_failed", f"couldn't start the clone: {exc}") from None
    return {"hub": hub_status(home)}


def _claim_clone() -> bool:
    global _clone_claimed
    with _JOBS_LOCK:
        if _clone_claimed:
            return False
        _clone_claimed = True
        return True


def _release_claim() -> None:
    global _clone_claimed
    with _JOBS_LOCK:
        _clone_claimed = False


def _clone_running() -> bool:
    with _JOBS_LOCK:
        return _clone_claimed


@contextmanager
def _apply_lock():
    """_LOCK for an apply: it waits for another apply as before, but a
    clone claiming or holding the lock is a 409, never a wait of minutes
    on one of the hub's few request workers."""
    while not _LOCK.acquire(timeout=_APPLY_POLL_S):
        if _clone_running():
            raise PageError(409, "busy", "A hub clone is still running. Wait for it, then try again.")
    try:
        yield
    finally:
        _LOCK.release()


def _clone(home: Path, url: str) -> None:
    """The clone thread: ``swe setup --hub``'s own clone, then release the
    setup lock and the claim the request took for it.

    The clone's MCP sync rewrites the harness configs the MCP page
    writes, so it holds that page's CONFIG_LOCK, only for the sync: the
    clone and plugin installs before it can take minutes, and the MCP
    page waits on the lock rather than answering busy."""
    from swe.init.hub import HubError, clone_and_register, public_url

    try:
        cloned = clone_and_register(home, url, mcp_lock=CONFIG_LOCK)
    except HubError as exc:
        _finish(home, {"state": "failed", "error": str(exc)})
    except Exception as exc:  # the page must hear about it, never a hung "cloning"
        _finish(home, {"state": "failed", "error": public_url(f"the clone failed: {exc}")})
    else:
        _finish(home, {"state": "done", "cloned": cloned})
    finally:
        _LOCK.release()
        _release_claim()


def _finish(home: Path, update: dict) -> None:
    with _JOBS_LOCK:
        job = _JOBS.setdefault(str(home), _idle())
        job.update(update)


def _install(ctx, body: dict) -> dict:
    from swe.harness.installers import METHODS, installers_for
    from swe.webapp.pages.doctor import install_now

    pid, via = body.get("id"), body.get("via")
    if pid not in PROVIDER_IDS:
        raise PageError(404, "unknown_harness", f"no agent named {pid!r}")
    if via not in METHODS or via not in {inst.via for inst in installers_for(pid)}:
        raise PageError(422, "bad_via", f"{pid} has no {via!r} install command")
    home = home_path(ctx)
    error = install_now(home, pid, via)
    return {"error": error, "setup": get(ctx, {})}


def _starred(value) -> list[str]:
    """The ticked agents, in order, as a list of names, or a refusal."""
    if not isinstance(value, list) or len(value) > len(PROVIDERS) + 200:
        raise PageError(422, "bad_starred", "starred must be a list of agent names")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > NAME_MAX:
            raise PageError(422, "bad_starred", "starred must be a list of agent names")
        if item.strip() not in names:
            names.append(item.strip())
    return names


def _first_run(home: Path) -> bool:
    """``FirstRun.shouldOffer`` without the Mac's done flag: no shared
    AGENTS.md on this machine yet."""
    from swe.init.layout import agents_file

    return not agents_file(home).exists()


def _agents(home: Path, discovered: set[str]) -> dict:
    """Step 1: every catalog agent, found or not, and the stars.

    ``discovered`` is the harnesses setup's own discovery found and would
    register (the preview's ``harness``). On a machine swe has never seen
    the registry is empty, so without them nothing would read as found;
    the Mac app asks ``swe list`` alone."""
    from swe.harness.installers import METHOD_TOOL, WEBSITES, installed_app, installers_for
    from swe.harness.registry import load_registry_readonly, starred_names, state_of
    from swe.harness.tools import is_installed

    error = None
    try:
        registry = load_registry_readonly()
    except (OSError, ValueError) as exc:
        # A check that couldn't run says nothing about what is installed
        # (ProviderVisibilityModel's checkError); the extra signs still do.
        registry, error = {}, str(exc)
    # `swe list --json`: not archived, and its command on PATH.
    swe_installed = {
        name for name, info in registry.items()
        if isinstance(info, dict) and state_of(info) != "archived"
        and is_installed(info.get("command") or name)
    }

    def expand(path: str) -> str:
        return str(home / path[2:]) if path.startswith("~/") else path

    rows = []
    for pid, commands, paths in PROVIDERS:
        found = (pid in swe_installed or pid in discovered
                 or any(shutil.which(cmd) for cmd in commands)
                 or any(os.path.exists(expand(p)) for p in paths))
        row = {"id": pid, "installed": bool(found)}
        if not found:
            row["install"] = [
                {"via": inst.via, "command": inst.display(),
                 "available": shutil.which(METHOD_TOOL[inst.via]) is not None}
                for inst in installers_for(pid)
            ]
            row["website"] = WEBSITES.get(pid)
            app = installed_app(pid)
            if app:
                row["app"] = app
        rows.append(row)
    return {
        "providers": rows,
        "starred": starred_names(registry) if error is None else [],
        "error": error,
    }
