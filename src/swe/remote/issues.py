"""GitHub issues on the phone: ``GET /v1/issues``,
``GET /v1/issues/<o>/<r>/<n>``, ``POST /v1/issues/resolve`` and
``POST /v1/issues/comment``.

The reads answer from the same 60-second ``gh`` cache the web app's
Repositories page uses (``github/shared.py``), per pinned repo,
so the phone and the page agree and a slow ``gh`` shows up once. A
repo's issue rows add ``mine`` (the hub's ``gh`` login authored it) so
the client filters All/Mine without a second round trip; ``agent_running``
may join a row when the hub can tell an agent is already on the issue —
clients treat its absence as no chip. ``?state=open|closed|all`` picks
the ``gh issue list --state`` the rows come from (``open`` by default,
so the Closed chip's rows cost a second read only when asked for).

The resolve: body ``{repo: "owner/repo", number, harness, prompt?,
context?}``. The
hub checks what a client can act on straight away (a pinned repo, a
harness that starts here, tmux, a signed-in ``gh``, no other resolve of
the issue still starting: ``409 resolve_pending``) and answers ``202
{launch_id, state: "pending", note}``; the rest runs on a thread behind
that launch id, the way a ``POST /v1/sessions`` launch does:

1. read the issue (``github/issues.py``), for the prompt and the title;
2. ``manage.issue_worktree``: the repo's mirror (cloned on first use),
   then branch ``quiver/issue-<n>`` checked out under
   ``~/.quiver/worktrees``. A second resolve of the same issue resumes
   in the same checkout;
3. ``supervise.launch_new`` in that checkout with the client's prompt,
   or ``build_prompt`` when none was sent. ``context`` is added as a
   "Context for this run" block either way (once).

While pending, the launch's ``note`` names the step ("Cloning mirror…",
"Creating worktree quiver/issue-<n>…", "Starting <harness>…"), so
``GET /v1/launches/<id>`` reads as progress. Any failure settles the
launch ``failed`` with a readable note. When adoption names the new
session, its title becomes ``<owner>/<repo>#<n> <issue title>``
(``title_adopted``, called from ``HubState.emit``). Hub machine only:
satellites have no mirrors.

The comment: body ``{repo, number, body}``. The hub posts ``body`` on
the issue with its own ``gh`` login, through ``swe gh issue comment``'s
``post_comment``: the text goes to ``gh issue comment --body-file -``
on stdin, never argv, where every process on the machine could read it.
Only a pinned repo, and nothing past GitHub's own size cap, so a paired
client can't post anywhere the hub's ``gh`` login reaches.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from swe.github import cli as ghcli
from swe.github import gh, manage, repos
from swe.github import issues as gh_issues
from swe.remote import catalog as catalog_mod
from swe.remote import protocol, sessionmeta, supervise
from swe.remote import tasks as tasks_mod
from swe.github import shared as G

# ``swe gh``'s list cap: open issues per pinned repo in one answer.
LIST_LIMIT = 50

# "Your context" is a paragraph or two typed on a phone or in a sheet.
MAX_CONTEXT = 8_000
# Room for the "Context for this run, from <login>:" line (a GitHub
# login is at most 39 characters) when context joins a client's prompt.
_CONTEXT_HEADER_ROOM = 80

# GitHub refuses a comment body longer than this many characters.
MAX_COMMENT = 65_536

# Issues whose resolve is still between the 202 and launch_new's
# pending marker, as (repo lower, number). A second resolve of one of
# them, or of one whose marker still waits for adoption in the same
# checkout, is refused: adoption has only the folder and start time to
# go on, so two launches waiting there could each claim the other's
# session, or neither.
_preparing: set[tuple[str, int]] = set()
_preparing_lock = threading.Lock()


def _refresh(args) -> bool:
    """A query dict's ``refresh=1``/``refresh=true``."""
    return str((args.get("refresh") or [""])[0]) in {"1", "true"}


def _repo_arg(owner, name) -> str | None:
    """``/v1/issues/<owner>/<repo>/...`` → ``owner/repo``, else None."""
    return repos.normalize_repo_input(f"{owner}/{name}")


def _number_arg(raw) -> int | None:
    """A path's issue number: canonical digits, at most nine of them."""
    if (not isinstance(raw, str) or not raw.isdigit()
            or not raw.isascii() or len(raw) > 9):
        return None
    number = int(raw)
    return number if number >= 1 else None


def _body_number(raw) -> int | None:
    """A JSON body's issue number: an int or digit string, as
    ``_number_arg`` reads a path's. ``true`` is no number."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        raw = str(raw)
    return _number_arg(raw.strip() if isinstance(raw, str) else raw)


# ------------------------------------------------------------------ reads

def list_view(state, args) -> tuple[int, dict]:
    """``GET /v1/issues``: every pinned repo's issues.

    ``{repos: [{name, status, issues, error?}], machine, machine_id,
    github, env_blocked?, fetched_at}``; each issue is ``cli._issue_row``
    plus ``mine``. A missing or signed-out ``gh`` answers ``env_blocked``
    before any fetch, as ``swe gh`` stops on the same words.
    ``?state=`` is ``gh``'s own vocabulary — ``open``, ``closed`` or
    ``all`` — and anything else is a ``422 bad_state``."""
    wanted = str((args.get("state") or [""])[0]) or "open"
    if wanted not in ("open", "closed", "all"):
        return 422, protocol.error(
            "bad_state", f"state must be open, closed or all, not "
                         f"{wanted!r}")
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
            _list_key(state.home, name, wanted),
            lambda: _repo_issues(name, env.login, wanted),
            refresh=_refresh(args),
            ok=lambda e: e.get("status") == "ok")

    names = [p["name"] for p in pins]
    replies = G.fan(names, entry)
    body["repos"] = [value for value, _at in replies]
    if replies:
        body["fetched_at"] = min(at for _value, at in replies)
    # tasks.py's store says which issues have an agent on them, as
    # pulls.py's list does — only a running one earns the flag.
    running = tasks_mod.running_task_ids(state)
    for repo_row in body["repos"]:
        name = str(repo_row.get("name") or "")
        for issue in repo_row.get("issues") or []:
            if isinstance(issue, dict) and tasks_mod.issue_task_id(
                    name, issue.get("number")) in running:
                issue["agent_running"] = True
    return 200, body


def _list_key(home, name: str, wanted: str) -> tuple:
    """The open list shares its cache slot with ``tasks``'s own read of
    it (the ``("issues", hk, repo)`` entry ``_repo_tasks`` builds rows
    from); any other state gets a fourth key part so a closed page
    never lands in the open slot, or the other way around. Both shapes
    still fall under ``repositories._drop_repo``'s ``k[2] == repo``
    match."""
    if wanted == "open":
        return ("issues", G.home_key(home), name)
    return ("issues", G.home_key(home), name, wanted)


def _repo_issues(name: str, login: str, state: str = "open") -> dict:
    """One repo's list entry: ``_issue_row``s plus the viewer flag.

    ``mine`` is the row's author matching the hub's ``gh`` login — the
    authored reading ``gh pr status`` gives a pull, asked straight from
    the row since issues carry no review flag to fetch alongside.
    ``state`` is ``gh issue list --state``'s own word; ``tasks`` calls
    this with the ``open`` default only.
    """
    listed = ghcli._fetch_issues(name, state, LIST_LIMIT)
    if listed["status"] != "ok":
        return {"name": name, "status": listed["status"], "issues": [],
                "error": listed.get("error") or listed["status"]}
    issues = [{**row, "mine": bool(login) and row.get("author") == login}
              for row in listed["issues"]]
    return {"name": name, "status": "ok", "issues": issues}


def detail(state, owner: str, name: str, number_raw: str,
           args) -> tuple[int, dict]:
    """``GET /v1/issues/<o>/<r>/<n>``: one issue in full — body,
    comments, the branch and worktree a resolve uses and the
    ``suggested_prompt`` its sheet prefills (``github/issues.py``'s
    ``shape``, which ``swe gh issue view --json`` also answers)."""
    repo = _repo_arg(owner, name)
    if repo is None:
        return 400, protocol.error("bad_repo",
                                   "issue path wants owner/repo")
    number = _number_arg(number_raw)
    if number is None:
        return 422, protocol.error("bad_issue",
                                   "number must be an issue number")
    pinned = _pinned(state.home, repo)
    if pinned is None:
        return 404, protocol.error(
            "not_pinned", f"{repo} is not pinned on the hub")
    env = G.probe(state.home, refresh=_refresh(args))
    base = {"github": G.env_payload(env), "repo": pinned,
            "machine": (state.machine or {}).get("name"),
            "machine_id": state.machine_id}
    if env.status != "ok":
        return 200, {**base, "status": env.status, "issue": None,
                     "error": gh.env_error(env)}

    def fetch():
        result = gh_issues.fetch(pinned, number, state.home)
        if result.status != "ok":
            return {"repo": pinned, "status": result.status, "issue": None,
                    "error": result.error or result.status}
        return {"repo": pinned, "status": "ok", "issue": result.data}

    value, at = G.CACHE.get(
        ("issue", G.home_key(state.home), pinned, str(number)), fetch,
        refresh=_refresh(args), ok=lambda v: v["status"] == "ok")
    issue = value.get("issue")
    if isinstance(issue, dict) and "suggested_prompt" not in issue:
        # An entry another reader put under this key (a bare row, not
        # ``shape``'s) still answers with the fields only this route adds.
        worktree = manage.issue_worktree_path(pinned, number, state.home)
        issue = {**issue,
                 "branch": manage.issue_branch(number),
                 "worktree": worktree,
                 "suggested_prompt": gh_issues.build_prompt(
                     pinned, issue, worktree)}
        value = {**value, "issue": issue}
    return 200, {**base, **value, "fetched_at": at}


def parse_body(body) -> dict:
    """Validate a resolve body; raises ValueError on bad input."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    repo = body.get("repo")
    name = repos.normalize_repo_input(repo) if isinstance(repo, str) else None
    if not name:
        raise ValueError("repo must be owner/name")
    number = body.get("number")
    if isinstance(number, str) and number.isdigit():
        number = int(number)
    if not isinstance(number, int) or isinstance(number, bool) \
            or number < 1:
        raise ValueError("number must be a positive issue number")
    harness = body.get("harness")
    if not isinstance(harness, str) \
            or not protocol._HARNESS_RE.fullmatch(harness):
        raise ValueError("harness must be a harness name")
    prompt = body.get("prompt")
    if prompt is None:
        prompt = ""
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    context = body.get("context")
    if context is None:
        context = ""
    if not isinstance(context, str):
        raise ValueError("context must be a string")
    if len(context) > MAX_CONTEXT:
        raise ValueError(f"context is over {MAX_CONTEXT} characters")
    room = _CONTEXT_HEADER_ROOM if prompt.strip() and context.strip() else 0
    if len(prompt) + len(context) + room > protocol.MAX_LAUNCH_PROMPT:
        raise ValueError(
            f"prompt and context are over {protocol.MAX_LAUNCH_PROMPT} "
            "characters together")
    return {"repo": name, "number": number, "harness": harness,
            "prompt": prompt if prompt.strip() else "", "context": context,
            "model": protocol._launch_value(body, "model"),
            "effort": protocol._launch_value(body, "effort")}


def comment(state, body: dict | None) -> tuple[int, dict]:
    """``POST /v1/issues/comment``: post ``body`` on issue ``number`` of
    a pinned ``repo`` as the hub's ``gh`` login. Answers ``200 {ok,
    github, repo, number, comment: {author, body, created_at, url},
    machine, machine_id}``; the issue's cached reads are dropped so the
    next detail read lists the new comment."""
    if body is None:
        return 400, protocol.error("bad_body", "unreadable JSON body")
    raw = body.get("repo")
    name = repos.normalize_repo_input(raw) if isinstance(raw, str) else None
    if not name:
        return 422, protocol.error("bad_repo", "repo must be owner/name")
    number = _body_number(body.get("number"))
    if number is None:
        return 422, protocol.error("bad_issue",
                                   "number must be an issue number")
    text = body.get("body")
    if not isinstance(text, str):
        return 422, protocol.error("bad_comment",
                                   "body must be the comment's text")
    # The Mac's composer posts the trimmed text; so does this.
    text = text.strip()
    if not text:
        return 422, protocol.error("bad_comment", "The comment is empty")
    if len(text) > MAX_COMMENT:
        return 422, protocol.error(
            "comment_too_long",
            f"The comment is over GitHub's {MAX_COMMENT:,} characters")
    pinned = _pinned(state.home, name)
    if pinned is None:
        return 404, protocol.error(
            "not_pinned", f"{name} is not pinned on the hub")
    env = G.probe(state.home, refresh=True)
    if env.status != "ok":
        return 409, protocol.error("gh_" + env.status.replace("-", "_"),
                                   gh.env_error(env))
    with G.WRITE_LOCK:
        result = gh_issues.post_comment(pinned, number, text, env.login)
    # Dropped even after a failure: a timeout may still have posted.
    _drop_issue(state.home, pinned, number)
    if result.status == "not-found":
        return 404, protocol.error(
            "unknown_issue", f"No issue #{number} in {pinned}: "
                             f"{result.error or 'not found'}")
    if result.status != "ok":
        return 502, protocol.error(
            "comment_failed", result.error or result.status)
    return 200, {"ok": True, "github": G.env_payload(env), "repo": pinned,
                 "number": number, "comment": result.data,
                 "machine": (state.machine or {}).get("name"),
                 "machine_id": state.machine_id}


def _drop_issue(home, repo: str, number: int) -> None:
    """Forget the issue's detail and every list that counts its
    comments (the phone's ``/v1/issues``, the Repositories board and
    focus surfaces, the task rows), as the Mac drops its own after a
    post."""
    hk = G.home_key(home)
    G.CACHE.drop(lambda k: isinstance(k, tuple) and len(k) > 2
                 and k[1] == hk and k[2] == repo
                 and (k[0] in ("issues", "board", "focus", "task")
                      or (k[0] == "issue" and k[3:4] == (str(number),))))


def _pinned(home, name: str) -> str | None:
    """The pin ``name`` matches, spelled as pinned, or None."""
    for pin in repos.load_pinned(manage.pins_file(home)):
        if pin["name"].lower() == name.lower():
            return pin["name"]
    return None


def final_prompt(cmd: dict, issue: dict | None, cwd: str,
                 login: str = "") -> str:
    """The prompt the agent starts with: the client's, else the built
    one, with the context block added once."""
    if not cmd["prompt"]:
        return gh_issues.build_prompt(
            cmd["repo"], issue or {"number": cmd["number"]}, cwd,
            context=cmd["context"], context_author=login)
    prompt = cmd["prompt"]
    block = gh_issues.context_block(cmd["context"], login)
    if block and cmd["context"].strip() not in prompt:
        prompt = prompt.rstrip() + "\n\n" + block
    return prompt


def request(state, body: dict | None) -> tuple[int, dict]:
    """``POST /v1/issues/resolve``: answers ``(status, payload)``."""
    if body is None:
        return 400, protocol.error("bad_body", "unreadable JSON body")
    try:
        cmd = parse_body(body)
    except ValueError as exc:
        return 422, protocol.error("bad_command", str(exc))
    repo = _pinned(state.home, cmd["repo"])
    if repo is None:
        return 422, protocol.error(
            "not_pinned",
            f"{cmd['repo']} isn't pinned on this hub: pin it with "
            f"`swe gh repo add {cmd['repo']}`")
    cmd["repo"] = repo
    harness = cmd["harness"]
    ready = launch_ready(cmd)
    if isinstance(ready, tuple):
        return ready
    binary, tracked = ready.binary, ready.tracked
    rows = state.catalog.tail_rows()
    ref = (repo.lower(), cmd["number"])
    tree = manage.issue_worktree_path(repo, cmd["number"], state.home)
    with _preparing_lock:
        # An untracked harness's marker is never adopted, so it guards
        # nothing: only a tracked harness waits on it.
        if ref in _preparing or (tracked and supervise.fork_blocker(
                state.home, harness, tree)):
            return 409, protocol.error(
                "resolve_pending",
                f"an agent for {repo}#{cmd['number']} is still starting in "
                f"its worktree; resolve again once its session shows up")
        _preparing.add(ref)
    launch_id = state.new_launch(
        state.machine_id, harness, "",
        model=cmd["model"] or "", effort=cmd["effort"] or "")
    state.settle_launch(launch_id, note="preparing worktree")
    threading.Thread(
        target=_run,
        args=(state, launch_id, cmd, binary, tracked, rows, ready.login),
        name=f"remote-launch-{launch_id}", daemon=True,
    ).start()
    return 202, {"ok": True, "launch_id": launch_id, "state": "pending",
                 "note": "preparing worktree"}


@dataclass
class LaunchReady:
    """What ``launch_ready`` found: the harness's command, the ``gh``
    login (for the prompt's context line), and whether swe can read the
    harness's sessions (an untracked one gets no chat to open)."""

    binary: str
    login: str
    tracked: bool


def launch_ready(cmd: dict) -> "LaunchReady | tuple[int, dict]":
    """The checks a resolve and a PR open both make before their 202:
    the harness can start here with the asked model and effort, tmux is
    installed and ``gh`` is signed in. The refusal ``(status, payload)``
    otherwise."""
    harness = cmd["harness"]
    binary = catalog_mod.launch_command(harness)
    if binary is None:
        return 422, protocol.error(
            "not_launchable",
            f"{harness} can't start here: archived, switched off in "
            f"Provider visibility, or not installed")
    blocked = supervise.fresh_launch_error(
        harness, cmd["model"], cmd["effort"])
    if blocked:
        return 409, protocol.error("unsupported_launch_option", blocked)
    if not supervise.tmux_bin():
        return 422, protocol.error("launch_failed", "tmux is not installed")
    env = gh.probe_env()
    if env.status != "ok":
        return 422, protocol.error("gh_unavailable", gh.env_error(env))
    tracked = harness not in catalog_mod.untracked(
        catalog_mod.launchable_harnesses())
    return LaunchReady(binary, env.login, tracked)


def run_launch(state, launch_id: str, harness: str, tracked: bool,
               prepare: Callable[[], str], verb: str,
               release: Callable[[], None]) -> None:
    """The thread behind a resolve's or PR open's 202: ``prepare()``
    returns a failure note or ""; any failure or exception settles the
    launch ``failed`` (it must never stay pending forever), an untracked
    harness settles ``untracked``, and ``release()`` always runs."""
    def fail(text: str) -> None:
        state.settle_launch(launch_id, state="failed", note=text)

    try:
        failure = prepare()
        if failure:
            fail(failure)
        elif not tracked:
            state.settle_launch(launch_id, state="untracked", note=(
                f"{harness} is running under tmux, but swe can't "
                f"read its sessions, so there's no chat to open"))
    except Exception as exc:
        fail(f"{verb} failed: {exc}")
    finally:
        release()


def _run(state, launch_id: str, cmd: dict, binary: str, tracked: bool,
         rows, login: str) -> None:
    def release() -> None:
        # launch_new has written its pending marker by now (or failed),
        # so fork_blocker takes over guarding the checkout.
        with _preparing_lock:
            _preparing.discard((cmd["repo"].lower(), cmd["number"]))

    run_launch(
        state, launch_id, cmd["harness"], tracked,
        lambda: _prepare_and_launch(state, launch_id, cmd, binary, rows,
                                    login),
        "Resolve", release)


def _prepare_and_launch(state, launch_id: str, cmd: dict, binary: str,
                        rows, login: str) -> str:
    """Issue, worktree, prompt, ``launch_new``; the failure note, or ""."""
    repo, number, harness = cmd["repo"], cmd["number"], cmd["harness"]

    def note(text: str) -> None:
        # settle_launch writes only while the launch is pending, so a
        # note never revives a launch that already settled.
        state.settle_launch(launch_id, note=text)

    fetched = gh_issues.fetch(repo, number, state.home)
    issue = fetched.data if fetched.status == "ok" else None
    if issue is None and not cmd["prompt"]:
        return (f"Couldn't read {repo}#{number}: "
                f"{fetched.error or fetched.status}")
    title = f"{repo}#{number}"
    if issue and issue.get("title"):
        title += f" {issue['title']}"
    # ``origin`` rides the launch record into adoption: emit's
    # origin_adopted links the session to this issue in tasks.json.
    # launch_linked records the launch itself now, so the task row
    # reads agent_running through the whole prepare window below.
    origin = tasks_mod.issue_ref(repo, number, issue)
    state.settle_launch(launch_id, title=title, origin=origin)
    tasks_mod.launch_linked(state, launch_id, origin)

    made = manage.issue_worktree(repo, number, state.home, progress=note)
    if made.status != "ok":
        return made.error or made.status
    cwd = made.data["path"]
    state.settle_launch(launch_id, cwd=cwd)
    prompt = final_prompt(cmd, issue, cwd, login)
    if len(prompt) > protocol.MAX_LAUNCH_PROMPT:
        return (f"The prompt is over {protocol.MAX_LAUNCH_PROMPT} "
                "characters; shorten it or the context")

    note(f"Starting {harness}…")
    # Every same-cwd session that exists now (a resumed checkout can have
    # some): adoption refuses these ids.
    known = catalog_mod.known_session_ids(rows, harness, cwd)
    ok, msg, _name = supervise.launch_new(
        state.home, harness, cwd, prompt,
        known_ids=known, launch_id=launch_id, binary=binary,
        model=cmd["model"], effort=cmd["effort"],
    )
    return "" if ok else (msg or "the harness did not start")


def title_adopted(state, launch_id: str, key: str) -> None:
    """Name the session a resolve launch became after its issue.

    Called once, when ``session.launched`` settles the launch. A launch
    without a ``title`` (every ordinary ``POST /v1/sessions``) is left
    alone. ``session.meta`` goes on the global stream only: anything in
    the session's own journal before its tailer starts would make the
    tailer skip the first turn as history already sent.
    """
    view = state.launch_view(launch_id) or {}
    title = view.get("title") or ""
    if not title:
        return
    try:
        entry = sessionmeta.update(state.home, key, "title", {"title": title})
    except (ValueError, OSError, sessionmeta.TooManyEntries):
        return
    row = state.catalog.reapply_meta(key)
    data = sessionmeta.fields(row) if row is not None else {
        "title": entry["title"], "harness_title": "",
        "pinned": entry["pinned"], "pinned_at": entry["pinned_at"]}
    from swe.remote.hub import GLOBAL_KEY

    state.emit(GLOBAL_KEY, protocol.event(
        "session.meta", key, state.machine_id, data))
