"""Cursor's usage feed, ingested into the ledger (ports
``CursorUsageFeed.swift``).

The feed covers the editor, cursor-agent, cloud agents and Grok Bot,
which signs in with the Cursor account; Grok Bot's own models are stored
under a ``grok-bot`` harness so they price and chart separately from
Cursor's. Where the Mac polls ``swe harness usage-events cursor --json``
as a subprocess, the web app is inside swe already, so it calls
``swe.harness.rate_limits.fetch_cursor_usage_events`` directly — same
reader, same reply shape.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from pathlib import Path

from swe.ledger.models import LedgerRecord, UsageEvent, json_int
from swe.ledger.pricing import SUPPLEMENT, alias_target
from swe.ledger.store import LedgerStore
from swe.paths import swe_dir_for

# Cursor serves at most the last 180 days; a first poll asks for all of
# it and each later one overlaps by two days so an event that lands late
# on an old page is still picked up (re-ingest is idempotent).
WINDOW_S = 180 * 86400
OVERLAP_S = 2 * 86400
# The app's keepFresh cadence: ``WebApp`` starts a background round when
# this much time has passed, and the gate inside ``poll()`` is the check
# both the scheduler and any direct caller go through.
INTERVAL_S = 15 * 60

# The scan's `meta` slot for `cache/cursor-feed.json` — same contents as
# the app's progress file, kept with the store it describes.
_PROGRESS_KEY = "feed.cursor"

# Cursor's user-data dirs per platform — the default sign-in's account
# home, matching ``_CURSOR_STATE_DB_CANDIDATES``. Mac is first: the
# default's order is also the order `accounts` lists them in.
_USER_DATA_DIRS = ("Library/Application Support/Cursor",
                   ".config/Cursor",
                   "AppData/Roaming/Cursor")

_DAY_BUCKET = re.compile(r"cursor-\d{4}-\d{2}-\d{2}")


def _default_fetch(state_db: str | None, since: float, now: float):
    """``swe.harness.rate_limits.fetch_cursor_usage_events``, imported
    here so the ledger package never drags the registry in at load."""
    from swe.harness import rate_limits
    return rate_limits.fetch_cursor_usage_events(state_db, since, now)


def _default_user_data(home: Path) -> str | None:
    """The default sign-in's account home, or ``None`` when nothing under
    ``home`` shows Cursor ever ran — a scan then skips the feed entirely
    instead of probing sign-in stores for an install that isn't there."""
    for name in _USER_DATA_DIRS:
        if (home / name).is_dir():
            return str(home / name)
    if (home / ".config" / "cursor" / "auth.json").is_file() or (home / ".cursor").is_dir():
        return str(home / _USER_DATA_DIRS[0])
    return None


def _profiles(home: Path) -> list[str]:
    """Each extra Cursor profile's desktop dir
    (``accounts/cursor/<id>/desktop``) — the list ``AppModel`` hands the
    feed, from ``accounts/profiles.json``."""
    accounts = swe_dir_for(home) / "accounts"
    try:
        data = json.loads((accounts / "profiles.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw = data.get("profiles") if isinstance(data, dict) else None
    return [str(accounts / "cursor" / p["id"] / "desktop")
            for p in raw or []
            if isinstance(p, dict) and p.get("vendor") == "cursor"
            and not p.get("isDefault") and isinstance(p.get("id"), str)]


def _accounts(home: Path) -> list[tuple[str, str | None]]:
    """``(account home, state db)`` pairs in poll order — the default
    sign-in first, then each extra Cursor profile
    (``CursorUsageFeed.Account``). A ``None`` state db means the fetcher
    finds the token itself, as swe's lookup without ``--account-dir``."""
    out: list[tuple[str, str | None]] = []
    default = _default_user_data(home)
    if default is not None:
        out.append((default, None))
    for desktop in _profiles(home):
        out.append((desktop, os.path.join(desktop, "User", "globalStorage", "state.vscdb")))
    return out


def _progress(store: LedgerStore) -> dict:
    """The store's saved progress — which account each profile answers
    as, each account's newest imported timestamp, and when the last round
    ran (``CursorFeedProgress``)."""
    try:
        raw = json.loads(store.meta(_PROGRESS_KEY) or "")
    except ValueError:
        raw = None
    progress = raw if isinstance(raw, dict) else {}
    progress.setdefault("schema", 1)
    progress.setdefault("profiles", {})
    progress.setdefault("accounts", {})
    return progress


# One in-flight round per store file — ``CursorUsageFeed``'s actor
# serializes its rounds the same way. The web app's background round runs
# on its own connection, so ordering here is what keeps progress's
# read-modify-write from interleaving with another poller's.
_POLL_LOCKS: dict[str, threading.Lock] = {}
_POLL_LOCKS_GUARD = threading.Lock()


def _round_lock(store: LedgerStore) -> threading.Lock:
    with _POLL_LOCKS_GUARD:
        return _POLL_LOCKS.setdefault(
            os.path.realpath(store.path), threading.Lock())


def due(store: LedgerStore, now: float | None = None) -> bool:
    """Whether ``keepFresh`` would start a round now — the caller's cheap
    check before it spends a thread and a second connection on one."""
    now = time.time() if now is None else now
    return now - (_progress(store).get("polled_at") or 0) >= INTERVAL_S


def _note(errors: list | None, message: str) -> None:
    """One copy of a surfaced status line in the round's notes — two
    accounts reporting the same thing reads as one page note, not two."""
    if errors is not None and message not in errors:
        errors.append(message)


def poll(store: LedgerStore, home: Path | None = None, *, fetch=None,
         now: float | None = None, errors: list | None = None) -> int:
    """Fetch every Cursor account's feed once and ingest it, returning
    how many rows changed (``CursorUsageFeed.poll``).

    Rounds are serialized on the store's file: a scan on the caller's own
    connection and this round's can never interleave, and a round that
    starts just after another sees its fresh ``polled_at`` and returns
    early rather than fetching again.

    Two profiles can sign in to the same Cursor account, so a reply's
    ``account_id`` that a sibling already imported this round is skipped:
    the events would key identically anyway. Any other per-account miss —
    signed out, an expired session, the endpoint down — keeps the stored
    rows and is retried next round; the misses a page can act on
    (``unavailable``, a truncated ``incomplete`` window) leave a note.
    """
    with _round_lock(store):
        return _poll(store, home, fetch=fetch, now=now, errors=errors)


def _poll(store: LedgerStore, home: Path | None = None, *, fetch=None,
          now: float | None = None, errors: list | None = None) -> int:
    home = Path(home) if home is not None else Path.home()
    now = time.time() if now is None else now
    progress = _progress(store)
    if now - (progress.get("polled_at") or 0) < INTERVAL_S:
        return 0
    progress["polled_at"] = now
    fetch = fetch or _default_fetch
    full_window = now - WINDOW_S
    changed = 0
    seen: set[str] = set()

    # The window each account is read through: its high-water mark minus
    # the overlap, or the whole feed window when unknown.
    def window(account_id: str | None) -> float:
        through = (progress["accounts"].get(account_id) or {}).get("through")
        return max(0.0, through - OVERLAP_S) if through is not None else full_window

    for account_home, state_db in _accounts(home):
        expected = progress["profiles"].get(account_home)
        try:
            status, events, account_id = fetch(state_db, window(expected), now)
            # A switched or newly seen account reads through its own mark
            # — the profile's old account's overlap would skip its
            # history.
            if account_id is not None and account_id != expected:
                own = window(account_id)
                if own != window(expected):
                    status, events, account_id = fetch(state_db, own, now)
        except Exception as exc:  # noqa: BLE001 — a failed poll keeps the rows
            if errors is not None:
                errors.append(f"cursor feed: {exc}")
            continue
        if account_id is not None and account_id in seen:
            continue
        # signed-out / auth-required / unavailable all keep the stored
        # data — and, because ``seen`` only records an id after its
        # import ran, an expired profile never blocks a working sibling
        # on the same account. Of the misses only ``unavailable`` earns a
        # note: a signed-out profile is a normal state, while a down
        # endpoint explains rows that stopped moving.
        if status not in ("ok", "incomplete"):
            if status == "unavailable":
                _note(errors, "cursor feed: the usage endpoint is "
                              "unavailable; keeping the stored rows")
            continue
        if status == "incomplete":
            _note(errors, "cursor feed: part of the window hit the page "
                          "cap; ingesting what arrived")
        # The key is the account when the fetcher reports one, the
        # profile's home when it cannot. Two profiles on one account
        # therefore write the same keys, and ``accountHome`` stays with
        # the first-seen profile.
        account_key = account_id or account_home
        for event in events:
            changed += int(store.upsert(record(event, account_key, account_home)))
        # Commit before the next account's fetch: a write left open across
        # that network call holds the lock every page scan needs.
        store.db.commit()
        if account_id is not None:
            seen.add(account_id)
            progress["profiles"][account_home] = account_id
            # ``through`` advances only on ``ok``: ``incomplete`` means
            # part of the window may be missing, so the next poll asks
            # for the same range again (re-ingest is idempotent).
            if status == "ok":
                newest = max((e.get("timestamp") for e in events
                              if isinstance(e.get("timestamp"), (int, float))), default=0.0)
                acc = progress["accounts"].setdefault(account_id, {})
                acc["through"] = max(acc.get("through") or 0.0, float(newest))
    store.set_meta(_PROGRESS_KEY, json.dumps(progress, sort_keys=True))
    store.db.commit()
    return changed


def record(event: dict, account_key: str, account_home: str) -> LedgerRecord:
    """One feed event as a ledger record (``CursorUsageFeed.record``).

    ``account_key`` is the account the profile reports (its
    ``account_id``), or the profile home when the fetcher cannot name
    one; ``account_home`` is always the profile that read it. The key
    names the account, event instant, model and conversation — the
    event's identity — and never its counts: Cursor reports a correction
    as new counts on the same event, and a key that embedded them stored
    it as a second event instead of replacing the row. An overlapping
    refetch — or a second profile on the same account — writes the same
    rows again rather than adding new ones.
    """
    model = event.get("model")
    model = model if isinstance(model, str) else ""
    conv = event.get("conversation_id")
    conv = conv if isinstance(conv, str) else None
    ts = event.get("timestamp")
    ts = float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else 0.0
    # Swift's ``(ts * 1000).rounded()`` — half away from zero, not
    # Python's banker's rounding.
    milliseconds = int(math.floor(ts * 1000 + 0.5)) if ts >= 0 else int(-math.floor(-ts * 1000 + 0.5))
    cost = event.get("cost_usd")
    return LedgerRecord(
        key=f"cursor-feed:{account_key}:{milliseconds}:{model}:{conv or ''}",
        authoritative=True,
        # A real account_id in the key means a sibling profile's stale
        # row is the same event; keyed by profile home instead, each
        # profile's rows belong to it alone.
        account_spans_profiles=account_key != account_home,
        event=UsageEvent(
            harness="grok-bot" if _is_grok_bot(model) else "cursor",
            account=account_home, session=conv or f"cursor-{_day_label(ts)}",
            cwd=None, model=model, ts=ts,
            input=json_int(event.get("input_tokens")),
            output=json_int(event.get("output_tokens")),
            cache_read=json_int(event.get("cache_read_tokens")),
            cache_write=json_int(event.get("cache_write_tokens")),
            cost=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool)
            else None))


def is_day_bucket(session: str) -> bool:
    """Whether ``session`` is a day bucket rather than a conversation id
    (``isDayBucket`` — the prefix test first, as there)."""
    return session.startswith("cursor-") and _DAY_BUCKET.fullmatch(session) is not None


def _day_label(ts: float) -> str:
    """``yyyy-mm-dd`` in the local calendar, for the session label of
    events Cursor does not give a conversation id to (``dayLabel``)."""
    t = time.localtime(ts)
    return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"


def _load_aliases() -> dict[str, str]:
    """The supplement's ``aliases`` table, keyed lower-cased — what
    ``ModelBrands.resolvedName`` reads (the same load ``_brands`` does)."""
    try:
        raw = json.loads(SUPPLEMENT.read_text(encoding="utf-8")).get("aliases") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {k.lower(): v for k, v in raw.items() if isinstance(v, str)}


_ALIASES = _load_aliases()


def _is_grok_bot(model: str) -> bool:
    """``ModelBrands.forModel(model).id == "grok-bot"``: the logged name
    resolves through the supplement's aliases and loses its ``provider/``
    prefix, and ``grok-bot`` is the only brand pattern that claims a name
    starting ``grok-bot`` (xAI's ``grok`` pattern specifically excludes
    it)."""
    name = (alias_target(model, _ALIASES) or model).lower()
    return name[name.rfind("/") + 1:].startswith("grok-bot")
