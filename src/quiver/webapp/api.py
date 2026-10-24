"""JSON for the web app's pages (``GET /v1/app/*`` on the hub).

Each page's builder runs in process, like ``/v1/usage``. The ledger is
scanned on demand: a request finds the store fresh if the last scan is
under ``SCAN_TTL_S`` old, else it scans first (new lines only, so a
warm scan is quick). Only one scan runs at a time; a second request
waits for it rather than reading the same files again.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path

from quiver.ledger import overview as overview_mod
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

    def _ensure(self, rescan: bool) -> None:
        if self._store is None:
            self._store = LedgerStore(ledger_path_for(self.home))
        now = time.time()
        if self._catalog is None or now - self._catalog_at > CATALOG_TTL_S:
            self._catalog = load_catalog(self.home, download=self.download_prices)
            self._catalog_at = now
        if rescan or now - self._scanned_at > SCAN_TTL_S:
            self._last_scan = scan(self._store, self.home)
            self._scanned_at = time.time()

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
                 rescan: bool = False) -> dict:
        """The Sessions page: the hub's catalog rows with usage joined."""
        with self._lock:
            self._ensure(rescan)
            usage = by_session(self._store, self._catalog)
            return {
                "sessions": sessions_mod.build(rows, usage, hub_machine_id, machines,
                                               scanned=(self._last_scan is not None
                                                        and not self._last_scan.errors)),
                "ledger_harnesses": sorted(sessions_mod.LEDGER_HARNESSES),
                "scanned_at": self._scanned_at,
                "notes": self._notes(),
            }

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
        if self._last_scan is None or not self._last_scan.errors:
            return []
        errors = self._last_scan.errors
        return [f"{len(errors)} log files couldn't be read: " + "; ".join(errors[:3])]

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                self._store.close()
                self._store = None
