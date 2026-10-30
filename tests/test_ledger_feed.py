"""ledger/feed.py: Cursor's usage feed (``CursorUsageFeed.swift``) —
record mapping, per-account windows and progress, and the lookalike
cleanup an authoritative write performs."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from quiver.ledger import feed
from quiver.ledger.models import LedgerRecord, UsageEvent
from quiver.ledger.scan import scan
from quiver.ledger.store import LedgerStore, ledger_path_for

T0 = 1_760_000_000.0


def _event(ts=T0, model="composer-1", conv="conv-1", **kw):
    return {"timestamp": ts, "model": model, "kind": "included",
            "conversation_id": conv, "headless": False,
            "input_tokens": 1000, "output_tokens": 50,
            "cache_read_tokens": 200, "cache_write_tokens": 30,
            "cost_usd": 0.04, **kw}


class TestFeedRecord(unittest.TestCase):
    def test_key_dims_and_flags(self):
        rec = feed.record(_event(), "acct-1", "/desktop/cursor")
        self.assertEqual(
            rec.key,
            f"cursor-feed:acct-1:{int(T0 * 1000)}:composer-1:conv-1")
        self.assertTrue(rec.authoritative)
        self.assertTrue(rec.account_spans_profiles)
        ev = rec.event
        self.assertEqual((ev.harness, ev.account, ev.session),
                         ("cursor", "/desktop/cursor", "conv-1"))
        self.assertEqual((ev.input, ev.output, ev.cache_read, ev.cache_write),
                         (1000, 50, 200, 30))
        self.assertEqual(ev.cost, 0.04)
        self.assertEqual(ev.ts, T0)

    def test_day_bucket_when_no_conversation(self):
        rec = feed.record(_event(conv=None), "acct-1", "/desktop/cursor")
        day = time.strftime("%Y-%m-%d", time.localtime(T0))
        self.assertEqual(rec.event.session, f"cursor-{day}")
        self.assertTrue(feed.is_day_bucket(rec.event.session))
        self.assertFalse(feed.is_day_bucket("conv-1"))
        self.assertTrue(rec.key.endswith(":"))

    def test_grok_bot_models_split_harness(self):
        for model, expected in (("grok-bot-default", "grok-bot"),
                                ("xai/grok-bot-4", "grok-bot"),
                                ("grok-4", "cursor"), ("composer-1", "cursor")):
            rec = feed.record(_event(model=model), "a", "h")
            self.assertEqual(rec.event.harness, expected, model)

    def test_profile_keyed_rows_do_not_span_profiles(self):
        rec = feed.record(_event(), "/desktop/a", "/desktop/a")
        self.assertFalse(rec.account_spans_profiles)
        self.assertTrue(rec.key.startswith("cursor-feed:/desktop/a:"))


class TestFeedPoll(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.store = LedgerStore(ledger_path_for(self.home))
        self.addCleanup(self.store.close)
        # The default sign-in's user-data dir exists: one account.
        self.default_home = self.home / "Library" / "Application Support" / "Cursor"
        self.default_home.mkdir(parents=True)
        self.calls = []

    def _fetch(self, reply):
        events, account_id, status = reply

        def fetch(state_db, since, now):
            self.calls.append((state_db, since, now))
            return status, list(events), account_id
        return fetch

    def _progress(self):
        return json.loads(self.store.meta(feed._PROGRESS_KEY))

    def test_first_poll_full_window_then_overlap(self):
        changed = feed.poll(self.store, self.home, now=T0,
                            fetch=self._fetch(([_event()], "acct-1", "ok")))
        self.assertEqual(changed, 1)
        self.assertEqual(len(self.calls), 1)
        _state_db, since, _now = self.calls[0]
        self.assertAlmostEqual(since, T0 - feed.WINDOW_S)

        row = self.store.db.execute(
            "SELECT harness, account, session, input FROM events").fetchone()
        self.assertEqual(row, ("cursor", str(self.default_home), "conv-1", 1000))
        progress = self._progress()
        self.assertEqual(progress["profiles"][str(self.default_home)], "acct-1")
        self.assertEqual(progress["accounts"]["acct-1"]["through"], T0)

        # Next round: the account's high-water mark minus the overlap.
        changed = feed.poll(self.store, self.home, now=T0 + feed.INTERVAL_S,
                            fetch=self._fetch(([_event()], "acct-1", "ok")))
        self.assertEqual(changed, 0)
        self.assertAlmostEqual(self.calls[1][1], T0 - feed.OVERLAP_S)

    def test_poll_is_gated_to_the_apps_cadence(self):
        fetch = self._fetch(([], "acct-1", "ok"))
        feed.poll(self.store, self.home, now=T0, fetch=fetch)
        self.assertEqual(feed.poll(self.store, self.home,
                                   now=T0 + 1, fetch=fetch), 0)
        self.assertEqual(len(self.calls), 1)
        feed.poll(self.store, self.home, now=T0 + feed.INTERVAL_S, fetch=fetch)
        self.assertEqual(len(self.calls), 2)

    def test_signed_out_and_auth_required_keep_the_rows(self):
        now = T0
        feed.poll(self.store, self.home, now=now,
                  fetch=self._fetch(([_event()], "acct-1", "ok")))
        for status in ("signed-out", "auth-required", "unavailable"):
            now += feed.INTERVAL_S
            changed = feed.poll(self.store, self.home, now=now,
                                fetch=self._fetch(([], None, status)))
            self.assertEqual(changed, 0, status)
            count = self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            self.assertEqual(count, 1, status)

    def test_unavailable_and_incomplete_leave_a_note(self):
        """The misses a page can act on surface in ``errors``; the rows
        the feed already stored are kept either way (the app's surfaced
        status through ``lastError``/the log)."""
        feed.poll(self.store, self.home, now=T0,
                  fetch=self._fetch(([_event()], "acct-1", "ok")))
        errors = []
        feed.poll(self.store, self.home, now=T0 + feed.INTERVAL_S,
                  fetch=self._fetch(([], None, "unavailable")), errors=errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("unavailable", errors[0])
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM events").fetchone()[0], 1)
        errors.clear()
        # incomplete still ingests what arrived — the same key re-upsert
        # is a no-op — but the truncated window is noted.
        feed.poll(self.store, self.home, now=T0 + 2 * feed.INTERVAL_S,
                  fetch=self._fetch(([_event()], "acct-1", "incomplete")),
                  errors=errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("page cap", errors[0])
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_signed_out_and_auth_required_stay_quiet(self):
        """A signed-out or expired profile is a normal state, not a note
        — the machine without a sign-in would nag otherwise."""
        errors = []
        for i, status in enumerate(("signed-out", "auth-required")):
            feed.poll(self.store, self.home, now=T0 + i * feed.INTERVAL_S,
                      fetch=self._fetch(([], None, status)), errors=errors)
        self.assertEqual(errors, [])

    def test_incomplete_ingests_but_the_mark_stays(self):
        feed.poll(self.store, self.home, now=T0,
                  fetch=self._fetch(([_event()], "acct-1", "incomplete")))
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM events").fetchone()[0], 1)
        progress = self._progress()
        # `through` only advances on ok: the same window is asked again.
        self.assertNotIn("acct-1", progress["accounts"])
        feed.poll(self.store, self.home, now=T0 + feed.INTERVAL_S,
                  fetch=self._fetch(([_event()], "acct-1", "ok")))
        self.assertAlmostEqual(self.calls[1][1], T0 + feed.INTERVAL_S - feed.WINDOW_S)

    def test_two_profiles_one_account_poll_once(self):
        accounts_dir = self.home / ".quiver" / "accounts"
        accounts_dir.mkdir(parents=True)
        profile_desktop = accounts_dir / "cursor" / "p1" / "desktop"
        (accounts_dir / "profiles.json").write_text(json.dumps({"profiles": [
            {"id": "p1", "vendor": "cursor", "isDefault": False}]}))
        feed.poll(self.store, self.home, now=T0,
                  fetch=self._fetch(([_event()], "acct-1", "ok")))
        # Both accounts were tried; the profile's unknown expected mark
        # sends it through the account's own window first (a second fetch,
        # as in Swift), and only then does the shared id skip the import.
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[1][0],
                         str(profile_desktop / "User" / "globalStorage" / "state.vscdb"))
        self.assertAlmostEqual(self.calls[2][1], T0 - feed.OVERLAP_S)
        count = self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(count, 1)

    def test_no_write_is_held_across_the_next_accounts_fetch(self):
        accounts_dir = self.home / ".quiver" / "accounts"
        accounts_dir.mkdir(parents=True)
        (accounts_dir / "profiles.json").write_text(json.dumps({"profiles": [
            {"id": "p1", "vendor": "cursor", "isDefault": False}]}))
        open_during_fetch = []

        def fetch(state_db, since, now):
            open_during_fetch.append(self.store.db.in_transaction)
            n = len(open_during_fetch)
            return "ok", [_event(conv=f"conv-{n}")], f"acct-{n}"

        feed.poll(self.store, self.home, now=T0, fetch=fetch)
        # The first account's rows were written before the later fetches.
        self.assertGreater(len(open_during_fetch), 1)
        self.assertEqual(open_during_fetch, [False] * len(open_during_fetch))
        self.assertFalse(self.store.db.in_transaction)

    def test_switched_account_reads_its_own_window(self):
        home = str(self.default_home)
        self.store.set_meta(feed._PROGRESS_KEY, json.dumps({
            "schema": 1, "profiles": {home: "acct-old"},
            "accounts": {"acct-old": {"through": T0 - 10},
                         "acct-new": {"through": T0 - 100}},
            "polled_at": T0}))
        feed.poll(self.store, self.home, now=T0 + feed.INTERVAL_S,
                  fetch=self._fetch(([_event()], "acct-new", "ok")))
        # First call used the profile's old mark, then the new account's
        # own mark asked again.
        self.assertAlmostEqual(self.calls[0][1], T0 - 10 - feed.OVERLAP_S)
        self.assertAlmostEqual(self.calls[1][1], T0 - 100 - feed.OVERLAP_S)

    def test_fetch_failure_is_a_note_not_a_crash(self):
        def boom(state_db, since, now):
            raise OSError("cannot run")
        errors = []
        self.assertEqual(feed.poll(self.store, self.home, now=T0,
                                   fetch=boom, errors=errors), 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("cursor feed", errors[0])

    def test_scan_leaves_the_feed_alone(self):
        """``scan`` reads files only — the web app polls the feed in the
        background (``keepFresh``), so a scan never fetches."""
        fetch = self._fetch(([_event()], "acct-1", "ok"))
        with mock.patch.object(feed, "_default_fetch", fetch):
            scan(self.store, self.home)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.store.db.execute(
            "SELECT key FROM events").fetchone())

    def test_due_gates_on_the_saved_polled_at(self):
        self.assertTrue(feed.due(self.store, now=T0))
        feed.poll(self.store, self.home, now=T0,
                  fetch=self._fetch(([], "acct-1", "ok")))
        self.assertFalse(feed.due(self.store, now=T0 + 1))
        self.assertTrue(feed.due(self.store, now=T0 + feed.INTERVAL_S))

    def test_rounds_on_one_file_are_serialized(self):
        """Two stores on the same file share the round lock: a second
        poll waits, then sees the first's fresh polled_at and skips
        fetching (``CursorUsageFeed``'s actor ordering)."""
        other = LedgerStore(self.store.path)
        self.addCleanup(other.close)
        entered = threading.Event()
        release = threading.Event()
        done = []

        def slow(state_db, since, now):
            entered.set()
            release.wait(30)
            return "ok", [_event()], "acct-1"

        first = threading.Thread(
            target=lambda: done.append(
                feed.poll(self.store, self.home, now=T0, fetch=slow)),
            daemon=True)
        first.start()
        self.assertTrue(entered.wait(10))
        second = threading.Thread(
            target=lambda: done.append(
                feed.poll(other, self.home, now=T0 + feed.INTERVAL_S / 2,
                          fetch=self._fetch(([_event()], "acct-1", "ok")))),
            daemon=True)
        second.start()
        # Held out of the first round's progress read-modify-write.
        second.join(0.2)
        self.assertTrue(second.is_alive())
        release.set()
        first.join(30)
        second.join(30)
        self.assertEqual(sorted(done), [0, 1])
        # The waiting round saw polled_at fresh and never fetched.
        self.assertEqual(self.calls, [])


class TestFeedScheduling(unittest.TestCase):
    """``WebApp`` runs the poll off the page lock (``keepFresh``)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / "Library" / "Application Support" / "Cursor").mkdir(parents=True)
        from quiver.webapp.api import WebApp
        self.webapp = WebApp(self.home, download_prices=False)
        self.addCleanup(self.webapp.close)

    def test_the_poll_runs_off_the_page_lock(self):
        entered = threading.Event()
        release = threading.Event()
        threads = []

        def slow(state_db, since, now):
            threads.append(threading.current_thread().name)
            entered.set()
            release.wait(30)
            return "ok", [_event()], "acct-1"

        with mock.patch.object(feed, "_default_fetch", slow):
            self.webapp._ensure(True)
            self.assertTrue(entered.wait(10))
            # _ensure returned while the fetch still blocks, and the
            # fetch is on the feed's own thread — not the page lock's.
            self.assertEqual(threads, ["ledger-cursor-feed"])
            release.set()
            self.webapp._feed_thread.join(30)
        self.assertEqual(self.webapp._store.db.execute(
            "SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_feed_notes_reach_the_page(self):
        def down(state_db, since, now):
            return "unavailable", [], None

        with mock.patch.object(feed, "_default_fetch", down):
            self.webapp._ensure(True)
            self.webapp._feed_thread.join(30)
        self.assertIn("unavailable", "; ".join(self.webapp._notes()))

    def test_a_second_ensure_does_not_start_a_second_round(self):
        calls = []

        def fetch(state_db, since, now):
            calls.append(1)
            return "ok", [], "acct-1"

        with mock.patch.object(feed, "_default_fetch", fetch):
            self.webapp._ensure(True)
            self.webapp._feed_thread.join(30)
            self.webapp._ensure(True)
            if self.webapp._feed_thread is not None:
                self.webapp._feed_thread.join(30)
        # The second round found polled_at fresh and did not even start.
        self.assertEqual(len(calls), 1)


class TestLookalikeCleanup(unittest.TestCase):
    """``selectSameEvent``/``selectSameEventAnyProfile`` through upsert."""

    def setUp(self):
        self.store = LedgerStore(Path(tempfile.mkdtemp()) / "l.sqlite")
        self.addCleanup(self.store.close)

    def _feed_rec(self, key_account, row_account, conv="conv-1", ts=T0):
        return feed.record(_event(ts=ts, conv=conv), key_account, row_account)

    def _keys(self):
        return sorted(r[0] for r in self.store.db.execute("SELECT key FROM events"))

    def _insert_stale(self, key, account, inp, out):
        self.store.db.execute(
            "INSERT INTO events (key, msg, sidechain, harness, account, session, cwd,"
            " model, ts, input, output, cache_read, cache_write, cache_write_1h,"
            " reasoning, session_total, cost) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, None, 0, "cursor", account, "conv-1", None,
             "composer-1", T0, inp, out, 200, 30, 0, 0, 0, 0.02))

    def test_old_counts_keyed_row_is_removed_same_profile(self):
        fresh = self._feed_rec("acct-1", "/desktop/a")
        # The old key scheme appended the event's counts, so the stale
        # row's own columns rebuild its key — that is the match.
        self._insert_stale(f"{fresh.key}:900:40", "/desktop/a", 900, 40)
        self.store.upsert(fresh)
        self.assertEqual(self._keys(), [fresh.key])

    def test_lookalike_under_another_profile_is_removed_when_spans(self):
        fresh = self._feed_rec("acct-1", "/desktop/a")
        self._insert_stale(f"{fresh.key}:900:40", "/desktop/b", 900, 40)
        self.store.upsert(fresh)
        # The stale row named acct-1 in its key: the candidate's own
        # counts rebuild it, so it goes even though its account dim is a
        # different profile's.
        self.assertEqual(self._keys(), [fresh.key])

    def test_a_same_dims_row_with_its_own_key_survives(self):
        fresh = self._feed_rec("acct-1", "/desktop/a")
        # Same event dims and instant under another account, but the key
        # is not "<fresh>:<its input>:<its output>", so it is not the
        # old scheme's row for this event and stays.
        self._insert_stale("hand-keyed", "/desktop/b", 5, 5)
        self.store.upsert(fresh)
        self.assertEqual(self._keys(), [fresh.key, "hand-keyed"])

    def test_same_event_under_same_key_is_a_noop(self):
        rec = self._feed_rec("acct-1", "/desktop/a")
        self.assertTrue(self.store.upsert(rec))
        self.assertFalse(self.store.upsert(rec))
        # A correction under the same key — new counts — replaces it.
        corrected = self._feed_rec("acct-1", "/desktop/a")
        corrected.event.input = 1100
        self.assertTrue(self.store.upsert(corrected))
        self.assertEqual(self.store.db.execute(
            "SELECT input FROM events WHERE key = ?", (rec.key,)).fetchone()[0], 1100)



class TestPageReadsAndTheFeedConnection(unittest.TestCase):
    """A page read on the shared connection must not pin a WAL snapshot.

    The pricing reads fill a temp ``thresholds`` table, and that DELETE
    once opened an implicit transaction no read path ever closed. The
    feed round then committed on its own connection, so the page
    connection's next write (the scan's ``scanned_at``) had to upgrade a
    stale snapshot: SQLite answers ``database is locked`` at once, and
    with the transaction still open every later scan failed the same way
    until the hub restarted."""

    def setUp(self):
        from quiver.webapp.api import WebApp
        self.home = Path(tempfile.mkdtemp())
        self.webapp = WebApp(self.home, download_prices=False)
        self.addCleanup(self.webapp.close)

    def test_a_feed_commit_after_a_page_read_leaves_the_next_scan_working(self):
        from quiver.webapp.api import WebApp
        # Hold the round back so it lands after the page's read, the
        # order the wedged hub's WAL shows (scan, read, feed commit).
        with mock.patch.object(WebApp, "_kick_feed"):
            self.webapp.overview("month", None)
        self.assertFalse(self.webapp._store.db.in_transaction)
        self.webapp._poll_feed()
        self.assertEqual(self.webapp._feed_notes, [])
        for _ in range(2):  # the wedge was permanent: every scan failed
            self.webapp.overview("month", None, rescan=True)
            self.webapp.sessions([], "hub", {}, rescan=True)

    def test_a_failed_scan_ends_its_transaction(self):
        import sqlite3

        from quiver.webapp import api

        def fails(store, home):
            store.set_meta("probe", "1")  # opens the implicit transaction
            raise sqlite3.OperationalError("database is locked")

        with mock.patch.object(api, "scan", fails), \
                mock.patch.object(api.WebApp, "_kick_feed"):
            with self.assertRaises(sqlite3.OperationalError):
                self.webapp.overview("month", None)
        # Rolled back: nothing half-written, no snapshot left pinned for
        # the next request to trip over.
        self.assertFalse(self.webapp._store.db.in_transaction)
        self.assertIsNone(self.webapp._store.meta("probe"))
        self.webapp.overview("month", None, rescan=True)


if __name__ == "__main__":
    unittest.main()
