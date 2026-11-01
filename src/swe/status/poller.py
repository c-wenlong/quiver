"""Reads the status pages politely, and remembers what they said.

A port of the Mac app's ``QuiverCore/Status/ServiceStatusService.swift``:
each page at most every five minutes (one minute for a manual refresh),
conditional requests so an unchanged page costs a 304, and a provider's
``Retry-After`` honoured. Readings are kept in
``~/.quiver/cache/web-service-status.json`` (apart from the Mac app's own
file) so a hub restart does not poll again, and a reading older than an
hour is never shown as current.

Two things differ from the app because a web request is waiting on the
answer: every request has its own timeout, and ``statuses`` waits at most
``wait`` seconds in all. A page still answering after that is left to
finish in the background and fills its card on the next load.
"""

from __future__ import annotations

import datetime
import email.utils
import http.client
import json
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from swe.paths import atomic_write_text, cache_dir_for, locked_beside
from swe.status.catalog import SERVICES, StatusService
from swe.status.parsers import Report, google_cloud, statuspage


CACHE_NAME = "web-service-status.json"
INTERVAL = 300.0       # the least time between two requests to a page
MANUAL_FLOOR = 60.0    # the same, for a manual refresh
STALE_AFTER = 3600.0   # a reading older than this is not shown
TIMEOUT = 8.0          # one request
WAIT = 10.0            # all of a load's requests together
DOWNLOAD_LIMIT_FACTOR = 2  # a whole download, in timeouts
USER_AGENT = "Quiver (hub)"
# Google Cloud's incidents.json holds a year of incidents; nothing real
# comes near this, and a runaway body must not fill the hub's memory.
MAX_BODY = 32 * 1024 * 1024


def cache_file_for(home: Path | None = None) -> Path:
    return cache_dir_for(home) / CACHE_NAME


# ----------------------------------------------------------------- transport

@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None


# ``transport(url, headers, timeout) -> Response``; raises OSError when the
# page could not be reached at all.
Transport = Callable[[str, Mapping[str, str], float], Response]


def http_get(url: str, headers: Mapping[str, str], timeout: float) -> Response:
    """One GET, with the TLS fallback ``harness/rate_limits.py`` uses: a
    python.org build on macOS ships without CA certificates, so a failed
    verification is retried once with the OS trust store (or certifi).
    Verification is never turned off."""
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        return _open(request, timeout, None)
    except urllib.error.URLError as exc:  # before OSError: URLError is one
        if not isinstance(exc.reason, ssl.SSLError):
            raise
    except ssl.SSLError:
        pass
    from swe.harness.rate_limits import _verified_context
    context = _verified_context()
    if context is None:
        raise OSError("no trusted CA bundle")
    return _open(request, timeout, context)


def _open(request: urllib.request.Request, timeout: float, context: ssl.SSLContext | None) -> Response:
    # ``timeout`` bounds each socket wait; this bounds the whole download,
    # so a feed that trickles bytes cannot hold a check (and every load
    # that joins it) open for hours.
    deadline = time.monotonic() + DOWNLOAD_LIMIT_FACTOR * timeout
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as resp:
            return Response(resp.status, read_body(resp, deadline), dict(resp.headers.items()))
    except urllib.error.HTTPError as exc:  # 304, 429, 5xx: an answer, not a failure
        try:
            body = read_body(exc, deadline)
        except OSError:
            body = b""
        return Response(exc.code, body, dict(exc.headers.items()) if exc.headers else {})


def read_body(resp: Any, deadline: float, chunk: int = 64 * 1024) -> bytes:
    """The body, up to ``MAX_BODY``; TimeoutError once ``deadline``
    (``time.monotonic()``) passes before it ends."""
    parts: list[bytes] = []
    size = 0
    while size < MAX_BODY:
        if time.monotonic() > deadline:
            raise TimeoutError("the status page took too long to send its feed")
        data = resp.read(min(chunk, MAX_BODY - size))
        if not data:
            break
        parts.append(data)
        size += len(data)
    return b"".join(parts)


def retry_after(value: str | None, now: float) -> float | None:
    """The moment a ``Retry-After`` value points at: delay seconds or an
    HTTP date. None when absent or unreadable."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        pass
    else:
        if seconds == seconds and seconds not in (float("inf"), float("-inf")):
            return now + max(0.0, seconds)
        return None
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when.timestamp()


# ---------------------------------------------------------------- the cache

@dataclass
class Entry:
    """What one page last said, and when to ask again."""

    report: Report | None = None
    # When the page last answered with a reading (200 or 304).
    checked_at: float | None = None
    # When a request was last sent, answered or not.
    attempted_at: float | None = None
    etag: str | None = None
    last_modified: str | None = None
    cooldown_until: float | None = None
    # Why the last attempt failed, if it did. Never shown as an outage.
    problem: str | None = None
    # For "asked us to wait until": the time, which the browser formats in
    # its own zone (the Mac app writes it into the text).
    problem_until: float | None = None
    # Set instead of a reading when the page was untracked: a tombstone
    # that outlives this process, so another hub sharing the cache cannot
    # land or serve a reading taken before the removal.
    removed_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "report": self.report.to_dict() if self.report else None,
            "checked_at": self.checked_at, "attempted_at": self.attempted_at,
            "etag": self.etag, "last_modified": self.last_modified,
            "cooldown_until": self.cooldown_until,
            "problem": self.problem, "problem_until": self.problem_until,
            "removed_at": self.removed_at,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Entry | None":
        if not isinstance(raw, dict):
            return None

        def number(key: str) -> float | None:
            value = raw.get(key)
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

        def text(key: str) -> str | None:
            value = raw.get(key)
            return value if isinstance(value, str) else None

        return cls(
            report=Report.from_dict(raw.get("report")),
            checked_at=number("checked_at"), attempted_at=number("attempted_at"),
            etag=text("etag"), last_modified=text("last_modified"),
            cooldown_until=number("cooldown_until"),
            problem=text("problem"), problem_until=number("problem_until"),
            removed_at=number("removed_at"),
        )


def newer(mine: Entry | None, theirs: Entry | None) -> Entry | None:
    """Of two entries for one page, the one from the later attempt or
    removal: another hub on this home may have saved a newer reading or
    cooldown, or removed the page since an older attempt started."""
    if mine is None:
        return theirs
    if theirs is None:
        return mine
    key = lambda e: max(e.attempted_at or 0, e.removed_at or 0)
    # A removal beats a reading of the same stamp: the reading can always
    # be fetched again, the untrack was a deliberate act.
    if key(theirs) > key(mine) or (key(theirs) == key(mine)
                                  and theirs.removed_at is not None
                                  and mine.removed_at is None):
        return theirs
    return mine


@dataclass
class ServiceStatus:
    """What is known about one service right now."""

    service: StatusService
    # None when there has been no reading in the last hour.
    report: Report | None = None
    # When the page last answered. A reused reading keeps its own time.
    checked_at: float | None = None
    problem: str | None = None
    problem_until: float | None = None
    # True when ``report`` is an older reading shown because this check failed.
    stale: bool = False


class _Flight:
    """A request under way, which an overlapping load waits on instead of
    sending its own."""

    def __init__(self) -> None:
        self.done = threading.Event()


class StatusPoller:
    def __init__(
        self,
        cache_path: Path,
        transport: Transport = http_get,
        now: Callable[[], float] = time.time,
        interval: float = INTERVAL,
        manual_floor: float = MANUAL_FLOOR,
        stale_after: float = STALE_AFTER,
        timeout: float = TIMEOUT,
        wait: float = WAIT,
    ) -> None:
        self.cache_path = Path(cache_path)
        self._transport = transport
        self._now = now
        self._interval = interval
        self._manual_floor = manual_floor
        self._stale_after = stale_after
        self._timeout = timeout
        self._wait = wait
        self._lock = threading.Lock()
        # Every entry this process has seen, so the polling limits hold even
        # when the cache file cannot be written.
        self._memory: dict[str, Entry] = {}
        self._in_flight: dict[str, _Flight] = {}

    # -- reading

    def cached(self, services: Iterable[StatusService] = SERVICES) -> list[ServiceStatus]:
        """The last readings, without touching the network."""
        with self._lock:
            entries = self._current()
        now = self._now()
        return [self._status(s, entries.get(s.id), now) for s in services]

    def statuses(self, services: Iterable[StatusService] = SERVICES, force: bool = False) -> list[ServiceStatus]:
        """Every service's status, asking the pages that are due. ``force``
        cuts the five-minute wait to one minute but never skips a cooldown."""
        services = list(services)
        floor = self._manual_floor if force else self._interval
        flights: list[_Flight] = []
        with self._lock:
            entries = self._current()
            start = self._now()
            for service in services:
                running = self._in_flight.get(service.id)
                if running is not None:
                    flights.append(running)
                    continue
                entry = entries.get(service.id)
                if entry is not None and entry.cooldown_until is not None and entry.cooldown_until > start:
                    continue
                if entry is not None and entry.attempted_at is not None and start - entry.attempted_at < floor:
                    continue
                # A tombstone is not a base for a new attempt: clearing
                # removed_at lets the fresh reading land over it.
                attempt = replace(entry, removed_at=None) if entry is not None else Entry()
                attempt.attempted_at = start
                # Recorded before the request goes out, so the next call sees it.
                self._memory[service.id] = attempt
                flight = _Flight()
                self._in_flight[service.id] = flight
                flights.append(flight)
                threading.Thread(target=self._run, args=(service, attempt, flight),
                                 name=f"status-{service.id}", daemon=True).start()

        deadline = time.monotonic() + self._wait
        for flight in flights:
            flight.done.wait(max(0.0, deadline - time.monotonic()))

        with self._lock:
            merged = self._current()
        now = self._now()
        return [self._status(s, merged.get(s.id), now) for s in services]

    def _status(self, service: StatusService, entry: Entry | None, now: float) -> ServiceStatus:
        if entry is None or entry.removed_at is not None:
            return ServiceStatus(service)
        fresh = entry.checked_at is not None and now - entry.checked_at <= self._stale_after
        report = entry.report if fresh else None
        return ServiceStatus(service, report, entry.checked_at, entry.problem, entry.problem_until,
                             stale=report is not None and entry.problem is not None)

    # -- one request

    def _run(self, service: StatusService, attempt: Entry, flight: _Flight) -> None:
        try:
            try:
                entry = self._check(service, attempt)
            except Exception:  # a parser or transport bug must not wedge the flight
                entry = replace(attempt, problem=f"Couldn't read {service.host}", problem_until=None)
            with self._lock:
                # A flight forget() unregistered must not land its answer:
                # the page was removed (or re-added under a newer flight),
                # and a saved entry would bring the old reading back.
                if self._in_flight.get(service.id) is flight:
                    self._save({service.id: entry})
        finally:
            # Whatever happened, the page is no longer in flight, or no
            # later load would ever ask it again.
            with self._lock:
                if self._in_flight.get(service.id) is flight:
                    del self._in_flight[service.id]
            flight.done.set()

    def forget(self, service_id: str) -> None:
        """Drop everything kept for a page: this process's memory, a
        request in flight and the cache file. Mirrors the Mac's
        ``ServiceStatusService.forget``: a removed page's reading must not
        reach a same-named page added later.

        The cache keeps a tombstone (``removed_at``) rather than a gap:
        another hub on this home may hold the page in its own memory or
        finish a request started before the removal, and only a mark it
        merges against can keep that reading from landing or being served."""
        with self._lock:
            self._memory.pop(service_id, None)
            # The flight cannot be cancelled; unregistered, its landing is
            # not saved (or the page would reappear).
            self._in_flight.pop(service_id, None)
            tombstone = Entry(removed_at=self._now())
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                with locked_beside(self.cache_path):
                    entries = self._load()
                    entries[service_id] = newer(tombstone, entries.get(service_id))
                    self._write(entries)
            except OSError:
                pass

    def _check(self, service: StatusService, entry: Entry) -> Entry:
        """One request, folded into the page's entry."""
        entry = replace(entry)
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        # Validators only with a reading to fall back on, or a 304 leaves nothing.
        if entry.report is not None:
            if entry.etag:
                headers["If-None-Match"] = entry.etag
            if entry.last_modified:
                headers["If-Modified-Since"] = entry.last_modified

        try:
            response = self._transport(service.feed_url, headers, self._timeout)
        except (OSError, ValueError, http.client.HTTPException):
            # HTTPException: a truncated body or a garbled status line, which
            # are not OSErrors but mean the same as a dropped connection.
            entry.problem, entry.problem_until = f"Couldn't reach {service.host}", None
            return entry
        # Read after the answer: a Retry-After counts from when it arrived,
        # and a slow response must not shorten the wait.
        now = self._now()

        if 200 <= response.status < 300:
            if service.feed == "google_cloud":
                report = google_cloud(response.body, service.products, now)
            else:
                report = statuspage(response.body)
            if report is None:
                entry.problem, entry.problem_until = f"Couldn't read {service.host}", None
                entry.etag = entry.last_modified = None
                return entry
            entry.report = report
            entry.checked_at = now
            entry.etag = response.header("ETag")
            entry.last_modified = response.header("Last-Modified")
            entry.cooldown_until = None
            entry.problem = entry.problem_until = None
        elif response.status == 304 and entry.report is not None:
            entry.checked_at = now
            entry.cooldown_until = None
            entry.problem = entry.problem_until = None
        elif response.status in (429, 503):
            # Without a Retry-After, back off for a quarter of an hour; with
            # one, never longer than six hours.
            asked = retry_after(response.header("Retry-After"), now)
            until = min(asked if asked is not None else now + 900, now + 6 * 3600)
            entry.cooldown_until = until
            entry.problem, entry.problem_until = f"{service.host} asked us to wait until", until
        else:
            entry.problem, entry.problem_until = f"{service.host} answered {response.status}", None
        return entry

    # -- the cache file (callers hold self._lock)

    def _current(self) -> dict[str, Entry]:
        """The disk and this process's memory, newest per page."""
        merged = self._load()
        for id, entry in self._memory.items():
            merged[id] = newer(entry, merged.get(id))  # type: ignore[assignment]
        return merged

    def _load(self) -> dict[str, Entry]:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            return {}
        services = raw.get("services") if isinstance(raw, dict) else None
        if not isinstance(services, dict):
            return {}
        out = {}
        for id, value in services.items():
            entry = Entry.from_dict(value)
            if entry is not None:
                out[id] = entry
        return out

    def _save(self, updated: dict[str, Entry]) -> None:
        """Folds ``updated`` into memory and into what is on disk now, under
        a lock shared with any other hub on this home. A failed write loses
        nothing for this process: memory still has every entry."""
        for id, entry in updated.items():
            self._memory[id] = newer(entry, self._memory.get(id))  # type: ignore[assignment]
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with locked_beside(self.cache_path):
                entries = self._load()
                for id, entry in updated.items():
                    entries[id] = newer(entry, entries.get(id))  # type: ignore[assignment]
                self._write(entries)
        except OSError:
            pass

    def _write(self, entries: dict[str, Entry]) -> None:
        """The whole cache, atomically (callers hold the lock file)."""
        atomic_write_text(self.cache_path, json.dumps(
            {"version": 1, "services": {k: v.to_dict() for k, v in sorted(entries.items())}},
            indent=2, sort_keys=True))


# One poller per cache file, shared by every request the hub serves, so
# overlapping loads join one another's requests.
_POLLERS: dict[Path, StatusPoller] = {}
_POLLERS_LOCK = threading.Lock()


def poller_for(home: Path | None = None) -> StatusPoller:
    path = cache_file_for(home)
    with _POLLERS_LOCK:
        poller = _POLLERS.get(path)
        if poller is None:
            poller = _POLLERS[path] = StatusPoller(path)
        return poller
