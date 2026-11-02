"""`swe init --hub=<git url>` and `swe setup --hub=<git url>`: bring an
existing hub onto a new machine.

A hub is the user's own git repository (a fork of the demo hub, or one that
grew on their first machine). Cloning it as ``~/.swe`` gives a second
machine the same AGENTS.md, skills, plugins, hooks and MCP config, with the
user's URL as ``origin`` so they push to their own hub from day one (#696).

swe never clones over a hub or over a file: ``~/.swe`` has to be absent,
or a folder with no hub in it yet (no ``AGENTS.md``, no ``.git``). What the
app, the remote hub and earlier swe runs write there on their own (caches,
the usage ledgers, pairing state) does not count, since a hub's
``.gitignore`` keeps all of it out of git. The clone is moved in around it,
and a path that exists on both sides stops the clone before anything moves.
The URL is checked with ``git ls-remote`` first, so a typo or a missing
credential fails before anything is written.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

from swe.paths import GITIGNORE_BODY, swe_dir_for

# Long enough for a slow first contact over SSH, short enough that a host
# that never answers does not hang a terminal forever.
REACH_TIMEOUT_S = 30
CLONE_TIMEOUT_S = 600

# After a clone, registering the hub's marketplaces and installing their
# plugins (one harness CLI call each, seconds when it works) share this
# many seconds. Each call gets at most HUB_COMMAND_TIMEOUT_S, never past
# the budget, and none starts with less than HUB_MIN_START_S left: those
# are reported as OUT_OF_TIME with the command that does them later. The
# MCP sync after them writes files in process and takes no budget.
# REACH + CLONE + this budget must stay well inside the Mac app's limit
# for `swe setup --hub` (SweClient.hubCloneTimeout, 900 s), which
# tests/test_setup_hub_installs.py checks.
HUB_INSTALL_BUDGET_S = 150
HUB_COMMAND_TIMEOUT_S = 90
HUB_MIN_START_S = 15
OUT_OF_TIME = "not attempted: setup ran out of time"
# What `swe init --hub` and `swe sync` give each call: no shared budget.
HUB_PLAIN_TIMEOUT_S = 300

# The public demo hub people fork (or use as a template) to start their own
# (#1446). Every surface that asks for a hub URL shows it as a link line, and
# hides that line when this is empty.
DEMO_HUB_URL = "https://github.com/c-wenlong/swe-demo-hub"

# What every surface says beside the hub URL field.
FORK_NOTE = ("Fork a hub repo first (or use it as a template for a private copy), "
             "then paste your fork's URL.")


class HubError(Exception):
    """Why the hub could not be cloned, worded for the person running init.
    ``keep_staging`` asks clone_hub to leave its staging folder, which the
    message names, because part of the clone could not be put back."""

    def __init__(self, message: str, keep_staging: bool = False):
        super().__init__(message)
        self.keep_staging = keep_staging


def public_url(url: str) -> str:
    """``url`` safe to print: userinfo (a token in the URL) is dropped.

    A pattern rather than urlsplit, so a URL too malformed to parse still
    loses its credentials. scp-style ``git@host:path`` has no ``://`` and
    is left as is: ``git`` there is a login name, not a secret.
    """
    return _USERINFO.sub("", url)


_USERINFO = re.compile(r"(?<=://)[^/?#]*@")


# Either one means ~/.swe already holds a hub: the seed or cloned
# AGENTS.md, or a repository of its own.
HUB_MARKERS = ("AGENTS.md", ".git")


def check_target(home: Path) -> None:
    """Raise unless ``~/.swe`` can take a clone: absent, or a folder with
    no hub in it yet. Machine-local state already there is fine."""
    root = swe_dir_for(home)
    if not os.path.lexists(root):
        return
    if root.is_symlink():
        raise HubError("~/.swe is a symlink; swe will not clone through it")
    if not root.is_dir():
        raise HubError("~/.swe is a file, not a folder")
    try:
        names = {entry.name for entry in root.iterdir()}
    except OSError as exc:
        raise HubError(f"can't read ~/.swe: {exc.strerror or exc}") from None
    found = [name for name in HUB_MARKERS if name in names]
    if found:
        raise HubError(
            f"~/.swe already has a hub (it has {' and '.join(found)}), so swe will "
            "not clone over it. Run swe init without --hub to repair it, or move it "
            "aside first.")


def _plan_merge(src: Path, dest: Path, rel: str = "") -> list[tuple[Path, Path]]:
    """The renames that move ``src``'s contents into ``dest`` without
    overwriting anything: a whole entry where ``dest`` has nothing by that
    name, a walk into a folder both sides have. Raises HubError naming the
    first path both sides have that isn't a folder on both."""
    moves = []
    for entry in sorted(src.iterdir(), key=lambda p: p.name):
        there = dest / entry.name
        shown = f"{rel}{entry.name}"
        if not os.path.lexists(there):
            moves.append((entry, there))
        elif (entry.is_dir() and not entry.is_symlink()
              and there.is_dir() and not there.is_symlink()):
            moves += _plan_merge(entry, there, f"{shown}/")
        else:
            raise HubError(
                f"~/.swe/{shown} is on this machine and in the hub, so swe will "
                "not clone over it. Move it aside, then try again.")
    return moves


def _move_in(clone: Path, root: Path) -> None:
    """Move the finished clone into ``~/.swe``: a plain rename while it
    is absent, else entry by entry around what is already there. Every
    collision is found before anything moves, and a rename that fails puts
    back the ones before it, so no half-merged hub is left behind."""
    if not os.path.lexists(root):
        clone.rename(root)
        return
    moves = _plan_merge(clone, root)
    done: list[tuple[Path, Path]] = []
    try:
        for src, dest in moves:
            src.rename(dest)
            done.append((src, dest))
    except OSError as exc:
        stuck = []
        for src, dest in reversed(done):
            try:
                dest.rename(src)
            except OSError:
                stuck.append(dest)
        reason = f"can't move the clone into ~/.swe: {exc.strerror or exc}"
        if not stuck:
            raise HubError(reason) from None
        # Some of the clone is in ~/.swe and won't come back out: say
        # exactly what, and keep the rest of it where the user can see it.
        names = ", ".join(f"~/.swe/{p.relative_to(root)}" for p in reversed(stuck))
        raise HubError(
            f"{reason}. These couldn't be moved back and are still there: {names}. "
            f"The rest of the clone is in {clone}; remove both before trying again.",
            keep_staging=True) from None


def _exclude_local_state(root: Path) -> None:
    """Keep this machine's own state out of the cloned hub's git.

    A hub's ``.gitignore`` may be older than ours and miss a path swe now
    writes (``data/web-ledger/``), and the clone lands beside that state.
    The missing patterns go into ``.git/info/exclude``, which is this
    clone's alone, so the hub's own files stay untouched."""
    git_dir = root / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink():
        return
    patterns = [line.strip() for line in GITIGNORE_BODY.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    have: set[str] = set()
    for name in (root / ".gitignore", git_dir / "info" / "exclude"):
        try:
            have.update(line.strip() for line in name.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            pass
    missing = [p for p in patterns if p not in have]
    if not missing:
        return
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    try:
        before = exclude.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        before = ""
    lead = "" if not before or before.endswith("\n") else "\n"
    with exclude.open("a", encoding="utf-8") as fh:
        fh.write(f"{lead}# Added by swe: this machine's own state, never versioned.\n")
        fh.write("".join(f"{p}\n" for p in missing))


def can_clone(home: Path) -> bool:
    """Whether a hub can be cloned as ``~/.swe`` now. Setup asks for a
    hub URL only then."""
    try:
        check_target(home)
    except HubError:
        return False
    return True


def _git(args: list[str], timeout: int, run) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        return run(["git", *args], capture_output=True, text=True,
                   timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise HubError(f"git {args[0]} took longer than {timeout}s") from None
    except OSError as exc:
        raise HubError(f"could not run git: {exc}") from None


def clone_hub(home: Path, url: str, run=subprocess.run) -> Path:
    """Clone ``url`` as ``~/.swe`` and return the path; HubError otherwise."""
    url = url.strip()
    if not url:
        raise HubError("--hub needs a git URL: swe init --hub=<url>")
    if url.startswith("-"):
        raise HubError(public_url(f"not a git URL: {url}"))
    check_target(home)
    shown = public_url(url)
    reach = _git(["ls-remote", "--exit-code", url, "HEAD"], REACH_TIMEOUT_S, run)
    # 2 is --exit-code's "reachable, but no HEAD": an empty repository.
    if reach.returncode not in (0, 2):
        detail = (reach.stderr or "").strip().splitlines()
        reason = f": {detail[-1]}" if detail else ""
        raise HubError(public_url(f"can't reach {shown}{reason}"))
    # Clone beside ~/.swe and move it into place only once git is done,
    # so a failed or timed-out clone never leaves a half-written hub that
    # the next --hub run would refuse.
    root = swe_dir_for(home)
    try:
        staging = Path(tempfile.mkdtemp(prefix=".swe-clone-", dir=home))
    except OSError as exc:
        raise HubError(f"can't make a folder to clone into: {exc.strerror or exc}") from None
    target = staging / "hub"
    keep = False
    try:
        cloned = _git(["clone", url, str(target)], CLONE_TIMEOUT_S, run)
        if cloned.returncode != 0:
            detail = (cloned.stderr or "").strip().splitlines()
            reason = f": {detail[-1]}" if detail else ""
            raise HubError(public_url(f"git clone {shown} failed{reason}"))
        check_target(home)  # no hub appeared there while git ran
        try:
            _move_in(target, root)
        except HubError as exc:
            keep = exc.keep_staging
            raise
        except OSError as exc:
            raise HubError(f"can't move the clone into ~/.swe: {exc.strerror or exc}") from None
    finally:
        if not keep:
            shutil.rmtree(staging, ignore_errors=True)
    try:
        _exclude_local_state(root)
    except OSError:
        # The clone is in place and the hub's own .gitignore still applies;
        # failing now would report a finished clone as not done.
        pass
    return root


class Budget:
    """The time setup's harness CLI calls after a clone share, so ``swe
    setup --hub --json`` answers inside the Mac app's limit for it
    (``SweClient.hubCloneTimeout``) however many plugins a hub lists.

    ``timeout()`` is the limit for the next command: at most
    ``HUB_COMMAND_TIMEOUT_S``, never past the deadline, and None once too
    little is left to start one, so that command is not attempted."""

    def __init__(self, seconds: float, clock=time.monotonic):
        self._clock = clock
        self._end = clock() + seconds

    def timeout(self) -> int | None:
        left = self._end - self._clock()
        if left < HUB_MIN_START_S:
            return None
        return int(min(HUB_COMMAND_TIMEOUT_S, left))


def clone_and_register(home: Path, url: str, run=subprocess.run, mcp_lock=None) -> dict:
    """Clone ``url`` as ``~/.swe``, register its marketplaces, install
    their plugins and sync its MCP servers into each harness.

    What ``swe setup --hub`` runs, and the web Setup page's clone; ``swe
    init --hub`` only registers the marketplaces. Returns ``{"url",
    "path", "marketplaces": [{"harness", "marketplace", "error"}],
    "plugins": [...], "mcp": [...]}`` (see ``install_hub_plugins`` and
    ``sync_hub_mcp``) with ``url`` already stripped of credentials.
    Raises HubError when nothing was cloned; nothing after the clone
    raises, a failure is a row.

    After the clone, registering the marketplaces and installing the
    plugins share one ``HUB_INSTALL_BUDGET_S`` budget; what it leaves
    no time for is a row saying so. ``mcp_lock``, when given, is held
    while the MCP servers are written: the web app passes the lock its
    MCP page holds for its own writes to the same harness configs.
    """
    root = clone_hub(home, url, run=run)
    budget = Budget(HUB_INSTALL_BUDGET_S)
    marketplaces = [
        {"harness": harness, "marketplace": marketplace, "error": error}
        for harness, marketplace, error in register_marketplaces(home, budget=budget)
    ]
    plugins = install_hub_plugins(home, budget=budget)
    with mcp_lock if mcp_lock is not None else nullcontext():
        mcp = sync_hub_mcp(home)
    return {
        "url": public_url(url.strip()),
        "path": str(root),
        "marketplaces": marketplaces,
        "plugins": plugins,
        "mcp": mcp,
    }


def _archived(home: Path) -> tuple[dict, set[str]]:
    """The registry (empty when it can't be read) and its archived names."""
    from swe.configuration import ConfigurationError
    from swe.init.layout import archived_names, load_registry

    try:
        registry = load_registry(home)
    except ConfigurationError:
        registry = {}
    return registry, archived_names(registry)


def install_hub_plugins(home: Path, run=None, budget: Budget | None = None) -> list[dict]:
    """Install every plugin each hub marketplace lists, in each harness
    that has that marketplace registered and is not archived, as ``swe
    plugins install <plugin>@<marketplace> --harness=<h>`` does.

    One row per plugin and harness: ``{"harness", "name", "marketplace",
    "skipped", "error", "retry"}``. ``skipped`` is ``"already installed"``
    for one that was, or ``OUT_OF_TIME`` for one ``budget`` left no time
    to start; ``error`` says why an install failed. Installed when both
    are null. ``retry`` is the command that installs a plugin this left
    out, null otherwise. A failure never stops the plugins after it. A
    row with an empty ``harness`` and ``name`` is about the whole hub.
    """
    from swe.find.plugin_drift import HARNESSES, registered_hub_plugins
    from swe.plugins.actions import plugin_action

    rows = []
    try:
        _registry, archived = _archived(home)
        wanted = registered_hub_plugins(home, tuple(h for h in HARNESSES if h not in archived))
    except Exception as exc:  # a broken manifest must not undo the clone
        return [{"harness": "", "name": "", "marketplace": "", "skipped": None,
                 "error": f"couldn't read the hub's plugins: {exc}", "retry": None}]
    for harness, marketplace, name in wanted:
        ref = f"{name}@{marketplace}"
        row = {"harness": harness, "name": name, "marketplace": marketplace,
               "skipped": None, "error": None, "retry": None}
        rows.append(row)
        timeout = budget.timeout() if budget is not None else HUB_PLAIN_TIMEOUT_S
        if timeout is None:
            row["skipped"] = OUT_OF_TIME
        else:
            try:
                result = plugin_action(home, "install", ref, harness, run=run, timeout=timeout)
            except Exception as exc:
                row["error"] = str(exc)
            else:
                if not result["changed"]:
                    if result["detail"] == "already installed":
                        row["skipped"] = result["detail"]
                    else:
                        row["error"] = result["detail"]
        if row["error"] or row["skipped"] == OUT_OF_TIME:
            row["retry"] = f"swe plugins install {ref} --harness={harness}"
    return rows


def mcp_targets(home: Path) -> list[str]:
    """The harnesses setup links into that have an MCP config format swe
    knows: installed (its folder is there, or its command is on PATH),
    not archived, with a verified ``MCP_CONFIG_MAP`` entry. A harness swe
    would only guess a config path for is left out."""
    from swe.init.layout import HARNESS_SIGNATURES, harness_installed, registry_name
    from swe.mcp.cli import MCP_CONFIG_MAP

    registry, archived = _archived(home)
    targets: list[str] = []
    for label, sig in HARNESS_SIGNATURES.items():
        name = registry_name(label)
        if (name in MCP_CONFIG_MAP and name not in targets and name not in archived
                and harness_installed(label, sig, home, registry)):
            targets.append(name)
    return targets


def _hub_server_names(home: Path) -> list[str]:
    """The hub's ``mcp.json`` server names: none when the file is absent.
    Raises CorruptConfigurationError when it is there but can't be read
    or parsed, or its ``mcpServers`` is not an object."""
    from swe.configuration import CorruptConfigurationError
    from swe.mcp.cli import load_json
    from swe.paths import mcp_source_file_for

    path = mcp_source_file_for(home)
    servers = load_json(path).get("mcpServers", {})
    if not isinstance(servers, dict):
        raise CorruptConfigurationError(
            f"mcpServers in {path} must be a JSON object: fix or remove the file")
    return sorted(servers)


def secret_fix(names: list[str]) -> str:
    """What to say about a server left out for want of ``${NAME}`` values."""
    sets = "; ".join(f"swe mcp secret set {name}" for name in names)
    return (f"no value for {', '.join(names)}; "
            f"set {'it' if len(names) == 1 else 'them'} with: {sets}")


def sync_hub_mcp(home: Path) -> list[dict]:
    """``swe mcp sync swe <targets>`` for the harnesses ``mcp_targets``
    names, without asking.

    One row per server and harness: ``{"harness", "name", "skipped",
    "error", "needs"}``, synced when ``skipped`` and ``error`` are null.
    A server whose ``${NAME}`` has no value is skipped with the command
    that sets it (``secret_fix``) and those names in ``needs``, and the
    rest still sync; a server the harness already has is left as it is.
    Only names reach a row, never a secret's value. A hub ``mcp.json``
    that is there but can't be read or parsed is one error row with an
    empty ``harness`` and ``name``; an absent one syncs nothing.
    """
    from swe.configuration import CorruptConfigurationError
    from swe.mcp.cli import LOCAL_ONLY_NOTE, get_tool_config, sync_hub_to

    try:
        names = _hub_server_names(home)
    except CorruptConfigurationError as exc:
        # One row about the whole file: nothing could sync from it.
        return [{"harness": "", "name": "", "skipped": None,
                 "error": str(exc), "needs": []}]
    if not names:
        return []
    rows: list[dict] = []
    for target in mcp_targets(home):
        def row(name, skipped=None, error=None, needs=(), target=target):
            rows.append({"harness": target, "name": name, "skipped": skipped,
                         "error": error, "needs": list(needs)})
        try:
            report = sync_hub_to(target)
        except Exception as exc:
            for name in names:
                row(name, error=str(exc))
            continue
        if report["code"]:
            said = report["said"].splitlines()
            reason = said[-1] if said else f"swe mcp sync stopped with status {report['code']}"
            for name in names:
                row(name, error=reason)
            continue
        label = (get_tool_config(target) or {}).get("label", target)
        done = report["targets"][0] if report["targets"] else {}
        for name in done.get("added", []) + done.get("updated", []):
            row(name)
        for name in done.get("skipped", []):
            row(name, skipped=f"{label} already has a server by this name; left as it is")
        for name in done.get("unsupported", []):
            row(name, skipped=f"a remote server; {label} {LOCAL_ONLY_NOTE}")
        for name, needs in sorted(report["missing"].items()):
            row(name, skipped=secret_fix(needs), needs=needs)
    return rows


def register_marketplaces(home: Path, run=None,
                          budget: Budget | None = None) -> list[tuple[str, str, str | None]]:
    """Register each hub ``plugins/<name>`` marketplace with every harness.

    The work is ``swe find plugins``'s own ``unregistered`` fixes, so the
    commands are the ones that module builds (``claude plugin marketplace
    add <dir>``, ``codex plugin marketplace add <dir>``) and a harness that
    is not installed is never asked. Returns ``(harness, marketplace,
    error)`` per attempt; ``error`` is None when it worked, and
    ``OUT_OF_TIME`` for one ``budget`` left no time to start.
    """
    from swe.find.plugin_drift import FixError, apply_fix, plugin_drift

    results = []
    for finding in plugin_drift(home):
        if finding.kind != "unregistered":
            continue
        timeout = budget.timeout() if budget is not None else HUB_PLAIN_TIMEOUT_S
        if timeout is None:
            results.append((finding.harness, finding.marketplace, OUT_OF_TIME))
            continue
        try:
            apply_fix(home, finding.id, run=run, timeout=timeout)
        except FixError as exc:
            results.append((finding.harness, finding.marketplace, str(exc)))
        else:
            results.append((finding.harness, finding.marketplace, None))
    return results
