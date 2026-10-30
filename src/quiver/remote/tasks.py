"""The hub's work-item store: which sessions and launches work on
which task.

A *task* is a normalized work item — a GitHub issue or pull request, a
Linear card, a Notion page — independent of where it lives:

    {source: github|linear|notion, kind: issue|pull|card|page,
     repo?|project?, ref, title, url}

``tasks.json`` under ``data/remote`` (0600) maps each task's id to what
the hub ran for it, the shape #844 asks for::

    task_ref → {task, sessions: [keys], launches: [ids],
                created_at, last_active_at}

Two paths write links. An issue resolve (``issues.py``) or a PR open
(``pulls.py``) stamps ``origin`` on its launch record while it is still
pending; adoption — the ``session.launched`` event that names the
session — then links the session key and launch id through
``origin_adopted``, called beside ``title_adopted`` in
``HubState.emit``. ``POST /v1/sessions/{key}/task`` links a running
session by hand and ``DELETE`` unlinks it.

``GET /v1/tasks`` aggregates what a client draws: every task the
sources list (GitHub: the pinned repos' open issues and PRs, behind the
same ``gh`` cache ``/v1/pulls`` reads), overlaid with the store, so
each row carries ``agent_running`` (a linked session is active, or its
launch is still pending), ``sessions``, ``last_active_at`` and the
``state`` the linked objects imply — ``open``, ``in_progress``,
``pr_open``, ``merged`` or ``closed``. For an issue the linked PR is
the one on its ``quiver/issue-<n>`` branch.

A stored task the sources can't read this round — the source is
blocked or down, or the task sits outside the ``MAX_REFRESH`` window
and off the open list — derives its state from what its source last
said, not from linkage alone. That memory is a separate cache,
``cache/task_states.json`` (``task_id → {state, pr}``): written only
when a fresh read changes a stored task's state, read only when no
fresh read happened, and regenerable, so ``tasks.json`` stays links
only. Each row's ``state_source`` says which it got: ``live``,
``cached`` (last known) or ``links`` (nothing ever read).

``TaskSource`` is the seam other trackers plug into: ``list()``
for what the source has open, ``get(ref)`` to refresh one stored task's
state. GitHub, Linear (``remote/linear.py``) and Notion
(``remote/notion.py``) are live adapters.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from quiver.github import cli as ghcli
from quiver.github import gh, manage, repos
from quiver.paths import (atomic_write_text, remote_dir_for,
                          task_states_cache_for)
from quiver.remote import linear as linear_mod
from quiver.remote import notion as notion_mod
from quiver.remote import protocol
from quiver.github import shared as G

_BASENAME = "tasks.json"

SOURCES = ("github", "linear", "notion")
KINDS = ("issue", "pull", "card", "page")
# Kinds the GitHub adapter carries; "card" and "page" are valid
# task_refs, just not GitHub ones.
_GITHUB_KINDS = ("issue", "pull")

# Bounds on the file: a client that links in a loop, and a session that
# gets linked to (or a launch recorded against) everything it touches.
MAX_TASKS = 2000
MAX_LINKS = 100
# Per-list bound on stored-task refreshes: each one is a gh call or
# two, and the store's history can run to MAX_TASKS. The most recently
# active win; the rest keep their last-known state this round.
MAX_REFRESH = 24
# Field bounds on a TaskRef from a client: the store keeps whatever is
# linked, and every /v1/tasks answer carries it back out. A title past
# its bound is cut (an issue's own title can be long); the rest refuse.
MAX_REF = 200
MAX_PROJECT = 200
MAX_TITLE = 1000
MAX_URL = 2048
# PRs read when looking for the one an issue branch produced.
LINKED_PR_PAGE = 20

# The hub is the only writer; this covers its concurrent handlers.
_lock = threading.Lock()


def tasks_file(home: Path | str | None = None) -> Path:
    return remote_dir_for(
        Path(home) if home is not None else None) / _BASENAME


def states_file(home: Path | str | None = None) -> Path:
    return task_states_cache_for(Path(home) if home is not None else None)


# ------------------------------------------------------------------ refs

def task_id(task: dict) -> str:
    """A task's canonical id: ``source:kind:scope:ref``, lowercased.
    ``scope`` is the repo for github, the project anywhere else. A
    Notion page id reads in its dashed form however it was spelled, so
    a link made with a bare id meets the row the source lists."""
    scope = task.get("repo") or task.get("project") or ""
    ref = task.get("ref")
    if task.get("source") == "notion":
        ref = notion_mod.normalize_id(str(ref or "")) or ref
    return f"{task.get('source')}:{task.get('kind')}:{scope}:{ref}".lower()


def parse_ref(body) -> dict:
    """Validate a TaskRef; ``ValueError`` on bad input.

    ``ref`` accepts ``5``, ``"5"`` and ``"#5"``; for github it is the
    issue/PR number, canonicalized, and ``repo`` is required. A github
    ref without a ``url`` gets the canonical ``github.com`` one. ``title``
    and ``url`` default to "" — a launch stamps its origin before the
    issue or PR has been read, so a bare ref must still store.
    """
    if not isinstance(body, dict):
        raise ValueError("task_ref must be a JSON object")
    source = body.get("source")
    if source not in SOURCES:
        raise ValueError("task_ref.source must be github, linear or notion")
    kind = body.get("kind")
    if kind not in KINDS:
        raise ValueError("task_ref.kind must be issue, pull, card or page")
    ref = body.get("ref")
    if isinstance(ref, bool):
        ref = None
    if isinstance(ref, int):
        ref = str(ref)
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("task_ref.ref is required")
    ref = ref.strip().lstrip("#")
    if len(ref) > MAX_REF:
        raise ValueError(f"task_ref.ref is over {MAX_REF} characters")
    title = body.get("title")
    url = body.get("url")
    if title is not None and not isinstance(title, str):
        raise ValueError("task_ref.title must be a string")
    if url is not None and not isinstance(url, str):
        raise ValueError("task_ref.url must be a string")
    title = (title or "")[:MAX_TITLE]
    if url:
        # Clients render the url as a link: a javascript: or file: one
        # must never be stored.
        if len(url) > MAX_URL:
            raise ValueError(f"task_ref.url is over {MAX_URL} characters")
        try:
            parts = urlsplit(url)
            host = parts.hostname  # netloc can be just ":80" or "user@"
        except ValueError:
            host = None
        if not host or parts.scheme.lower() not in ("http", "https"):
            raise ValueError("task_ref.url must be an http(s) URL")
    task = {"source": source, "kind": kind, "ref": ref,
            "title": title, "url": url or ""}
    if source == "github":
        if kind not in _GITHUB_KINDS:
            raise ValueError("a github task_ref.kind is issue or pull")
        raw_repo = body.get("repo")
        repo = (repos.normalize_repo_input(raw_repo)
                if isinstance(raw_repo, str) else None)
        if repo is None:
            raise ValueError("task_ref.repo must be owner/name")
        if not ref.isdigit() or int(ref) < 1:
            raise ValueError(
                "a github task_ref.ref is the issue/PR number")
        task["repo"] = repo
        task["ref"] = str(int(ref))
        if not task["url"]:
            leaf = "pull" if kind == "pull" else "issues"
            task["url"] = f"https://github.com/{repo}/{leaf}/{task['ref']}"
    else:
        project = body.get("project")
        if project is not None and not isinstance(project, str):
            raise ValueError("task_ref.project must be a string")
        if project and len(project) > MAX_PROJECT:
            raise ValueError(
                f"task_ref.project is over {MAX_PROJECT} characters")
        if project:
            task["project"] = project
        if source == "notion":
            task["ref"] = notion_mod.normalize_id(ref) or ref
    return task


def issue_ref(repo: str, number: int, issue: dict | None = None) -> dict:
    """The TaskRef an issue resolve stamps as its launch's ``origin``."""
    issue = issue or {}
    return {"source": "github", "kind": "issue", "repo": repo,
            "ref": str(number),
            "title": issue.get("title") or f"{repo}#{number}",
            "url": issue.get("url")
            or f"https://github.com/{repo}/issues/{number}"}


def pull_ref(repo: str, number: int, pr: dict | None = None) -> dict:
    """The TaskRef a PR open stamps as its launch's ``origin``."""
    pr = pr or {}
    return {"source": "github", "kind": "pull", "repo": repo,
            "ref": str(number),
            "title": pr.get("title") or f"{repo}#{number}",
            "url": pr.get("url")
            or f"https://github.com/{repo}/pull/{number}"}


def pull_task_id(repo: str, number) -> str:
    """The id a ``/v1/pulls`` row's ``agent_running`` flag is keyed on."""
    return task_id({"source": "github", "kind": "pull",
                    "repo": repo, "ref": str(number)})


def issue_task_id(repo: str, number) -> str:
    """The id a ``/v1/issues`` row's ``agent_running`` flag is keyed on."""
    return task_id({"source": "github", "kind": "issue",
                    "repo": repo, "ref": str(number)})


# ------------------------------------------------------------------ store

def _load(home: Path | str | None) -> dict[str, dict]:
    """``task_id → entry``; a corrupt or missing file reads empty."""
    try:
        data = json.loads(tasks_file(home).read_text(encoding="utf-8"))
        rows = data.get("tasks") if isinstance(data, dict) else None
    except Exception:
        return {}
    if not isinstance(rows, dict):
        return {}
    out: dict[str, dict] = {}
    for tid, entry in rows.items():
        if not isinstance(entry, dict) \
                or not isinstance(entry.get("task"), dict):
            continue
        session_values = entry.get("sessions")
        launch_values = entry.get("launches")
        sessions = [s for s in session_values if isinstance(s, str)] \
            if isinstance(session_values, list) else []
        launches = [s for s in launch_values if isinstance(s, str)] \
            if isinstance(launch_values, list) else []
        row = {
            "task": dict(entry["task"]),
            "sessions": sessions,
            "launches": launches,
            "created_at": _num(entry.get("created_at")),
            "last_active_at": _num(entry.get("last_active_at")),
        }
        key = str(tid)
        if row["task"].get("source") == "notion":
            # A page linked before refs were normalized sits under its
            # bare-id key: re-key it, folding into the dashed one.
            key = task_id(row["task"])
            row["task"]["ref"] = notion_mod.normalize_id(
                str(row["task"].get("ref") or "")) or row["task"].get("ref")
        if key in out:
            row = _fold(out[key], row)
        out[key] = row
    return out


def _fold(a: dict, b: dict) -> dict:
    """Two stored entries for one task, as one: links unioned in order,
    the earliest creation and the latest activity."""
    return {
        "task": b["task"] if b["last_active_at"] >= a["last_active_at"]
        else a["task"],
        "sessions": list(dict.fromkeys(a["sessions"] + b["sessions"])),
        "launches": list(dict.fromkeys(a["launches"] + b["launches"])),
        "created_at": min(a["created_at"] or b["created_at"],
                          b["created_at"] or a["created_at"]),
        "last_active_at": max(a["last_active_at"], b["last_active_at"]),
    }


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _save(home: Path | str | None, rows: dict[str, dict]) -> None:
    if len(rows) > MAX_TASKS:
        # The most recently touched tasks keep their rows.
        keep = sorted(
            rows.items(),
            key=lambda kv: kv[1].get("last_active_at") or 0,
            reverse=True,
        )[:MAX_TASKS]
        rows = dict(keep)
    atomic_write_text(
        tasks_file(home), json.dumps({"tasks": rows}, indent=1),
        private=True,
    )


def link(home: Path | str | None, task: dict, *, session_key: str = "",
         launch_id: str = "", now: float | None = None) -> dict:
    """Add ``session_key`` / ``launch_id`` to a task's links; the row is
    created on first sight and ``task`` freshens its title/url on every
    write. Answers the stored entry."""
    now = time.time() if now is None else now
    tid = task_id(task)
    with _lock:
        rows = _load(home)
        entry = rows.get(tid)
        if entry is None:
            entry = {"task": dict(task), "sessions": [], "launches": [],
                     "created_at": now, "last_active_at": now}
            rows[tid] = entry
        else:
            entry["task"] = {
                **entry["task"], **{k: v for k, v in task.items() if v}}
            entry["last_active_at"] = now
        if session_key and session_key not in entry["sessions"]:
            entry["sessions"].append(session_key)
            del entry["sessions"][:-MAX_LINKS]
        if launch_id and launch_id not in entry["launches"]:
            entry["launches"].append(launch_id)
            del entry["launches"][:-MAX_LINKS]
        _save(home, rows)
        return dict(entry)


def unlink(home: Path | str | None, session_key: str,
           tid: str | None = None) -> list[str]:
    """Drop ``session_key`` from one task's links, or from every task's
    when ``tid`` is None; answers the ids it left. A task with no links
    left is dropped — the store tracks links, not work items."""
    with _lock:
        rows = _load(home)
        ids = [tid] if tid else list(rows)
        left = []
        for i in ids:
            entry = rows.get(i)
            if not entry or session_key not in entry["sessions"]:
                continue
            entry["sessions"] = [
                s for s in entry["sessions"] if s != session_key]
            left.append(i)
            if not entry["sessions"] and not entry["launches"]:
                del rows[i]
        if left:
            _save(home, rows)
        return left


# ------------------------------------------------------------------ states

def _load_states(home: Path | str | None) -> dict[str, dict]:
    """``task_id → {state, pr}``, what each stored task's source last
    said; a corrupt or missing cache reads empty."""
    try:
        data = json.loads(states_file(home).read_text(encoding="utf-8"))
        rows = data.get("states") if isinstance(data, dict) else None
    except Exception:
        return {}
    if not isinstance(rows, dict):
        return {}
    out: dict[str, dict] = {}
    for tid, entry in rows.items():
        if not isinstance(entry, dict) \
                or not isinstance(entry.get("state"), str):
            continue
        pr = entry.get("pr")
        out[str(tid)] = {"state": entry["state"],
                         "pr": dict(pr) if isinstance(pr, dict) else None}
    return out


def _remember(home: Path | str | None, fresh: dict[str, dict]) -> None:
    """Persist the fresh reads that changed a stored task's last-known
    state. The comparison runs again under the lock, so a concurrent
    poll that already wrote the same state costs no second write; a
    write also drops tasks the store no longer holds, read under the
    same lock so a slow poll can't prune a task linked while it ran.
    Best-effort: a cache that can't be written only loses the memory,
    never the read.
    """
    with _lock:
        cached = _load_states(home)
        changed = {tid: entry for tid, entry in fresh.items()
                   if cached.get(tid) != entry}
        if not changed:
            return
        cached.update(changed)
        keep = set(_load(home))
        cached = {tid: e for tid, e in cached.items() if tid in keep}
        try:
            atomic_write_text(
                states_file(home), json.dumps({"states": cached}, indent=1),
                private=True)
        except OSError:
            pass


# ------------------------------------------------------------------ routes

def launch_linked(state, launch_id: str, origin) -> None:
    """Record a pending launch against its ``origin`` task.

    Called the moment the origin is stamped — before the worktree clone
    and harness start — so the task row reads ``agent_running`` through
    the whole prepare window, not just after adoption. Best-effort, as
    ``origin_adopted``.
    """
    try:
        task = parse_ref(origin)
    except ValueError:
        return
    try:
        link(state.home, task, launch_id=launch_id)
    except OSError:
        pass


def origin_adopted(state, launch_id: str, key: str) -> None:
    """Link the task a launch's ``origin`` names to its session.

    Called beside ``title_adopted`` when ``session.launched`` settles a
    launch: a launch without an origin (an ordinary
    ``POST /v1/sessions``) is left alone. The write is best-effort — a
    store failure must not lose the adoption.
    """
    view = state.launch_view(launch_id) or {}
    try:
        task = parse_ref(view.get("origin"))
    except ValueError:
        return
    try:
        link(state.home, task, session_key=key, launch_id=launch_id)
    except OSError:
        pass


def link_request(state, key: str, body) -> tuple[int, dict]:
    """``POST /v1/sessions/{key}/task {task_ref}`` → ``(status, payload)``."""
    if body is None:
        return 400, protocol.error("bad_body", "unreadable JSON body")
    ref = body.get("task_ref")
    if ref is None:
        # The ref itself is a fine body too: {source, kind, ref, ...}.
        ref = body if body.get("source") is not None else None
    if ref is None:
        return 422, protocol.error("bad_command", "task_ref is required")
    try:
        task = parse_ref(ref)
    except ValueError as exc:
        return 422, protocol.error("bad_command", str(exc))
    try:
        entry = link(state.home, task, session_key=key)
    except OSError:
        return 500, protocol.error(
            "store_failed", "could not save the link")
    return 200, {"ok": True, "task_id": task_id(task),
                 "sessions": entry["sessions"]}


def unlink_request(state, key: str, body) -> tuple[int, dict]:
    """``DELETE /v1/sessions/{key}/task`` → ``(status, payload)``; a
    ``task_ref`` in the body unlinks one task, none unlinks them all."""
    if body is None:
        return 400, protocol.error("bad_body", "unreadable JSON body")
    ref = body.get("task_ref")
    if ref is None and body.get("source") is not None:
        ref = body
    tid = None
    if ref is not None:
        try:
            tid = task_id(parse_ref(ref))
        except ValueError as exc:
            return 422, protocol.error("bad_command", str(exc))
    try:
        left = unlink(state.home, key, tid)
    except OSError:
        return 500, protocol.error(
            "store_failed", "could not save the change")
    return 200, {"ok": True, "unlinked": left}


def _refresh(args) -> bool:
    """A query dict's ``refresh=1``/``refresh=true``, as pulls.py's."""
    return str((args.get("refresh") or [""])[0]) in {"1", "true"}


def list_view(state, args) -> tuple[int, dict]:
    """``GET /v1/tasks``: the store over what the sources list.

    ``{tasks: [TaskRow], repos?: [per-repo failures], github, linear,
    notion, env_blocked?, machine, machine_id, fetched_at?}``. A ``TaskRow`` is
    the TaskRef fields plus ``id``, derived ``state``, ``sessions``,
    ``launches``, ``agent_running``, ``linked``, ``last_active_at``,
    ``created_at`` (linked tasks) and ``pr`` (the PR an issue's
    ``quiver/issue-<n>`` branch produced — open or merged — so the
    issue's ``pr_open``/``merged`` reads off one row) and
    ``state_source`` (``live``, ``cached`` or ``links``; see
    ``_last_known``).

    Each source answers its env block — ``github`` as ``gh``'s, plus a
    top-level ``env_blocked`` when it blocks; ``linear`` as its own
    block (``disabled`` with no section, ``auth-required``/
    ``upstream-error`` with the reason in ``error``). A blocked source
    leaves its stored tasks showing their last-known state, from the
    state cache. ``notion`` answers the same block as ``linear``; a
    Notion database that fails names itself in ``error`` while the
    other databases' rows still list.
    """
    refresh = _refresh(args)
    stored = _load(state.home)
    sources = {"github": GitHubTasks(state.home, refresh=refresh),
               "linear": linear_mod.LinearTasks(state.home,
                                                refresh=refresh),
               "notion": notion_mod.NotionTasks(state.home,
                                                refresh=refresh)}
    env = sources["github"].probe()
    # The configured-tracker sources, each answering its own env block.
    trackers = {"linear": (linear_mod, "Linear"),
                "notion": (notion_mod, "Notion")}
    envs = {name: sources[name].probe() for name in trackers}
    body = {"github": G.env_payload(env),
            **{name: mod.env_payload(envs[name])
               for name, (mod, _label) in trackers.items()},
            "machine": (state.machine or {}).get("name"),
            "machine_id": state.machine_id,
            "tasks": []}
    rows: dict[str, dict] = {}
    source_state: dict[str, str] = {}
    # A source's own word for the state, where it has one beyond the
    # ladder's: a Linear workflow state's name, a Notion status.
    status_name: dict[str, str] = {}
    # Tasks a source listed this round, and those whose PR a read
    # answered outright: a listed issue with no open PR on its branch
    # may still have a merged one the open list can't see.
    on_list: set[str] = set()
    pr_read: set[str] = set()
    fetched = []
    if env.status == "ok":
        listed = sources["github"].list()
        for item in listed["tasks"]:
            tid = task_id(item["task"])
            rows[tid] = {**item["task"], "id": tid,
                         "sessions": [], "launches": [],
                         "pr": item.get("pr"),
                         "updated_at": item.get("updated_at")}
            source_state[tid] = str(item.get("state") or "")
            on_list.add(tid)
            if item.get("pr") is not None:
                pr_read.add(tid)
        if listed.get("repos"):
            body["repos"] = listed["repos"]
        if listed.get("fetched_at") is not None:
            fetched.append(listed["fetched_at"])
    elif env.status in ("unavailable", "auth-required"):
        body["env_blocked"] = gh.env_error(env)

    for name, (_mod, label) in trackers.items():
        if envs[name].status != "ok":
            continue
        listed = sources[name].list()
        # A failed list answers no rows; a partly failed one (a Notion
        # database whose mapping doesn't fit) still answers the rest.
        for item in listed.get("tasks") or []:
            tid = task_id(item["task"])
            rows[tid] = {**item["task"], "id": tid,
                         "sessions": [], "launches": [],
                         "pr": item.get("pr"),
                         "updated_at": item.get("updated_at")}
            source_state[tid] = str(item.get("state") or "")
            if item.get("status_name"):
                status_name[tid] = str(item["status_name"])
            on_list.add(tid)
            if item.get("pr") is not None:
                pr_read.add(tid)
        if listed.get("status") == "ok" or listed.get("tasks"):
            if listed.get("fetched_at") is not None:
                fetched.append(listed["fetched_at"])
            if listed.get("truncated"):
                # More open items than one list reads: say so rather
                # than pass the first pages off as all of them.
                body[name] = {**body[name], "truncated": True}
        if listed.get("status") != "ok":
            # The probe passed but the fetch failed anyway: say so on
            # the source's block and keep the stored rows' last state.
            body[name] = {**body[name],
                          "status": str(listed.get("status")
                                        or "upstream-error"),
                          "error": listed.get("error")
                                   or f"{label} is unreachable"}

    for tid, entry in stored.items():
        task = entry["task"]
        row = rows.get(tid)
        if row is None:
            row = {**task, "id": tid, "sessions": [], "launches": []}
            rows[tid] = row
        else:
            # The fresh row keeps its fields; the stored ref fills gaps.
            for k, v in task.items():
                row.setdefault(k, v)
        row["sessions"] = list(entry["sessions"])
        row["launches"] = list(entry["launches"])
        row["created_at"] = entry["created_at"]

    readers = {}
    if env.status == "ok":
        readers["github"] = sources["github"].get
    for name in trackers:
        if envs[name].status == "ok":
            readers[name] = sources[name].get
    if readers:
        # A stored task's own read sees what the open list can't: the
        # merged PR behind a ``quiver/issue-<n>`` branch, a closed
        # issue. Bounded and fanned — the store keeps every link ever
        # made, so old history keeps its last-known state rather than
        # stall the whole answer behind serial source reads.
        recent = {
            task_id(e["task"]): e["task"]
            for e in sorted(
                (e for e in stored.values()
                 if e["task"].get("source") in readers),
                key=lambda e: e.get("last_active_at") or 0,
                reverse=True,
            )[:MAX_REFRESH]
        }
        ids = list(recent)
        for tid, fresh in zip(
                ids, G.fan(
                    ids,
                    lambda t: readers[recent[t]["source"]](recent[t]))):
            if not fresh:
                continue
            row = rows[tid]
            for k in ("title", "url"):
                if fresh.get(k):
                    row[k] = fresh[k]
            if fresh.get("state"):
                source_state[tid] = str(fresh["state"])
            if fresh.get("status_name"):
                status_name[tid] = str(fresh["status_name"])
            if fresh.get("pr"):
                row["pr"] = fresh["pr"]
            if "pr" in fresh:
                pr_read.add(tid)

    _last_known(state.home, stored, rows, source_state, on_list, pr_read)

    sessions_row = {str(r.get("key")): r for r in state.catalog.rows()}
    active = {k for k, r in sessions_row.items()
              if str(r.get("status") or "") == "active"}
    pending = _pending_launch_ids(state, stored)
    out = []
    for tid, row in rows.items():
        row.setdefault("pr", None)
        row.setdefault("updated_at", None)
        row.setdefault("state_source", "live")
        sessions = row.get("sessions") or []
        launches = row.get("launches") or []
        row["linked"] = bool(sessions or launches)
        row["agent_running"] = (
            any(k in active for k in sessions)
            or any(l in pending for l in launches))
        row["state"] = _derive(row.get("kind"), source_state.get(tid, ""),
                               row.get("pr"), row["linked"])
        row["status"] = _status(row, status_name.get(tid, ""))
        last = stored.get(tid, {}).get("last_active_at") or 0.0
        for k in sessions:
            last = max(last, _num(
                (sessions_row.get(k) or {}).get("last_active_at")))
        stamp = ghcli._parse_iso(row.get("updated_at") or "")
        if stamp:
            last = max(last, stamp)
        row["last_active_at"] = last
        out.append(row)
    out.sort(key=lambda r: r["last_active_at"], reverse=True)
    body["tasks"] = out
    if fetched:
        body["fetched_at"] = min(fetched)
    return 200, body


def _last_known(home, stored: dict[str, dict], rows: dict[str, dict],
                source_state: dict[str, str], on_list: set[str],
                pr_read: set[str]) -> None:
    """Fill each stored task a source didn't read this round from the
    state cache, and remember the fresh reads that changed one.

    A stored task is fresh when a source's list or ``get`` gave it a
    state; its ``{state, pr}`` then goes to the cache, written only if
    it differs from what the cache holds, so a quiet poll writes
    nothing. A fresh read that couldn't see the PR (the branch lookup
    failed, or only the open list read it) keeps the remembered one,
    except an open PR the open list no longer shows. One without a
    fresh read takes its cached state and PR and reads
    ``state_source: cached``; with no cache entry either, ``links``.
    """
    cached = _load_states(home)
    fresh: dict[str, dict] = {}
    for tid in stored:
        row = rows[tid]
        if source_state.get(tid):
            row["state_source"] = "live"
            last_pr = (cached.get(tid) or {}).get("pr")
            if row.get("pr") is None and tid not in pr_read and last_pr \
                    and not (tid in on_list
                             and last_pr.get("state") == "open"):
                row["pr"] = last_pr
            entry = {"state": source_state[tid], "pr": row.get("pr")}
            if cached.get(tid) != entry:
                fresh[tid] = entry
            continue
        last = cached.get(tid)
        if last is None:
            row["state_source"] = "links"
            continue
        source_state[tid] = last["state"]
        if row.get("pr") is None:
            row["pr"] = last["pr"]
        row["state_source"] = "cached"
    if fresh:
        _remember(home, fresh)


# The words ``status`` falls back to for a ladder state, where neither
# the pipeline nor the source has a better one.
_STATE_WORDS = {"open": "Open", "in_progress": "Started", "pr_open": "PR open",
                "merged": "Merged", "closed": "Closed"}


def _status(row: dict, source_name: str) -> str:
    """What a client shows as the task's status, in the source's words.

    ``state`` is the ladder, a sort and group key only (#875). GitHub's
    progress reads off the pipeline: an agent running, then its PR, then
    the merge; a linked session that has ended says nothing, so the
    issue reads Open again rather than "in progress" forever. Linear's
    workflow state and a Notion status pass through by name ("In Review").
    """
    state = row.get("state") or "open"
    if row.get("source") == "github":
        if state in ("merged", "closed", "pr_open"):
            return _STATE_WORDS[state]
        if row.get("agent_running"):
            return "Agent running"
        pr = row.get("pr") or {}
        if row.get("kind") == "pull" and pr.get("draft"):
            return "Draft"
        return "Open"
    if source_name:
        return source_name
    if row.get("agent_running") and state not in ("merged", "closed"):
        return "Agent running"
    return _STATE_WORDS.get(state, "Open")


def _derive(kind, source_state: str, pr, linked: bool) -> str:
    """``open → in_progress → pr_open → merged|closed``, as far as the
    linked objects let us infer."""
    source_state = str(source_state or "").lower()
    pr_state = str((pr or {}).get("state") or "").lower()
    if kind == "pull":
        if source_state in {"merged", "closed"}:
            return source_state
        return "in_progress" if linked else "open"
    if kind == "issue":
        if pr_state == "merged":
            return "merged"
        if source_state == "closed":
            return "closed"
        if pr_state == "open":
            return "pr_open"
        # A source can also declare in_progress itself — a Linear issue
        # in a "started" workflow state, with no agent linked at all.
        return "in_progress" if linked or source_state == "in_progress" \
            else "open"
    # card / page: a link or the source's own word is all there is to
    # infer from — a Notion row whose status reads done is closed.
    if source_state == "closed":
        return "closed"
    return "in_progress" if linked or source_state == "in_progress" \
        else "open"


def _pending_launch_ids(state, stored: dict[str, dict]) -> set[str]:
    """Linked launches that still answer pending — the agent is on its
    way (or just adopted, before the catalog shows the row)."""
    out = set()
    for entry in stored.values():
        for lid in entry.get("launches") or []:
            view = state.launch_view(lid)
            if view is not None and view.get("state") == "pending":
                out.add(lid)
    return out


def running_task_ids(state) -> set[str]:
    """Ids of tasks with an agent on them: any linked session ``active``
    in the catalog, or a linked launch still pending."""
    stored = _load(state.home)
    if not stored:
        return set()
    active = {str(r.get("key")) for r in state.catalog.rows()
              if str(r.get("status") or "") == "active"}
    pending = _pending_launch_ids(state, stored)
    return {tid for tid, entry in stored.items()
            if any(k in active for k in entry["sessions"])
            or any(l in pending for l in entry["launches"])}


# ------------------------------------------------------------------ sources

class TaskSource(Protocol):
    """One work-item provider. ``list()`` answers the source's open
    items as ``{task, state, updated_at, pr?}`` rows; ``get(ref)``
    refreshes one stored TaskRef's state — where a closed issue or a
    merged PR leaves the open list, a stored task still gets told."""

    def list(self) -> dict: ...
    def get(self, task: dict) -> dict | None: ...


def _pr_brief(pr: dict) -> dict:
    """The PR fields a task row carries: what an issue's branch produced."""
    return {"number": pr.get("number"),
            "title": pr.get("title") or "",
            "state": str(pr.get("state") or "").lower(),
            "draft": bool(pr.get("draft")),
            "url": pr.get("url") or ""}


class GitHubTasks:
    """``TaskSource`` over the hub's pinned repos, on the shared ``gh``
    cache the phone's ``/v1/pulls`` and the Repositories page read."""

    def __init__(self, home: Path | None, refresh: bool = False):
        self.home = home
        self.refresh = refresh

    def probe(self):
        return G.probe(self.home, refresh=self.refresh)

    def list(self) -> dict:
        """``{status, tasks, repos?, fetched_at?}``; a repo that failed
        keeps its status/error in ``repos`` and its rows are skipped,
        as ``/v1/pulls`` reports them."""
        hk = G.home_key(self.home)
        login = self._login()

        def entry(name: str):
            # The same per-repo entries /v1/pulls and /v1/issues read,
            # under their cache keys — a phone that just read either
            # pays nothing here, and a wrong-shape value can never sit
            # under a shared key.
            from quiver.remote import issues as issues_mod
            from quiver.remote import pulls as pulls_mod
            pulls_v, p_at = G.CACHE.get(
                ("pulls", hk, name),
                lambda: pulls_mod._repo_pulls(name, login),
                refresh=self.refresh,
                ok=lambda v: v.get("status") == "ok")
            issues_v, i_at = G.CACHE.get(
                ("issues", hk, name),
                lambda: issues_mod._repo_issues(name, login),
                refresh=self.refresh,
                ok=lambda v: v.get("status") == "ok")
            return _repo_tasks(name, issues_v, pulls_v), min(p_at, i_at)

        names = [p["name"] for p in G.pinned(self.home)]
        replies = G.fan(names, entry)
        tasks: list[dict] = []
        failed: list[dict] = []
        ats = []
        for value, at in replies:
            tasks.extend(value["tasks"])
            ats.append(at)
            if value.get("status") != "ok":
                failed.append(
                    {k: v for k, v in value.items() if k != "tasks"})
        out = {"status": "ok", "tasks": tasks}
        if failed:
            out["repos"] = failed
        if ats:
            out["fetched_at"] = min(ats)
        return out

    def _login(self) -> str:
        env = G.probe(self.home, refresh=self.refresh)
        return env.login if env.status == "ok" else ""

    def get(self, task: dict) -> dict | None:
        """Fresh ``{state, title, url, pr?}`` for a stored task: a
        closed issue or a merged PR the open list no longer shows.
        Cached like the lists; ``None`` when ``gh`` can't tell."""
        repo = str(task.get("repo") or "")
        if not repo:
            return None
        value, _at = G.CACHE.get(
            ("task", G.home_key(self.home), repo, task_id(task)),
            lambda: self._refresh(repo, task),
            refresh=self.refresh, ok=lambda v: v.get("status") == "ok")
        return value if value.get("status") == "ok" else None

    def _refresh(self, repo: str, task: dict) -> dict:
        if task.get("kind") == "pull":
            result = gh.run_json([
                "pr", "view", str(task.get("ref")), "--repo", repo,
                "--json", ghcli._PR_FIELDS])
            if result.status != "ok" or not isinstance(result.data, dict):
                return {"status": result.status,
                        "error": result.error or result.status}
            pr = ghcli._pr_row(result.data)
            return {"status": "ok", "state": pr["state"],
                    "title": pr["title"], "url": pr["url"],
                    "pr": _pr_brief(pr)}
        result = gh.run_json([
            "issue", "view", str(task.get("ref")), "--repo", repo,
            "--json", "number,title,state,url"])
        if result.status != "ok" or not isinstance(result.data, dict):
            return {"status": result.status,
                    "error": result.error or result.status}
        issue = result.data
        out = {"status": "ok",
               "state": str(issue.get("state") or "").lower(),
               "title": issue.get("title") or "",
               "url": issue.get("url") or ""}
        ok, pr = self._linked_pr(repo, issue.get("number"))
        if ok:
            # No ``pr`` key at all when the branch lookup failed, so the
            # caller keeps what it knew rather than read "no PR".
            out["pr"] = pr
        return out

    def _linked_pr(self, repo: str, number) -> tuple[bool, dict | None]:
        """``(answered, pr)``: the PR ``quiver/issue-<n>`` produced, in
        any state — the open list only sees open ones, and a resolved
        issue's PR is usually merged. ``answered`` is False when ``gh``
        failed, so "no PR" and "couldn't tell" stay apart.

        ``--head`` matches the branch name only (gh takes no
        ``owner:branch``), so forks' PRs from a same-named branch come
        back too: read a page and keep the first the repo owns, since a
        limit of 1 could return just a fork's."""
        result = gh.run_json([
            "pr", "list", "--repo", repo, "--head",
            manage.issue_branch(number), "--state", "all",
            "--limit", str(LINKED_PR_PAGE), "--json", ghcli._PR_FIELDS])
        if result.status != "ok" or not isinstance(result.data, list):
            return False, None
        for raw in result.data:
            if isinstance(raw, dict):
                pr = ghcli._pr_row(raw)
                if _same_repo_head(repo, pr):
                    return True, _pr_brief(pr)
        return True, None


def _same_repo_head(name: str, pr: dict) -> bool:
    """A fork's ``quiver/issue-<n>`` branch is not this repo's: only a
    head the repo itself owns counts. ``head_owner`` empty means gh
    couldn't say — match on the branch alone, as before."""
    head = str(pr.get("head_owner") or "").lower()
    return not head or head == name.split("/", 1)[0].lower()


def _repo_tasks(name: str, issues: dict, pulls: dict) -> dict:
    """One repo's open issues and PRs as source rows; each issue's open
    ``quiver/issue-<n>`` PR rides along so ``pr_open`` reads off it."""
    status = "ok"
    error = ""
    for reply in (issues, pulls):
        if reply.get("status") != "ok":
            status = str(reply.get("status") or "upstream-error")
            error = str(reply.get("error") or status)
            break
    tasks = []
    open_by_branch = {}
    for pr in pulls.get("pulls") or []:
        if isinstance(pr, dict) and pr.get("branch") \
                and _same_repo_head(name, pr):
            open_by_branch.setdefault(str(pr["branch"]), pr)
    for issue in issues.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        pr = open_by_branch.get(
            manage.issue_branch(issue.get("number") or 0))
        tasks.append({
            "task": {"source": "github", "kind": "issue", "repo": name,
                     "ref": str(issue.get("number")),
                     "title": issue.get("title") or "",
                     "url": issue.get("url") or ""},
            "state": str(issue.get("state") or "open").lower() or "open",
            "updated_at": issue.get("updated_at"),
            "pr": _pr_brief(pr) if pr else None,
        })
    for pr in pulls.get("pulls") or []:
        if not isinstance(pr, dict):
            continue
        tasks.append({
            "task": {"source": "github", "kind": "pull", "repo": name,
                     "ref": str(pr.get("number")),
                     "title": pr.get("title") or "",
                     "url": pr.get("url") or ""},
            "state": str(pr.get("state") or "open").lower() or "open",
            "updated_at": pr.get("updated_at"),
            # The pull's task carries the PR itself, as the docs say.
            "pr": _pr_brief(pr),
        })
    out = {"name": name, "status": status, "tasks": tasks}
    if error:
        out["error"] = error
    return out
