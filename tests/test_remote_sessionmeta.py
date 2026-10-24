"""sessionmeta.py and the pin/title routes: store, overlay, hub dispatch."""

import json
import tempfile
import threading
import unittest
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
