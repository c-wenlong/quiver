"""readstate.py and POST /v1/sessions/<key>/read: per-device unread."""

import json
import time
import unittest

from swe.remote import catalog as catalog_mod
from swe.remote import pairing, readstate

from tests.test_remote_hub import _Server, _session


class TestReadRoute(unittest.TestCase):
    def setUp(self):
        self.t = time.time() - 60
        self.srv = _Server(sessions=[_session(sid="a", ts=self.t),
                                     _session(sid="b", ts=self.t)])
        self.addCleanup(self.srv.stop)
        # Pretend the store began (and these devices paired) long ago,
        # so both sessions moved after the baseline.
        readstate._since(self.srv.home, self.t - 3600)

    def hdr(self, cred):
        return {"Authorization": f"Bearer {cred['credential']}"}

    def pair_old(self):
        cred = self.srv.pair()
        devices = pairing.load_devices(self.srv.home)
        devices[cred["device_id"]]["created_at"] = self.t - 3600
        pairing._save_devices(self.srv.home, devices)
        return cred

    def unread(self, cred, path="/v1/sessions"):
        status, data = self.srv.request("GET", path, headers=self.hdr(cred))
        self.assertEqual(status, 200, data)
        return {r["key"]: r["unread"] for r in json.loads(data)["sessions"]}

    def read(self, cred, key, body=None):
        return self.srv.request(
            "POST", f"/v1/sessions/{key}/read", body=body,
            headers=self.hdr(cred) if cred else {})

    def touch(self, sid, ts):
        sessions = [_session(sid="a", ts=self.t), _session(sid="b", ts=self.t)]
        sessions = [_session(sid=sid, ts=ts) if s.session_id == sid else s
                    for s in sessions]
        self.srv.state.catalog._fetch = lambda: sessions
        self.srv.state.catalog.refresh()

    def test_requires_auth_and_known_session(self):
        status, _ = self.read(None, "claude:a")
        self.assertEqual(status, 401)
        cred = self.pair_old()
        status, _ = self.read(cred, "claude:nope")
        self.assertEqual(status, 404)
        status, data = self.read(cred, "claude:a", {"at": "soon"})
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(data)["error"]["code"], "bad_read")
        status, _ = self.read(cred, "claude:a", {"at": 10 ** 400})
        self.assertEqual(status, 422)

    def test_read_clears_for_this_device_only(self):
        phone, laptop = self.pair_old(), self.pair_old()
        self.assertEqual(self.unread(phone),
                         {"claude:a": True, "claude:b": True})
        status, data = self.read(phone, "claude:a")
        self.assertEqual(status, 200, data)
        self.assertEqual(self.unread(phone),
                         {"claude:a": False, "claude:b": True})
        self.assertEqual(self.unread(laptop),
                         {"claude:a": True, "claude:b": True})
        # The catalog's shared rows never pick up a device's flag.
        self.assertNotIn("unread", self.srv.state.catalog.row_for("claude:a"))

    def test_new_activity_makes_it_unread_again(self):
        cred = self.pair_old()
        self.read(cred, "claude:a")
        self.touch("a", time.time() + 30)
        self.assertTrue(self.unread(cred)["claude:a"])

    def test_at_is_capped_and_never_moves_back(self):
        cred = self.pair_old()
        status, data = self.read(cred, "claude:a", {"at": 1e12})
        self.assertEqual(status, 200)
        stored = json.loads(data)["at"]
        self.assertLess(stored, time.time() + 5)
        status, data = self.read(cred, "claude:a", {"at": self.t - 10})
        self.assertEqual(json.loads(data)["at"], stored)

    def test_ahead_clock_row_reads_as_read(self):
        # A satellite clock ahead of the hub: the default cursor is the
        # row's own last_active_at, not the hub's now.
        cred = self.pair_old()
        self.touch("a", time.time() + 120)
        self.read(cred, "claude:a")
        self.assertFalse(self.unread(cred)["claude:a"])

    def test_new_device_starts_with_nothing_unread(self):
        cred = self.srv.pair()  # paired now, after both sessions moved
        self.assertEqual(self.unread(cred),
                         {"claude:a": False, "claude:b": False})
        self.touch("b", time.time() + 30)
        self.assertEqual(self.unread(cred),
                         {"claude:a": False, "claude:b": True})

    def test_satellite_row_reads_on_the_hub(self):
        sat = self.srv.satellite("m2")
        status, data = self.srv.request(
            "POST", "/v1/agent/catalog",
            {"machine_id": "m2", "rows": [{
                "key": "claude:r", "harness": "claude",
                "last_active_at": time.time(), "status": "idle"}]},
            headers=sat)
        self.assertEqual(status, 200, data)
        cred = self.pair_old()
        key = "claude:r@m2"
        self.assertTrue(self.unread(cred)[key])
        status, data = self.read(cred, key.replace("@", "%40"))
        self.assertEqual(status, 200, data)
        self.assertFalse(self.unread(cred)[key])

    def test_app_sessions_rows_carry_unread(self):
        cred = self.pair_old()
        self.read(cred, "claude:a")
        self.assertEqual(self.unread(cred, "/v1/app/sessions"),
                         {"claude:a": False, "claude:b": True})

    def test_store_sits_beside_devices_and_is_private(self):
        cred = self.pair_old()
        self.read(cred, "claude:a")
        path = readstate.reads_file(self.srv.home)
        self.assertEqual(path.parent,
                         pairing.devices_file(self.srv.home).parent)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class TestPruning(unittest.TestCase):
    def setUp(self):
        from tests._tempdir import temp_dir
        self.home = temp_dir(self)
        self.d1 = pairing.exchange(
            self.home, pairing.mint_pair_token(self.home), "one")["device_id"]
        self.d2 = pairing.exchange(
            self.home, pairing.mint_pair_token(self.home), "two")["device_id"]

    def test_gone_sessions_drop_on_write(self):
        readstate.mark_read(self.home, self.d1, "k1", 5.0, ["k1", "k2"])
        readstate.mark_read(self.home, self.d1, "k2", 6.0, ["k2", "k3"])
        self.assertEqual(readstate.cursors(self.home, self.d1), {"k2": 6.0})

    def test_empty_catalog_prunes_nothing(self):
        readstate.mark_read(self.home, self.d1, "k1", 5.0, ["k1", "k2"])
        readstate.mark_read(self.home, self.d1, "k2", 6.0, [])
        self.assertEqual(readstate.cursors(self.home, self.d1),
                         {"k1": 5.0, "k2": 6.0})

    def test_a_catalog_of_only_the_read_session_still_prunes(self):
        readstate.mark_read(self.home, self.d1, "k1", 5.0, ["k1", "k2"])
        readstate.mark_read(self.home, self.d1, "k2", 6.0, ["k2"])
        self.assertEqual(readstate.cursors(self.home, self.d1),
                         {"k2": 6.0})

    def test_an_unreadable_store_is_not_erased(self):
        readstate.mark_read(self.home, self.d1, "k1", 5.0, ["k1"])
        path = readstate.reads_file(self.home)
        before = path.read_text()
        path.write_text("not json")
        readstate.mark_read(self.home, self.d1, "k2", 6.0, ["k2"])
        readstate._since(self.home, 1.0)
        self.assertEqual(path.read_text(), "not json")
        self.assertEqual(readstate.cursors(self.home, self.d1), {})
        path.write_text(before)
        self.assertEqual(readstate.cursors(self.home, self.d1),
                         {"k1": 5.0})

    def test_revoked_and_unknown_devices_drop(self):
        readstate.mark_read(self.home, self.d1, "k", 5.0, ["k"])
        readstate.mark_read(self.home, "ghost", "k", 5.0, ["k"])
        pairing.revoke_device(self.home, self.d1)
        readstate.mark_read(self.home, self.d2, "k", 5.0, ["k"])
        raw = json.loads(readstate.reads_file(self.home).read_text())
        self.assertEqual(list(raw["devices"]), [self.d2])

    def test_tailnet_identity_kept_until_its_signin_is_revoked(self):
        pairing.note_signin(self.home, "n1", "laptop")
        readstate.mark_read(self.home, "tailnet:n1", "k", 5.0, ["k"])
        readstate.mark_read(self.home, self.d1, "k", 5.0, ["k"])
        self.assertEqual(readstate.cursors(self.home, "tailnet:n1"),
                         {"k": 5.0})
        pairing.revoke_signin(self.home, "n1")
        readstate.mark_read(self.home, self.d1, "k", 6.0, ["k"])
        self.assertEqual(readstate.cursors(self.home, "tailnet:n1"), {})

    def test_per_device_cap_keeps_newest(self):
        old = readstate.MAX_PER_DEVICE
        readstate.MAX_PER_DEVICE = 2
        self.addCleanup(setattr, readstate, "MAX_PER_DEVICE", old)
        for i in range(4):
            readstate.mark_read(self.home, self.d1, f"k{i}", float(i), [])
        self.assertEqual(readstate.cursors(self.home, self.d1),
                         {"k2": 2.0, "k3": 3.0})

    def test_baseline_is_later_of_pairing_and_store_start(self):
        now = time.time()
        devices = pairing.load_devices(self.home)
        devices[self.d1]["created_at"] = now - 100
        pairing._save_devices(self.home, devices)
        readstate._since(self.home, now - 500)
        self.assertEqual(readstate.baseline(self.home, self.d1), now - 100)
        devices[self.d1]["created_at"] = now - 900
        pairing._save_devices(self.home, devices)
        self.assertEqual(readstate.baseline(self.home, self.d1), now - 500)

    def test_no_device_means_nothing_unread(self):
        rows = readstate.annotate(self.home, None,
                                  [{"key": "k", "last_active_at": 1e12}])
        self.assertEqual(rows, [{"key": "k", "last_active_at": 1e12,
                                 "unread": False}])


if __name__ == "__main__":
    unittest.main()
