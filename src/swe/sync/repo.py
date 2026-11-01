"""Keep the ``~/.swe`` hub repo in step with its remote (#525).

``swe sync`` commits what changed here, brings in the remote, and pushes.
Two branch models:

- **Device mode**, when the checkout sits on ``device/<machine>``: each
  machine keeps its own branch. Sync merges ``origin/device/<machine>``
  (the same branch pushed from another clone) and then the remote's
  default branch (``origin/main``) into it, and pushes the device branch
  only. ``main`` itself is never pushed.
- **Single-branch mode**, any other branch: every machine shares it. Sync
  merges ``origin/<branch>`` and pushes ``<branch>``.

Both modes merge rather than rebase: a merge never rewrites a commit, and
every merge is tried first with ``git merge-tree`` on unreferenced
commits, so a conflict is found before the branch, the index or the
working tree changes. A refused sync leaves the repo exactly as it was.
Nothing is ever force-pushed.

What is never committed: everything ``paths.GITIGNORE_BODY`` lists
(``secrets/``, ``cache/``, the ledgers, ``data/remote/`` ...) is excluded
by pathspec on every ``git add``, so a hub whose own ``.gitignore`` is
older than this swe still keeps them out. They are reported as held back.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from swe.paths import GITIGNORE_BODY, swe_dir_for

DEVICE_PREFIX = "device/"
REMOTE = "origin"
GIT_TIMEOUT_S = 60
NETWORK_TIMEOUT_S = 180
# git hash-object -t tree /dev/null: what an unborn branch diffs against.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
MESSAGE_FILE_LIMIT = 20


class SyncError(Exception):
    """Why sync stopped, worded for the person running it."""


def never_synced() -> list[str]:
    """Top-level paths sync never stages, from ``GITIGNORE_BODY``.

    ``secrets`` is always in the list, whatever the body says."""
    names = []
    for line in GITIGNORE_BODY.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(line.strip("/"))
    if "secrets" not in names:
        names.append("secrets")
    return names


def _is_never_synced(path: str) -> bool:
    return any(path == name or path.startswith(f"{name}/") for name in never_synced())


def group_of(path: str) -> str:
    """The hub primitive a changed path belongs to, for status and messages."""
    head = path.split("/", 1)[0]
    if head in ("skills", "plugins", "hooks", "mcp") and "/" in path:
        return head
    if head == "AGENTS_MD":
        return "AGENTS.md"
    if head in ("AGENTS.md", "mcp.json"):
        return head
    return "config"


def _env(extra: dict | None = None) -> dict:
    # No optional locks: a status read must not rewrite the real index.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_MERGE_AUTOEDIT": "no",
           "GIT_OPTIONAL_LOCKS": "0"}
    if extra:
        env.update(extra)
    return env


def _git(root: Path, *args: str, check: bool = True, timeout: int = GIT_TIMEOUT_S,
         env: dict | None = None, input: str | None = None) -> subprocess.CompletedProcess:
    try:
        done = subprocess.run(["git", *args], cwd=root, capture_output=True,
                              text=True, timeout=timeout, env=_env(env), input=input)
    except subprocess.TimeoutExpired:
        raise SyncError(f"git {args[0]} took longer than {timeout}s") from None
    except OSError as exc:
        raise SyncError(f"could not run git: {exc}") from None
    if check and done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        reason = f": {detail[-1]}" if detail else ""
        raise SyncError(f"git {args[0]} failed{reason}")
    return done


def _out(root: Path, *args: str, **kw) -> str:
    return _git(root, *args, **kw).stdout.strip()


def _ref_exists(root: Path, ref: str) -> bool:
    return _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}",
                check=False).returncode == 0


def _is_ancestor(root: Path, a: str, b: str) -> bool:
    return _git(root, "merge-base", "--is-ancestor", a, b, check=False).returncode == 0


def _count(root: Path, rng: str) -> int:
    try:
        return int(_out(root, "rev-list", "--count", rng) or 0)
    except ValueError:
        return 0


def machine_name() -> str:
    return socket.gethostname().split(".", 1)[0] or "this machine"


@dataclass
class RepoState:
    root: Path
    branch: str
    mode: str                      # "device" or "single"
    machine: str
    remote_url: str | None
    head: str | None               # None on an unborn branch
    upstream: str | None           # origin/<branch> when the remote has it
    base: str | None               # origin/main in device mode
    git_dir: Path = field(default=Path())

    @property
    def incoming(self) -> list[str]:
        """Remote refs sync merges, in order."""
        return [ref for ref in (self.upstream, self.base) if ref]


def _operation_in_progress(git_dir: Path) -> str | None:
    for name, what in (("MERGE_HEAD", "a merge"), ("rebase-merge", "a rebase"),
                       ("rebase-apply", "a rebase or am"),
                       ("CHERRY_PICK_HEAD", "a cherry-pick"),
                       ("REVERT_HEAD", "a revert")):
        if (git_dir / name).exists():
            return what
    return None


def inspect(home: Path) -> RepoState:
    """The hub repo's branch, mode and remote refs. Raises SyncError when
    there is nothing sync can work with."""
    root = swe_dir_for(home)
    if not root.is_dir():
        raise SyncError("~/.swe does not exist yet; run swe init first")
    if shutil.which("git") is None:
        raise SyncError("git is not installed")
    probe = _git(root, "rev-parse", "--show-toplevel", "--absolute-git-dir", check=False)
    lines = probe.stdout.strip().splitlines()
    if probe.returncode != 0 or len(lines) < 2:
        raise SyncError("~/.swe is not a git repository; run git init there, "
                        "add a remote named origin, then swe sync")
    if Path(lines[0]).resolve() != root.resolve():
        raise SyncError(f"~/.swe sits inside another git repository ({lines[0]}); "
                        "swe sync only works on a hub that is its own repository")
    git_dir = Path(lines[1])
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch.returncode != 0:
        raise SyncError("~/.swe is on a detached HEAD; check out a branch first")
    name = branch.stdout.strip()
    url = _git(root, "remote", "get-url", REMOTE, check=False)
    remote_url = url.stdout.strip() if url.returncode == 0 else None
    head = None
    if _ref_exists(root, "HEAD"):
        head = _out(root, "rev-parse", "HEAD")
    device = name.startswith(DEVICE_PREFIX) and len(name) > len(DEVICE_PREFIX)
    upstream = f"{REMOTE}/{name}"
    base = None
    if device:
        default = _git(root, "symbolic-ref", "--quiet", "--short",
                       f"refs/remotes/{REMOTE}/HEAD", check=False)
        base = default.stdout.strip() if default.returncode == 0 else f"{REMOTE}/main"
        if base == upstream or not _ref_exists(root, base):
            base = None
    return RepoState(
        root=root, branch=name, mode="device" if device else "single",
        machine=name[len(DEVICE_PREFIX):] if device else machine_name(),
        remote_url=remote_url, head=head,
        upstream=upstream if _ref_exists(root, upstream) else None,
        base=base, git_dir=git_dir)


def fetch(state: RepoState) -> None:
    if not state.remote_url:
        raise SyncError(f"~/.swe has no remote named {REMOTE}; add one with "
                        f"git -C ~/.swe remote add {REMOTE} <url>")
    _git(state.root, "fetch", "--quiet", "--prune", REMOTE, timeout=NETWORK_TIMEOUT_S)
    # Ask the remote which branch is its default, so device mode merges
    # it even in a hub that was `git init`ed (no origin/HEAD) or whose
    # default was renamed since the clone.
    _git(state.root, "remote", "set-head", REMOTE, "--auto", check=False,
         timeout=NETWORK_TIMEOUT_S)


def _parse_name_status(raw: str) -> list[tuple[str, str]]:
    parts = raw.split("\0")
    rows = []
    i = 0
    while i + 1 < len(parts):
        status, path = parts[i], parts[i + 1]
        if status:
            rows.append((status[0], path))
        i += 2
    return rows


class _ScratchIndex:
    """A copy of the repo's index that ``git add`` can fill without
    touching the real one, so a preview or a dry run changes nothing."""

    def __init__(self, state: RepoState):
        self.state = state

    def __enter__(self) -> dict:
        self.dir = tempfile.mkdtemp(prefix="swe-sync-")
        index = Path(self.dir) / "index"
        real = self.state.git_dir / "index"
        if real.exists():
            # copy2 keeps the mtime: git spots a "racily clean" entry (a
            # file changed in the same second the index was written) by
            # comparing against the index file's own mtime, and a fresh
            # mtime would hide that edit from the preview.
            shutil.copy2(real, index)
        return {"GIT_INDEX_FILE": str(index)}

    def __exit__(self, *exc) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def _changed_paths(state: RepoState, env: dict | None = None) -> list[str]:
    """Every path ``git add --all`` would stage, minus the never-synced ones."""
    raw = _git(state.root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
               "--no-renames", env=env).stdout
    paths = [entry[3:] for entry in raw.split("\0") if len(entry) > 3]
    return [p for p in paths if not _is_never_synced(p.rstrip("/"))]


def _stage(state: RepoState, env: dict | None = None) -> None:
    """Stage what changed, path by path. Exclude pathspecs would read
    better, but git fails the whole add when one names an ignored folder."""
    paths = _changed_paths(state, env)
    if paths:
        _git(state.root, "add", "--all", "--pathspec-from-file=-", "--pathspec-file-nul",
             env=env, input="\0".join(paths))


def _staged(state: RepoState, env: dict | None = None) -> list[tuple[str, str]]:
    raw = _git(state.root, "diff", "--cached", "--name-status", "--no-renames", "-z",
               state.head or EMPTY_TREE, env=env).stdout
    return _parse_name_status(raw)


def pending_changes(state: RepoState) -> list[tuple[str, str]]:
    """``(status, path)`` for what sync would commit, read through a
    scratch index."""
    with _ScratchIndex(state) as env:
        _stage(state, env)
        return _staged(state, env)


def held_back(state: RepoState) -> list[str]:
    """Changed paths sync keeps out although the hub's .gitignore lets
    them in: the hub's .gitignore is older than this swe's."""
    raw = _git(state.root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
               "--no-renames", "--", *never_synced()).stdout
    return [entry[3:] for entry in raw.split("\0") if len(entry) > 3]


def _changes_json(changes: list[tuple[str, str]]) -> list[dict]:
    return [{"status": s, "path": p, "group": group_of(p)} for s, p in changes]


def _groups(changes: list[tuple[str, str]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for _, path in changes:
        group = group_of(path)
        out[group] = out.get(group, 0) + 1
    return out


def status(home: Path, do_fetch: bool = False) -> dict:
    """The ``swe sync status --json`` document. A hub sync cannot work
    with comes back with ``ready: false`` and ``problem`` instead of
    raising, so a settings card can show why."""
    root = swe_dir_for(home)
    doc: dict = {"path": str(root), "ready": False, "problem": None}
    try:
        state = inspect(home)
    except SyncError as exc:
        doc["problem"] = str(exc)
        return doc
    fetched, fetch_error = False, None
    if do_fetch:
        try:
            fetch(state)
            fetched = True
            state = inspect(home)
        except SyncError as exc:
            fetch_error = str(exc)
    read_error = None
    try:
        changes = pending_changes(state)
        kept_out = held_back(state)
    except SyncError as exc:
        changes, kept_out, read_error = [], [], str(exc)
    ahead = behind = base_behind = 0
    if state.head and state.upstream:
        ahead = _count(root, f"{state.upstream}..HEAD")
        behind = _count(root, f"HEAD..{state.upstream}")
    elif state.head:
        ahead = _count(root, "HEAD")
    if state.head and state.base:
        base_behind = _count(root, f"HEAD..{state.base}")
    operation = _operation_in_progress(state.git_dir)
    problem = read_error
    if operation:
        problem = f"~/.swe is in the middle of {operation}; finish or abort it first"
    elif not state.remote_url:
        problem = f"~/.swe has no remote named {REMOTE}"
    from swe.init.hub import public_url
    doc.update({
        "ready": problem is None,
        "problem": problem,
        "mode": state.mode,
        "branch": state.branch,
        "machine": state.machine,
        "remote": public_url(state.remote_url) if state.remote_url else None,
        "upstream": state.upstream,
        "base": state.base,
        "ahead": ahead,
        "behind": behind,
        "base_behind": base_behind,
        "changes": _changes_json(changes),
        "groups": _groups(changes),
        "held_back": kept_out,
        "fetched": fetched,
        "fetch_error": fetch_error,
    })
    return doc


def commit_message(state: RepoState, changes: list[tuple[str, str]]) -> str:
    groups = _groups(changes)
    summary = ", ".join(f"{name} {n}" for name, n in sorted(groups.items()))
    noun = "file" if len(changes) == 1 else "files"
    lines = [f"swe sync from {state.machine}: {len(changes)} {noun} ({summary})", ""]
    for code, path in changes[:MESSAGE_FILE_LIMIT]:
        lines.append(f"{code} {path}")
    if len(changes) > MESSAGE_FILE_LIMIT:
        lines.append(f"... and {len(changes) - MESSAGE_FILE_LIMIT} more")
    return "\n".join(lines) + "\n"


def _commit_tree(state: RepoState, tree: str, parents: list[str], message: str) -> str:
    args = ["commit-tree", tree]
    for parent in parents:
        args += ["-p", parent]
    args += ["-m", message]
    return _out(state.root, *args)


def _trial(state: RepoState, changes, message: str) -> tuple[list[dict], list[str]]:
    """Merge every incoming ref onto a would-be commit of ``changes``,
    all in unreferenced objects. Returns ``(merges, conflicts)``; nothing
    in the repo moves either way."""
    tip = state.head
    if changes:
        with _ScratchIndex(state) as env:
            _stage(state, env)
            tree = _out(state.root, "write-tree", env=env)
        tip = _commit_tree(state, tree, [state.head] if state.head else [], message)
    merges = []
    for ref in state.incoming:
        new = _count(state.root, f"{tip}..{ref}") if tip else _count(state.root, ref)
        if not new:
            continue
        merges.append({"ref": ref, "commits": new})
        if tip is None or _is_ancestor(state.root, tip, ref):
            tip = _out(state.root, "rev-parse", ref)
            continue
        if _git(state.root, "merge-base", tip, ref, check=False).returncode != 0:
            raise SyncError(f"~/.swe and {ref} share no history, so swe sync will not "
                            f"merge them; merge it once by hand (git -C ~/.swe merge "
                            f"--allow-unrelated-histories {ref}), then sync")
        trial = _git(state.root, "merge-tree", "--write-tree", "--name-only",
                     "--no-messages", tip, ref, check=False)
        lines = trial.stdout.splitlines()
        if trial.returncode == 1:
            files = sorted({line for line in lines[1:] if line.strip()})
            return merges, files
        if trial.returncode != 0 or not lines:
            detail = (trial.stderr or "").strip().splitlines()
            raise SyncError("git merge-tree failed (swe sync needs git 2.38 or newer)"
                            + (f": {detail[-1]}" if detail else ""))
        tip = _commit_tree(state, lines[0], [tip, ref], f"trial merge {ref}")
    return merges, []


def _rollback(state: RepoState, head: str | None, mid: str | None, index_tree: str) -> None:
    """Put the repo back as it was before the sync: abort a merge in
    progress, take the working tree back from any merge that already
    landed (``--keep`` moves only files the merges changed and refuses
    to touch an edit of the user's), then restore HEAD and the index."""
    _git(state.root, "merge", "--abort", check=False)
    if mid and _out(state.root, "rev-parse", "HEAD", check=False) != mid:
        _git(state.root, "reset", "--quiet", "--keep", mid, check=False)
    if head:
        _git(state.root, "reset", "--quiet", "--soft", head, check=False)
    else:
        _git(state.root, "update-ref", "-d", "HEAD", check=False)
    _git(state.root, "read-tree", index_tree, check=False)


def sync(home: Path, dry_run: bool = False, relink=None) -> dict:
    """Commit, merge in, push, then re-link. Returns the ``swe sync
    --json`` document; ``result`` is ``synced``, ``up-to-date``,
    ``would-sync`` (dry run), ``conflict`` or ``push-failed``. Raises
    SyncError when the repo can't be synced at all.

    ``relink(home, changed_paths)`` runs after new content came in; the
    default re-links harnesses the way ``swe init`` does after a clone.

    One sync per repo at a time (the app, the web card and a terminal can
    all start one): a second is refused rather than run, since its
    rollback could otherwise undo the first one's commit."""
    state = inspect(home)
    with _sync_lock(state.git_dir):
        return _sync_locked(home, dry_run, relink)


@contextlib.contextmanager
def _sync_lock(git_dir: Path):
    # Imported here: `swe --help` must start on a platform without fcntl.
    try:
        import fcntl
    except ImportError:
        raise SyncError("swe sync needs fcntl (macOS or Linux)") from None
    path = git_dir / "swe-sync.lock"
    try:
        handle = open(path, "a+")
    except OSError as exc:
        raise SyncError(f"can't open {path}: {exc.strerror or exc}") from None
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SyncError("another swe sync is running on ~/.swe; wait for it, "
                            "then try again") from None
        yield
    finally:
        handle.close()


def _sync_locked(home: Path, dry_run: bool, relink) -> dict:
    state = inspect(home)
    operation = _operation_in_progress(state.git_dir)
    if operation:
        raise SyncError(f"~/.swe is in the middle of {operation}; finish or abort "
                        "it (git -C ~/.swe status says how), then sync again")
    fetch(state)
    state = inspect(home)
    changes = pending_changes(state)
    message = commit_message(state, changes)
    merges, conflicts = _trial(state, changes, message)
    doc = {
        "dry_run": dry_run,
        "mode": state.mode,
        "branch": state.branch,
        "machine": state.machine,
        "base": state.base,
        "commit": ({"message": message.splitlines()[0], "files": _changes_json(changes)}
                   if changes else None),
        "merges": merges,
        "conflicts": conflicts,
        "held_back": held_back(state),
        "would_push": False,
        "pushed": False,
        "push_error": None,
        "relinked": None,
    }
    if conflicts:
        doc["result"] = "conflict"
        return doc
    unpushed = state.head is not None and (
        state.upstream is None or _count(state.root, f"{state.upstream}..HEAD") > 0)
    will_push = bool(changes or merges or unpushed)
    doc["would_push"] = will_push
    if dry_run:
        doc["result"] = "would-sync" if will_push else "up-to-date"
        return doc
    if not will_push:
        doc["result"] = "up-to-date"
        return doc

    index_tree = _out(state.root, "write-tree")
    before = mid = state.head
    try:
        if changes:
            _stage(state)
            leaked = [p for _, p in _staged(state) if _is_never_synced(p)]
            if leaked:
                raise SyncError("refusing to commit paths that never sync: "
                                + ", ".join(leaked))
            _git(state.root, "commit", "--quiet", "-m", message)
        mid = _out(state.root, "rev-parse", "HEAD") if _ref_exists(state.root, "HEAD") else None
        for merge in merges:
            ref = merge["ref"]
            _git(state.root, "merge", "--quiet", "--no-edit", "-m",
                 f"Merge {ref} into {state.branch} (swe sync from {state.machine})", ref)
    except SyncError:
        _rollback(state, before, mid, index_tree)
        raise
    # Link what came in before pushing: the merge has landed, and a push
    # that fails or hangs must not leave it unlinked (a retry has no
    # merge left to notice it by).
    if merges:
        raw = _out(state.root, "diff", "--name-only", "-z", mid or EMPTY_TREE, "HEAD")
        changed = [p for p in raw.split("\0") if p]
        if changed:
            doc["relinked"] = (relink or default_relink)(home, changed)
    # Never --force: a push the remote refuses (someone pushed meanwhile)
    # is reported, and the next sync merges it in.
    push_args = ["push", "--quiet"]
    if state.upstream is None:
        push_args.append("--set-upstream")
    try:
        push = _git(state.root, *push_args, REMOTE, f"HEAD:refs/heads/{state.branch}",
                    check=False, timeout=NETWORK_TIMEOUT_S)
    except SyncError as exc:
        doc["push_error"] = str(exc)
    else:
        if push.returncode == 0:
            doc["pushed"] = True
        else:
            detail = (push.stderr or "").strip().splitlines()
            doc["push_error"] = detail[-1] if detail else "git push failed"
    doc["result"] = "synced" if doc["pushed"] else "push-failed"
    return doc


def default_relink(home: Path, changed: list[str]) -> dict:
    """What ``swe init --hub`` does after a clone, for content that just
    came in: link every harness, register new plugin marketplaces, and push
    the hub's MCP servers out when ``mcp.json`` changed. A problem here is
    a note, never a failed sync: the merge and the push already happened."""
    notes: list[str] = []
    linked = 0
    try:
        from swe.init.commands import run_init
        run = run_init(home, quiet=True, ask=False)
        rows = run.inst_rows + run.skill_rows + run.hook_rows
        linked = sum(1 for _, result in rows if result == "linked")
        blocked = [s for s, result in rows if result == "blocked"]
        if blocked:
            notes.append(f"{len(blocked)} path(s) blocked; swe init --force replaces them")
    except Exception as exc:  # noqa: BLE001 - the sync itself already landed
        notes.append(f"linking failed: {exc}")
    marketplaces = []
    try:
        from swe.init.hub import register_marketplaces
        for harness, marketplace, error in register_marketplaces(home):
            marketplaces.append({"harness": harness, "marketplace": marketplace,
                                 "error": error})
    except Exception as exc:  # noqa: BLE001
        notes.append(f"registering marketplaces failed: {exc}")
    mcp = None
    if "mcp.json" in changed:
        from swe.console import strip_ansi
        from swe.mcp.cli import _sync as mcp_sync
        report = {"source": None, "dry_run": False, "selected": [], "targets": []}
        said = io.StringIO()
        try:
            with contextlib.redirect_stdout(said):
                code = mcp_sync(["swe", "--all", "--no-interactive"], report)
            mcp = {"code": code, "selected": report.get("selected", []),
                   "targets": report.get("targets", [])}
            if code:
                # The remedy (a secret missing here, say) is in what it printed.
                lines = [strip_ansi(line).strip() for line in said.getvalue().splitlines()]
                reason = " ".join(line for line in lines if line)[-300:]
                notes.append(f"MCP servers not copied (swe mcp sync swe --all exited {code})"
                             + (f": {reason}" if reason else ""))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"MCP sync failed: {exc}")
    return {"changed": len(changed), "linked": linked,
            "marketplaces": marketplaces, "mcp": mcp, "notes": notes}
