"""Registry records and ``--json`` payload models for the harness domain.

One ``to_json()`` per model is the single definition of each row the
``swe`` JSON envelope carries. ``RateLimitInfo`` lives here (imported from
``swe.harness.rate_limits`` for compatibility) because it is the
domain's record, not fetcher machinery.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from swe.console import c


def display_name(info: dict) -> str | None:
    """The harness's human name ("Qwen Code"), or None when unset or blank."""
    value = info.get("display_name")
    return value.strip() or None if isinstance(value, str) else None


def primary_color(name: str, info: dict) -> str | None:
    """The entry's own ``primary_color``; the catalog default fills in at read time.

    An entry without the field must not be rewritten just to gain the
    catalog's colour, so the fallback resolves here rather than on load.
    """
    from swe.harness.catalog import HARNESS_CATALOG

    return info.get("primary_color") or HARNESS_CATALOG.get(name, {}).get("primary_color")


@dataclass(frozen=True)
class HarnessRow:
    """One ``swe list --json`` row."""

    name: str
    display_name: str | None
    command: str
    description: str
    version: str | None
    notes: str | None
    aliases: list
    tags: list
    primary_color: str | None
    state: str
    sessions_100d: int | None
    rate_limit: "RateLimitInfo | None"
    installed: bool
    archived: dict | None
    # Why ``rate_limit`` is null: "signed_out", "error", or None when there
    # is a reading or no reason is known (see ``get_all_rate_limits``).
    no_reading: str | None = None
    # The actionable version of the same verdict: {"status", "remedy"}
    # from the remedies vocabulary, or None on a healthy row.
    issue: dict | None = None

    @classmethod
    def of(cls, name: str, info: dict, starred, archived: dict,
           counts: dict, rate, no_reading: str | None = None,
           issue_status: str | None = None) -> "HarnessRow":
        from swe.harness.remedies import remedy_for
        from swe.harness.tools import is_installed

        # Sentinel plan types are a status, not a plan: they carry the
        # issue a healthy reading would not. ``issue_status`` applies only
        # to a row with no reading — it is why the reading is missing.
        sentinel = (rate.plan_type
                    if rate is not None
                    else issue_status)
        if sentinel not in ("auth-required", "timeout",
                            "signed-out", "permission-required",
                            "unavailable", "no-sub"):
            sentinel = None
        if sentinel == "no-sub":
            # A plan state, not a failure: the card words it already.
            sentinel = None
        issue = ({"status": sentinel, "remedy": remedy_for(name, sentinel)}
                 if sentinel is not None else None)
        if name in archived:
            state = "archived"
        elif name in starred:
            state = "starred"
        else:
            state = "active"
        return cls(
            name=name,
            display_name=display_name(info),
            command=info.get("command") or name,
            description=info.get("description") or "",
            version=info.get("version") or None,
            notes=info.get("notes") or None,
            aliases=[a for a in info.get("aliases", []) if a != name],
            tags=list(info.get("tags", [])),
            primary_color=primary_color(name, info),
            state=state,
            sessions_100d=counts.get(name),
            rate_limit=rate,
            installed=is_installed(info.get("command") or name),
            archived=archived.get(name),
            no_reading=None if rate is not None else no_reading,
            issue=issue,
        )

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "command": self.command,
            "description": self.description,
            "version": self.version,
            "notes": self.notes,
            "aliases": self.aliases,
            "tags": self.tags,
            "primary_color": self.primary_color,
            "state": self.state,
            "sessions_100d": self.sessions_100d,
            "rate_limit": self.rate_limit.to_json() if self.rate_limit is not None else None,
            "installed": self.installed,
            "archived": self.archived,
            "no_reading": self.no_reading,
            "issue": self.issue,
        }


@dataclass(frozen=True)
class DoctorFinding:
    """One ``swe doctor --json`` row. ``harness`` names the registry
    harness a finding is about, so the app can show its logo; it is left
    out of rows that are about the machine rather than one harness.
    ``action``, when set, is the swe command the app may offer to run:
    "setup", "init", "discover", or "install" (``swe install <harness>
    --via=<via>``, reinstalling an off-PATH harness the way it got there).

    ``source`` names the file or place the finding comes from
    ("~/.claude/settings.json", "PATH order"), and ``remedies`` the ways
    out, best first: each is a dict ``{kind, title, detail?, ...}`` where
    ``kind`` is "action" (a fix swe can run itself, with ``action``/``via``
    fields like the finding's own), "agent" (a ``prompt`` to hand a coding
    agent), "command" (a ``command`` to copy) or "steps" (manual work,
    ``steps`` listed). ``fix`` stays the one-line summary readers without
    remedy support show."""

    check: str
    severity: str
    message: str
    fix: str | None = None
    harness: str | None = None
    action: str | None = None
    via: str | None = None
    source: str | None = None
    remedies: tuple | None = None

    def to_json(self) -> dict:
        finding = {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "fix": self.fix,
        }
        if self.harness:
            finding["harness"] = self.harness
        if self.action:
            finding["action"] = self.action
        if self.via:
            finding["via"] = self.via
        if self.source:
            finding["source"] = self.source
        if self.remedies:
            finding["remedies"] = [dict(r) for r in self.remedies]
        return finding


@dataclass(frozen=True)
class DoctorReport:
    """The ``swe doctor --json`` payload: ``ok`` mirrors the exit status."""

    findings: list

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def to_json(self) -> dict:
        return {"ok": self.ok, "findings": [f.to_json() for f in self.findings]}


@dataclass(frozen=True)
class StateReply:
    """The ``swe star`` reply: the harness's new registry state."""

    name: str
    state: str

    def to_json(self) -> dict:
        return {"name": self.name, "state": self.state}


@dataclass(frozen=True)
class ArchiveReply:
    """The ``swe archive``/unarchive reply, carrying the archive entry."""

    name: str
    state: str
    archived: dict | None

    def to_json(self) -> dict:
        return {"name": self.name, "state": self.state, "archived": self.archived}


@dataclass(frozen=True)
class EditReply:
    """The ``swe edit --json`` reply: the fields changed plus the new row."""

    name: str
    changes: list
    harness: dict

    def to_json(self) -> dict:
        return {"name": self.name, "changes": self.changes, "harness": self.harness}


# ---------------------------------------------------------------------------
# Rate-limit record
# ---------------------------------------------------------------------------

# One-glyph pie: five fill steps, colour does the fine-grained work on a
# red→green xterm ramp (196 = red, 226 = yellow, 46 = green).
_PIE_GLYPHS = "○◔◑◕●"
_PIE_RAMP = (196, 202, 208, 214, 220, 226, 190, 154, 118, 46)


def _pie(remaining_percent: int) -> str:
    """A one-cell pie glyph, red when empty through green when full."""
    remaining_percent = max(0, min(100, remaining_percent))
    glyph = _PIE_GLYPHS[round(remaining_percent / 25)]
    code = _PIE_RAMP[round(remaining_percent / 100 * (len(_PIE_RAMP) - 1))]
    return f"\033[38;5;{code}m{glyph}\033[0m"


def _window(
    label: str,
    used: float,
    reset_at: float,
    window_seconds: int,
    unavailable: bool = False,
) -> dict:
    """One quota window, as ``RateLimitInfo.windows`` holds it.

    ``unavailable`` marks a window the provider should report but did not
    answer this time (the call failed or missed its budget), distinct from
    a window the provider legitimately has none of. The caller fills the
    figures in from the last saved reading; ``as_of`` is that reading's
    date. Normal windows carry neither key.
    """
    window = {
        "window": label,
        "used_percent": int(round(min(100.0, max(0.0, used)))),
        "limit_reached": used >= 100,
        "reset_at": reset_at,
        "window_seconds": window_seconds,
    }
    if unavailable:
        window["unavailable"] = True
    return window


@dataclass
class RateLimitInfo:
    """Normalised rate limit data for a single tool."""

    tool_name: str
    used_percent: int          # 0-100
    limit_reached: bool
    reset_at: float            # epoch seconds, 0 if unknown
    plan_type: str             # e.g. "plus", "individual/edu", "—"
    window_seconds: int        # e.g. 604800 for weekly, 0 if unknown
    window: str = ""           # e.g. "5h"/"7d"/"7d_sonnet" — Claude returns
                                # 3 windows in one request; we surface the
                                # most-restrictive (highest utilization) and
                                # label it via this short abbreviation in
                                # format_column(). Empty for Codex / Copilot
                                # (single-window response). Default keeps
                                # all existing call sites + cache
                                # reconstruction (RateLimitInfo(**raw))
                                # backward-compatible.
    remaining_units: float | None = None
    total_units: float | None = None
    # Every window the provider reported, each a ``_window()`` dict, for
    # tools with more than one (Devin's daily and weekly, Cursor's included,
    # auto and API). The fields above stay the most-used one, which the
    # table shows; the app lists them all. Empty for single-window tools.
    windows: list = field(default_factory=list)
    # Prepaid usage left beyond the plan (Devin's overage balance), in USD.
    extra_usage_usd: float | None = None
    # Usage billed beyond the plan, as ``_cursor_on_demand()`` describes it.
    on_demand: dict | None = None
    # The sign-in's own address, where the credential files or the usage
    # reply carry one (Claude's .claude.json, Codex's id_token, Cursor's
    # state db, Devin's userStatus). Identity, not usage: kept on
    # auth-required readings too, so a signed-out card can still say who.
    email: str | None = None
    fetched_at: float = 0.0    # when the reading was actually taken;
                               # 0 means "just now", the common case.
                               # Lets a fetcher hand back a saved reading
                               # (Claude's refresh-pending path) without
                               # the aggregator stamping it fresh.

    def to_json(self) -> dict:
        """The ``swe list --usage --json`` shape.

        The sentinel plan types become ``status`` so the app never shows
        "no-sub" as a plan name.
        """
        status = (self.plan_type
                  if self.plan_type in ("no-sub", "auth-required", "timeout")
                  else "ok")
        return {
            "status": status,
            "used_percent": self.used_percent,
            "remaining_percent": self.remaining_percent,
            "limit_reached": self.limit_reached,
            "reset_at": self.reset_at or None,
            "window": self.window or None,
            "window_seconds": self.window_seconds or None,
            # "—" is the table's placeholder for a provider that names no
            # plan; in JSON that is null, not a plan called "—".
            "plan_type": (None if status != "ok" or self.plan_type in ("", "—", "-")
                          else self.plan_type),
            "remaining_units": self.remaining_units,
            "total_units": self.total_units,
            "windows": [
                {
                    "window": w["window"],
                    "used_percent": w["used_percent"],
                    "remaining_percent": 100 - w["used_percent"],
                    "limit_reached": w["limit_reached"],
                    "reset_at": w["reset_at"] or None,
                    "window_seconds": w["window_seconds"] or None,
                    # Unavailable/carried keys appear only when set, same
                    # as in the cache dict; the app reads both as optional.
                    **({"unavailable": True} if w.get("unavailable") else {}),
                    **({"as_of": w["as_of"]} if w.get("as_of") else {}),
                }
                for w in self.windows
            ] if status == "ok" else [],
            "extra_usage_usd": self.extra_usage_usd if status == "ok" else None,
            "on_demand": self.on_demand if status == "ok" else None,
            # Identity stays on a failed reading: a re-login card still
            # names the account it is about.
            "email": self.email,
            "fetched_at": self.fetched_at or None,
        }

    @property
    def remaining_percent(self) -> int:
        """Percentage of the selected quota window still available."""
        return max(0, min(100, 100 - self.used_percent))

    @property
    def reset_in_human(self) -> str:
        """Human-readable reset countdown (e.g. '5d12h', '3h45m')."""
        if self.reset_at <= 0:
            return "—"
        remaining = self.reset_at - time.time()
        if remaining <= 0:
            return "now"
        days = int(remaining // 86400)
        hours = int((remaining % 86400) // 3600)
        minutes = int((remaining % 3600) // 60)
        if days > 0:
            return f"{days}d{hours}h"
        if hours > 0:
            return f"{hours}h{minutes}m"
        return f"{minutes}m"

    def format_column(self) -> str:
        """One-line display string for the ``swe list`` column."""
        # Sentinel plan type for tools whose rate-limit endpoint is
        # intentionally not polled (e.g. Claude with no subscription).
        # Renders a compact label instead of a misleading "0% —" figure.
        if self.plan_type == "no-sub":
            return c("dim", "no-sub")
        if self.plan_type == "auth-required":
            return c("red", "re-login")
        if self.plan_type == "timeout":
            return c("yellow", "…")
        remaining = self.remaining_percent
        if self.remaining_units is not None and self.total_units is not None:
            def compact(value: float) -> str:
                return (
                    str(int(value)) if value.is_integer()
                    else f"{value:.1f}".rstrip("0").rstrip(".")
                )

            tail = (
                f"{compact(float(self.remaining_units))}/"
                f"{compact(float(self.total_units))}"
            )
            reset = self.reset_in_human
            # Unit quotas still carry a reset (Devin's planEnd,
            # Freebuff's resetAt), so it rides the tail when known.
            if reset != "—":
                tail += f" {reset}"
        else:
            reset = self.reset_in_human
            # Compact form (`5h`, `7d`, `7ds`) keeps the column narrow
            # while letting the user tell which window the pie is
            # drawn from. Examples:
            #     ◔ 5h:3h12m  ← most-restrictive is the 5h rolling
            #     ○ 7d:4d3h   ← most-restrictive is the weekly
            tail = f"{self.window}:{reset}" if self.window else reset
        # Blocked trumps the percentage when a provider derives the two
        # independently (Droid averages budgets, Copilot derives it
        # separately): a reached limit renders the empty red pie.
        return f"{_pie(0 if self.limit_reached else remaining)} {c('dim', tail)}"


@dataclass(frozen=True)
class DiscoveryRow:
    """One ``swe harness discover --json`` row (a bare list, no envelope)."""

    name: str
    command: str
    path: str
    confidence: str
    source: str
    status: str
    description: str
    tags: list
    aliases: list
    primary_color: str | None

    @classmethod
    def of(cls, f) -> "DiscoveryRow":
        return cls(
            name=f.name,
            command=f.command,
            path=f.path,
            confidence=f.confidence,
            source=f.source,
            status=f.status,
            description=f.description,
            tags=list(f.tags),
            aliases=list(f.aliases),
            primary_color=f.primary_color or None,
        )

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "command": self.command,
            "path": self.path,
            "confidence": self.confidence,
            "source": self.source,
            "status": self.status,
            "description": self.description,
            "tags": self.tags,
            "aliases": self.aliases,
            "primary_color": self.primary_color,
        }
