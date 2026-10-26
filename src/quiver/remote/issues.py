"""Resolve a GitHub issue: ``POST /v1/issues/resolve``.

Body ``{repo: "owner/repo", number, harness, prompt?, context?}``. The
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
"""

from __future__ import annotations

import threading

from quiver.github import gh, manage, repos
from quiver.github import issues as gh_issues
from quiver.remote import catalog as catalog_mod
from quiver.remote import protocol, sessionmeta, supervise

# "Your context" is a paragraph or two typed on a phone or in a sheet.
MAX_CONTEXT = 8_000
# Room for the "Context for this run, from <login>:" line (a GitHub
# login is at most 39 characters) when context joins a client's prompt.
_CONTEXT_HEADER_ROOM = 80

# Issues whose resolve is still between the 202 and launch_new's
# pending marker, as (repo lower, number). A second resolve of one of
# them, or of one whose marker still waits for adoption in the same
# checkout, is refused: adoption has only the folder and start time to
# go on, so two launches waiting there could each claim the other's
# session, or neither.
_preparing: set[tuple[str, int]] = set()
_preparing_lock = threading.Lock()


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
            "prompt": prompt if prompt.strip() else "", "context": context}


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
    binary = catalog_mod.launch_command(harness)
    if binary is None:
        return 422, protocol.error(
            "not_launchable",
            f"{harness} can't start here: archived, switched off in "
            f"Provider visibility, or not installed")
    if not supervise.tmux_bin():
        return 422, protocol.error("launch_failed", "tmux is not installed")
    env = gh.probe_env()
    if env.status != "ok":
        return 422, protocol.error("gh_unavailable", gh.env_error(env))
    tracked = harness not in catalog_mod.untracked(
        catalog_mod.launchable_harnesses())
    rows = state.catalog.tail_rows()
    ref = (repo.lower(), cmd["number"])
    tree = manage.issue_worktree_path(repo, cmd["number"], state.home)
    with _preparing_lock:
        if ref in _preparing or supervise.fork_blocker(
                state.home, harness, tree):
            return 409, protocol.error(
                "resolve_pending",
                f"an agent for {repo}#{cmd['number']} is still starting in "
                f"its worktree; resolve again once its session shows up")
        _preparing.add(ref)
    launch_id = state.new_launch(state.machine_id, harness, "")
    state.settle_launch(launch_id, note="preparing worktree")
    threading.Thread(
        target=_run,
        args=(state, launch_id, cmd, binary, tracked, rows, env.login),
        name=f"remote-launch-{launch_id}", daemon=True,
    ).start()
    return 202, {"ok": True, "launch_id": launch_id, "state": "pending",
                 "note": "preparing worktree"}


def _run(state, launch_id: str, cmd: dict, binary: str, tracked: bool,
         rows, login: str) -> None:
    def fail(text: str) -> None:
        state.settle_launch(launch_id, state="failed", note=text)

    try:
        failure = _prepare_and_launch(state, launch_id, cmd, binary, rows,
                                      login)
        if failure:
            fail(failure)
        elif not tracked:
            state.settle_launch(launch_id, state="untracked", note=(
                f"{cmd['harness']} is running under tmux, but swe can't "
                f"read its sessions, so there's no chat to open"))
    except Exception as exc:  # a launch must never stay pending forever
        fail(f"Resolve failed: {exc}")
    finally:
        # launch_new has written its pending marker by now (or failed),
        # so fork_blocker takes over guarding the checkout.
        with _preparing_lock:
            _preparing.discard((cmd["repo"].lower(), cmd["number"]))


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
    state.settle_launch(launch_id, title=title)

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
    from quiver.remote.hub import GLOBAL_KEY

    state.emit(GLOBAL_KEY, protocol.event(
        "session.meta", key, state.machine_id, data))
