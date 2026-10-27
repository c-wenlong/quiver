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

``TaskSource`` is the seam other trackers plug into later: ``list()``
for what the source has open, ``get(ref)`` to refresh one stored task's
state. GitHub is the first adapter; Linear and Notion refs validate and
link today, and sit there until their adapters land.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Protocol

from quiver.github import cli as ghcli
from quiver.github import gh, manage, repos
from quiver.paths import atomic_write_text, remote_dir_for
from quiver.remote import protocol
from quiver.webapp.pages import _github as G

_BASENAME = "tasks.json"

SOURCES = ("github", "linear", "notion")
KINDS = ("issue", "pull", "card", "page")
# Kinds the GitHub adapter carries; "card" and "page" are valid
# task_refs, just not GitHub ones.
_GITHUB_KINDS = ("issue", "pull")
# The derived state every task row reports, in issue #844's order.
STATES = ("open", "in_progress", "pr_open", "merged", "closed")

# ``swe gh``'s list cap: open items per pinned repo in one answer.
LIST_LIMIT = 50
# Bounds on the file: a client that links in a loop, and a session that
# gets linked to (or a launch recorded against) everything it touches.
MAX_TASKS = 2000
MAX_LINKS = 100

# The hub is the only writer; this covers its concurrent handlers.
_lock = threading.Lock()


def tasks_file(home: Path | str | None = None) -> Path:
    return remote_dir_for(
        Path(home) if home is not None else None) / _BASENAME


# ------------------------------------------------------------------ refs

def task_id(task: dict) -> str:
    """A task's canonical id: ``source:kind:scope:ref``, lowercased.
    ``scope`` is the repo for github, the project anywhere else."""
    scope = task.get("repo") or task.get("project") or ""
    return f"{task.get('source')}:{task.get('kind')}:{scope}:{task.get('ref')}".lower()


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
    title = body.get("title")
    url = body.get("url")
    if title is not None and not isinstance(title, str):
        raise ValueError("task_ref.title must be a string")
    if url is not None and not isinstance(url, str):
        raise ValueError("task_ref.url must be a string")
    task = {"source": source, "kind": kind, "ref": ref,
            "title": title or "", "url": url or ""}
    if source == "github":
        if kind not in _GITHUB_KINDS:
            raise ValueError("a github task_ref.kind is issue or pull")
        repo = repos.normalize_repo_input(body.get("repo") or "")
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
        if project:
            task["project"] = project
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
        sessions = [str(s) for s in (entry.get("sessions") or [])
                    if isinstance(s, str)]
        launches = [str(s) for s in (entry.get("launches") or [])
                    if isinstance(s, str)]
        out[str(tid)] = {
            "task": dict(entry["task"]),
            "sessions": sessions,
            "launches": launches,
            "created_at": _num(entry.get("created_at")),
            "last_active_at": _num(entry.get("last_active_at")),
        }
    return out


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


# ------------------------------------------------------------------ routes

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

    ``{tasks: [TaskRow], repos?: [per-repo failures], github,
    env_blocked?, machine, machine_id, fetched_at?}``. A ``TaskRow`` is
    the TaskRef fields plus ``id``, derived ``state``, ``sessions``,
    ``launches``, ``agent_running``, ``linked``, ``last_active_at``,
    ``created_at`` (linked tasks) and ``pr`` (the PR an issue's
    ``quiver/issue-<n>`` branch produced — open or merged — so the
    issue's ``pr_open``/``merged`` reads off one row).
    """
    refresh = _refresh(args)
    stored = _load(state.home)
    source = GitHubTasks(state.home, refresh=refresh)
    env = source.probe()
    body = {"github": G.env_payload(env),
            "machine": (state.machine or {}).get("name"),
            "machine_id": state.machine_id,
            "tasks": []}
    rows: dict[str, dict] = {}
    source_state: dict[str, str] = {}
    fetched = []
    if env.status == "ok":
        listed = source.list()
        for item in listed["tasks"]:
            tid = task_id(item["task"])
            rows[tid] = {**item["task"], "id": tid,
                         "sessions": [], "launches": [],
                         "pr": item.get("pr"),
                         "updated_at": item.get("updated_at")}
            source_state[tid] = str(item.get("state") or "")
        if listed.get("repos"):
            body["repos"] = listed["repos"]
        if listed.get("fetched_at") is not None:
            fetched.append(listed["fetched_at"])
    elif env.status in ("unavailable", "auth-required"):
        body["env_blocked"] = gh.env_error(env)

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
        # A stored task's own read sees what the open list can't: the
        # merged PR behind a ``quiver/issue-<n>`` branch, a closed issue.
        if env.status == "ok" and task.get("source") == "github":
            fresh = source.get(task)
            if fresh:
                for k in ("title", "url"):
                    if fresh.get(k):
                        row[k] = fresh[k]
                if fresh.get("state"):
                    source_state[tid] = str(fresh["state"])
                if fresh.get("pr"):
                    row["pr"] = fresh["pr"]
        row["sessions"] = list(entry["sessions"])
        row["launches"] = list(entry["launches"])
        row["created_at"] = entry["created_at"]

    sessions_row = {str(r.get("key")): r for r in state.catalog.rows()}
    active = {k for k, r in sessions_row.items()
              if str(r.get("status") or "") == "active"}
    pending = _pending_launch_ids(state, stored)
    out = []
    for tid, row in rows.items():
        row.setdefault("pr", None)
        row.setdefault("updated_at", None)
        sessions = row.get("sessions") or []
        launches = row.get("launches") or []
        row["linked"] = bool(sessions or launches)
        row["agent_running"] = (
            any(k in active for k in sessions)
            or any(l in pending for l in launches))
        row["state"] = _derive(row.get("kind"), source_state.get(tid, ""),
                               row.get("pr"), row["linked"])
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
        return "in_progress" if linked else "open"
    # card / page: a link is all there is to infer from, for now.
    return "in_progress" if linked else "open"


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
        return {"status": "ok",
                "state": str(issue.get("state") or "").lower(),
                "title": issue.get("title") or "",
                "url": issue.get("url") or "",
                "pr": self._linked_pr(repo, issue.get("number"))}

    def _linked_pr(self, repo: str, number) -> dict | None:
        """The PR ``quiver/issue-<n>`` produced, in any state — the
        open list only sees open ones, and a resolved issue's PR is
        usually merged."""
        result = gh.run_json([
            "pr", "list", "--repo", repo, "--head",
            manage.issue_branch(number), "--state", "all",
            "--limit", "1", "--json", ghcli._PR_FIELDS])
        if result.status != "ok" or not isinstance(result.data, list):
            return None
        for raw in result.data:
            if isinstance(raw, dict):
                return _pr_brief(ghcli._pr_row(raw))
        return None


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
        if isinstance(pr, dict) and pr.get("branch"):
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
            "pr": None,
        })
    out = {"name": name, "status": status, "tasks": tasks}
    if error:
        out["error"] = error
    return out
