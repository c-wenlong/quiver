"""The Repositories page: the Mac app's RepositoriesPage, RepoDetailPage
and PrDetailSheet, read only.

``GET /v1/app/repositories`` answers three views:

- no ``repo``: the dashboard. The probe and pins ``swe gh --json``
  reports, plus every pin's board (``swe gh board --json``: open pull
  requests, open issues, runs, Pages, releases, tags, branches).
- ``repo=owner/name``: the focus page. The same seven surfaces in
  every state, at the app's focus windows. ``surface=<key>&limit=N``
  refetches one surface with a wider window (the app's scroll loader).
- ``repo=owner/name&pr=N``: one pull request, as ``swe gh pr view
  --json``.

Only pinned repos are read. ``refresh=1`` skips the cache.
"""

from __future__ import annotations

from quiver.github import cli as ghcli
from quiver.github import gh

from quiver.webapp.pages import PageError
from quiver.webapp.pages import _github as G

# The focus page's surfaces and first windows (GitHubModel's
# focusDefaultLimit): pull requests and issues come back in every state.
FOCUS = {
    "pull_requests": (50, lambda n, lim: ghcli._fetch_prs(n, "all", lim)),
    "issues": (50, lambda n, lim: ghcli._fetch_issues(n, "all", lim)),
    "runs": (30, lambda n, lim: ghcli._fetch_runs(n, lim)),
    "pages": (0, lambda n, _lim: ghcli._fetch_pages(n)),
    "releases": (20, lambda n, lim: ghcli._fetch_releases(n, lim)),
    "tags": (30, lambda n, lim: ghcli._fetch_tags(n, lim)),
    "branches": (50, lambda n, lim: ghcli._fetch_branches(n, lim)),
}
MAX_LIMIT = 200  # swe gh's own --limit cap


def get(ctx, args: dict) -> dict:
    refresh = G.flag(args, "refresh")
    machine = (ctx.machine or {}).get("name")
    if args.get("repo"):
        repo = G.pinned_name(ctx.home, args["repo"])
        if args.get("pr"):
            body = pull_request(ctx.home, repo, args["pr"], refresh)
        elif args.get("surface"):
            body = surface(ctx.home, repo, args["surface"],
                           args.get("limit"), refresh)
        else:
            body = focus(ctx.home, repo, refresh)
    else:
        body = dashboard(ctx.home, refresh)
    body["machine"] = machine
    return body


def _surfaces_ok(board: dict) -> bool:
    return all(s.get("status") == "ok" for s in board["surfaces"].values())


def dashboard(home, refresh: bool = False) -> dict:
    """``swe gh --json`` plus each pin's board. A missing or signed-out
    ``gh`` stops before any read: the page shows the setup card."""
    env = G.probe(home, refresh=refresh)
    pins = G.pinned(home)
    body = {"github": G.env_payload(env), "repos": pins, "boards": {}}
    if env.status in ("unavailable", "auth-required") or not pins:
        return body

    def board(name: str) -> dict:
        value, at = G.CACHE.get(
            ("board", G.home_key(home), name),
            lambda: ghcli._fetch_board(name),
            refresh=refresh, ok=_surfaces_ok)
        return {"surfaces": value["surfaces"], "fetched_at": at}

    names = [p["name"] for p in pins]
    for name, entry in zip(names, G.fan(names, board)):
        body["boards"][name] = entry
    return body


def _focus_entry(home, repo: str, key: str, limit: int,
                 refresh: bool) -> tuple[dict, float]:
    _default, fetch = FOCUS[key]
    return G.CACHE.get(
        ("focus", G.home_key(home), repo, key, limit),
        lambda: fetch(repo, limit), refresh=refresh,
        ok=lambda e: e.get("status") == "ok")


def focus(home, repo: str, refresh: bool = False) -> dict:
    """Every surface of one repo, fetched together so no tab waits on
    its own first call (RepoDetailPage's reason for being)."""
    keys = list(FOCUS)
    replies = G.fan(keys, lambda k: _focus_entry(
        home, repo, k, FOCUS[k][0], refresh))
    surfaces = {k: entry for k, (entry, _at) in zip(keys, replies)}
    env = G.env_after(home, list(surfaces.values()), refresh=refresh)
    return {
        "github": G.env_payload(env),
        "repo": repo,
        "surfaces": surfaces,
        "limits": {k: FOCUS[k][0] for k in keys},
        "fetched_at": min(at for _e, at in replies),
    }


def surface(home, repo: str, key: str, limit, refresh: bool = False) -> dict:
    """One focus surface at a wider window: the app's load-more."""
    if key not in FOCUS or key == "pages":
        raise PageError(400, "bad_surface", f"Unknown surface: {key}")
    try:
        n = int(limit) if limit not in (None, "") else FOCUS[key][0]
    except ValueError as exc:
        raise PageError(400, "bad_limit", "limit must be a number") from exc
    n = max(1, min(n, MAX_LIMIT))
    entry, at = _focus_entry(home, repo, key, n, refresh)
    env = G.env_after(home, [entry], refresh=refresh)
    return {"github": G.env_payload(env), "repo": repo, "surface": key,
            "entry": entry, "limit": n, "fetched_at": at}


def pull_request(home, repo: str, ref: str, refresh: bool = False) -> dict:
    """``swe gh pr view <N> --repo=<repo> --json``: the probe first, as
    swe does, then the detail fields the sheet draws."""
    number = str(ref or "").strip().lstrip("#")
    if not number.isdigit():
        raise PageError(400, "bad_pr", "pr must be a pull request number")
    env = G.probe(home, refresh=refresh)
    if env.status != "ok":
        return {"github": G.env_payload(env), "repo": None,
                "status": env.status, "pr": None,
                "error": ghcli._env_error(env)}

    def fetch() -> dict:
        result = gh.run_json(
            ["pr", "view", number, "--repo", repo,
             "--json", ghcli._PR_DETAIL_FIELDS], timeout=60)
        if result.status != "ok" or not isinstance(result.data, dict):
            return {"repo": repo, "status": result.status, "pr": None,
                    "error": result.error
                    or "gh returned an invalid pull request"}
        return {"repo": repo, "status": "ok",
                "pr": ghcli._pr_detail(result.data, repo)}

    value, at = G.CACHE.get(("pr", G.home_key(home), repo, number), fetch,
                            refresh=refresh,
                            ok=lambda v: v["status"] == "ok")
    return {"github": G.env_payload(env), **value, "fetched_at": at}
