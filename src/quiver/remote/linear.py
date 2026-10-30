"""The Linear task source: the user's Linear issues as task rows.

``tasks.py``'s ``TaskSource`` over Linear's GraphQL endpoint — the
``linear`` counterpart of ``GitHubTasks`` — so "my context" covers
work assigned in Linear without opening the Linear app.

Auth is a personal API key the config names by reference, never in
plaintext. ``~/.quiver/config/config.json`` carries a ``linear``
section::

    "linear": {"key_ref": "${LINEAR_API_KEY}", "teams": ["QUI"]}

and ``mcp/secrets.py``'s ``${NAME}`` store
(``~/.quiver/secrets/mcp/.api_keys``, filled by ``swe mcp secret set``)
holds the value. The section is opt-in: absent, the source answers
``disabled`` and ``/v1/tasks`` lists no Linear rows; a section whose
key won't resolve or gets refused answers ``auth-required`` — the
``env_blocked``-style state a missing ``gh`` produces — and stored
Linear tasks keep their last-known state. ``key_ref`` defaults to
``${LINEAR_API_KEY}`` and ``teams`` to none, so a bare ``"linear": {}``
reads the issues assigned to the key's owner; ``teams`` names team
keys whose open issues join that list.

Reads only. Launching writes the prompt but never moves an issue's
workflow state — states are per-team in Linear, so ``issueUpdate``
write-back is a later step, not this one.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quiver import __version__, paths
from quiver.mcp import secrets as mcp_secrets
from quiver.github import shared as G

GRAPHQL_URL = "https://api.linear.app/graphql"
# Linear personal API keys travel bare in the Authorization header —
# no Bearer scheme, no OAuth exchange.
TIMEOUT = 15
# Issues per page: the same cap `swe gh` puts on a repo's open work.
LIST_LIMIT = 50
# Pages read per list, newest-updated first, and the time the later
# pages may take: the phone gives up on /v1/tasks after 15 s. Past
# either the list says ``truncated`` instead of passing for everything
# that's open.
LIST_PAGES = 5
LIST_BUDGET_S = 8.0
DEFAULT_KEY_REF = "${LINEAR_API_KEY}"

# A Linear workflow state's ``type`` → the task row's source state.
# Teams name their states freely, so the stable type is what's mapped;
# ``_derive`` in tasks.py then applies it. Anything not listed
# ("triage", "backlog", "unstarted") reads as "open".
_STATE_MAP = {
    "completed": "closed",
    "canceled": "closed",
    "cancelled": "closed",
    "duplicate": "closed",
    "started": "in_progress",
}
# State types that keep an issue off the open-work list.
_TERMINAL = ("completed", "canceled", "cancelled", "duplicate")

_VIEWER_QUERY = "query { viewer { displayName email } }"

_LIST_QUERY = """\
query QuiverTasks($filter: IssueFilter, $first: Int!, $after: String) {
  issues(first: $first, after: $after, filter: $filter, orderBy: updatedAt) {
    nodes {
      identifier title url updatedAt
      state { name type }
      team { key }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# ``issue(id:)`` takes the human identifier ("QUI-123") as well as the
# UUID, so a stored ref looks itself up without a search.
_GET_QUERY = """\
query QuiverTask($id: String!) {
  issue(id: $id) {
    identifier title url updatedAt description
    state { name type }
    team { key }
  }
}
"""


def load_section(home: Path | str | None) -> dict | None:
    """The ``linear`` section of ``config.json``; ``None`` when absent.

    The section is opt-in: a missing ``linear`` key (or a file that
    won't read) answers ``None`` — "not configured" — while a present
    but empty one answers ``{}``, meaning "on, with defaults". Neither
    may take ``/v1/tasks`` down.
    """
    root = Path(home) if home is not None else None
    try:
        data = json.loads(
            paths.config_file_for(root).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or "linear" not in data:
        return None
    section = data["linear"]
    # A non-object section ("linear": 5) is read as an empty one: on
    # with defaults, so the error names the key that's actually wrong.
    return dict(section) if isinstance(section, dict) else {}


# ------------------------------------------------------------------ env

@dataclass
class LinearEnv:
    """What the hub's Linear setup looks like, probed once per request.

    ``configured`` mirrors ``GhEnv.installed``: no ``linear`` section
    means the source is simply off, apart from a key that won't resolve
    (``auth-required``) or an endpoint that won't answer
    (``upstream-error``).
    """

    configured: bool = False
    authenticated: bool = False
    login: str = ""
    upstream: bool = False
    error: str = ""

    @property
    def status(self) -> str:
        if not self.configured:
            return "disabled"
        if self.upstream:
            return "upstream-error"
        if not self.authenticated:
            return "auth-required"
        return "ok"


def env_payload(env: LinearEnv) -> dict:
    """The ``linear`` block ``/v1/tasks`` always answers, shaped like
    the ``github`` block (``ghcli._env_payload``)."""
    return {"status": env.status, "login": env.login or None,
            "error": env.error or None}


# ------------------------------------------------------------------ transport

@dataclass
class GraphQLResult:
    """One GraphQL reply, typed like ``gh.GhResult`` so a bad key never
    reads as an empty list."""

    status: str          # ok | auth-required | upstream-error
    data: Any = None
    error: str = ""


def _graphql(api_key: str, query: str,
             variables: dict | None = None) -> GraphQLResult:
    """POST one GraphQL operation; never raises."""
    body = json.dumps({"query": query,
                       "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL_URL, data=body, method="POST",
        headers={"Authorization": api_key,
                 "Content-Type": "application/json",
                 "User-Agent": f"quiver/{__version__}"})
    # The agent's urlopen: a certificate failure retries against the OS
    # CA bundle (python.org builds on macOS ship none), still verified.
    from quiver.remote.agent import _urlopen
    try:
        with _urlopen(req, TIMEOUT) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        if code in (401, 403):
            return GraphQLResult(
                "auth-required",
                error=f"Linear refused the API key (HTTP {code})")
        return GraphQLResult(
            "upstream-error", error=f"Linear answered HTTP {code}")
    except Exception as exc:
        return GraphQLResult(
            "upstream-error", error=f"Linear is unreachable ({exc})")
    try:
        payload = json.loads(text)
    except ValueError:
        return GraphQLResult(
            "upstream-error", error="Linear answered non-JSON")
    if not isinstance(payload, dict):
        return GraphQLResult(
            "upstream-error", error="Linear answered a non-object body")
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        # Linear answers 200 with an ``errors`` list for a query it
        # couldn't run, auth failures included — both read here.
        message = "; ".join(
            str(e.get("message")) for e in errors[:3]
            if isinstance(e, dict) and e.get("message")) or "GraphQL error"
        status = ("auth-required" if "auth" in message.lower()
                  else "upstream-error")
        return GraphQLResult(status, error=message)
    return GraphQLResult("ok", data=payload.get("data") or {})


# ------------------------------------------------------------------ source

class LinearTasks:
    """``TaskSource`` over Linear's GraphQL, behind the same minute
    cache the ``gh`` reads share (``github/shared.py``)."""

    def __init__(self, home: Path | str | None, refresh: bool = False):
        self.home = Path(home) if home is not None else None
        self.refresh = refresh

    def _api_key(self, cfg: dict) -> tuple[str | None, str]:
        """The resolved key, or ``(None, why)`` — the why reads like
        ``gh.env_error``'s, since it lands in the payload the same way.

        A literal is refused outright: ``config.json`` stays
        credential-free (``swe config show`` prints it, ``git add`` can
        commit it), so the key must come through a ``${NAME}``
        reference into the secrets store.
        """
        ref = cfg.get("key_ref")
        if ref is None:
            ref = DEFAULT_KEY_REF
        if not isinstance(ref, str):
            return None, "linear.key_ref must be a ${NAME} reference"
        if not mcp_secrets.referenced_names(ref):
            return None, (
                "linear.key_ref must be a ${NAME} reference into the "
                "secrets store, not the key itself — config.json is "
                "credential-free")
        missing = mcp_secrets.unresolved_names(ref, home=self.home)
        if missing:
            names = " ".join("${" + n + "}" for n in missing)
            return None, (
                f"the Linear key needs {names}, which isn't in the "
                f"secrets store: run `swe mcp secret set {missing[0]}`")
        key = mcp_secrets.resolve(ref, home=self.home)
        return str(key).strip(), ""

    # -------------------------------------------------------------- env

    def probe(self) -> LinearEnv:
        """Section, key and a ``viewer`` call, cached like the reads it
        classifies, as ``G.probe`` caches ``gh auth status``."""
        env, _at = G.CACHE.get(
            ("linear-env", G.home_key(self.home)), self._probe,
            refresh=self.refresh, ok=lambda e: e.status == "ok")
        return env

    def _probe(self) -> LinearEnv:
        cfg = load_section(self.home)
        if cfg is None:
            return LinearEnv()
        env = LinearEnv(configured=True)
        key, error = self._api_key(cfg)
        if key is None:
            env.error = error
            return env          # auth-required
        result = _graphql(key, _VIEWER_QUERY)
        if result.status == "ok":
            viewer = result.data.get("viewer") \
                if isinstance(result.data, dict) else None
            env.authenticated = True
            if isinstance(viewer, dict):
                env.login = str(
                    viewer.get("displayName") or viewer.get("email")
                    or "")
        elif result.status == "auth-required":
            env.error = result.error or "Linear refused the API key"
        else:
            env.upstream = True
            env.error = result.error or "Linear is unreachable"
        return env

    # -------------------------------------------------------------- list

    def list(self) -> dict:
        """``{status, tasks, truncated?, error?, fetched_at}`` — the
        assigned and configured-team issues that aren't in a terminal
        state, up to ``LIST_PAGES`` pages; ``truncated`` when more
        remain."""
        value, at = G.CACHE.get(
            ("linear-issues", G.home_key(self.home)), self._list,
            refresh=self.refresh, ok=lambda v: v.get("status") == "ok")
        return {**value, "fetched_at": at}

    def _list(self) -> dict:
        cfg = load_section(self.home) or {}
        teams, bad = _teams(cfg.get("teams"))
        if bad:
            # A scalar or mixed ``teams`` (``swe config set`` takes one)
            # must not crash the route — say what's wrong, as a missing
            # key does, instead of iterating characters or raising.
            return {"status": "bad-config", "tasks": [],
                    "error": bad}
        key, error = self._api_key(cfg)
        if key is None:
            return {"status": "auth-required", "tasks": [],
                    "error": error}
        tasks: list[dict] = []
        after = None
        deadline = time.monotonic() + LIST_BUDGET_S
        for page in range(LIST_PAGES):
            if page and time.monotonic() > deadline:
                break  # a slow Linear: answer what came back
            result = _graphql(key, _LIST_QUERY,
                              {"filter": _list_filter(teams),
                               "first": LIST_LIMIT, "after": after})
            if result.status != "ok":
                return {"status": result.status, "tasks": [],
                        "error": result.error or result.status}
            issues = result.data.get("issues") \
                if isinstance(result.data, dict) else None
            issues = issues if isinstance(issues, dict) else {}
            tasks.extend(
                {"task": _ref(node), "state": _task_state(node),
                 "status_name": _status_name(node),
                 "updated_at": node.get("updatedAt")}
                for node in issues.get("nodes") or []
                if isinstance(node, dict))
            info = issues.get("pageInfo")
            info = info if isinstance(info, dict) else {}
            after = info.get("endCursor")
            if not (info.get("hasNextPage") and isinstance(after, str)):
                return {"status": "ok", "tasks": tasks}
        return {"status": "ok", "tasks": tasks, "truncated": True}

    # -------------------------------------------------------------- get

    def get(self, task: dict) -> dict | None:
        """Fresh ``{state, title, url, description}`` for a stored ref,
        cached like the list; ``None`` when Linear can't tell. The
        description is the markdown body a resolve prompt quotes, as
        the GitHub adapter's issue detail carries ``body``."""
        ref = str(task.get("ref") or "").strip()
        if not ref:
            return None
        value, _at = G.CACHE.get(
            ("linear-task", G.home_key(self.home), ref.lower()),
            lambda: self._refresh(ref),
            refresh=self.refresh, ok=lambda v: v.get("status") == "ok")
        return value if value.get("status") == "ok" else None

    def _refresh(self, ref: str) -> dict:
        cfg = load_section(self.home) or {}
        key, error = self._api_key(cfg)
        if key is None:
            return {"status": "auth-required", "error": error}
        result = _graphql(key, _GET_QUERY, {"id": ref})
        if result.status != "ok":
            return {"status": result.status,
                    "error": result.error or result.status}
        issue = result.data.get("issue") \
            if isinstance(result.data, dict) else None
        if not isinstance(issue, dict):
            return {"status": "not-found",
                    "error": f"Linear has no issue {ref}"}
        return {"status": "ok", "state": _task_state(issue),
                "status_name": _status_name(issue),
                "title": issue.get("title") or "",
                "url": issue.get("url") or "",
                "description": issue.get("description") or "",
                "updated_at": issue.get("updatedAt")}


# ------------------------------------------------------------------ rows

def _ref(node: dict) -> dict:
    """The TaskRef an issue node maps to; ``project`` is its team key,
    so the task id reads ``linear:issue:qui:qui-123``."""
    ref = {"source": "linear", "kind": "issue",
           "ref": str(node.get("identifier") or ""),
           "title": node.get("title") or "",
           "url": node.get("url") or ""}
    team = node.get("team")
    if isinstance(team, dict) and team.get("key"):
        ref["project"] = str(team["key"])
    return ref


def _task_state(node: dict) -> str:
    """The workflow state's ``type`` as a source state ``_derive`` reads."""
    state = node.get("state")
    stype = str(state.get("type") or "").lower() \
        if isinstance(state, dict) else ""
    return _STATE_MAP.get(stype, "open")


def _status_name(node: dict) -> str:
    """The workflow state's own name ("In Review"), what Linear shows."""
    state = node.get("state")
    name = state.get("name") if isinstance(state, dict) else None
    return name.strip() if isinstance(name, str) else ""


def _list_filter(teams: list[str]) -> dict:
    """Assigned-to-me or a configured team's issues, minus terminal
    states — Linear's reading of "open work"."""
    open_only = {"state": {"type": {"nin": list(_TERMINAL)}}}
    mine = {"assignee": {"isMe": {"eq": True}}}
    if teams:
        return {**open_only,
                "or": [mine, {"team": {"key": {"in": list(teams)}}}]}
    return {**open_only, **mine}


def _teams(raw) -> tuple[list[str], str]:
    """``(team keys, error)`` — the ``teams`` setting normalized.

    Anything but a list of strings is the error, since a bare ``"QUI"``
    would otherwise iterate into ``["Q", "U", "I"]`` and a number would
    raise mid-route. Empty entries drop out.
    """
    if raw is None:
        return [], ""
    if not isinstance(raw, list) \
            or not all(isinstance(t, str) for t in raw):
        return [], ('linear.teams must be a list of team keys, '
                    'e.g. ["QUI"]')
    return [t.strip() for t in raw if t.strip()], ""
