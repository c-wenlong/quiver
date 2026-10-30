"""The Calendar page (ports ``CalendarPage.swift`` and the model in
``QuiverCore/Calendar``: ``CalendarModel``, ``Activity``,
``ActivityBlocks``, ``ProjectResolver``, ``CalendarFormat``).

When each harness was actually working, read from the ledger's response
timestamps, and for Pi and Factory Droid from their own session files
(``_activity``), as the Mac reads them. One session's responses form a
block until more than
``GAP_S`` of silence passes; blocks of one harness, account and project
then merge under the same gap, so parallel sessions in a repository read
as one stretch of work. Every Grok Bot session shares one project, "Grok
Bot", since its Bots pass one task between them.

``GET /v1/app/calendar?mode=week&anchor=2026-10-24&tz=Asia/Singapore
&first_weekday=1`` answers the visible range (``mode`` is ``day``,
``week``, ``month`` or ``days`` with ``n`` from 2 to 9) with buffer days
each side for the horizontal swipe, its blocks with
tokens and cost, the legend's totals and accounts, and the session rows a
block click opens. Read only. Lane layout, the hidden toggles and the
month cells are the page's (``static/pages/calendar.js``).
"""

from __future__ import annotations

import base64
import calendar as stdcal
import json
import math
import os
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path

from quiver.ledger.overview import Priced, zone
from quiver.paths import quiver_dir_for
from quiver.remote import catalog as catalog_mod
from quiver.sessions.parsers import codex_thread_id
from quiver.webapp import sessions as sessions_mod
from quiver.webapp.pages import _activity, tilde

# A project's responses read as one block until this much silence.
GAP_S = 30 * 60
# Responses are read this far past each edge, so a block straddling the
# range keeps its real start and end (CalendarModel.edgeLookout).
LOOKOUT_S = 24 * 3600
# Harnesses whose ledger events drive the calendar (Ledger.calendarHarnesses).
CALENDAR_HARNESSES = ("claude", "codex", "devin", "grok-bot", "opencode")
# A harness's own bookkeeping calls: a session of nothing else draws no block.
HELPER_MODELS = {"summarizer", "compactor"}
NO_FOLDER = "No folder"
MODES = ("day", "week", "month", "days")
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
# HarnessMark.names for the harnesses the calendar draws, and the account
# name rule (a Claude account is "Claude").
NAMES = {"claude": "Claude Code", "codex": "Codex", "devin": "Devin",
         "grok-bot": "Grok Bot", "opencode": "OpenCode", "cursor": "Cursor",
         "droid": "Factory Droid"}
GROK_SUBAGENT = "sand-subagent-"


def _name(harness: str) -> str:
    return NAMES.get(harness) or (harness[:1].upper() + harness[1:])


def _account_name(harness: str) -> str:
    return "Claude" if harness == "claude" else _name(harness)


# ---------------------------------------------------------------- range

def normalized(mode: str, n: int) -> tuple[str, int]:
    """``days`` under 2 reads as ``day`` and caps at 9; unknown is week."""
    if mode not in MODES:
        return "week", 7
    if mode == "days":
        if n <= 1:
            return "day", 1
        return "days", min(n, 9)
    return mode, {"day": 1, "week": 7, "month": 42}[mode]


def week_start(day: date, first_weekday: int) -> date:
    """The first day of ``day``'s week; ``first_weekday`` is ISO (1 Monday
    to 7 Sunday)."""
    return day - timedelta(days=(day.isoweekday() - first_weekday) % 7)


def visible_days(mode: str, n: int, anchor: date, first_weekday: int) -> list[date]:
    """The grid's days. A week starts on the first weekday; a month is
    always six week rows from the first weekday on or before the 1st, so
    adjacent-month days fill it; N days start on the anchor itself."""
    if mode == "day":
        start, count = anchor, 1
    elif mode == "week":
        start, count = week_start(anchor, first_weekday), 7
    elif mode == "month":
        start, count = week_start(anchor.replace(day=1), first_weekday), 42
    else:
        start, count = anchor, max(1, n)
    return [start + timedelta(days=i) for i in range(count)]


def buffer_days(mode: str, n: int) -> int:
    """Days the grid renders past each edge of the visible range, so a
    horizontal swipe shows painted columns before the page re-centres on
    where it came to rest (CalendarModel.renderRange, widened to a week
    so a day swipes freely). Whole weeks in week mode, which snaps by
    week; none in month mode, which does not slide."""
    return {"day": 7, "week": 14, "month": 0}.get(mode, max(7, n))


def add_months(day: date, count: int) -> date:
    """Calendar month arithmetic: the 31st lands on a shorter month's last day."""
    year, month = divmod(day.month - 1 + count, 12)
    year += day.year
    month += 1
    return day.replace(year=year, month=month,
                       day=min(day.day, stdcal.monthrange(year, month)[1]))


def shifted(mode: str, n: int, anchor: date, count: int) -> date:
    """The anchor moved by ``count`` mode steps."""
    if mode == "month":
        return add_months(anchor, count)
    step = {"day": 1, "week": 7}.get(mode, max(1, n))
    return anchor + timedelta(days=step * count)


def week_of_year(day: date, first_weekday: int) -> int:
    """``Calendar.component(.weekOfYear)``: ISO weeks for a Monday-first
    calendar (a first week needs four days of the year), and the week
    holding January 1 as week 1 for any other first weekday."""
    min_days = 4 if first_weekday == 1 else 1

    def first_week(year: int) -> date:
        jan1 = date(year, 1, 1)
        start = week_start(jan1, first_weekday)
        inside = 7 - (jan1 - start).days
        return start if inside >= min_days else start + timedelta(days=7)

    year = day.year
    if day >= first_week(year + 1):
        return 1
    start = first_week(year)
    if day < start:
        start = first_week(year - 1)
    return (day - start).days // 7 + 1


def empty_noun(mode: str, n: int) -> str:
    """The empty state's phrase (CalendarModel.emptyRangeNoun)."""
    return {"day": "today", "week": "this week", "month": "this month"}.get(
        mode, f"in these {n} days")


def _midnight(day: date, tz: tzinfo) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=tz).timestamp()


# ---------------------------------------------------------------- projects

def _pattern_root(cwd: str) -> str | None:
    """``SessionProjects.patternRoot``: the repository a worktree-shaped
    path names when no ``.git`` is left. ``~/.codex/worktrees`` holds
    unrelated repositories, so it names none."""
    parts = [p for p in cwd.split("/") if p]
    for i, part in enumerate(parts):
        if i == 0:
            continue
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        if part == ".codex" and nxt == "worktrees":
            return None
        if (part == ".claude" and nxt == "worktrees") or part in {".worktrees", "worktrees"}:
            return "/" + "/".join(parts[:i])
    return None


class Projects:
    """``ProjectResolver``: a folder's ``(key, name)``, memoised. The key
    is the repository root, so same-named folders of different
    repositories stay apart; the name is ``project_of``."""

    def __init__(self):
        self._cache: dict[str, tuple[str, str]] = {}

    def identity(self, cwd: str | None) -> tuple[str, str]:
        if not cwd or cwd == "/":
            return "", NO_FOLDER
        if cwd not in self._cache:
            root = sessions_mod._root(cwd)
            name = sessions_mod.project_of(cwd) or NO_FOLDER
            self._cache[cwd] = (root or _pattern_root(cwd) or cwd, name)
        return self._cache[cwd]


def _pick_folder(votes: dict[str, int]) -> str | None:
    """``SessionFolders.pick``: the folder most responses used; a Claude
    side-session notes folder only when nothing else is known; ties go to
    the folder seen later."""
    best = None
    for cwd, count in votes.items():
        if not cwd:
            continue
        notes = "/side-session-notes/" in cwd
        if best is None:
            best = (cwd, count, notes)
        elif best[2] != notes:
            if best[2]:
                best = (cwd, count, notes)
        elif count >= best[1]:
            best = (cwd, count, notes)
    return best[0] if best else None


# ---------------------------------------------------------------- blocks

_COUNTS = ("responses", "tokens", "cost_usd", "unpriced_tokens")


def _add_counts(into: dict, counts: dict) -> None:
    for k in _COUNTS:
        into[k] = into.get(k, 0) + counts[k]


def session_pieces(rows: list[tuple], priced: Priced, gap: float = GAP_S) -> list[dict]:
    """One block per stretch of a session's responses with no silence
    longer than ``gap``, with its tokens, cost and the folder most of its
    responses used, and the same counts per local day (``priced.tz``) so
    a month cell counts only its own day's share of an overnight block.
    ``rows`` come from ``LedgerStore.activity_events``."""
    pieces: list[dict] = []
    current = None
    day_of: dict[int, str] = {}  # 15-minute bucket to local date: zones shift by 15 minutes
    for (harness, account, session, cwd, model, tier, ts, inp, out, cr, cw, cw1h,
         reas, logged) in rows:
        same = (current is not None and current["harness"] == harness
                and current["account"] == account and current["session"] == session)
        if not same or ts - current["end"] > gap:
            current = {"harness": harness, "account": account, "session": session,
                       "start": ts, "end": ts, "responses": 0, "tokens": 0,
                       "cost_usd": 0.0, "unpriced_tokens": 0, "votes": {}, "days": {}}
            pieces.append(current)
        threshold = priced.thresholds.get(model)
        long = threshold is not None and inp + cr + cw > threshold
        cost, unpriced = priced.price(model, tier, long, logged is not None, inp, out, cr,
                                      cw, cw1h, reas, logged or 0.0)
        counts = {"responses": 1, "tokens": inp + out + cr + cw + reas,
                  "cost_usd": cost, "unpriced_tokens": unpriced}
        bucket = int(ts // 900)
        if bucket not in day_of:
            day_of[bucket] = datetime.fromtimestamp(bucket * 900, priced.tz).date().isoformat()
        current["end"] = ts
        _add_counts(current, counts)
        _add_counts(current["days"].setdefault(day_of[bucket], {}), counts)
        if cwd:
            current["votes"][cwd] = current["votes"].get(cwd, 0) + 1
    for piece in pieces:
        piece["cwd"] = _pick_folder(piece.pop("votes"))
    return pieces


def file_pieces(sessions: list[tuple[str, dict]], lo: float, hi: float, tz: tzinfo,
                gap: float = GAP_S) -> list[dict]:
    """``ActivityBlocks.make`` over each file source session's response
    times (``_activity.read``): a block until more than ``gap`` of
    silence, kept when it touches ``[lo, hi)`` with its real start and
    end. The whole history is read, so a block straddling an edge is
    never cut. File sources carry no tokens or spend, so those counts are
    zero and the page shows time and responses only, as the Mac does."""
    pieces: list[dict] = []
    day_of: dict[int, str] = {}

    def close(harness: str, session: dict, run: list[float]) -> None:
        if not run or run[-1] < lo or run[0] >= hi:
            return
        days: dict[str, dict] = {}
        for ts in run:
            bucket = int(ts // 900)
            if bucket not in day_of:
                day_of[bucket] = datetime.fromtimestamp(bucket * 900, tz).date().isoformat()
            _add_counts(days.setdefault(day_of[bucket], {}), _ONE_RESPONSE)
        pieces.append({"harness": harness, "account": session["account"],
                       "session": session["session"], "cwd": session["cwd"],
                       "start": run[0], "end": run[-1], "responses": len(run),
                       "tokens": 0, "cost_usd": 0.0, "unpriced_tokens": 0, "days": days})

    for harness, session in sessions:
        run: list[float] = []
        # A time past any calendar (an "infinite" stamp) never touches a
        # range on the Mac either.
        for ts in (t for t in session["timestamps"] if math.isfinite(t)):
            if run and ts - run[-1] > gap:
                close(harness, session, run)
                run = []
            run.append(ts)
        close(harness, session, run)
    return pieces


_ONE_RESPONSE = {"responses": 1, "tokens": 0, "cost_usd": 0.0, "unpriced_tokens": 0}


def _ref(piece: dict) -> dict:
    return {"id": piece["session"], "cwd": piece["cwd"], "start": piece["start"],
            "end": piece["end"], "responses": piece["responses"], "tokens": piece["tokens"],
            "cost_usd": piece["cost_usd"], "unpriced_tokens": piece["unpriced_tokens"],
            "days": {d: dict(c) for d, c in piece["days"].items()}}


def _union_refs(refs: list[dict], more: list[dict]) -> list[dict]:
    """Session refs by id: a session in both keeps its earliest start and
    latest end, and its counts sum."""
    out = {r["id"]: dict(r) for r in refs}
    for ref in more:
        have = out.get(ref["id"])
        if have is None:
            out[ref["id"]] = dict(ref)
            continue
        have["start"] = min(have["start"], ref["start"])
        have["end"] = max(have["end"], ref["end"])
        _add_counts(have, ref)
        days = {d: dict(c) for d, c in have["days"].items()}
        for d, c in ref["days"].items():
            _add_counts(days.setdefault(d, {}), c)
        have["days"] = days
        have["cwd"] = have["cwd"] or ref["cwd"]
    return sorted(out.values(), key=lambda r: (r["start"], r["id"]))


def group_by_project(pieces: list[dict], projects: Projects, gap: float = GAP_S) -> list[dict]:
    """``ProjectResolver.group``: per-session blocks re-keyed by harness,
    account and project, merged while the next starts within ``gap`` of
    the running end. Every Grok Bot session is the "Grok Bot" project."""
    groups: dict[tuple[str, str, str], tuple[str, list[dict]]] = {}
    for piece in pieces:
        if piece["harness"] == "grok-bot":
            key, name = "grok-bot", _name("grok-bot")
        else:
            key, name = projects.identity(piece["cwd"])
        group = groups.setdefault((piece["harness"], piece["account"], key), (name, []))
        group[1].append(piece)
    blocks: list[dict] = []
    for (harness, account, key), (name, members) in groups.items():
        merged: list[dict] = []
        for piece in sorted(members, key=lambda p: (p["start"], p["session"])):
            last = merged[-1] if merged else None
            if last is not None and piece["start"] - last["end"] <= gap:
                last["end"] = max(last["end"], piece["end"])
                _add_counts(last, piece)
                last["sessions"] = _union_refs(last["sessions"], [_ref(piece)])
            else:
                merged.append({
                    "harness": harness, "account": account, "project": name,
                    "project_key": key, "start": piece["start"], "end": piece["end"],
                    "responses": piece["responses"], "tokens": piece["tokens"],
                    "cost_usd": piece["cost_usd"], "unpriced_tokens": piece["unpriced_tokens"],
                    "sessions": [_ref(piece)],
                })
        blocks += merged
    for block in blocks:
        block["id"] = (f"{block['harness']}|{block['account']}|"
                       f"{block['project_key'] or block['project']}|{block['start']}")
    return sorted(blocks, key=lambda b: (b["start"], b["id"]))


def is_real(harness: str, usage: dict | None) -> bool:
    """A session that draws blocks: not one of only helper calls, and
    not an accidental one (``SessionFilter``), both judged on its whole
    stored history. A session the ledger has no history for stays."""
    if usage is None:
        return True
    models = [m["model"] for m in usage.get("models") or []]
    if models and all(m in HELPER_MODELS for m in models):
        return False
    return not sessions_mod.is_trivial(harness, usage, True)


def union_seconds(spans: list[tuple[float, float]]) -> float:
    """Wall-clock time the spans cover, parallel ones counted once."""
    total, held = 0.0, None
    for start, end in sorted(spans):
        if held is not None and start <= held[1]:
            held = (held[0], max(held[1], end))
            continue
        if held is not None:
            total += held[1] - held[0]
        held = (start, end)
    if held is not None:
        total += held[1] - held[0]
    return total


def totals(blocks: list[dict], start: float, end: float, key) -> dict[str, float]:
    """Seconds per ``key(block)`` inside ``[start, end)``, each block
    clipped first. Legend totals read every block, hidden ones too."""
    spans: dict[str, list[tuple[float, float]]] = {}
    for block in blocks:
        name = key(block)
        if name is None or not (block["end"] > start and block["start"] < end):
            continue
        spans.setdefault(name, []).append((max(block["start"], start), min(block["end"], end)))
    return {name: union_seconds(s) for name, s in spans.items()}


# ---------------------------------------------------------------- accounts

def _profiles(home: Path | None) -> list[dict]:
    """``~/.quiver/accounts/profiles.json``, read only, with the Claude and
    Codex defaults the app synthesises when the file lacks them."""
    stored: list[dict] = []
    path = quiver_dir_for(home) / "accounts" / "profiles.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("profiles") if isinstance(data, dict) else None
        stored = [p for p in raw or [] if isinstance(p, dict)
                  and isinstance(p.get("id"), str) and isinstance(p.get("vendor"), str)]
    except (OSError, ValueError):
        stored = []
    out = list(stored)
    for vendor in ("claude", "codex"):
        if not any(p.get("isDefault") and p["vendor"] == vendor for p in stored):
            out.append({"id": f"{vendor}-default", "vendor": vendor, "name": "Default",
                        "isDefault": True})
    return out


class Accounts:
    """``AccountDirectory``: what to call an account home, and the homes
    each harness can have, so a quiet account still lists in the legend."""

    def __init__(self, home: Path | None):
        user = Path(home) if home is not None else Path.home()
        root = quiver_dir_for(home) / "accounts"
        self.user = str(user)
        self.names: dict[str, str] = {}
        self.homes: dict[str, list[str]] = {
            "claude": [str(user / ".claude")],
            "codex": [str(user / ".codex")],
            "devin": [str(user / ".local/share/devin/cli")],
        }
        for p in _profiles(home):
            vendor = p["vendor"]
            if p.get("isDefault"):
                if vendor not in ("claude", "codex"):
                    continue
                path = str(user / (".codex" if vendor == "codex" else ".claude"))
            else:
                path = str(root / vendor / p["id"] / "home")
                if vendor in self.homes:
                    self.homes[vendor].append(path)
            self.names[os.path.normpath(path)] = f"{_account_name(vendor)}: {p.get('name') or p['id']}"
        self.owner = {os.path.normpath(h): harness
                      for harness, homes in self.homes.items() for h in reversed(homes)}

    def label(self, account: str) -> str:
        path = os.path.normpath(account) if account else account
        if path in self.names:
            return self.names[path]
        if path in self.owner:
            return _account_name(self.owner[path])
        if path == os.path.join(self.user, ".local/share/opencode"):
            return _name("opencode")
        return tilde(path, self.user) if path else path


# ---------------------------------------------------------------- titles

# Grok Bot's own persistence folder (Application Support, under the
# hub's home): ``*.blob`` files named by the lowercase unpadded base32 of
# a key. The Bot list is the one whose key ends with this suffix.
_GROK_STORE = "Library/Application Support/Grok Bot/sand-client-persistence"
_GROK_ROSTER = ".roster.last-roster"


def _grok_key(stem: str) -> str | None:
    try:
        raw = base64.b32decode(stem.upper() + "=" * (-len(stem) % 8))
        return raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _grok_names(home: Path | None) -> dict[str, str]:
    """Bot id -> name from Grok Bot's own roster, as ``GrokBotRoster``
    reads it: only the roster blob is opened (no transcript or secret
    blob), and only each row's ``id`` and ``name``. The Mac app's memory
    of deleted Bots is its own store and stays unread, so a deleted Bot
    titles by its id here. Anything unreadable is an empty map."""
    folder = (Path(home) if home is not None else Path.home()) / _GROK_STORE
    try:
        blobs = [p for p in folder.iterdir() if p.suffix == ".blob"
                 and (_grok_key(p.stem) or "").endswith(_GROK_ROSTER)]
        roster = max(blobs, key=lambda p: p.stat().st_mtime) if blobs else None
        data = json.loads(roster.read_text(encoding="utf-8")) if roster else None
    except (OSError, ValueError):
        return {}
    value = data.get("value") if isinstance(data, dict) else None
    rows = value.get("rows") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        return {}
    return {r["id"]: r["name"] for r in rows
            if isinstance(r, dict) and isinstance(r.get("id"), str)
            and isinstance(r.get("name"), str)}


def session_title(title: str | None, session_id: str, harness: str,
                  grok: dict[str, str] | None = None) -> str:
    """``CalendarFormat.sessionTitle``: the title, else a Grok Bot's name,
    else the id, whole when readable and cut to eight characters when it
    looks like a UUID."""
    if title:
        return title
    if harness == "grok-bot":
        name = (grok or {}).get(session_id)
        if name:
            return name
        if session_id.startswith(GROK_SUBAGENT):
            return "Subagent"
    bare = session_id.replace("-", "")
    uuid_like = len(session_id) > 24 or (
        len(bare) == 32 and all(c in "0123456789abcdefABCDEF" for c in bare))
    return session_id[:8] + "…" if uuid_like else session_id


# ---------------------------------------------------------------- page

def _int(text: str | None, default: int) -> int:
    try:
        return int(text or "")
    except ValueError:
        return default


def get(ctx, args: dict) -> dict:
    tz = zone(args.get("tz") or None)
    now = datetime.now(tz)
    today = now.date()
    mode, n = normalized(args.get("mode") or "week", _int(args.get("n"), 7))
    try:
        anchor = date.fromisoformat(args.get("anchor") or "")
    except ValueError:
        anchor = today
    if not 1971 <= anchor.year <= 9998:
        # Far enough from date's limits that a week or month step never overflows.
        anchor = today
    first_weekday = _int(args.get("first_weekday"), 1)
    if not 1 <= first_weekday <= 7:
        first_weekday = 1
    days = visible_days(mode, n, anchor, first_weekday)
    bounds = [_midnight(d, tz) for d in days] + [_midnight(days[-1] + timedelta(days=1), tz)]
    start, end = bounds[0], bounds[-1]
    pad = buffer_days(mode, n)
    render_days = [days[0] + timedelta(days=i) for i in range(-pad, len(days) + pad)]
    render_bounds = ([_midnight(d, tz) for d in render_days]
                     + [_midnight(render_days[-1] + timedelta(days=1), tz)])
    render_start, render_end = render_bounds[0], render_bounds[-1]
    if mode == "month":
        first = anchor.replace(day=1)
        totals_range = (_midnight(first, tz), _midnight(add_months(first, 1), tz))
    else:
        totals_range = (start, end)

    rescan = (args.get("rescan") or "") in {"1", "true"}
    with ctx.state.webapp.ledger(rescan=rescan) as (store, catalog, scan_ok):
        priced = Priced(store, catalog, tz)
        rows = store.activity_events(render_start - LOOKOUT_S, render_end + LOOKOUT_S,
                                     list(CALENDAR_HARNESSES))
        usage = ctx.state.webapp.session_usage()
        file_stats = {h: store.activity_session_stats(h) for h in _activity.TRIVIAL_FILTERED}
        scanned_at = store.meta("scanned_at")
    pieces = [p for p in session_pieces(rows, priced)
              if is_real(p["harness"], usage.get((p["harness"], p["session"])))]
    pieces += file_pieces(_activity.read(ctx.home, file_stats, sessions_mod.is_trivial),
                          render_start - LOOKOUT_S, render_end + LOOKOUT_S, tz)
    blocks = [b for b in group_by_project(pieces, Projects())
              if b["end"] >= render_start and b["start"] < render_end]

    accounts = Accounts(ctx.home)
    window = [b for b in blocks if b["start"] < end
              and (b["end"] > start or (b["start"] == b["end"] and b["start"] >= start))]
    lo, hi = totals_range
    by_harness = totals(blocks, lo, hi, lambda b: b["harness"])
    legend = []
    for harness in sorted({b["harness"] for b in window}):
        homes = sorted({b["account"] for b in window if b["harness"] == harness}
                       | set(accounts.homes.get(harness, [])))
        per_account = totals(blocks, lo, hi,
                             lambda b, h=harness: b["account"] if b["harness"] == h else None)
        legend.append({
            "harness": harness, "total_s": by_harness.get(harness, 0.0),
            "accounts": [{"home": home, "label": accounts.label(home),
                          "total_s": per_account.get(home, 0.0)} for home in homes],
        })
    labels = {b["account"]: accounts.label(b["account"]) for b in blocks}
    for block in blocks:
        block["account_label"] = labels[block["account"]]

    sessions = _session_rows(ctx, blocks, usage, scan_ok)
    return {
        "mode": mode, "n": n, "anchor": anchor.isoformat(), "today": today.isoformat(),
        "first_weekday": first_weekday,
        "time_zone": str(getattr(tz, "key", "") or now.tzname() or ""),
        "title": f"{MONTHS[anchor.month - 1]} {anchor.year}",
        "subtitle": "" if mode == "month" else f"Week {week_of_year(days[0], first_weekday)}",
        "empty": f"No harness activity {empty_noun(mode, n)}",
        "previous": shifted(mode, n, anchor, -1).isoformat(),
        "next": shifted(mode, n, anchor, 1).isoformat(),
        "range": {"start": start, "end": end},
        "totals_range": {"start": lo, "end": hi},
        "days": [{"date": d.isoformat(), "start": bounds[i], "end": bounds[i + 1]}
                 for i, d in enumerate(days)],
        # The visible days with the buffer each side: the columns a
        # horizontal swipe scrolls through. Blocks cover all of them.
        "render_days": [{"date": d.isoformat(), "start": render_bounds[i], "end": render_bounds[i + 1]}
                        for i, d in enumerate(render_days)],
        "gap_s": GAP_S,
        "blocks": blocks,
        "legend": legend,
        "sessions": sessions,
        "scanned_at": float(scanned_at) if scanned_at else None,
        "notes": [] if scan_ok else
        ["Some log files couldn't be read in the last scan, so blocks may be missing."],
        "machine": ctx.machine.get("name"),
    }


def _session_rows(ctx, blocks: list[dict], usage: dict, scan_ok: bool) -> dict[str, dict]:
    """The Sessions page's row for every session a block holds, keyed by
    the catalog row's key, so a click opens the same detail sheet. Each
    ref gets ``row`` (that key) and its ``title``. A session gone from the
    listing gets a stand-in row built from the ledger, as the app does."""
    hub = ctx.state.machine_id
    listed = [r for r in ctx.state.catalog.rows()
              if r.get("machine_id") == hub and not r.get("remote")]
    by_id = {(r.get("harness"), r.get("id")): r for r in listed}
    threads = {codex_thread_id(r.get("id") or ""): r for r in listed
               if r.get("harness") == "codex" and codex_thread_id(r.get("id") or "")}
    grok = _grok_names(ctx.home) if any(b["harness"] == "grok-bot" for b in blocks) else {}
    chosen: dict[str, dict] = {}
    for block in blocks:
        harness = block["harness"]
        for ref in block["sessions"]:
            sid = ref["id"]
            row = by_id.get((harness, sid))
            if row is None and harness == "codex":
                row = threads.get(codex_thread_id(sid))
            if row is None:
                row = {"key": f"calendar|{harness}|{block['account']}|{sid}", "id": sid,
                       "harness": harness, "agent": _name(harness), "title": "",
                       "cwd": ref["cwd"], "last_active_at": ref["end"], "machine_id": hub,
                       "remote": False, "resume": None, "status": None}
            chosen.setdefault(row["key"], row)
            ref["row"] = row["key"]
            ref["title"] = session_title(row.get("title"), sid, harness, grok)
    machines = catalog_mod.load_machines(ctx.home)
    built = sessions_mod.build(list(chosen.values()), usage, hub, machines,
                               scanned=scan_ok, home=ctx.home)
    return {r["key"]: r for r in built}
