"""JSON for the web app's pages (``GET /v1/app/*`` on the hub).

Each page's builder runs in process, like ``/v1/usage``. The ledger is
scanned on demand: a request finds the store fresh if the last scan is
under ``SCAN_TTL_S`` old, else it scans first (new lines only, so a
warm scan is quick). Only one scan runs at a time; a second request
waits for it rather than reading the same files again.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from quiver.ledger import feed, overview as overview_mod
from quiver.ledger.sessions import by_session
from quiver.ledger.pricing import load_catalog
from quiver.ledger.scan import scan
from quiver.ledger.store import LedgerStore, ledger_path_for
from quiver.webapp import sessions as sessions_mod

SCAN_TTL_S = 60.0
CATALOG_TTL_S = 3600.0


class WebApp:
    """The state the pages share: one ledger store and price list."""

    def __init__(self, home: Path | None = None, download_prices: bool = True):
        self.home = home
        self.download_prices = download_prices
        self._lock = threading.Lock()
        self._store: LedgerStore | None = None
        self._catalog = None
        self._catalog_at = 0.0
        self._scanned_at = 0.0
        self._last_scan = None
        # by_session over the whole ledger, kept until the next scan or
        # price list: ``(scanned_at, catalog, usage)``.
        self._usage: tuple | None = None
        # The Calendar file sources' per-account totals, kept until the
        # next scan: ``((scanned_at, harnesses), stats)``. The Cursor feed
        # writes no Pi or Droid rows, so its rounds leave them be.
        self._activity_stats: tuple | None = None
        # Cursor's feed fetches over the network, so a round runs off
        # this lock on its own thread and store (``keepFresh``); its last
        # notes surface with the next page's.
        self._feed_thread: threading.Thread | None = None
        self._feed_notes: list[str] = []
        # The LiteLLM price download (up to 15 s) runs on its own thread
        # too; pages price from the copy on disk meanwhile. A pricing
        # write bumps ``_prices_gen`` so a download that started before
        # it can't put back the old overrides.
        self._prices_thread: threading.Thread | None = None
        self._prices_gen = 0

    def _ensure(self, rescan: bool) -> None:
        if self._store is None:
            self._store = LedgerStore(ledger_path_for(self.home))
        now = time.time()
        if self._catalog is None or now - self._catalog_at > CATALOG_TTL_S:
            # With downloads off nothing swaps a fresh list in later, so
            # an expired one rereads the disk (its copy, the overrides).
            if self._catalog is None or not self.download_prices:
                self._catalog = load_catalog(self.home, download=False)
            self._catalog_at = now
            self._kick_prices()
        if rescan or now - self._scanned_at > SCAN_TTL_S:
            try:
                self._last_scan = scan(self._store, self.home)
            except sqlite3.Error:
                # End the failed scan's transaction: left open, it keeps
                # its snapshot, and a busy store would fail every later
                # page until the hub restarts instead of this one.
                self._store.db.rollback()
                raise
            self._scanned_at = time.time()
        self._kick_feed()

    def _kick_prices(self) -> None:
        """Refresh the LiteLLM download on a background thread; the
        caller holds the lock, which the download must never hold."""
        if not self.download_prices:
            return
        if self._prices_thread is not None and self._prices_thread.is_alive():
            return
        self._prices_thread = threading.Thread(
            target=self._refresh_prices, args=(self._prices_gen,),
            name="ledger-prices", daemon=True)
        self._prices_thread.start()

    def _refresh_prices(self, gen: int) -> None:
        try:
            catalog = load_catalog(self.home, download=True)
        except Exception:  # offline or unreadable: keep the disk copy
            return
        with self._lock:
            if gen == self._prices_gen:
                self._catalog = catalog

    def _kick_feed(self) -> None:
        """Start a background feed round when the cadence says one is
        due and none is running. A first signed-in poll walks 180 days
        one slice at a time, which must never hold the page lock."""
        if self._feed_thread is not None and self._feed_thread.is_alive():
            return
        if not feed.due(self._store):
            return
        self._feed_thread = threading.Thread(
            target=self._poll_feed, name="ledger-cursor-feed", daemon=True)
        self._feed_thread.start()

    def _poll_feed(self) -> None:
        """One feed round on a second connection to the same store: WAL
        keeps a reader and this writer unblocked, ``feed.poll``'s round
        lock keeps two rounds ordered, and either side finishing or
        failing leaves the other's rows untouched."""
        notes: list[str] = []
        try:
            store = LedgerStore(ledger_path_for(self.home))
            try:
                feed.poll(store, self.home, errors=notes)
            finally:
                store.close()
        except Exception as exc:  # a failed poll must not sink a page
            notes.append(f"cursor feed: {exc}")
        with self._lock:
            self._feed_notes = notes
            # The round may have written usage rows: group them again.
            self._usage = None

    def overview(self, period: str, tz_name: str | None, rescan: bool = False) -> dict:
        with self._lock:
            self._ensure(rescan)
            body = overview_mod.build(self._store, self._catalog, period, tz_name)
            body["scanned_at"] = self._scanned_at
            body["notes"] = self._notes()
            body["first_run"] = self._first_run()
            return body

    def _first_run(self) -> bool:
        """The Setup page's ``first_run``: no shared AGENTS.md on this
        machine yet, so Overview can offer setup without building the
        setup preview."""
        from quiver.webapp.pages.setup import _first_run

        return _first_run(self.home or Path.home())

    def sessions(self, rows: list[dict], hub_machine_id: str, machines: dict,
                 rescan: bool = False, pending: dict | None = None) -> dict:
        """The Sessions page: the hub's catalog rows with usage joined.
        ``pending`` maps session key -> outstanding approval request id;
        a row carrying one reads ``approval`` so the list shows which
        agent waits on an answer, the inbox's job on the old remote
        page."""
        with self._lock:
            self._ensure(rescan)
            usage = self.session_usage()
            sessions = sessions_mod.build(
                rows, usage, hub_machine_id, machines,
                scanned=(self._last_scan is not None
                         and not self._last_scan.errors),
                home=self.home)
            if pending:
                for s in sessions:
                    if s.get("key") in pending:
                        s["status"] = "approval"
            return {
                "sessions": sessions,
                "ledger_harnesses": sorted(sessions_mod.LEDGER_HARNESSES),
                # Whether "Run in terminal" is wired on this hub (the
                # sessions page plugin opens Terminal.app via osascript).
                "open_terminal": sys.platform == "darwin",
                "scanned_at": self._scanned_at,
                "notes": self._notes(),
            }

    def session_usage(self) -> dict:
        """``by_session`` for the current store and prices; the caller
        holds the page lock (inside ``ledger()`` or a page method).

        It groups every session in the ledger's history, so the Sessions
        and Calendar pages share one answer per scan instead of paying
        for it on each navigation."""
        cached = self._usage
        if cached is not None and cached[0] == self._scanned_at \
                and cached[1] is self._catalog:
            return cached[2]
        usage = by_session(self._store, self._catalog)
        self._usage = (self._scanned_at, self._catalog, usage)
        return usage

    def activity_session_stats(self, harnesses: tuple[str, ...]) -> dict:
        """``LedgerStore.activity_session_stats`` for the Calendar's file
        sources, kept until the next scan like ``session_usage``; the
        caller holds the page lock."""
        cached = self._activity_stats
        if cached is not None and cached[0] == (self._scanned_at, harnesses):
            return cached[1]
        stats = self._store.activity_session_stats(list(harnesses))
        self._activity_stats = ((self._scanned_at, harnesses), stats)
        return stats

    def prices_changed(self) -> None:
        """``config/pricing.json`` was written: the next read builds the
        price list again, so every page prices with the new rates."""
        with self._lock:
            self._catalog = None
            self._prices_gen += 1

    @contextmanager
    def ledger(self, rescan: bool = False):
        """``(store, catalog, scan_ok)`` for a page plugin, fresh as the
        Overview's and held under the same lock: pages read the shared
        store, never open their own. ``scan_ok`` is false when the last
        scan couldn't read every source."""
        with self._lock:
            self._ensure(rescan)
            yield (self._store, self._catalog,
                   self._last_scan is not None and not self._last_scan.errors)

    def _notes(self) -> list[str]:
        notes: list[str] = []
        if self._last_scan is not None and self._last_scan.errors:
            errors = self._last_scan.errors
            notes.append(f"{len(errors)} log files couldn't be read: "
                         + "; ".join(errors[:3]))
        notes += self._feed_notes
        return notes

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                self._store.close()
                self._store = None
