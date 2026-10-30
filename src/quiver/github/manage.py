"""Pinning, repo search and the catalog sync, for any home.

``swe gh repo add``, ``swe gh repo search``, ``swe gh sync`` and
``swe gh pr worktree`` and ``swe gh issue worktree`` print what these
return (the hub's ``POST /v1/issues/resolve`` calls ``issue_worktree``
too); the web app's Repositories and Settings pages call
them in process for the hub's own home. Nothing here prints. Every
function takes ``home`` (``None`` is the real one) and reads its pins,
catalog and owner cache from that home's ``~/.quiver``.

The caller probes ``gh`` (``gh.probe_env``) and passes the result in, so
the CLI's one probe per command and the web app's cached probe both
work. ``search_repos`` takes the probe as a callable instead: a catalog
hit answers without ever running ``gh``.
"""

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from quiver import paths
from quiver.github import catalog, gh, repos

# `gh repo list` rows per owner; past this an org's tail is dropped, which
# beats a second paginated call for catalogs this size.
SYNC_REPO_LIMIT = 1000
_SYNC_FIELDS = ("nameWithOwner,description,stargazerCount,"
                "updatedAt,isPrivate")
_SEARCH_FIELDS = "fullName,description,stargazersCount,updatedAt"

# Org membership barely moves, and the sheet re-searches on every typing
# pause — each `swe` run would otherwise re-fetch `user/orgs`. Cached per
# login under ~/.quiver/cache so an `gh auth switch` can't leak scopes.
OWNERS_TTL = 600


# The module-level paths when ``home`` is None, read at call time, so a
# test that patches ``paths.GITHUB_*`` moves every caller at once.

def pins_file(home: Path | None) -> Path:
    return paths.github_file_for(home) if home else paths.GITHUB_FILE


def catalog_file(home: Path | None) -> Path:
    return paths.github_catalog_for(home) if home else paths.GITHUB_CATALOG


def mirrors_dir(home: Path | None) -> Path:
    return paths.github_mirrors_for(home) if home else paths.GITHUB_MIRRORS


def worktrees_dir(home: Path | None) -> Path:
    return paths.worktrees_dir_for(home) if home else paths.WORKTREES_DIR


def owners_cache_file(home: Path | None) -> Path:
    return (paths.github_owners_cache_for(home) if home
            else paths.GITHUB_OWNERS_CACHE)


# ------------------------------------------------------------------ pin

def add_repo(name: str, home: Path | None = None) -> gh.GhResult:
    """Pin ``name`` (already ``owner/name``) once GitHub says it exists.

    ``gh repo view`` both proves the repo is reachable with this login
    and gives its canonical spelling, which is what gets pinned. ``data``
    is ``{"repo", "pinned"}``: the pinned name and every pin after it.
    Pinning a repo that is already pinned changes nothing and succeeds.
    """
    check = gh.run_json(["repo", "view", name, "--json", "nameWithOwner"])
    if check.status != "ok" or not isinstance(check.data, dict):
        status = check.status if check.status != "ok" else "upstream-error"
        return gh.GhResult(status, error=f"Cannot reach {name}: "
                                         f"{check.error or status}")
    name = check.data.get("nameWithOwner") or name
    pinned = repos.add_pinned(name, pins_file(home))
    return gh.GhResult("ok", data={"repo": name,
                                   "pinned": [r["name"] for r in pinned]})


# ------------------------------------------------------------------ search

def search_repos(query: str, limit: int, probe: Callable[[], gh.GhEnv],
                 home: Path | None = None,
                 everywhere: bool = False) -> dict:
    """Repos to pin for ``query``: ``{"status", "error", "env_blocked",
    "query", "results", "source", "scoped"}``.

    The synced catalog answers first. A query it misses, or a home with
    no catalog yet, falls through to a live ``gh search repos`` scoped to
    the login and its orgs, so a repo created since the last sync is
    still findable. ``everywhere`` skips the catalog and the scope: all
    of GitHub. ``source`` is ``"catalog"`` or ``"github"``; ``scoped``
    says whether a live search was narrowed to the account's owners.
    ``status`` is ``"ok"`` or why the live search could not run;
    ``env_blocked`` marks a ``gh`` that is missing or signed out, as
    opposed to a search GitHub refused.
    """
    body = {"status": "ok", "error": None, "env_blocked": False,
            "query": query, "results": [], "source": "github",
            "scoped": False}
    db = catalog_file(home)
    if not everywhere and catalog.status(db) is not None:
        found = catalog.search(query, limit, db)
        if found:
            return {**body, "results": found, "source": "catalog"}
    env = probe()
    if env.status != "ok":
        return {**body, "status": env.status, "error": gh.env_error(env),
                "env_blocked": True}
    args = ["search", "repos", query, "--limit", str(limit),
            "--json", _SEARCH_FIELDS]
    owners = [] if everywhere else search_owners(env, home)[0]
    for owner in owners:
        args += ["--owner", owner]
    result = gh.run_json(args)
    if result.status != "ok":
        return {**body, "status": result.status,
                "error": result.error or result.status}
    found = [
        {"name": r.get("fullName"), "description": r.get("description"),
         "stars": r.get("stargazersCount"),
         "updated_at": r.get("updatedAt"),
         "url": f"https://github.com/{r.get('fullName')}"}
        for r in (result.data or []) if isinstance(r, dict)]
    return {**body, "results": found, "scoped": bool(owners)}


def search_owners(env: gh.GhEnv,
                  home: Path | None = None) -> tuple[list[str], str | None]:
    """Who a repo search may hit: the login itself plus every org the
    account belongs to, so results are repos the user can actually pin
    rather than all of public GitHub. Returns ``(owners, error)`` — the
    error is why the org list is missing; the search path ignores it
    (scoping to the login is still useful) but sync reports it so a
    partial sync doesn't look clean.
    """
    cache = owners_cache_file(home)
    cached = _cached_owners(env.login, cache)
    if cached is not None:
        return cached, None
    owners = [env.login] if env.login else []
    orgs = gh.run_gh(["api", "user/orgs", "--paginate",
                      "--jq", ".[].login"])
    if orgs.status == "ok" and isinstance(orgs.data, str):
        for line in orgs.data.splitlines():
            name = line.strip()
            if name and name not in owners:
                owners.append(name)
        # Only a successful orgs read may cache — a transient failure
        # would otherwise hide org repos until the TTL ran out.
        _cache_owners(env.login, owners, cache)
        return owners, None
    return owners, orgs.error or "gh could not list organizations"


def _cached_owners(login: str, path: Path) -> list[str] | None:
    """The owner list under the TTL, keyed by login — ``None`` on a
    miss, a stale entry or an unreadable file."""
    if not login:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data.get("logins", {}).get(login, {})
        if time.time() - float(entry.get("fetched_at", 0)) < OWNERS_TTL:
            owners = entry.get("owners")
            if isinstance(owners, list):
                return [str(o) for o in owners]
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def _cache_owners(login: str, owners: list[str], path: Path) -> None:
    """Best-effort write; a cache that can't persist just means the
    next search re-fetches."""
    if not login:
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data.get("logins"), dict):
        data["logins"] = {}
    data["logins"][login] = {"owners": owners, "fetched_at": time.time()}
    try:
        paths.atomic_write_text(path, json.dumps(data))
    except OSError:
        pass


# ------------------------------------------------------------------ sync

def sync_catalog(env: gh.GhEnv, home: Path | None = None) -> dict:
    """``swe gh sync``: list every repo the login and its orgs own into
    the catalog. ``env`` must be a probe whose status is ``"ok"``.

    Answers ``{"error", "repos", "synced_at", "owners", "failed",
    "listed"}``: the catalog's summary after the write, the owners that
    did not list (``[{"owner", "error"}]``, with ``"<orgs>"`` first when
    the org list itself failed), and one ``{"owner", "status", "error",
    "repos"}`` per owner that was asked. ``error`` is set only when
    neither the login nor an org is known; nothing is written then.
    """
    owners, owners_error = search_owners(env, home)
    failed = ([{"owner": "<orgs>", "error": owners_error}]
              if owners_error else [])
    if not owners:
        return {"error": "Could not resolve the account or its orgs.",
                "repos": None, "synced_at": None, "owners": [],
                "failed": failed, "listed": []}

    def fetch(owner: str) -> dict:
        result = gh.run_json(
            ["repo", "list", owner, "--limit", str(SYNC_REPO_LIMIT),
             "--json", _SYNC_FIELDS], timeout=60)
        # A non-list reply on a clean exit is a failure too — counting it
        # as a synced owner would prune that owner's rows on write.
        if result.status != "ok" or not isinstance(result.data, list):
            return {"owner": owner,
                    "status": (result.status if result.status != "ok"
                               else "upstream-error"),
                    "error": result.error
                             or "gh returned an invalid repository list",
                    "rows": []}
        return {"owner": owner, "status": "ok", "error": None, "rows": [
            {"full_name": r["nameWithOwner"], "owner": owner,
             "description": r.get("description"),
             "stars": r.get("stargazerCount"),
             "updated_at": r.get("updatedAt"),
             "is_private": bool(r.get("isPrivate"))}
            for r in result.data
            if isinstance(r, dict) and r.get("nameWithOwner")]}

    if len(owners) == 1:
        entries = [fetch(owners[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(owners))) as pool:
            entries = list(pool.map(fetch, owners))
    ok = [e for e in entries if e["status"] == "ok"]
    failed += [{"owner": e["owner"], "error": e["error"]}
               for e in entries if e["status"] != "ok"]
    summary = catalog.write(
        [r for e in ok for r in e["rows"]], [e["owner"] for e in ok],
        path=catalog_file(home), login=env.login)
    return {"error": None, "repos": summary.get("repos", 0),
            "synced_at": summary.get("synced_at"),
            "owners": summary.get("owners", []),
            "failed": failed,
            "listed": [{"owner": e["owner"], "status": e["status"],
                        "error": e["error"], "repos": len(e["rows"])}
                       for e in entries]}


# ------------------------------------------------------------------ PR worktree

def run_git(args: list[str], timeout: int = 120) -> gh.GhResult:
    """``git <args>`` with ``gh.run_gh``'s never-raise contract."""
    try:
        done = subprocess.run(
            ["git", *args], capture_output=True, text=True,
            timeout=timeout)
    except FileNotFoundError:
        return gh.GhResult("unavailable", error="git is not installed")
    except subprocess.TimeoutExpired:
        return gh.GhResult("upstream-error",
                           error=f"git timed out after {timeout}s")
    except OSError as exc:
        return gh.GhResult("unavailable", error=str(exc))
    if done.returncode != 0:
        return gh.GhResult("upstream-error",
                           error=(done.stderr or done.stdout or "").strip())
    return gh.GhResult("ok", data=done.stdout)


def _registered_worktrees(mirror: str) -> list[str]:
    result = run_git(["-C", mirror, "worktree", "list", "--porcelain"])
    if result.status != "ok":
        return []
    return [line.split(" ", 1)[1]
            for line in (result.data or "").splitlines()
            if line.startswith("worktree ")]


def _fail(step: str, result: gh.GhResult) -> gh.GhResult:
    """A failed step, named first ("Clone failed: …")."""
    return gh.GhResult(result.status if result.status != "ok" else "upstream-error",
                       error=f"{step}: {result.error or result.status}")


def mirror_path(repo: str, home: Path | None = None) -> Path:
    """Where ``repo``'s bare mirror lives (it may not exist yet)."""
    return mirrors_dir(home) / (repo.replace("/", "__") + ".git")


# One lock per mirror path, the only table for it: two first-time
# resolves of one repo (the hub runs each on its own thread) must not
# clone into the same folder, nor use it while the other is still
# cloning, and two PR checkouts must not interleave on its FETCH_HEAD.
# Keyed lowercased: GitHub names are case-insensitive, and so is the
# mirror folder on a default macOS volume.
_MIRROR_LOCKS: dict[str, threading.RLock] = {}
_MIRROR_LOCKS_GUARD = threading.Lock()


def _mirror_lock(mirror: Path) -> threading.RLock:
    with _MIRROR_LOCKS_GUARD:
        return _MIRROR_LOCKS.setdefault(str(mirror).lower(),
                                        threading.RLock())


def _ensure_mirror(repo: str,
                   home: Path | None) -> tuple[str, gh.GhResult | None]:
    """``(mirror, failure)``: clone the bare, blob-filtered mirror when
    it is missing. ``failure`` is None once the mirror is there. The
    check and the clone run under the mirror's lock, so a second caller
    waits for the first clone instead of racing it."""
    mirror = mirror_path(repo, home)
    with _mirror_lock(mirror):
        if not mirror.is_dir():
            mirrors_dir(home).mkdir(parents=True, exist_ok=True)
            result = gh.run_gh(
                ["repo", "clone", repo, str(mirror), "--",
                 "--bare", "--filter=blob:none"], timeout=300)
            if result.status != "ok":
                return str(mirror), _fail("Clone failed", result)
    return str(mirror), None


def _is_registered(mirror: str, path: str) -> bool:
    # git lists checkouts by their real path: a home under a symlink
    # (macOS's /var is /private/var) must still match its own checkout.
    registered = {os.path.realpath(p) for p in _registered_worktrees(mirror)}
    return os.path.realpath(path) in registered


def pr_worktree(repo: str, number: str, home: Path | None = None) -> gh.GhResult:
    """``swe gh pr worktree``: a detached checkout of PR ``number``'s
    head under the home's ``~/.quiver/worktrees``.

    The repo's bare mirror lives under ``data/github/mirrors``; the PR
    head is fetched via ``refs/pull/N/head``, GitHub's own ref, so fork
    PRs need no fork remote. Asking again fetches the head and returns
    the existing checkout, left where it is. ``data`` is ``{"path",
    "head", "pr_head", "created"}``, ``head`` the checkout's own;
    a failure's ``error`` names the step ("Clone failed: …").

    The whole call holds the mirror's lock: two PRs of one repo share
    its FETCH_HEAD, so a second fetch landing between this one's fetch
    and checkout would hand it the wrong head. A second caller waits,
    then reuses the mirror (and the checkout, for the same PR).
    """
    with _mirror_lock(mirror_path(repo, home)):
        return _pr_worktree_locked(repo, number, home)


def _pr_worktree_locked(repo: str, number: str,
                        home: Path | None) -> gh.GhResult:
    mirror, failed = _ensure_mirror(repo, home)
    if failed is not None:
        return failed

    result = run_git(["-C", mirror, "fetch", "origin",
                      f"refs/pull/{number}/head"], timeout=120)
    if result.status != "ok":
        return _fail("Fetch failed", result)
    sha = run_git(["-C", mirror, "rev-parse", "--short=12", "FETCH_HEAD"])
    head = (sha.data or "").strip() if sha.status == "ok" else None

    trees = worktrees_dir(home)
    path = str(trees / f"{repo.replace('/', '__')}-pr{number}")
    created = False
    if not _is_registered(mirror, path):
        trees.mkdir(parents=True, exist_ok=True)
        result = run_git(["-C", mirror, "worktree", "add", "--detach",
                          path, "FETCH_HEAD"], timeout=300)
        if result.status != "ok":
            return _fail("Worktree failed", result)
        created = True
    pr_head = head
    if not created:
        # A reused checkout stays where it was (it may hold work), so
        # ``head`` is what it has checked out, and ``pr_head`` the PR's
        # head just fetched: they differ when the PR moved on.
        here = run_git(["-C", path, "rev-parse", "--short=12", "HEAD"])
        head = (here.data or "").strip() if here.status == "ok" else None
    return gh.GhResult("ok", data={"path": path, "head": head,
                                   "pr_head": pr_head, "created": created})


# ------------------------------------------------------------------ issue worktree

def issue_branch(number) -> str:
    """The branch an agent resolving issue ``number`` works on."""
    return f"quiver/issue-{int(number)}"


def issue_worktree_path(repo: str, number, home: Path | None = None) -> str:
    """Where ``issue_worktree`` puts issue ``number``'s checkout."""
    return str(worktrees_dir(home)
               / f"{repo.replace('/', '__')}-issue{int(number)}")


def _default_branch(mirror: str) -> str | None:
    """The remote's default branch, asked of the remote so a rename on
    GitHub is followed. A bare clone keeps no ``refs/remotes/origin/HEAD``;
    its own ``HEAD`` (the default when it was cloned) is the fallback."""
    remote = run_git(["-C", mirror, "ls-remote", "--symref", "origin",
                      "HEAD"], timeout=60)
    if remote.status == "ok":
        for line in (remote.data or "").splitlines():
            if line.startswith("ref: refs/heads/"):
                name = line[len("ref: refs/heads/"):].split()[0]
                if name:
                    return name
    local = run_git(["-C", mirror, "symbolic-ref", "--short", "HEAD"])
    name = (local.data or "").strip() if local.status == "ok" else ""
    return name or None


def _branch_exists(mirror: str, branch: str) -> bool:
    found = run_git(["-C", mirror, "rev-parse", "--verify", "--quiet",
                     f"refs/heads/{branch}"])
    return found.status == "ok"


def issue_worktree(repo: str, number, home: Path | None = None,
                   progress: Callable[[str], None] | None = None,
                   ) -> gh.GhResult:
    """``swe gh issue worktree``: a checkout of a new branch
    ``quiver/issue-<n>`` off the default branch's tip, for an agent to
    resolve the issue on.

    Unlike a PR checkout this is a named branch, since the agent's job
    ends in a push and a PR. The mirror's default branch is fetched
    first (into ``origin/<default>``, with ``origin/HEAD`` pointed at
    it), so the branch starts from today's tip. Asking again resumes: a
    registered checkout comes back as is (``created`` false), and a
    branch whose checkout folder was deleted is checked out again
    rather than recreated, so the commits on it survive.

    ``progress`` hears each slow step ("Cloning mirror…", "Creating
    worktree quiver/issue-<n>…"); the hub shows them as the launch note.
    ``data`` is ``{"path", "branch", "base", "created"}``, ``base`` the
    ``origin/<default>`` ref the branch was cut from. A failure's
    ``error`` names the step, as ``pr_worktree``'s does.

    Everything runs under the mirror's lock: two resolves of one repo
    at once would otherwise race the clone, or both update
    ``refs/remotes/origin/<default>`` and one fail on git's ref lock.
    """
    def say(text: str) -> None:
        if progress is not None:
            progress(text)

    if not mirror_path(repo, home).is_dir():
        say("Cloning mirror…")
    with _mirror_lock(mirror_path(repo, home)):
        return _issue_worktree_locked(repo, int(number), home, say)


def _issue_worktree_locked(repo: str, number: int, home: Path | None,
                           say: Callable[[str], None]) -> gh.GhResult:
    branch = issue_branch(number)
    mirror, failed = _ensure_mirror(repo, home)
    if failed is not None:
        return failed

    default = _default_branch(mirror)
    if not default:
        return gh.GhResult("upstream-error",
                           error="Fetch failed: could not tell the "
                                 "repository's default branch")
    base = f"origin/{default}"
    # --no-write-fetch-head: pr_worktree checks out FETCH_HEAD in this
    # same mirror, and this fetch must never swap that under it.
    result = run_git(["-C", mirror, "fetch", "--no-write-fetch-head",
                      "origin",
                      f"+refs/heads/{default}:refs/remotes/{base}"],
                     timeout=300)
    if result.status != "ok":
        return _fail("Fetch failed", result)
    # Best effort: lets the agent say origin/HEAD inside the checkout.
    run_git(["-C", mirror, "symbolic-ref", "refs/remotes/origin/HEAD",
             f"refs/remotes/{base}"])

    path = issue_worktree_path(repo, number, home)
    data = {"path": path, "branch": branch, "base": base, "created": False}
    # A checkout folder deleted by hand stays listed (and its branch
    # "checked out" there) until pruned, so the folder must exist too.
    if os.path.isdir(path) and _is_registered(mirror, path):
        # Someone may have switched branches in there; an agent told it
        # is on quiver/issue-<n> must not commit to something else.
        head = run_git(["-C", path, "symbolic-ref", "--quiet", "--short",
                        "HEAD"])
        current = (head.data or "").strip() if head.status == "ok" else ""
        if current != branch:
            return gh.GhResult(
                "upstream-error",
                error=f"Worktree is on {current or 'a detached HEAD'}, not "
                      f"{branch}: switch {path} back to {branch} or "
                      "remove that folder")
        return gh.GhResult("ok", data=data)

    say(f"Creating worktree {branch}…")
    run_git(["-C", mirror, "worktree", "prune"])
    worktrees_dir(home).mkdir(parents=True, exist_ok=True)
    if _branch_exists(mirror, branch):
        args = ["-C", mirror, "worktree", "add", path, branch]
    else:
        # --no-track: the agent's first push sets the upstream; tracking
        # the default branch would make a bare `git push` refuse.
        args = ["-C", mirror, "worktree", "add", "--no-track", "-b",
                branch, path, f"refs/remotes/{base}"]
    result = run_git(args, timeout=300)
    if result.status != "ok":
        return _fail("Worktree failed", result)
    return gh.GhResult("ok", data={**data, "created": True})
