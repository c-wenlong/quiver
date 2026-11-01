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
- ``harnesses=1``: what ``POST /v1/sessions`` can start on the hub, for
  the PR sheet's Open in session.

Only pinned repos are read. ``refresh=1`` skips the cache.

Writes, ``POST /v1/app/repositories/<action>``, in process:

- ``merge``: the PR sheet's ``swe gh pr merge --json``, ``{"repo",
  "number", "method", "auto"}``. See ``merge`` for what it refuses.
- ``add``: the Add repository sheet's ``swe gh repo add --json``,
  ``{"repo"}`` as ``owner/name`` or a GitHub URL. See ``add``.
- ``remove``: the gear popover's Unpin, ``swe gh repo remove --json``,
  ``{"repo"}``. See ``remove``.
- ``reorder``: the rail's drag, ``swe gh repo reorder --json``,
  ``{"order": [names]}``. See ``reorder``.
- ``rerun``: an Actions row's Re-run failed jobs / Re-run all jobs,
  ``swe gh run rerun --json``, ``{"repo", "run", "failed"}``. See
  ``rerun``.
- ``worktree``: the PR sheet's Open in session, ``swe gh pr worktree
  --json``, ``{"repo", "number"}``. See ``worktree``. The page then
  starts the harness itself with ``POST /v1/sessions`` in that folder.
"""

from __future__ import annotations

from swe.github import cli as ghcli
from swe.github import gh, manage, repos

from swe.webapp.pages import PageError
from swe.github import shared as G
from swe.webapp.pages import _github as W

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
    refresh = W.flag(args, "refresh")
    machine = (ctx.machine or {}).get("name")
    if "search" in args:
        body = search(ctx.home, args["search"], refresh)
    elif W.flag(args, "harnesses"):
        body = {"harnesses": harnesses(ctx.home)}
    elif args.get("repo"):
        repo = W.pinned_name(ctx.home, args["repo"])
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
    # The writes' check: canonical digits, so "007" and "7" share one
    # cache entry and a long or non-ASCII digit string never reaches int().
    number = _pr_number_arg(str(ref or "").strip().lstrip("#"), status=400)
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


def _require_gh(home, fresh: bool = False) -> None:
    """A 409 in the setup words unless ``gh`` is installed and signed in.
    The cached probe is trusted before a write (and asked again when it
    says no); after a failed write the probe runs again, so a ``gh``
    signed out or removed since the last check reads as that rather
    than as GitHub refusing the write."""
    env = G.probe(home, refresh=fresh)
    if env.status != "ok" and not fresh:
        env = G.probe(home, refresh=True)
    if env.status != "ok":
        raise PageError(409, "gh_" + env.status.replace("-", "_"), gh.env_error(env))


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
    _require_gh(ctx.home)
    added = manage.add_repo(name, ctx.home)
    if added.status != "ok":
        _require_gh(ctx.home, fresh=True)
    if added.status == "not-found":
        raise PageError(404, "unknown_repo", added.error)
    if added.status != "ok":
        raise PageError(502, "add_failed", added.error)
    # A search that listed the repo as unpinned is out of date now.
    hk = G.home_key(ctx.home)
    G.CACHE.drop(lambda k: isinstance(k, tuple) and k[:2] == ("search", hk))
    return {"repo": added.data["repo"], "repos": G.pinned(ctx.home),
            "machine": (ctx.machine or {}).get("name")}


def remove(ctx, body: dict) -> dict:
    """Unpin a repo, as ``swe gh repo remove``. No ``gh`` call: the pin
    list is the hub's own file. The repo's cached board, focus surfaces
    and PR details go with it, so a re-pin starts from a fetch. Callers
    hold ``G.WRITE_LOCK``. Answers ``{"repo", "repos", "machine"}``; a
    repo that is not pinned is a 404 ``not_pinned``."""
    raw = body.get("repo")
    if isinstance(raw, str) and len(raw) > SEARCH_MAX:
        raise PageError(422, "bad_repo", "repo must be owner/name")
    name = _pinned_arg(ctx.home, raw)
    kept, _removed = repos.remove_pinned(name, manage.pins_file(ctx.home))
    _drop_repo(ctx.home, name)
    return {"repo": name, "repos": kept, "machine": (ctx.machine or {}).get("name")}


REORDER_MAX = 500  # far past any real pin list


def reorder(ctx, body: dict) -> dict:
    """Rewrite the pin order, as ``swe gh repo reorder``: the names in
    ``order`` take the first slots in that order, any pin not named keeps
    its place after them, and a name that is not pinned is ignored. So a
    drag can send the whole list it shows, and a pin added from another
    tab meanwhile is never lost. Callers hold ``G.WRITE_LOCK``. Answers
    ``{"repos", "machine"}``, the order as stored."""
    order = body.get("order")
    if (not isinstance(order, list) or len(order) > REORDER_MAX
            or not all(isinstance(n, str) and 0 < len(n) <= SEARCH_MAX for n in order)):
        raise PageError(422, "bad_order", "order must be a list of owner/name")
    names = [repos.normalize_repo_input(n) or n for n in order]
    pinned = repos.reorder_pinned(names, manage.pins_file(ctx.home))
    return {"repos": pinned, "machine": (ctx.machine or {}).get("name")}


# ---------------------------------------------------------------- rerun

def _rerun_args(home, body: dict) -> tuple[str, str, bool]:
    repo = _pinned_arg(home, body.get("repo"))
    run = body.get("run")
    if isinstance(run, bool) or not isinstance(run, (int, str)):
        raise PageError(422, "bad_run", "run must be a workflow run id")
    run = str(run).strip()
    # Run ids are about eleven digits today; twenty is room to grow and
    # keeps int() off absurd strings.
    if not run.isdigit() or not run.isascii() or len(run) > 20 or int(run) < 1:
        raise PageError(422, "bad_run", "run must be a workflow run id")
    failed = body.get("failed", False)
    if not isinstance(failed, bool):
        raise PageError(422, "bad_failed", "failed must be true or false")
    return repo, str(int(run)), failed


def rerun(ctx, repo: str, run: str, failed: bool) -> dict:
    """Re-run a workflow run's failed jobs, or all of them, as ``swe gh
    run rerun``. GitHub decides what can re-run (a run still going, or
    one with nothing failed, is refused there) and its words come back
    as a 502 ``rerun_failed``. Success drops the repo's cached views so
    the next look reads the run's new state. Callers hold
    ``G.WRITE_LOCK``. Answers ``{"repo", "run", "failed_only", "rerun",
    "machine"}``, ``rerun`` always true."""
    _require_gh(ctx.home)
    result = ghcli.rerun_run(repo, run, failed)
    if result.status != "ok":
        _require_gh(ctx.home, fresh=True)
    if result.status == "not-found":
        raise PageError(404, "unknown_run", f"No workflow run {run} in {repo}.")
    if result.status != "ok":
        raise PageError(502, "rerun_failed", result.error or result.status)
    _drop_repo(ctx.home, repo)
    return {"repo": repo, "run": int(run), "failed_only": failed, "rerun": True,
            "machine": (ctx.machine or {}).get("name")}


# ---------------------------------------------------------------- PR session

def harnesses(home: Path | None = None) -> list[dict]:
    """``{name, display_name, tracked, launch_model, launch_effort,
    launch_yolo, last_model, recent_models}`` for each harness the hub
    can start, in Provider
    visibility order (``catalog.launchable_harnesses``, what the phone's
    new-session sheet offers). ``recent_models`` is the harness's
    recently used models in ``home``'s ledger, newest first (at most 5);
    ``last_model`` is its head ("" when none), the picker's default. The
    Tasks page's launch sheet asks the same question."""
    from swe.remote import catalog as catalog_mod

    launchable = catalog_mod.launchable_harnesses()
    recent = catalog_mod.recent_models(
        home, [h["name"] for h in launchable])
    return [{"name": h["name"], "display_name": h["display_name"],
             "tracked": h["tracked"],
             "launch_model": h["launch_model"],
             "launch_effort": h["launch_effort"],
             "launch_yolo": h["launch_yolo"],
             "last_model": (recent.get(h["name"]) or [""])[0],
             "recent_models": recent.get(h["name"], [])}
            for h in launchable]


def _worktree_args(home, body: dict) -> tuple[str, str]:
    return _pinned_arg(home, body.get("repo")), _pr_number_arg(body.get("number"))


def worktree(ctx, repo: str, number: str) -> dict:
    """Check PR ``number``'s head out under the hub's
    ``~/.swe/worktrees``, as ``swe gh pr worktree``: a bare mirror
    per repo, the head fetched from ``refs/pull/N/head`` (fork PRs too),
    a detached checkout reused on the next ask. Answers ``{"repo",
    "number", "path", "head", "created", "machine"}``. Checkouts of one
    repo run one after another. 409 for a missing or signed-out ``gh``,
    502 when git or GitHub fails (the message names the step)."""
    _require_gh(ctx.home)
    # pr_worktree holds the mirror's lock, shared with the phone's open:
    # one repo's FETCH_HEAD can't move under either caller's checkout.
    made = manage.pr_worktree(repo, number, ctx.home)
    if made.status != "ok":
        _require_gh(ctx.home, fresh=True)
        raise PageError(502, "worktree_failed", made.error)
    return {"repo": repo, "number": int(number), **made.data,
            "machine": (ctx.machine or {}).get("name")}


# ---------------------------------------------------------------- merge

METHODS = ("squash", "rebase", "merge")  # PrDetailSheet's menu, in order


PIN_WRITES = {"add": lambda ctx, body: add(ctx, body),
              "remove": lambda ctx, body: remove(ctx, body),
              "reorder": lambda ctx, body: reorder(ctx, body)}


def post(ctx, action: str, body: dict) -> dict:
    if action in PIN_WRITES:
        with G.WRITE_LOCK:
            return PIN_WRITES[action](ctx, body)
    if action == "rerun":
        repo, run, failed = _rerun_args(ctx.home, body)
        with G.WRITE_LOCK:
            return rerun(ctx, repo, run, failed)
    if action == "worktree":
        repo, number = _worktree_args(ctx.home, body)
        return worktree(ctx, repo, number)
    if action != "merge":
        raise PageError(404, "unknown_action",
                        f"unknown repositories action: {action}")
    repo, number, method, auto = _merge_args(ctx.home, body)
    with G.WRITE_LOCK:
        return merge(ctx, repo, number, method, auto)


def _pinned_arg(home, raw) -> str:
    """A write's ``repo``: a pin, spelled as pinned. Malformed is a 422,
    not pinned a 404."""
    if not isinstance(raw, str) or not raw.strip():
        raise PageError(422, "bad_repo", "repo must be owner/name")
    try:
        return W.pinned_name(home, raw)
    except PageError as exc:
        if exc.status == 400:  # malformed, as opposed to not pinned
            raise PageError(422, exc.code, exc.message) from exc
        raise


def _pr_number_arg(number, status: int = 422) -> str:
    """A PR ``number`` as canonical digits, else ``status`` (a write's
    422; the detail read answers 400)."""
    if isinstance(number, bool) or not isinstance(number, (int, str)):
        raise PageError(status, "bad_pr", "number must be a pull request number")
    number = str(number).strip()
    # Nine digits is far past any real PR, and keeps int() off strings
    # long enough to trip Python's digit limit.
    if (not number.isdigit() or not number.isascii() or len(number) > 9
            or int(number) < 1):
        raise PageError(status, "bad_pr", "number must be a pull request number")
    return str(int(number))


def _merge_args(home, body: dict) -> tuple[str, str, str, bool]:
    repo = _pinned_arg(home, body.get("repo"))
    number = _pr_number_arg(body.get("number"))
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
    """Forget the repo's board, focus surfaces and PR details — and the
    phone's ``/v1/pulls`` and ``/v1/issues`` reads — so every view of it
    reads GitHub again after a merge."""
    hk = G.home_key(home)
    G.CACHE.drop(lambda k: isinstance(k, tuple) and len(k) > 2
                 and k[0] in ("board", "focus", "pr", "pulls",
                              "issue", "issues", "task")
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
