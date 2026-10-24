"""Pinning, repo search and the catalog sync, for any home.

``swe gh repo add``, ``swe gh repo search`` and ``swe gh sync`` print
what these return; the web app's Repositories and Settings pages call
them in process for the hub's own home. Nothing here prints. Every
function takes ``home`` (``None`` is the real one) and reads its pins,
catalog and owner cache from that home's ``~/.quiver``.

The caller probes ``gh`` (``gh.probe_env``) and passes the result in, so
the CLI's one probe per command and the web app's cached probe both
work. ``search_repos`` takes the probe as a callable instead: a catalog
hit answers without ever running ``gh``.
"""

import json
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
