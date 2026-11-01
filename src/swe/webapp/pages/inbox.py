"""The Inbox page: the Mac app's InboxPage.

``GET /v1/app/inbox`` is ``swe gh inbox --all --limit=50 --json``
built in process: the hub's GitHub notifications, read rows included
so the page's Unread only switch filters in the browser as the app's
does. ``refresh=1`` skips the cache.

``POST /v1/app/inbox/read`` marks notifications read, as ``swe gh inbox
read --json`` does: ``{"ids": ["123", ...]}`` for rows (each one the
inbox lists) or ``{"all": true}`` for everything. See ``post``.
"""

from __future__ import annotations

from swe.github import cli as ghcli

from swe.webapp.pages import PageError
from swe.github import shared as G
from swe.webapp.pages import _github as W

LIMIT = 50  # GitHubModel.loadInbox -> swe gh inbox --limit=50 --all
# The hub's feed can't cap at the page's LIMIT: an old unread thread
# crowded out of the listing would vanish from /v1/inbox while still
# unread — and unread rows must never age out. Ten pages of 100 covers
# essentially every inbox while bounding the fetch.
UNREAD_LIMIT = 1000


def _key(home) -> tuple:
    return ("inbox", G.home_key(home))


def _unread_key(home) -> tuple:
    return ("inbox-unread", G.home_key(home))


def _listing(home, refresh: bool = False) -> tuple[dict, float]:
    return G.CACHE.get(
        _key(home),
        lambda: ghcli._fetch_inbox(LIMIT, fetch_all=True),
        refresh=refresh, ok=lambda p: p.get("status") == "ok")


def _unread_listing(home) -> tuple[dict, float]:
    """The unread-only listing: the all-rows page can crowd an old
    unread thread out of ``LIMIT``, but unread rows must never age out
    of the hub's inbox, so the hub reads the unread endpoint."""
    return G.CACHE.get(
        _unread_key(home),
        lambda: ghcli._fetch_inbox(UNREAD_LIMIT, fetch_all=False),
        ok=lambda p: p.get("status") == "ok")


def _drop(home) -> None:
    """Forget both listings: a write changes either's rows."""
    keys = {_key(home), _unread_key(home)}
    G.CACHE.drop(keys.__contains__)


def unread_rows(home) -> list[dict] | None:
    """The unread notifications listing as ``github`` inbox rows.

    The hub's catalog polls these for ``/v1/inbox`` and the ntfy fan-out;
    the shared ``TtlCache`` folds each poll into one ``gh api
    notifications`` run per TTL alongside the page's own fetch. ``None``
    means the read failed — the caller keeps its last good rows. A
    broken ``gh`` answers before any subprocess: the env probe needs no
    ``gh`` at all when the binary is absent.
    """
    if G.probe(home).status != "ok":
        return None
    payload, _at = _unread_listing(home)
    if payload.get("status") != "ok":
        return None
    rows = []
    for note in payload.get("notifications") or []:
        if not note.get("unread", True):
            continue
        row = ghcli.inbox_row(note)
        if row is not None:
            rows.append(row)
    return rows


def get(ctx, args: dict) -> dict:
    refresh = W.flag(args, "refresh")
    payload, at = _listing(ctx.home, refresh)
    env = G.env_after(ctx.home, [payload], refresh=refresh)
    machine = (ctx.machine or {}).get("name")
    if env.status != "ok":
        # swe's blocked shape: the probe's status and no rows.
        return {"github": G.env_payload(env), "status": env.status,
                "notifications": [], "machine": machine}
    return {"github": G.env_payload(env), **payload,
            "fetched_at": at, "machine": machine}


def post(ctx, action: str, body: dict) -> dict:
    """Mark read, through the functions behind ``swe gh inbox read``.

    ``ids`` must be notification ids the page's own listing has (the
    cached ``GET`` reply); anything else is a 404, and a bad shape a
    422. ``all`` marks everything, as the Mac app's envelope button
    does. The answer is ``{"github", "all", "marked", "failed",
    "notifications", "machine"}``: ``notifications`` is the listing
    with the marked rows' ``unread`` flipped off (the Mac app's local
    update; GitHub can take a moment to show a mark-all), and
    ``failed`` the ids GitHub refused, each with its error."""
    if action != "read":
        raise PageError(404, "unknown_action", f"unknown inbox action: {action}")
    every, ids = _read_args(body)
    home = ctx.home
    # The probe and the listings are reads (cached, or a gh call each):
    # they run before WRITE_LOCK, which every other GitHub write on the
    # hub waits on, and only the mark itself holds it.
    # swe gh inbox read probes first; so does this, so a lapsed login
    # reads as the setup message rather than a failed mark.
    env = G.probe(home, refresh=True)
    if env.status != "ok":
        raise PageError(409, "gh_" + env.status.replace("-", "_"),
                        ghcli._env_error(env))
    payload, _at = _listing(home)
    if payload.get("status") != "ok":
        raise PageError(502, "inbox_unreadable",
                        payload.get("error") or "GitHub returned "
                        + str(payload.get("status")))
    notes = payload.get("notifications") or []
    # A thread the hub's unread-only feed shows must be markable even
    # when newer read rows crowded it off this page's listing.
    unread, _at2 = _unread_listing(home)
    listed = {str(n.get("id")) for n in notes}
    listed |= {
        str(n.get("id"))
        for n in (unread.get("notifications") or [])
    }
    unknown = [i for i in ids if i not in listed]
    if unknown:
        raise PageError(404, "unknown_notification",
                        "Not in the inbox: " + ", ".join(unknown))
    with G.WRITE_LOCK:
        if every:
            result = ghcli.mark_all_read()
            _drop(home)
            if result.status != "ok":
                raise PageError(502, "mark_failed",
                                f"Marking read failed: {result.error or result.status}")
            marked, failed = ["*"], []
            done = listed
        else:
            entries = ghcli.mark_threads_read(ids)
            _drop(home)
            marked = [e["id"] for e in entries if e["status"] == "ok"]
            failed = [{"id": e["id"], "error": e["error"]}
                      for e in entries if e["status"] != "ok"]
            done = set(marked)
    rows = [{**n, "unread": False} if str(n.get("id")) in done else n
            for n in notes]
    return {"github": G.env_payload(env), "all": every, "marked": marked,
            "failed": failed, "notifications": rows,
            "machine": (ctx.machine or {}).get("name")}


def _read_args(body: dict) -> tuple[bool, list[str]]:
    every = body.get("all", False)
    if not isinstance(every, bool):
        raise PageError(422, "bad_all", "all must be true or false")
    raw = body.get("ids")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise PageError(422, "bad_ids", "ids must be a list of notification ids")
    if every and raw:
        raise PageError(422, "ids_and_all", "Pick ids or all, not both.")
    if not every and not raw:
        raise PageError(422, "nothing_to_mark", "Name the notifications to mark read.")
    if len(raw) > LIMIT:
        raise PageError(422, "too_many_ids", f"At most {LIMIT} ids at a time.")
    ids: list[str] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise PageError(422, "bad_ids", "ids must be notification ids")
        text = str(item).strip()
        if not (text.isascii() and text.isdigit()) or len(text) > 20:
            raise PageError(422, "bad_ids", f"Not a notification id: {text[:40]}")
        if text not in ids:
            ids.append(text)
    return every, ids
