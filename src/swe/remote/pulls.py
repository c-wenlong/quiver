"""Pull requests on the phone: ``GET /v1/pulls``,
``GET /v1/pulls/<o>/<r>/<n>`` and ``POST /v1/pulls/<o>/<r>/<n>/open``.

The reads answer from the same 60-second ``gh`` cache the web app's
Repositories page uses (``github/shared.py``), per pinned repo,
so the phone and the page agree and a slow ``gh`` shows up once. A
repo's row adds ``mine`` and ``review_requested`` (from ``gh pr
status``) so the client can filter without a second round trip.

The open mirrors ``issues.py``'s resolve, ``POST /v1/issues/resolve``:
the hub validates what a client can act on straight away (a pinned
repo, a harness that starts here, tmux, a signed-in ``gh``, no other
open of the PR still starting: ``409 open_pending``) and answers
``202 {launch_id, state: "pending", note}``; the rest runs on a thread
behind that launch id:

1. read the PR (``github/pulls.py``), for the prompt and the title;
2. ``manage.pr_worktree``: the repo's mirror (cloned on first use),
   then a detached ``refs/pull/<n>/head`` checkout under
   ``~/.swe/worktrees``. A second open of the same PR resumes in
   the same checkout;
3. ``supervise.launch_new`` in that checkout with the client's prompt,
   or ``github/pulls.build_prompt`` when none was sent.

While pending the launch's ``note`` names the step and any failure
settles the launch ``failed`` with a readable note. Adoption names the
session ``<owner>/<repo>#<n> <pr title>`` through the same
``title_adopted`` hook a resolve uses. Hub machine only.
"""

from __future__ import annotations

import threading

from swe.github import cli as ghcli
from swe.github import gh, manage
from swe.github import pulls as gh_pulls
from swe.remote import catalog as catalog_mod
from swe.remote import issues as issues_mod
from swe.remote import protocol, supervise
from swe.remote import tasks as tasks_mod
from swe.github import shared as G

# ``swe gh``'s list cap: open PRs per pinned repo in one answer.
LIST_LIMIT = 50

# PRs whose open is still between the 202 and launch_new's pending
# marker, as (repo lower, number) — same reason issues.py's _preparing
# exists: two opens waiting in one checkout could each claim the
# other's session when adoption matches on folder and start time.
_preparing: set[tuple[str, int]] = set()
_preparing_lock = threading.Lock()


# The same path and query readers as ``/v1/issues``: an owner/repo
# pair, a canonical number of at most nine digits, ``refresh=1``.
_refresh = issues_mod._refresh
_repo_arg = issues_mod._repo_arg
_number_arg = issues_mod._number_arg


# ------------------------------------------------------------------ reads

def list_view(state, args) -> tuple[int, dict]:
    """``GET /v1/pulls``: every pinned repo's open PRs.

    ``{repos: [{name, status, pulls, error?}], machine, machine_id,
    github, env_blocked?, fetched_at}``; each pull is ``cli._pr_row``
    plus ``mine`` and ``review_requested``. A missing or signed-out
    ``gh`` answers ``env_blocked`` before any fetch, as ``swe gh``
    stops on the same words."""
    env = G.probe(state.home, refresh=_refresh(args))
    pins = G.pinned(state.home)
    body = {"github": G.env_payload(env),
            "machine": (state.machine or {}).get("name"),
            "machine_id": state.machine_id,
            "repos": []}
    if env.status in ("unavailable", "auth-required"):
        body["env_blocked"] = gh.env_error(env)
        return 200, body
    if not pins:
        return 200, body

    def entry(name: str):
        return G.CACHE.get(
            ("pulls", G.home_key(state.home), name),
            lambda: _repo_pulls(name, env.login),
            refresh=_refresh(args),
            ok=lambda e: e.get("status") == "ok")

    names = [p["name"] for p in pins]
    replies = G.fan(names, entry)
    body["repos"] = [value for value, _at in replies]
    if replies:
        body["fetched_at"] = min(at for _value, at in replies)
    # tasks.py's store says which PRs have an agent on them (a linked
    # session still active, or its launch still pending).
    running = tasks_mod.running_task_ids(state)
    for repo_row in body["repos"]:
        name = str(repo_row.get("name") or "")
        for pr in repo_row.get("pulls") or []:
            if isinstance(pr, dict):
                pr["agent_running"] = tasks_mod.pull_task_id(
                    name, pr.get("number")) in running
    return 200, body


def _repo_pulls(name: str, login: str) -> dict:
    """One repo's list entry: ``_pr_row``s plus the viewer flags.

    ``gh pr status`` says which open PRs the login authored or was
    asked to review; when it fails the rows still list, with
    ``review_requested`` null rather than a guess.
    """
    listed = ghcli._fetch_prs(name, "open", LIST_LIMIT)
    if listed["status"] != "ok":
        return {"name": name, "status": listed["status"], "pulls": [],
                "error": listed.get("error") or listed["status"]}
    status = ghcli._fetch_pr_status(name)
    status_ok = status.get("status") == "ok"
    authored = ({p.get("number") for p in status.get("authored") or []}
                if status_ok else set())
    requested = ({p.get("number") for p in status.get("review_requested") or []}
                 if status_ok else set())
    pulls = []
    for pr in listed["pull_requests"]:
        mine = (pr["number"] in authored
                if status_ok else bool(login) and pr.get("author") == login)
        pulls.append({
            **pr,
            "mine": mine,
            "review_requested": (pr["number"] in requested
                                 if status_ok else None),
        })
    return {"name": name, "status": "ok", "pulls": pulls}


def detail(state, owner: str, name: str, number_raw: str,
           args) -> tuple[int, dict]:
    """``GET /v1/pulls/<o>/<r>/<n>``: the row plus body, per-check rows
    and reviews — what the phone's detail page draws."""
    repo = _repo_arg(owner, name)
    if repo is None:
        return 400, protocol.error("bad_repo",
                                   "pull request path wants owner/repo")
    number = _number_arg(number_raw)
    if number is None:
        return 422, protocol.error("bad_pr",
                                   "number must be a pull request number")
    pinned = issues_mod._pinned(state.home, repo)
    if pinned is None:
        return 404, protocol.error(
            "not_pinned", f"{repo} is not pinned on the hub")
    env = G.probe(state.home, refresh=_refresh(args))
    base = {"github": G.env_payload(env), "repo": pinned,
            "machine": (state.machine or {}).get("name"),
            "machine_id": state.machine_id}
    if env.status != "ok":
        return 200, {**base, "status": env.status, "pr": None,
                     "error": gh.env_error(env)}

    def fetch():
        result = gh_pulls.fetch(pinned, number, state.home,
                                prompt_limit=protocol.MAX_LAUNCH_PROMPT)
        if result.status != "ok":
            return {"repo": pinned, "status": result.status, "pr": None,
                    "error": result.error or result.status}
        return {"repo": pinned, "status": "ok", "pr": result.data}

    # The Repositories page caches the same PR under ("pr", hk, repo,
    # n): one entry keeps the phone and the page on the same read, and
    # its merge/rerun invalidation (_drop_repo) covers us too. A
    # page-populated entry lacks the two fields only this route adds,
    # so they're filled in on the way out (both are pure).
    value, at = G.CACHE.get(
        ("pr", G.home_key(state.home), pinned, str(number)), fetch,
        refresh=_refresh(args), ok=lambda v: v["status"] == "ok")
    pr = value.get("pr")
    if isinstance(pr, dict) and "worktree" not in pr:
        worktree = gh_pulls.worktree_path(pinned, number, state.home)
        pr = {**pr, "worktree": worktree,
              "suggested_prompt": gh_pulls.build_prompt(
                  pinned, pr, worktree,
                  limit=protocol.MAX_LAUNCH_PROMPT)}
        value = {**value, "pr": pr}
    return 200, {**base, **value, "fetched_at": at}


# ------------------------------------------------------------------ open

def parse_body(body) -> dict:
    """Validate an open body ``{harness, prompt?}``; ValueError on bad."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    harness = body.get("harness")
    if not isinstance(harness, str) \
            or not protocol._HARNESS_RE.fullmatch(harness):
        raise ValueError("harness must be a harness name")
    prompt = body.get("prompt")
    if prompt is None:
        prompt = ""
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    if len(prompt) > protocol.MAX_LAUNCH_PROMPT:
        raise ValueError(
            f"prompt is over {protocol.MAX_LAUNCH_PROMPT} characters")
    return {"harness": harness, "prompt": prompt if prompt.strip() else "",
            "model": protocol._launch_value(body, "model"),
            "effort": protocol._launch_value(body, "effort")}


def request(state, owner: str, name: str, number_raw: str,
            body) -> tuple[int, dict]:
    """``POST /v1/pulls/<o>/<r>/<n>/open {harness, prompt?}`` →
    ``(status, payload)``, the resolve shape."""
    if body is None:
        return 400, protocol.error("bad_body", "unreadable JSON body")
    repo = _repo_arg(owner, name)
    if repo is None:
        return 422, protocol.error(
            "bad_repo", "open path wants /v1/pulls/<owner>/<repo>/<n>/open")
    number = _number_arg(number_raw)
    if number is None:
        return 422, protocol.error("bad_pr",
                                   "number must be a pull request number")
    try:
        cmd = parse_body(body)
    except ValueError as exc:
        return 422, protocol.error("bad_command", str(exc))
    repo = issues_mod._pinned(state.home, repo)
    if repo is None:
        return 422, protocol.error(
            "not_pinned",
            f"{owner}/{name} isn't pinned on this hub: pin it with "
            f"`swe gh repo add {owner}/{name}`")
    harness = cmd["harness"]
    ready = issues_mod.launch_ready(cmd)
    if isinstance(ready, tuple):
        return ready
    binary, tracked = ready.binary, ready.tracked
    rows = state.catalog.tail_rows()
    ref = (repo.lower(), number)
    tree = gh_pulls.worktree_path(repo, number, state.home)
    with _preparing_lock:
        # Same guard a resolve takes: _preparing covers the window
        # before launch_new's marker, fork_blocker the marker itself.
        if ref in _preparing or (tracked and supervise.fork_blocker(
                state.home, harness, tree)):
            return 409, protocol.error(
                "open_pending",
                f"an agent for {repo}#{number} is still starting in its "
                f"worktree; open it again once its session shows up")
        _preparing.add(ref)
    launch_id = state.new_launch(
        state.machine_id, harness, "",
        model=cmd["model"] or "", effort=cmd["effort"] or "")
    state.settle_launch(launch_id, note="preparing worktree")
    threading.Thread(
        target=_run,
        args=(state, launch_id, repo, number, cmd, binary, tracked, rows),
        name=f"remote-launch-{launch_id}", daemon=True,
    ).start()
    return 202, {"ok": True, "launch_id": launch_id, "state": "pending",
                 "note": "preparing worktree"}


def _run(state, launch_id: str, repo: str, number: int, cmd: dict,
         binary: str, tracked: bool, rows) -> None:
    def release() -> None:
        with _preparing_lock:
            _preparing.discard((repo.lower(), number))

    issues_mod.run_launch(
        state, launch_id, cmd["harness"], tracked,
        lambda: _prepare_and_launch(
            state, launch_id, repo, number, cmd, binary, rows),
        "Open", release)


def _prepare_and_launch(state, launch_id: str, repo: str, number: int,
                        cmd: dict, binary: str, rows) -> str:
    """PR, worktree, prompt, ``launch_new``; the failure note, or ""."""
    def note(text: str) -> None:
        state.settle_launch(launch_id, note=text)

    fetched = gh_pulls.fetch(repo, number, state.home)
    pr = fetched.data if fetched.status == "ok" else None
    if pr is None and not cmd["prompt"]:
        return (f"Couldn't read {repo}#{number}: "
                f"{fetched.error or fetched.status}")
    title = f"{repo}#{number}"
    if pr and pr.get("title"):
        title += f" {pr['title']}"
    # As in issues.py: adoption links the session to this PR's task,
    # and launch_linked marks the pending launch against it now.
    origin = tasks_mod.pull_ref(repo, number, pr)
    state.settle_launch(launch_id, title=title, origin=origin)
    tasks_mod.launch_linked(state, launch_id, origin)

    if not manage.mirror_path(repo, state.home).is_dir():
        note("Cloning mirror…")
    else:
        note(f"Checking out PR #{number}…")
    # pr_worktree holds the repo mirror's lock: a phone open and a page
    # open of two PRs in one repo can't interleave on FETCH_HEAD.
    made = manage.pr_worktree(repo, str(number), state.home)
    if made.status != "ok":
        return made.error or made.status
    cwd = made.data["path"]
    state.settle_launch(launch_id, cwd=cwd)

    prompt = cmd["prompt"] or gh_pulls.build_prompt(
        repo, pr or {"number": number}, cwd,
        limit=protocol.MAX_LAUNCH_PROMPT)
    if len(prompt) > protocol.MAX_LAUNCH_PROMPT:
        return (f"The prompt is over {protocol.MAX_LAUNCH_PROMPT} "
                "characters; shorten it")

    note(f"Starting {cmd['harness']}…")
    known = catalog_mod.known_session_ids(rows, cmd["harness"], cwd)
    ok, msg, _name = supervise.launch_new(
        state.home, cmd["harness"], cwd, prompt,
        known_ids=known, launch_id=launch_id, binary=binary,
        model=cmd["model"], effort=cmd["effort"],
    )
    return "" if ok else (msg or "the harness did not start")
