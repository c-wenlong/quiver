"""sessionmeta.py and the pin/title routes: store, overlay, hub dispatch."""

import json
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

from quiver.remote import catalog as catalog_mod
from quiver.remote import protocol, sessionmeta
from tests.test_remote_hub import _Server, _session


class TestStore(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_pin_round_trip_and_order(self):
        sessionmeta.set_pinned(self.home, "claude:a", True, now=10.0)
        sessionmeta.set_pinned(self.home, "claude:b", True, now=5.0)
        # Pinning again keeps the first pin time.
        sessionmeta.set_pinned(self.home, "claude:a", True, now=99.0)
        meta = sessionmeta.load(self.home)
        order = sorted(meta, key=lambda k: meta[k]["pinned_at"])
        self.assertEqual(order, ["claude:b", "claude:a"])
        self.assertEqual(meta["claude:a"]["pinned_at"], 10.0)
        sessionmeta.set_pinned(self.home, "claude:a", False)
        self.assertEqual(list(sessionmeta.load(self.home)), ["claude:b"])
        self.assertFalse(sessionmeta.get(self.home, "claude:a")["pinned"])

    def test_title_trim_cap_and_clear(self):
        parse = lambda t: sessionmeta.parse_title({"title": t})
        self.assertEqual(parse("  hello \n  world  "), "hello world")
        self.assertEqual(len(parse("x" * 500)), sessionmeta.MAX_TITLE_LEN)
        self.assertEqual(parse("   "), "")
        sessionmeta.set_title(self.home, "claude:a", "Mine")
        self.assertEqual(sessionmeta.get(self.home, "claude:a")["title"], "Mine")
        sessionmeta.set_title(self.home, "claude:a", "")
        self.assertEqual(sessionmeta.load(self.home), {})

    def test_bad_bodies_raise(self):
        for body in ({}, {"pinned": 1}, {"pinned": "true"}, {"pinned": None}):
            with self.assertRaises(ValueError):
                sessionmeta.parse_pin(body)
        for body in ({}, {"title": 5}, {"title": ["a"]}, {"title": None}):
            with self.assertRaises(ValueError):
                sessionmeta.parse_title(body)

    def test_persists_across_reload_and_is_private(self):
        sessionmeta.set_pinned(self.home, "claude:a", True, now=3.0)
        sessionmeta.set_title(self.home, "claude:a", "Name")
        again = sessionmeta.load(self.home)
        self.assertEqual(again["claude:a"],
                         {"pinned": True, "pinned_at": 3.0, "title": "Name"})
        mode = sessionmeta.meta_file(self.home).stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_corrupt_file_reads_empty(self):
        f = sessionmeta.meta_file(self.home)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{not json")
        self.assertEqual(sessionmeta.load(self.home), {})

    def test_concurrent_writes_lose_nothing(self):
        def pin(i):
            sessionmeta.set_pinned(self.home, f"claude:s{i}", True)

        def name(i):
            sessionmeta.set_title(self.home, f"claude:t{i}", f"n{i}")

        threads = [threading.Thread(target=pin, args=(i,)) for i in range(20)]
        threads += [threading.Thread(target=name, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        meta = sessionmeta.load(self.home)
        self.assertEqual(len(meta), 40)

    def test_apply_is_repeatable(self):
        row = {"title": "from harness"}
        sessionmeta.apply(row, {"title": "mine", "pinned": True,
                                "pinned_at": 2.0})
        sessionmeta.apply(row, {"title": "mine", "pinned": True,
                                "pinned_at": 2.0})
        self.assertEqual(row["title"], "mine")
        self.assertEqual(row["harness_title"], "from harness")
        sessionmeta.apply(row, None)
        self.assertEqual(row["title"], "from harness")
        self.assertIs(row["pinned"], False)
        self.assertIsNone(row["pinned_at"])


class _NotedLock:
    """The catalog's row lock, noting when a second thread queues on it.

    A race test waits for ``contended`` instead of sleeping, so the
    other thread is known to be blocked on the lock, not merely started.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.contended = threading.Event()

    def __enter__(self):
        if not self._lock.acquire(blocking=False):
            self.contended.set()
            self._lock.acquire()
        return self

    def __exit__(self, *exc):
        self._lock.release()


def _note_lock(cat):
    lock = _NotedLock()
    cat._meta_lock = cat._lock = lock
    return lock


class TestLimitsAndRaces(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_full_store_evicts_other_rename_not_the_write(self):
        with mock.patch.object(sessionmeta, "MAX_ENTRIES", 3):
            sessionmeta.set_title(self.home, "k:old", "old")
            sessionmeta.set_pinned(self.home, "k:p1", True)
            sessionmeta.set_pinned(self.home, "k:p2", True)
            sessionmeta.set_pinned(self.home, "k:new", True)
            meta = sessionmeta.load(self.home)
            self.assertEqual(sorted(meta), ["k:new", "k:p1", "k:p2"])

    def test_full_of_pins_rejects_instead_of_dropping(self):
        with mock.patch.object(sessionmeta, "MAX_ENTRIES", 2):
            sessionmeta.set_pinned(self.home, "k:a", True)
            sessionmeta.set_pinned(self.home, "k:b", True)
            with self.assertRaises(sessionmeta.TooManyEntries):
                sessionmeta.set_pinned(self.home, "k:c", True)
            with self.assertRaises(sessionmeta.TooManyEntries):
                sessionmeta.set_title(self.home, "k:c", "name")
            self.assertEqual(sorted(sessionmeta.load(self.home)),
                             ["k:a", "k:b"])

    def test_route_answers_409_when_full(self):
        srv = _Server(sessions=[_session(sid="a1"), _session(sid="b2")])
        self.addCleanup(srv.stop)
        cred = srv.pair()
        hdr = {"Authorization": f"Bearer {cred['credential']}"}
        with mock.patch.object(sessionmeta, "MAX_ENTRIES", 1):
            s1, _ = srv.request("POST", "/v1/sessions/claude:a1/pin",
                                body={"pinned": True}, headers=hdr)
            s2, data = srv.request("POST", "/v1/sessions/claude:b2/pin",
                                   body={"pinned": True}, headers=hdr)
        self.assertEqual(s1, 200)
        self.assertEqual(s2, 409)
        self.assertEqual(json.loads(data)["error"]["code"], "too_many_pins")

    def test_pin_saved_during_refresh_is_not_lost(self):
        cat = catalog_mod.Catalog(
            self.home, "m1", fetch_sessions=lambda: [_session(sid="a1")],
            status_of=lambda ss: ["idle"] * len(ss))
        cat.refresh()
        key = cat.rows()[0]["key"]
        lock = _note_lock(cat)

        def save_midway():
            cat._after_meta_read = None
            # The request thread saves, then blocks on reapply until the
            # refresh has swapped its rows.
            def request():
                sessionmeta.set_title(self.home, key, "Fresh")
                cat.reapply_meta(key)
            t = threading.Thread(target=request, daemon=True)
            t.start()
            started.append(t)
            self.assertTrue(lock.contended.wait(5))

        started = []
        cat._after_meta_read = save_midway
        cat.refresh()
        # Only the request thread this test started: joining every live
        # thread waited out ones other tests left running.
        for t in started:
            t.join(timeout=3)
            self.assertFalse(t.is_alive())
        self.assertEqual(cat.row_for(key)["title"], "Fresh")


    def test_probe_and_pin_during_one_refresh_both_survive(self):
        verdict = {"probe": None}

        def status_of(sessions):
            # A tail probe lands after this refresh began reading.
            if verdict["probe"]:
                verdict["probe"]()
            return ["done"] * len(sessions)

        cat = catalog_mod.Catalog(
            self.home, "m1", fetch_sessions=lambda: [_session(sid="a1")],
            status_of=status_of)
        cat.refresh()
        key = cat.rows()[0]["key"]
        verdict["probe"] = lambda: cat.note_status(
            key, "active", time.time() + 1)
        lock = _note_lock(cat)

        def save_midway():
            cat._after_meta_read = None
            # A title save and a second probe both wait for the swap.
            def request():
                sessionmeta.set_title(self.home, key, "Fresh")
                cat.note_status(key, "followup", time.time() + 2)
                cat.reapply_meta(key)
            t = threading.Thread(target=request, daemon=True)
            t.start()
            started.append(t)
            self.assertTrue(lock.contended.wait(5))

        started = []
        cat._after_meta_read = save_midway
        cat.refresh()
        # Only the request thread this test started: joining every live
        # thread waited out ones other tests left running.
        for t in started:
            t.join(timeout=3)
            self.assertFalse(t.is_alive())
        row = cat.row_for(key)
        self.assertEqual(row["title"], "Fresh")
        self.assertEqual(row["status"], "followup")
        self.assertEqual(cat.tail_rows()[0][2], "followup")
        # The probe-patched row kept its overlay, and the next refresh,
        # started after both probes, takes its own read but keeps the pin
        # metadata.
        verdict["probe"] = None
        with mock.patch.object(catalog_mod.time, "time",
                               return_value=time.time() + 60):
            cat.refresh()
        row = cat.row_for(key)
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["title"], "Fresh")

    def test_probe_during_refresh_keeps_status_with_overlay(self):
        cat = catalog_mod.Catalog(
            self.home, "m1", fetch_sessions=lambda: [_session(sid="a1")],
            status_of=lambda ss: ["done"] * len(ss))
        cat.refresh()
        key = cat.rows()[0]["key"]
        sessionmeta.set_title(self.home, key, "Mine")

        def probe_midway():
            cat._after_meta_read = None
            cat._fresh[key] = ("active", time.time() + 1)

        cat._after_meta_read = probe_midway
        cat.refresh()
        row = cat.row_for(key)
        self.assertEqual((row["status"], row["title"]), ("active", "Mine"))


class TestRoutes(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[_session(sid="a1"), _session(sid="b2")])
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {cred['credential']}"}

    def post(self, key, action, body):
        status, data = self.srv.request(
            "POST", f"/v1/sessions/{key}/{action}", body=body,
            headers=self.hdr)
        return status, json.loads(data)

    def rows(self):
        _s, data = self.srv.request("GET", "/v1/sessions", headers=self.hdr)
        return {r["key"]: r for r in json.loads(data)["sessions"]}

    def test_rows_carry_defaults(self):
        row = self.rows()["claude:a1"]
        self.assertIs(row["pinned"], False)
        self.assertIsNone(row["pinned_at"])
        self.assertEqual(row["title"], "test")
        self.assertEqual(row["harness_title"], "test")

    def test_pin_and_unpin(self):
        status, body = self.post("claude:a1", "pin", {"pinned": True})
        self.assertEqual(status, 200)
        self.assertIs(body["pinned"], True)
        self.assertIsInstance(body["pinned_at"], float)
        row = self.rows()["claude:a1"]
        self.assertIs(row["pinned"], True)
        self.assertEqual(row["pinned_at"], body["pinned_at"])
        self.assertIs(self.rows()["claude:b2"]["pinned"], False)
        status, body = self.post("claude:a1", "pin", {"pinned": False})
        self.assertEqual(status, 200)
        self.assertIsNone(self.rows()["claude:a1"]["pinned_at"])

    def test_title_override_and_clear(self):
        status, body = self.post("claude:a1", "title", {"title": " Release "})
        self.assertEqual(status, 200)
        self.assertEqual(body["title"], "Release")
        self.assertEqual(body["harness_title"], "test")
        self.assertEqual(self.rows()["claude:a1"]["title"], "Release")
        _s, data = self.srv.request("GET", "/v1/search?q=Release",
                                    headers=self.hdr)
        self.assertEqual(_s, 200)
        # Search finds the session by its new name.
        self.assertIn("claude:a1",
                      [r["key"] for r in json.loads(data)["results"]])
        status, body = self.post("claude:a1", "title", {"title": ""})
        self.assertEqual(body["title"], "test")
        self.assertEqual(self.rows()["claude:a1"]["title"], "test")

    def test_survives_catalog_refresh_and_restart(self):
        self.post("claude:a1", "pin", {"pinned": True})
        self.post("claude:a1", "title", {"title": "Kept"})
        self.srv.state.catalog.refresh()
        row = self.rows()["claude:a1"]
        self.assertEqual((row["title"], row["pinned"]), ("Kept", True))
        fresh = catalog_mod.Catalog(
            self.srv.home, "m1", fetch_sessions=lambda: [_session(sid="a1")],
            status_of=lambda ss: ["idle"] * len(ss))
        fresh.refresh()
        self.assertEqual(fresh.rows()[0]["title"], "Kept")

    def test_inbox_uses_override(self):
        srv = _Server(sessions=[_session(sid="f1", ts=__import__("time").time())],
                      statuses=lambda ss: ["followup"])
        self.addCleanup(srv.stop)
        cred = srv.pair()
        hdr = {"Authorization": f"Bearer {cred['credential']}"}
        srv.request("POST", "/v1/sessions/claude:f1/title",
                    body={"title": "Chosen"}, headers=hdr)
        _s, data = srv.request("GET", "/v1/inbox", headers=hdr)
        self.assertEqual(json.loads(data)["items"][0]["title"], "Chosen")

    def test_unknown_session_404(self):
        for action, body in (("pin", {"pinned": True}),
                             ("title", {"title": "x"})):
            status, err = self.post("claude:nope", action, body)
            self.assertEqual(status, 404)
            self.assertEqual(err["error"]["code"], "unknown_session")

    def test_bad_bodies_422(self):
        cases = [("pin", {}), ("pin", {"pinned": "yes"}),
                 ("pin", {"pinned": 1}), ("pin", {"pinned": ["a"]}),
                 ("title", {}), ("title", {"title": 5}),
                 ("title", {"title": {"a": 1}}), ("title", {"title": None})]
        for action, body in cases:
            status, err = self.post("claude:a1", action, body)
            self.assertEqual(status, 422, (action, body))
            self.assertEqual(err["error"]["code"], "bad_command")
        status, _ = self.srv.request(
            "POST", "/v1/sessions/claude:a1/pin", body=[1], headers=self.hdr)
        self.assertEqual(status, 400)

    def test_requires_auth(self):
        status, _ = self.srv.request(
            "POST", "/v1/sessions/claude:a1/pin", body={"pinned": True})
        self.assertEqual(status, 401)

    def test_emits_session_meta_event(self):
        q = self.srv.state.bus.subscribe("claude:a1")
        self.post("claude:a1", "pin", {"pinned": True})
        ev = q.get(timeout=2)
        self.assertEqual(ev["type"], "session.meta")
        self.assertIs(ev["data"]["pinned"], True)
        self.assertEqual(ev["data"]["harness_title"], "test")

    def test_satellite_session_works(self):
        catalog_mod.merge_remote_rows(self.srv.home, "sat1", [{
            "key": "claude:r1", "title": "remote one", "status": "idle",
            "harness": "claude", "agent": "Claude Code", "cwd": "/x",
            "last_active_at": 1.0, "ownership": "observed",
        }])
        self.srv.state.catalog.refresh()
        key = protocol.remote_key("sat1", "claude:r1")
        self.assertIn(key, self.rows())
        status, body = self.post(key, "pin", {"pinned": True})
        self.assertEqual(status, 200)
        status, body = self.post(key, "title", {"title": "Mine"})
        self.assertEqual(body["harness_title"], "remote one")
        row = self.rows()[key]
        self.assertEqual((row["title"], row["pinned"]), ("Mine", True))


if __name__ == "__main__":
    unittest.main()
