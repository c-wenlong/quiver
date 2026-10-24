"""The Repositories page: the Mac app's RepositoriesPage, RepoDetailPage
and PrDetailSheet.

``GET /v1/app/repositories`` answers three views:

- no ``repo``: the dashboard. The probe and pins ``swe gh --json``
  reports, plus every pin's board (``swe gh board --json``: open pull
  requests, open issues, runs, Pages, releases, tags, branches).
- ``repo=owner/name``: the focus page. The same seven surfaces in
  every state, at the app's focus windows. ``surface=<key>&limit=N``
  refetches one surface with a wider window (the app's scroll loader).
- ``repo=owner/name&pr=N``: one pull request, as ``swe gh pr view
  --json``.
- ``search=<text>``: repos to pin, as ``swe gh repo search --json``:
  the synced catalog first, then a live search scoped to the account.

Only pinned repos are read. ``refresh=1`` skips the cache.

Writes, ``POST /v1/app/repositories/<action>``, in process:

- ``merge``: the PR sheet's ``swe gh pr merge --json``, ``{"repo",
  "number", "method", "auto"}``. See ``merge`` for what it refuses.
- ``add``: the Add repository sheet's ``swe gh repo add --json``,
  ``{"repo"}`` as ``owner/name`` or a GitHub URL. See ``add``.
"""

from __future__ import annotations

from quiver.github import cli as ghcli
from quiver.github import gh, manage, repos

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
    if "search" in args:
        body = search(ctx.home, args["search"], refresh)
    elif args.get("repo"):
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
        "max_limit": MAX_LIMIT,
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
            "entry": entry, "limit": n, "max_limit": MAX_LIMIT,
            "fetched_at": at}


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


# ---------------------------------------------------------------- pin

SEARCH_LIMIT = 15  # swe gh repo search's default
SEARCH_MAX = 200   # longer text is no repo name anyone types


def search(home, text: str, refresh: bool = False) -> dict:
    """The Add repository sheet's droplist: ``{"github", "status",
    "error", "query", "results", "source"}``. ``status`` is ``ok`` or why
    the live search could not run; a catalog hit never runs ``gh``."""
    query = " ".join(str(text or "").split())
    if len(query) > SEARCH_MAX:
        raise PageError(400, "bad_search", "That search is too long.")
    if not query:
        return {"github": None, "status": "ok", "error": None, "query": "",
                "results": [], "source": None}
    env_seen = []

    def probe():
        env_seen.append(G.probe(home))
        return env_seen[-1]

    def fetch():
        return manage.search_repos(query, SEARCH_LIMIT, probe, home)

    found, _at = G.CACHE.get(("search", G.home_key(home), query.lower()), fetch,
                             refresh=refresh, ok=lambda v: v["status"] == "ok")
    return {"github": G.env_payload(env_seen[0]) if env_seen else None,
            "status": found["status"], "error": found["error"],
            "query": query, "results": found["results"],
            "source": found["source"]}


def add(ctx, body: dict) -> dict:
    """Pin a repo, as ``swe gh repo add``: GitHub must know it under the
    hub's login, and its own spelling is what gets pinned. Callers hold
    ``G.WRITE_LOCK``. Answers ``{"repo", "repos", "machine"}``: the pin
    and the hub's pins after it, in stored order.

    Refusals: 422 for text that is no ``owner/name`` or GitHub URL, 409
    for a missing or signed-out ``gh``, 404 for a repo GitHub can't find
    (or this login can't see), 502 when GitHub fails."""
    raw = body.get("repo")
    if not isinstance(raw, str) or len(raw) > SEARCH_MAX:
        raise PageError(422, "bad_repo", "repo must be owner/name or a GitHub URL")
    name = repos.normalize_repo_input(raw)
    if not name:
        raise PageError(422, "bad_repo", f"Not a GitHub repo: {raw.strip()}")
    env = G.probe(ctx.home)
    if env.status != "ok":
        env = G.probe(ctx.home, refresh=True)
    if env.status != "ok":
        raise PageError(409, "gh_" + env.status.replace("-", "_"), gh.env_error(env))
    added = manage.add_repo(name, ctx.home)
    if added.status == "not-found":
        raise PageError(404, "unknown_repo", added.error)
    if added.status != "ok":
        raise PageError(502, "add_failed", added.error)
    # A search that listed the repo as unpinned is out of date now.
    hk = G.home_key(ctx.home)
    G.CACHE.drop(lambda k: isinstance(k, tuple) and k[:2] == ("search", hk))
    return {"repo": added.data["repo"], "repos": G.pinned(ctx.home),
            "machine": (ctx.machine or {}).get("name")}


# ---------------------------------------------------------------- merge

METHODS = ("squash", "rebase", "merge")  # PrDetailSheet's menu, in order
METHOD_TITLES = {"squash": "Squash and merge", "rebase": "Rebase and merge",
                 "merge": "Create a merge commit"}


def post(ctx, action: str, body: dict) -> dict:
    if action == "add":
        with G.WRITE_LOCK:
            return add(ctx, body)
    if action != "merge":
        raise PageError(404, "unknown_action",
                        f"unknown repositories action: {action}")
    repo, number, method, auto = _merge_args(ctx.home, body)
    with G.WRITE_LOCK:
        return merge(ctx, repo, number, method, auto)


def _merge_args(home, body: dict) -> tuple[str, str, str, bool]:
    raw = body.get("repo")
    if not isinstance(raw, str) or not raw.strip():
        raise PageError(422, "bad_repo", "repo must be owner/name")
    try:
        repo = G.pinned_name(home, raw)
    except PageError as exc:
        if exc.status == 400:  # malformed, as opposed to not pinned
            raise PageError(422, exc.code, exc.message) from exc
        raise
    number = body.get("number")
    if isinstance(number, bool) or not isinstance(number, (int, str)):
        raise PageError(422, "bad_pr", "number must be a pull request number")
    number = str(number).strip()
    # Nine digits is far past any real PR, and keeps int() off strings
    # long enough to trip Python's digit limit.
    if (not number.isdigit() or not number.isascii() or len(number) > 9
            or int(number) < 1):
        raise PageError(422, "bad_pr", "number must be a pull request number")
    number = str(int(number))
    method = body.get("method", "squash")
    if method not in METHODS:
        raise PageError(422, "bad_method",
                        "method must be squash, rebase or merge")
    auto = body.get("auto", False)
    if not isinstance(auto, bool):
        raise PageError(422, "bad_auto", "auto must be true or false")
    return repo, number, method, auto


def blocked(pr: dict, auto: bool) -> tuple[str, str] | None:
    """Why PrDetailSheet would not offer this merge, as ``(code,
    message)`` in the app's own words, or ``None``. The sheet shows
    Merge and Auto-merge only on an open PR; both are off for a draft;
    Merge is off on conflicts; Auto-merge is off once armed. The app
    does not gate on checks, so neither does this: GitHub's own rules
    (required checks, reviews) refuse through ``gh`` instead."""
    number = pr.get("number")
    if pr.get("state") != "open":
        return "not_open", f"#{number} is {pr.get('state') or 'not open'}, so it can't merge."
    if pr.get("draft"):
        return "draft", "Draft PRs can't merge"
    if auto and pr.get("auto_merge"):
        return "auto_merge_armed", "Auto-merge is already armed"
    if not auto and pr.get("mergeable") == "conflicting":
        return "conflicting", "Resolve the conflicts first"
    return None


def _drop_repo(home, repo: str) -> None:
    """Forget the repo's board, focus surfaces and PR details, so every
    view of it reads GitHub again after a merge."""
    hk = G.home_key(home)
    G.CACHE.drop(lambda k: isinstance(k, tuple) and len(k) > 2
                 and k[0] in ("board", "focus", "pr")
                 and k[1] == hk and k[2] == repo)


def merge(ctx, repo: str, number: str, method: str, auto: bool) -> dict:
    """Merge, or arm auto-merge, after a fresh read of the PR: the
    state the user saw may be a minute old, and a PR merged or turned
    draft since then must not be merged on the old picture. Callers
    hold ``G.WRITE_LOCK``.

    Answers ``{"github", "repo", "number", "method", "auto", "merged",
    "pr", "fetched_at", "machine"}``, where ``pr`` is the PR as read
    again after the write (``null`` if that read failed) and ``merged``
    is false under ``auto``, as in ``swe gh pr merge --json``."""
    home = ctx.home
    env = G.probe(home, refresh=True)
    if env.status != "ok":
        raise PageError(409, "gh_" + env.status.replace("-", "_"),
                        ghcli._env_error(env))
    _drop_repo(home, repo)
    before = pull_request(home, repo, number)
    if before.get("status") == "not-found":
        raise PageError(404, "unknown_pr", f"No pull request #{number} in {repo}.")
    if before.get("status") != "ok" or not before.get("pr"):
        raise PageError(502, "pr_unreadable",
                        before.get("error") or "GitHub returned "
                        + str(before.get("status")))
    reason = blocked(before["pr"], auto)
    if reason:
        raise PageError(409, *reason)
    result = ghcli.merge_pr(repo, number, method, auto)
    _drop_repo(home, repo)
    if result.status != "ok":
        raise PageError(502, "merge_failed", result.error or result.status)
    after = pull_request(home, repo, number)
    return {
        "github": after.get("github") or G.env_payload(env),
        "repo": repo, "number": int(number), "method": method, "auto": auto,
        "merged": not auto,
        "pr": after.get("pr") if after.get("status") == "ok" else None,
        "fetched_at": after.get("fetched_at"),
        "machine": (ctx.machine or {}).get("name"),
    }
