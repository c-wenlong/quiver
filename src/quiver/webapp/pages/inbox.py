"""The Inbox page: the Mac app's InboxPage, read only.

``GET /v1/app/inbox`` is ``swe gh inbox --all --limit=50 --json``
built in process: the hub's GitHub notifications, read rows included
so the page's Unread only switch filters in the browser as the app's
does. ``refresh=1`` skips the cache. Marking read stays in ``swe gh
inbox read`` and the Mac app.
"""

from __future__ import annotations

from quiver.github import cli as ghcli

from quiver.webapp.pages import _github as G

LIMIT = 50  # GitHubModel.loadInbox -> swe gh inbox --limit=50 --all


def get(ctx, args: dict) -> dict:
    refresh = G.flag(args, "refresh")
    payload, at = G.CACHE.get(
        ("inbox", G.home_key(ctx.home)),
        lambda: ghcli._fetch_inbox(LIMIT, fetch_all=True),
        refresh=refresh, ok=lambda p: p.get("status") == "ok")
    env = G.env_after(ctx.home, [payload], refresh=refresh)
    machine = (ctx.machine or {}).get("name")
    if env.status != "ok":
        # swe's blocked shape: the probe's status and no rows.
        return {"github": G.env_payload(env), "status": env.status,
                "notifications": [], "machine": machine}
    return {"github": G.env_payload(env), **payload,
            "fetched_at": at, "machine": machine}
