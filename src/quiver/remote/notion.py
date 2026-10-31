"""The Notion task source: rows of configured Notion databases as task
rows.

``tasks.py``'s ``TaskSource`` over Notion's REST API — the ``notion``
counterpart of ``LinearTasks`` — so a roadmap table or project tracker
shows up beside GitHub issues on the phone.

Notion schemas are whatever the workspace made them, so there is no
fixed "issue" shape to read: the config names each database and which
of its properties carry the title, the status, the owner and the url.
``~/.quiver/config/config.json`` carries a ``notion`` section::

    "notion": {
      "token_ref": "${NOTION_TOKEN}",
      "user": "you@example.com",
      "databases": [
        {"id": "<database id or URL>", "name": "Roadmap",
         "properties": {"status": "Status", "owner": "Owner"}}
      ]
    }

Auth is an internal integration token (``ntn_…``) the config names by
reference, as Linear's key: ``mcp/secrets.py``'s ``${NAME}`` store
holds the value and a literal is refused. ``user`` is the person whose
rows list — a Notion user id, or the email they sign in with (looked up
through ``/v1/users``, which needs the integration's "Read user
information including email addresses" capability). Each database
lists the rows whose ``owner`` property (a person, created-by or
last-edited-by property) holds that user. ``properties.status`` (a
status, select or checkbox property) and ``properties.owner`` are
required; ``title`` defaults to the database's title property, ``url``
to the page's own Notion link. A ``properties`` object beside
``databases`` sets defaults every database inherits.

Sections that won't read answer ``bad-config`` for the whole source; a
database whose config or schema doesn't fit fails on its own, naming
the property, while the others still list. Reads only: write-back of
the status property is a later step.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quiver import __version__, paths
from quiver.mcp import secrets as mcp_secrets
from quiver.github import shared as G

API_URL = "https://api.notion.com/v1"
# One pinned API version for every call. 2022-06-28 is the newest one
# that still serves ``POST /v1/databases/<id>/query``: 2025-09-03 moves
# queries to data sources (``/v1/data_sources/<id>/query``), which a
# later step can adopt in one place, here.
NOTION_VERSION = "2022-06-28"
TIMEOUT = 15
DEFAULT_TOKEN_REF = "${NOTION_TOKEN}"
# Open rows one database answers: the same cap Linear and ``swe gh`` use.
LIST_LIMIT = 50
# Rows per query page (Notion's maximum) and pages per database, so a
# database full of finished rows costs at most five calls a minute.
PAGE_SIZE = 100
MAX_PAGES = 5
# Pages of ``/v1/users`` scanned to turn an email into a user id.
MAX_USER_PAGES = 5
# A page body's budget: child-list calls, nesting depth and characters.
MAX_BLOCK_CALLS = 12
MAX_DEPTH = 3
MAX_DESCRIPTION = 20000
# 429 handling: Notion averages three requests a second per integration
# and says how long to back off in Retry-After. Two waits, each capped,
# so a hub request never stalls long behind it.
MAX_RETRIES = 2
RETRY_CAP = 5.0

# Tests replace this so a 429 costs no wall time.
_sleep = time.sleep

STATUS_TYPES = ("status", "select", "checkbox")
OWNER_TYPES = ("people", "created_by", "last_edited_by")
URL_TYPES = ("url", "rich_text")

# Status words → the ladder, for select and status options (and status
# groups) whose names are free text. Open words are checked first so
# "Not started" never reads as started.
_OPEN_WORDS = ("not started", "to do", "to-do", "todo", "backlog",
               "planned", "next up", "idea")
_CLOSED_WORDS = ("done", "complete", "completed", "closed", "shipped",
                 "released", "resolved", "merged", "cancelled",
                 "canceled", "won't do", "wont do", "archived",
                 "abandoned", "dropped")
_STARTED_WORDS = ("in progress", "progress", "doing", "started",
                  "active", "in review", "review", "wip", "building",
                  "testing")

_UUID = re.compile(
    r"(?<![0-9a-f])([0-9a-f]{8})-?([0-9a-f]{4})-?([0-9a-f]{4})-?"
    r"([0-9a-f]{4})-?([0-9a-f]{12})(?![0-9a-f])", re.IGNORECASE)


def load_section(home: Path | str | None) -> dict | None:
    """The ``notion`` section of ``config.json``; ``None`` when absent,
    as ``linear.load_section`` reads its own."""
    root = Path(home) if home is not None else None
    try:
        data = json.loads(
            paths.config_file_for(root).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or "notion" not in data:
        return None
    section = data["notion"]
    return dict(section) if isinstance(section, dict) else {}


def normalize_id(raw) -> str | None:
    """A Notion id in its dashed form, from a bare id, a dashed one or
    a ``notion.so`` URL; ``None`` when there is none. A URL's query
    (``?v=<view id>``) is ignored, so a copied view link still names
    its database."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if "://" in text:
        try:
            text = urllib.parse.urlsplit(text).path
        except ValueError:
            return None
    found = _UUID.findall(text)
    if not found:
        return None
    return "-".join(found[-1]).lower()


# ------------------------------------------------------------------ config

@dataclass
class Database:
    """One configured database: its id, display name and mapping."""

    id: str
    name: str
    properties: dict
    error: str = ""


def _settings(cfg: dict) -> tuple[str, list[Database], str]:
    """``(user, databases, error)`` — the section normalized.

    ``error`` is a section-level fault (no ``user``, no ``databases``):
    it blocks the source. A database's own fault lands on that
    ``Database.error`` instead, so one bad entry doesn't hide the rest.
    """
    user = cfg.get("user")
    if not isinstance(user, str) or not user.strip():
        return "", [], ("notion.user must be your Notion user id or the "
                        "email you sign in to Notion with")
    user = user.strip()
    if "@" not in user and normalize_id(user) is None:
        return "", [], (f"notion.user {user!r} is neither an email nor a "
                         "Notion user id")
    raw = cfg.get("databases")
    if not isinstance(raw, list) or not raw:
        return user, [], ('notion.databases must list at least one '
                          'database, e.g. [{"id": "<database id>", '
                          '"properties": {"status": "Status", '
                          '"owner": "Owner"}}]')
    shared = cfg.get("properties")
    if shared is not None and not _str_map(shared):
        return user, [], ("notion.properties must map title, status, "
                          "owner and url to property names")
    out = []
    for i, entry in enumerate(raw):
        where = f"notion.databases[{i}]"
        if isinstance(entry, str):
            entry = {"id": entry}
        if not isinstance(entry, dict):
            out.append(Database("", where, {},
                                f"{where} must be an object with an id"))
            continue
        db_id = normalize_id(entry.get("id"))
        name = entry.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if db_id is None:
            out.append(Database("", name or where, {},
                                f"{where}.id must be a Notion database "
                                "id or URL"))
            continue
        own = entry.get("properties")
        if own is not None and not _str_map(own):
            out.append(Database(db_id, name, {},
                                f"{where}.properties must map title, "
                                "status, owner and url to property "
                                "names"))
            continue
        props = {**(shared or {}), **(own or {})}
        props = {k: v.strip() for k, v in props.items() if v.strip()}
        error = ""
        for need, kinds in (("status", "status, select or checkbox"),
                            ("owner", "person")):
            if need not in props:
                error = (f"{where}.properties.{need} is required: the "
                         f"name of the database's {kinds} property")
                break
        out.append(Database(db_id, name, props, error))
    return user, out, ""


def _str_map(value) -> bool:
    return isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items())


# ------------------------------------------------------------------ env

@dataclass
class NotionEnv:
    """The hub's Notion setup, probed once per request: section, token,
    the integration's own user and the configured person."""

    configured: bool = False
    authenticated: bool = False
    login: str = ""
    user_id: str = ""
    upstream: bool = False
    bad_config: bool = False
    error: str = ""

    @property
    def status(self) -> str:
        if not self.configured:
            return "disabled"
        if self.upstream:
            return "upstream-error"
        if not self.authenticated:
            return "auth-required"
        if self.bad_config:
            return "bad-config"
        return "ok"


def env_payload(env: NotionEnv) -> dict:
    """The ``notion`` block ``/v1/tasks`` always answers, shaped like
    the ``linear`` one."""
    return {"status": env.status, "login": env.login or None,
            "error": env.error or None}


# ------------------------------------------------------------------ transport

@dataclass
class NotionResult:
    """One REST reply; ``code`` is the HTTP status of a refusal, so a
    caller can tell "not shared with the integration" (404) apart."""

    status: str          # ok | auth-required | upstream-error
    data: Any = None
    error: str = ""
    code: int = 0


def _request(token: str, method: str, path: str,
             body: dict | None = None) -> NotionResult:
    """One call to ``api.notion.com/v1``; never raises. A 429 waits out
    its Retry-After (capped) up to ``MAX_RETRIES`` times."""
    # The agent's urlopen, as linear.py's: a certificate failure retries
    # against the OS CA bundle. Imported here to keep this module light.
    from quiver.remote.agent import _urlopen

    data = json.dumps(body).encode("utf-8") if body is not None else None
    for attempt in range(MAX_RETRIES + 1):
        req = urllib.request.Request(
            API_URL + path, data=data, method=method,
            headers={"Authorization": f"Bearer {token}",
                     "Notion-Version": NOTION_VERSION,
                     "Content-Type": "application/json",
                     "User-Agent": f"quiver/{__version__}"})
        try:
            with _urlopen(req, TIMEOUT) as resp:
                text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            code = exc.code
            wait = _retry_after(exc.headers)
            message = _error_message(exc)
            exc.close()
            if code == 429:
                if attempt < MAX_RETRIES:
                    _sleep(wait)
                    continue
                return NotionResult(
                    "upstream-error", code=429,
                    error="Notion is rate-limiting the hub (HTTP 429); "
                          "it clears within a minute")
            if code == 401:
                return NotionResult(
                    "auth-required", code=code,
                    error="Notion refused the integration token "
                          "(HTTP 401)")
            detail = f": {message}" if message else ""
            return NotionResult(
                "upstream-error", code=code,
                error=f"Notion answered HTTP {code}{detail}")
        except Exception as exc:
            return NotionResult(
                "upstream-error", error=f"Notion is unreachable ({exc})")
        try:
            payload = json.loads(text)
        except ValueError:
            return NotionResult(
                "upstream-error", error="Notion answered non-JSON")
        if not isinstance(payload, dict):
            return NotionResult(
                "upstream-error", error="Notion answered a non-object body")
        return NotionResult("ok", data=payload)
    return NotionResult("upstream-error", error="Notion did not answer")


def _retry_after(headers) -> float:
    """Seconds a 429 asks for, clamped to ``[0.5, RETRY_CAP]``."""
    try:
        value = float((headers or {}).get("Retry-After") or 1)
    except (TypeError, ValueError, AttributeError):
        value = 1.0
    return max(0.5, min(value, RETRY_CAP))


def _error_message(exc: urllib.error.HTTPError) -> str:
    """Notion's own ``message`` from an error body, when it sent one."""
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except Exception:
        return ""
    message = payload.get("message") if isinstance(payload, dict) else None
    return message.strip()[:300] if isinstance(message, str) else ""


# ------------------------------------------------------------------ source

class NotionTasks:
    """``TaskSource`` over Notion's REST API, behind the same minute
    cache the ``gh`` and Linear reads share."""

    def __init__(self, home: Path | str | None, refresh: bool = False):
        self.home = Path(home) if home is not None else None
        self.refresh = refresh

    def _cached(self, key: tuple, fetch, ok=None):
        value, at = G.CACHE.get(
            (key[0], G.home_key(self.home), *key[1:]), fetch,
            refresh=self.refresh,
            ok=ok or (lambda v: v.get("status") == "ok"))
        return value, at

    def _token(self, cfg: dict) -> tuple[str | None, str]:
        """The resolved token, or ``(None, why)``. A literal is refused:
        ``config.json`` stays credential-free."""
        ref = cfg.get("token_ref")
        if ref is None:
            ref = DEFAULT_TOKEN_REF
        if not isinstance(ref, str):
            return None, "notion.token_ref must be a ${NAME} reference"
        if not mcp_secrets.referenced_names(ref):
            return None, (
                "notion.token_ref must be a ${NAME} reference into the "
                "secrets store, not the token itself — config.json is "
                "credential-free")
        missing = mcp_secrets.unresolved_names(ref, home=self.home)
        if missing:
            names = " ".join("${" + n + "}" for n in missing)
            return None, (
                f"the Notion token needs {names}, which isn't in the "
                f"secrets store: run `swe mcp secret set {missing[0]}`")
        token = mcp_secrets.resolve(ref, home=self.home)
        return str(token).strip(), ""

    # -------------------------------------------------------------- env

    def probe(self) -> NotionEnv:
        """Section, token, ``/v1/users/me`` and the configured user,
        cached like the reads it classifies."""
        env, _at = G.CACHE.get(
            ("notion-env", G.home_key(self.home)), self._probe,
            refresh=self.refresh, ok=lambda e: e.status == "ok")
        return env

    def _probe(self) -> NotionEnv:
        cfg = load_section(self.home)
        if cfg is None:
            return NotionEnv()
        env = NotionEnv(configured=True)
        token, error = self._token(cfg)
        if token is None:
            env.error = error
            return env          # auth-required
        me = _request(token, "GET", "/users/me")
        if me.status == "auth-required" or me.code == 403:
            env.error = me.error or "Notion refused the integration token"
            return env
        if me.status != "ok":
            env.upstream = True
            env.error = me.error or "Notion is unreachable"
            return env
        env.authenticated = True
        env.login = str(me.data.get("name") or "")
        user, _dbs, error = _settings(cfg)
        if error:
            env.bad_config = True
            env.error = error
            return env
        found = _resolve_user(token, user)
        if found.status == "ok":
            env.user_id, name = found.data
            env.login = name or env.login
        elif found.status == "bad-config":
            env.bad_config = True
            env.error = found.error
        else:
            env.upstream = True
            env.error = found.error
        return env

    # -------------------------------------------------------------- list

    def list(self) -> dict:
        """``{status, tasks, truncated?, databases?, error?,
        fetched_at}`` — each configured database's rows owned by the
        user, minus the ones whose status reads done. A database that
        fails keeps its ``{id, name, status, error}`` in ``databases``
        and the others still answer rows; ``truncated`` says a database
        had more rows than the caps read."""
        value, at = self._cached(("notion-pages",), self._list)
        return {**value, "fetched_at": at}

    def _list(self) -> dict:
        cfg = load_section(self.home) or {}
        env = self.probe()
        if env.status != "ok":
            return {"status": env.status, "tasks": [],
                    "error": env.error or env.status}
        token, error = self._token(cfg)
        if token is None:
            return {"status": "auth-required", "tasks": [], "error": error}
        _user, databases, error = _settings(cfg)
        if error:
            return {"status": "bad-config", "tasks": [], "error": error}
        tasks: list[dict] = []
        failed: list[dict] = []
        truncated = False
        for db in databases:
            reply = self._database_rows(token, db, env.user_id)
            tasks.extend(reply["tasks"])
            truncated = truncated or bool(reply.get("truncated"))
            if reply["status"] != "ok":
                failed.append({"id": db.id or None, "name": db.name,
                               "status": reply["status"],
                               "error": reply["error"]})
        out: dict = {"status": "ok", "tasks": tasks}
        if truncated:
            out["truncated"] = True
        if failed:
            out["databases"] = failed
            out["status"] = failed[0]["status"]
            out["error"] = "; ".join(f["error"] for f in failed[:3])
        return out

    def _schema(self, token: str, db: Database) -> dict:
        """``{status, schema?, error?}`` for one database, cached."""
        def fetch():
            result = _request(token, "GET", f"/databases/{db.id}")
            if result.status == "ok":
                return {"status": "ok", "schema": result.data}
            label = db.name or db.id
            if result.code in (403, 404):
                return {"status": "bad-config",
                        "error": f"Notion can't see database {label}: "
                                 "share it with the integration (the "
                                 "database's ••• menu → Connections)"}
            return {"status": result.status,
                    "error": f"database {label}: {result.error}"}
        value, _at = self._cached(("notion-db", db.id), fetch)
        return value

    def _database_rows(self, token: str, db: Database,
                       user_id: str) -> dict:
        """One database's open rows for the user, paginated and capped."""
        if db.error:
            return {"status": "bad-config", "tasks": [], "error": db.error}
        got = self._schema(token, db)
        if got["status"] != "ok":
            return {"status": got["status"], "tasks": [],
                    "error": got["error"]}
        schema = got["schema"]
        name = db.name or _plain(schema.get("title")) or db.id
        mapping, error = _mapping(schema, db.properties, name)
        if error:
            return {"status": "bad-config", "tasks": [], "error": error}
        body: dict = {"filter": _owner_filter(mapping, user_id),
                      "sorts": [{"timestamp": "last_edited_time",
                                 "direction": "descending"}],
                      "page_size": PAGE_SIZE}
        if mapping["status_type"] == "checkbox":
            # An unticked box is the only open value: let Notion drop
            # the done rows before they cost a page.
            body["filter"] = {"and": [
                body["filter"],
                {"property": mapping["status"],
                 "checkbox": {"equals": False}}]}
        tasks: list[dict] = []
        more = False
        for _page in range(MAX_PAGES):
            result = _request(token, "POST", f"/databases/{db.id}/query",
                              body)
            if result.status != "ok":
                return {"status": result.status, "tasks": [],
                        "error": f"database {name}: {result.error}"}
            for page in result.data.get("results") or []:
                if not isinstance(page, dict) or _gone(page):
                    continue
                row = _row(page, mapping, name)
                if row["state"] == "closed":
                    continue
                tasks.append(row)
                if len(tasks) >= LIST_LIMIT:
                    # Rows past the cap may still be open ones.
                    return {"status": "ok", "tasks": tasks,
                            "truncated": True}
            cursor = result.data.get("next_cursor")
            more = bool(result.data.get("has_more") and cursor)
            if not more:
                break
            body = {**body, "start_cursor": cursor}
        out = {"status": "ok", "tasks": tasks}
        if more:
            # The page cap stopped the read with rows still unread.
            out["truncated"] = True
        return out

    # -------------------------------------------------------------- get

    def get(self, task: dict) -> dict | None:
        """Fresh ``{state, status_name, title, url, updated_at}`` for a
        stored page, cached like the list; ``None`` when Notion can't
        tell. The body stays out — ``detail`` reads it — since a list
        poll refreshes up to ``tasks.MAX_REFRESH`` stored tasks and
        Notion allows three calls a second."""
        ref = normalize_id(str(task.get("ref") or ""))
        if ref is None:
            return None
        value, _at = self._cached(("notion-task", ref),
                                  lambda: self._refresh(ref))
        return value if value.get("status") == "ok" else None

    def _refresh(self, ref: str) -> dict:
        cfg = load_section(self.home) or {}
        token, error = self._token(cfg)
        if token is None:
            return {"status": "auth-required", "error": error}
        result = _request(token, "GET", f"/pages/{ref}")
        if result.status != "ok":
            if result.code == 404:
                return {"status": "not-found",
                        "error": f"Notion has no page {ref} the "
                                 "integration can see"}
            return {"status": result.status,
                    "error": result.error or result.status}
        page = result.data
        mapping = None
        name = ""
        parent = page.get("parent") if isinstance(page, dict) else None
        parent_id = normalize_id((parent or {}).get("database_id"))
        _user, databases, _err = _settings(cfg)
        for db in databases:
            if db.id and db.id == parent_id and not db.error:
                got = self._schema(token, db)
                if got["status"] == "ok":
                    name = db.name or _plain(got["schema"].get("title"))
                    found, error = _mapping(got["schema"], db.properties,
                                            name or db.id)
                    mapping = None if error else found
                break
        if mapping is not None:
            row = _row(page, mapping, name or "")
            out = {"state": row["state"],
                   "status_name": row["status_name"],
                   "title": row["task"]["title"],
                   "url": row["task"]["url"]}
        else:
            # A page outside the configured databases (moved), or one
            # whose schema won't read right now: its title and link
            # still read, but no state, so the row keeps the last one
            # its source reported instead of reopening.
            out = {"state": "", "status_name": "",
                   "title": _page_title(page), "url": page.get("url") or ""}
        if _gone(page):
            out["state"] = "closed"
            out["status_name"] = "Archived"
        return {"status": "ok", **out,
                "updated_at": page.get("last_edited_time")}

    def detail(self, task: dict) -> dict | None:
        """``get`` plus ``description``: the page body as markdown, what
        a resolve prompt quotes (Linear's ``get`` answers its issue
        description the same way). ``None`` when the page won't read."""
        fresh = self.get(task)
        if fresh is None:
            return None
        ref = normalize_id(str(task.get("ref") or ""))
        value, _at = self._cached(("notion-body", ref),
                                  lambda: self._body(ref))
        return {**fresh, "description": value.get("description") or ""}

    def _body(self, ref: str) -> dict:
        cfg = load_section(self.home) or {}
        token, error = self._token(cfg)
        if token is None:
            return {"status": "auth-required", "error": error}
        budget = {"calls": MAX_BLOCK_CALLS, "cut": False}
        lines = _render(token, ref, budget, 0)
        text = "\n".join(lines).strip()
        if len(text) > MAX_DESCRIPTION:
            text = text[:MAX_DESCRIPTION].rstrip()
            budget["cut"] = True
        if budget["cut"]:
            text += "\n\n_(truncated: the rest of the page is in Notion)_"
        return {"status": "ok", "description": text}


# ------------------------------------------------------------------ users

def _resolve_user(token: str, user: str) -> NotionResult:
    """``data = (user id, name)`` for the configured user: an id is
    checked with ``/v1/users/<id>``, an email found in ``/v1/users``.
    ``status`` is ``bad-config`` when the user isn't there."""
    if "@" not in user:
        uid = normalize_id(user) or user
        got = _request(token, "GET", f"/users/{uid}")
        if got.status == "ok":
            return NotionResult("ok", data=(uid, str(
                got.data.get("name") or "")))
        if got.code == 403:
            # No user-information capability: the id can't be checked,
            # but it still filters the owner property fine.
            return NotionResult("ok", data=(uid, ""))
        if got.code in (400, 404):
            return NotionResult(
                "bad-config", error=f"notion.user: Notion has no user {uid}")
        return got
    wanted = user.lower()
    cursor = None
    saw_email = False
    more = False
    for _page in range(MAX_USER_PAGES):
        query = "?page_size=100" + (
            f"&start_cursor={urllib.parse.quote(cursor)}" if cursor else "")
        got = _request(token, "GET", "/users" + query)
        if got.code == 403:
            return NotionResult("bad-config", error=(
                "notion.user is an email, but the integration can't read "
                "users: give it the \"Read user information including "
                "email addresses\" capability, or set notion.user to your "
                "Notion user id"))
        if got.status != "ok":
            return got
        for person in got.data.get("results") or []:
            info = person.get("person") if isinstance(person, dict) else None
            email = (info or {}).get("email") if isinstance(info, dict) \
                else None
            if isinstance(email, str):
                saw_email = True
                if email.lower() == wanted:
                    return NotionResult("ok", data=(
                        str(person.get("id") or ""),
                        str(person.get("name") or "")))
        cursor = got.data.get("next_cursor")
        more = bool(got.data.get("has_more") and cursor)
        if not more:
            break
    if more:
        # The workspace has more people than the lookup reads: don't
        # claim the email is unknown when it may just be further on.
        return NotionResult("bad-config", error=(
            f"notion.user: {user} isn't among the first "
            f"{MAX_USER_PAGES * 100} people in the workspace, so the "
            "lookup stopped; set notion.user to your Notion user id"))
    if not saw_email:
        return NotionResult("bad-config", error=(
            "notion.user is an email, but Notion sent no emails: give the "
            "integration the \"Read user information including email "
            "addresses\" capability, or set notion.user to your Notion "
            "user id"))
    return NotionResult("bad-config", error=(
        f"notion.user: no one in the workspace signs in as {user}"))


# ------------------------------------------------------------------ rows

def _mapping(schema: dict, props: dict, name: str) -> tuple[dict, str]:
    """``(mapping, error)`` — the configured property names checked
    against the database's schema. ``mapping`` holds each role's name
    and type plus the status groups; ``error`` names the database and
    the property that doesn't fit, never a traceback."""
    columns = schema.get("properties")
    columns = columns if isinstance(columns, dict) else {}

    def kind(prop: str) -> str:
        col = columns.get(prop)
        return str(col.get("type") or "") if isinstance(col, dict) else ""

    def missing(role: str, prop: str) -> str:
        have = ", ".join(sorted(columns)[:12]) or "none"
        return (f'Notion database "{name}" has no property "{prop}" '
                f"for {role} (it has: {have})")

    title = props.get("title")
    if title is None:
        title = next((p for p in columns if kind(p) == "title"), "")
    elif title not in columns:
        return {}, missing("title", title)
    elif kind(title) != "title":
        return {}, (f'Notion database "{name}": "{title}" is a '
                    f"{kind(title)} property, not the title")
    checks = (("status", STATUS_TYPES, "a status, select or checkbox"),
              ("owner", OWNER_TYPES, "a person, created-by or "
                                     "last-edited-by"),
              ("url", URL_TYPES, "a url or text"))
    mapping = {"title": title}
    for role, kinds, words in checks:
        prop = props.get(role)
        if prop is None:
            mapping[role] = ""
            mapping[f"{role}_type"] = ""
            continue
        if prop not in columns:
            return {}, missing(role, prop)
        if kind(prop) not in kinds:
            return {}, (f'Notion database "{name}": {role} property '
                        f'"{prop}" is a {kind(prop) or "unknown"} '
                        f"property; it must be {words} property")
        mapping[role] = prop
        mapping[f"{role}_type"] = kind(prop)
    mapping["groups"] = _groups(columns.get(mapping["status"]))
    return mapping, ""


def _groups(column) -> dict:
    """A status property's ``option id → ladder state`` from its groups
    (To-do, In progress, Complete). Groups can be renamed but not
    reordered, so an unrecognized name falls back to its position."""
    if not isinstance(column, dict) or column.get("type") != "status":
        return {}
    groups = (column.get("status") or {}).get("groups")
    if not isinstance(groups, list):
        return {}
    by_place = ("open", "in_progress", "closed")
    out = {}
    for i, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        state = _classify(str(group.get("name") or ""))
        if state is None:
            state = by_place[i] if len(groups) == 3 else "open"
        for oid in group.get("option_ids") or []:
            out[str(oid)] = state
    return out


def _classify(word: str) -> str | None:
    """A status name's ladder state, or ``None`` when no word fits."""
    text = word.strip().lower()
    if not text:
        return None
    for words, state in ((_OPEN_WORDS, "open"), (_CLOSED_WORDS, "closed"),
                         (_STARTED_WORDS, "in_progress")):
        for w in words:
            if re.search(r"(?<![\w'])" + re.escape(w) + r"(?![\w'])", text):
                return state
    return None


def _owner_filter(mapping: dict, user_id: str) -> dict:
    """Rows whose owner property holds the user — a people, created-by
    or last-edited-by filter, each answering ``contains``."""
    kind = mapping["owner_type"]
    return {"property": mapping["owner"], kind: {"contains": user_id}}


def _status(page: dict, mapping: dict) -> tuple[str, str]:
    """``(state, status words)`` for one row's status property."""
    prop = (page.get("properties") or {}).get(mapping["status"])
    if not isinstance(prop, dict):
        return "open", ""
    kind = mapping["status_type"]
    if kind == "checkbox":
        # Ticked reads as done, in the property's own name ("Shipped").
        return ("closed", mapping["status"]) if prop.get("checkbox") \
            else ("open", "")
    option = prop.get(kind)
    if not isinstance(option, dict):
        return "open", ""
    name = str(option.get("name") or "").strip()
    state = mapping["groups"].get(str(option.get("id") or "")) \
        if kind == "status" else None
    return state or _classify(name) or "open", name


def _row(page: dict, mapping: dict, project: str) -> dict:
    """One query result as a ``list()`` row."""
    props = page.get("properties") or {}
    title = _value(props.get(mapping["title"])) if mapping["title"] \
        else ""
    url = _value(props.get(mapping["url"])) if mapping["url"] else ""
    state, words = _status(page, mapping)
    raw = str(page.get("id") or "")
    task = {"source": "notion", "kind": "page",
            "ref": normalize_id(raw) or raw,
            "title": title or _page_title(page),
            "url": url or page.get("url") or ""}
    if project:
        task["project"] = project
    return {"task": task, "state": state, "status_name": words,
            "updated_at": page.get("last_edited_time")}


def _value(prop) -> str:
    """A title, text or url property as plain text."""
    if not isinstance(prop, dict):
        return ""
    kind = prop.get("type")
    value = prop.get(kind) if isinstance(kind, str) else None
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return _plain(value)
    return ""


def _page_title(page: dict) -> str:
    """The page's title property, whatever it is called."""
    for prop in (page.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return _plain(prop.get("title"))
    return ""


def _gone(page: dict) -> bool:
    return bool(page.get("archived") or page.get("in_trash"))


def _plain(rich) -> str:
    """Rich text as plain text."""
    if not isinstance(rich, list):
        return ""
    return "".join(str(r.get("plain_text") or "") for r in rich
                   if isinstance(r, dict)).strip()


# ------------------------------------------------------------------ body

def _markdown(rich) -> str:
    """Rich text as inline markdown: code, bold, italic, strikethrough
    and links; colours and mentions read as their text."""
    if not isinstance(rich, list):
        return ""
    out = []
    for r in rich:
        if not isinstance(r, dict):
            continue
        text = str(r.get("plain_text") or "")
        if not text:
            continue
        marks = r.get("annotations") or {}
        if r.get("type") == "equation":
            text = f"${text}$"
        elif marks.get("code"):
            text = f"`{text}`"
        else:
            if marks.get("bold"):
                text = f"**{text}**"
            if marks.get("italic"):
                text = f"*{text}*"
            if marks.get("strikethrough"):
                text = f"~~{text}~~"
        href = r.get("href")
        if isinstance(href, str) and href:
            text = f"[{text}]({href})"
        out.append(text)
    return "".join(out)


def _children(token: str, block_id: str, budget: dict) -> list[dict]:
    """A block's children, paginated within the shared call budget."""
    blocks: list[dict] = []
    cursor = None
    while True:
        if budget["calls"] <= 0:
            budget["cut"] = True
            break
        budget["calls"] -= 1
        query = "?page_size=100" + (
            f"&start_cursor={urllib.parse.quote(cursor)}" if cursor else "")
        got = _request(token, "GET", f"/blocks/{block_id}/children{query}")
        if got.status != "ok":
            budget["cut"] = True
            break
        blocks.extend(b for b in got.data.get("results") or []
                      if isinstance(b, dict))
        cursor = got.data.get("next_cursor")
        if not got.data.get("has_more") or not cursor:
            break
    return blocks


_LISTS = ("bulleted_list_item", "numbered_list_item", "to_do", "toggle")
_FLAT = ("column_list", "column", "synced_block")
_FILES = ("image", "video", "file", "pdf", "audio")


def _render(token: str, block_id: str, budget: dict, depth: int) -> list[str]:
    """A block's children as markdown lines: lists stay tight, other
    blocks are separated by a blank line, nested children indent."""
    lines: list[str] = []
    number = 0
    last_list = False
    for block in _children(token, block_id, budget):
        kind = str(block.get("type") or "")
        number = number + 1 if kind == "numbered_list_item" else 0
        own = _block(block, kind, number)
        nested: list[str] = []
        if block.get("has_children") and kind not in (
                "child_page", "child_database"):
            if depth + 1 >= MAX_DEPTH:
                budget["cut"] = True
            elif kind == "table":
                own = _table(_children(token, block["id"], budget))
            else:
                nested = _render(token, str(block.get("id")), budget,
                                 depth + 1)
        if own is None and not nested:
            continue
        is_list = kind in _LISTS
        if lines and not (is_list and last_list):
            lines.append("")
        if own is not None:
            lines.extend(own.split("\n"))
        if nested:
            pad = "> " if kind in ("quote", "callout") else (
                "" if kind in _FLAT else "  ")
            lines.extend(pad + n if n else n for n in nested)
        last_list = is_list
    return lines


def _block(block: dict, kind: str, number: int) -> str | None:
    """One block's own line(s), without its children; ``None`` to skip."""
    body = block.get(kind) if isinstance(block.get(kind), dict) else {}
    text = _markdown(body.get("rich_text"))
    if kind == "paragraph":
        return text
    if kind in ("heading_1", "heading_2", "heading_3"):
        return "#" * int(kind[-1]) + " " + text
    if kind == "bulleted_list_item" or kind == "toggle":
        return "- " + text
    if kind == "numbered_list_item":
        return f"{number}. {text}"
    if kind == "to_do":
        return ("- [x] " if body.get("checked") else "- [ ] ") + text
    if kind == "quote":
        return "> " + text
    if kind == "callout":
        icon = body.get("icon") or {}
        emoji = icon.get("emoji") if isinstance(icon, dict) else None
        return "> " + (f"{emoji} " if emoji else "") + text
    if kind == "code":
        lang = str(body.get("language") or "").replace("plain text", "")
        return f"```{lang}\n{_plain(body.get('rich_text'))}\n```"
    if kind == "divider":
        return "---"
    if kind == "equation":
        return f"$${body.get('expression') or ''}$$"
    if kind == "child_page":
        return f"[child page: {body.get('title') or ''}]"
    if kind == "child_database":
        return f"[child database: {body.get('title') or ''}]"
    if kind in ("bookmark", "embed", "link_preview"):
        url = body.get("url") or ""
        caption = _plain(body.get("caption"))
        return f"[{caption or url}]({url})" if url else None
    if kind in _FILES:
        caption = _plain(body.get("caption")) or body.get("name") or kind
        # Notion-hosted files sit behind signed links that expire in an
        # hour, so only an external link is worth quoting.
        external = body.get("external") if body.get("type") == "external" \
            else None
        url = external.get("url") if isinstance(external, dict) else ""
        return f"[{kind}: {caption}]({url})" if url else f"[{kind}: {caption}]"
    # A table's rows, a column's blocks: they come from the children.
    return None


def _table(rows: list[dict]) -> str | None:
    """A table block's ``table_row`` children as a markdown table."""
    out = []
    for i, row in enumerate(rows):
        cells = (row.get("table_row") or {}).get("cells")
        if not isinstance(cells, list):
            continue
        out.append("| " + " | ".join(
            _markdown(c).replace("|", "\\|") for c in cells) + " |")
        if i == 0:
            out.append("|" + " --- |" * len(cells))
    return "\n".join(out) or None
