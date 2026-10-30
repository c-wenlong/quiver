"""Revoking a lost phone that shares the owner's tailnet login (#966).

The owner's own tailnet devices get in without pairing. Revoke used to
refuse their ``tailnet:<node>`` ids and nothing else could stop a node,
so a lost phone on the owner's login walked straight back in. The hub
now records those sign-ins, lists them, and refuses a revoked node until
a pairing from it is approved again; the owner's other nodes keep
working.
"""

import json
import unittest
from unittest.mock import patch

from quiver.remote import commands, pairing
from quiver.remote import devices as devices_mod

from tests.test_remote_commands import _run
from tests.test_remote_hub import _Server, _session

_PHONE_IP, _LAPTOP_IP = "100.64.0.9", "100.64.0.10"
_PEERS = {
    _PHONE_IP: {"node_id": "n-phone", "login": "dev@github",
                "node_name": "iphone.ts.net", "tagged": False},
    _LAPTOP_IP: {"node_id": "n-laptop", "login": "dev@github",
                 "node_name": "laptop.ts.net", "tagged": False},
}


class _Owner(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[_session()])
        self.addCleanup(self.srv.stop)
        state = self.srv.state
        state.trust_proxy = True
        state.proxy_peer = lambda server, peer: True
        state.owner_login = "dev@github"
        state.whois = lambda ip: _PEERS.get(ip)
        self.home = self.srv.home

    def owner(self, ip, method="GET", path="/v1/devices", body=None):
        headers = {"X-Forwarded-For": ip, "X-Quiver-Pair": "1"}
        if method == "POST":
            headers["Content-Type"] = "application/json"
        status, data = self.srv.request(method, path, body, headers=headers)
        return status, (json.loads(data) if data else None)

    def revoke(self, ip, device_id, **extra):
        return self.owner(ip, "POST", "/v1/devices/revoke",
                          {"device_id": device_id, **extra})


class TestSignins(_Owner):
    def test_owner_devices_are_listed(self):
        self.assertEqual(self.owner(_PHONE_IP)[0], 200)
        status, view = self.owner(_LAPTOP_IP)
        self.assertEqual(status, 200)
        rows = {r["id"]: r for r in view["signins"]}
        self.assertEqual(set(rows), {"tailnet:n-phone", "tailnet:n-laptop"})
        self.assertEqual(rows["tailnet:n-phone"]["node_name"], "iphone.ts.net")
        self.assertTrue(rows["tailnet:n-laptop"]["this_device"])
        self.assertFalse(rows["tailnet:n-phone"]["this_device"])
        self.assertFalse(rows["tailnet:n-phone"]["revoked"])

    def test_revoking_the_lost_phone_locks_only_it_out(self):
        self.owner(_PHONE_IP)
        status, reply = self.revoke(_LAPTOP_IP, "tailnet:n-phone")
        self.assertEqual(status, 200, reply)
        self.assertFalse(reply["this_device"])
        phone = next(r for r in reply["signins"] if r["id"] == "tailnet:n-phone")
        self.assertTrue(phone["revoked"])
        self.assertEqual(self.owner(_PHONE_IP)[0], 401)
        self.assertEqual(self.owner(_LAPTOP_IP)[0], 200)

    def test_a_revoked_node_cannot_approve_pair_requests(self):
        self.owner(_PHONE_IP)
        pairing.revoke_signin(self.home, "n-phone")
        status, _ = self.owner(_PHONE_IP, "POST", "/v1/pair/requests/approve",
                               {"code": "123456"})
        self.assertEqual(status, 401)

    def test_refusals(self):
        self.owner(_PHONE_IP)
        self.assertEqual(self.revoke(_LAPTOP_IP, "tailnet:n-nobody")[1]["error"]["code"],
                         "unknown_device")
        self.assertEqual(self.revoke(_LAPTOP_IP, "tailnet:")[1]["error"]["code"],
                         "unknown_device")
        self.revoke(_LAPTOP_IP, "tailnet:n-phone")
        status, reply = self.revoke(_LAPTOP_IP, "tailnet:n-phone")
        self.assertEqual((status, reply["error"]["code"]), (409, "already_revoked"))

    def test_revoking_ones_own_sign_in_needs_confirm(self):
        self.owner(_LAPTOP_IP)
        status, reply = self.revoke(_LAPTOP_IP, "tailnet:n-laptop")
        self.assertEqual((status, reply["error"]["code"]), (409, "confirm_this_device"))
        self.assertEqual(self.owner(_LAPTOP_IP)[0], 200)
        status, reply = self.revoke(_LAPTOP_IP, "tailnet:n-laptop", confirm_this_device=True)
        self.assertEqual(status, 200, reply)
        self.assertTrue(reply["this_device"])
        self.assertEqual(self.owner(_LAPTOP_IP)[0], 401)


class TestPairedDevicesAndNodes(_Owner):
    def pair_from(self, ip):
        peer = {**_PEERS[ip], "ip": ip}
        req = pairing.request_pair(self.home, peer, "phone")
        return pairing.approve_request(self.home, req["code"], req["request_id"])

    def test_revoking_a_tailnet_paired_device_revokes_its_node(self):
        dev = self.pair_from(_PHONE_IP)
        self.assertEqual(self.owner(_PHONE_IP)[0], 200)
        pairing.revoke_device(self.home, dev["device_id"])
        self.assertTrue(pairing.signin_revoked(self.home, "n-phone"))
        self.assertEqual(self.owner(_PHONE_IP)[0], 401)
        self.assertEqual(self.owner(_LAPTOP_IP)[0], 200)

    def test_a_token_paired_device_leaves_nodes_alone(self):
        dev = self.srv.pair()
        pairing.revoke_device(self.home, dev["device_id"])
        self.assertEqual(pairing.load_signins(self.home), {})

    def test_revoking_a_node_signs_out_devices_paired_from_it(self):
        dev = self.pair_from(_PHONE_IP)
        other = self.pair_from(_LAPTOP_IP)
        self.owner(_PHONE_IP)
        self.assertTrue(pairing.revoke_signin(self.home, "n-phone"))
        rows = pairing.load_devices(self.home)
        self.assertTrue(rows[dev["device_id"]]["revoked_at"])
        self.assertFalse(rows[other["device_id"]]["revoked_at"])

    def test_approving_a_new_pairing_from_the_node_restores_it(self):
        self.owner(_PHONE_IP)
        pairing.revoke_signin(self.home, "n-phone")
        self.assertEqual(self.owner(_PHONE_IP)[0], 401)
        self.pair_from(_PHONE_IP)
        self.assertFalse(pairing.signin_revoked(self.home, "n-phone"))
        self.assertEqual(self.owner(_PHONE_IP)[0], 200)

    def test_unknown_node_is_not_revocable(self):
        self.assertFalse(pairing.revoke_signin(self.home, "n-ghost"))


class TestReviewFixes(_Owner):
    def pair_from(self, ip):
        peer = {**_PEERS[ip], "ip": ip}
        req = pairing.request_pair(self.home, peer, "phone")
        return pairing.approve_request(self.home, req["code"], req["request_id"])

    def test_every_credential_from_the_node_goes(self):
        first, second = self.pair_from(_PHONE_IP), self.pair_from(_PHONE_IP)
        laptop = self.pair_from(_LAPTOP_IP)
        pairing.revoke_device(self.home, first["device_id"])
        rows = pairing.load_devices(self.home)
        self.assertTrue(rows[second["device_id"]]["revoked_at"])
        self.assertFalse(rows[laptop["device_id"]]["revoked_at"])

    def test_open_streams_see_a_revoked_sign_in(self):
        from quiver.remote import hub as hub_mod

        self.owner(_PHONE_IP)
        self.assertFalse(hub_mod._Handler._device_revoked(self.home, "tailnet:n-phone"))
        pairing.revoke_signin(self.home, "n-phone")
        self.assertTrue(hub_mod._Handler._device_revoked(self.home, "tailnet:n-phone"))

    def test_a_damaged_file_fails_closed_and_is_never_overwritten(self):
        self.owner(_PHONE_IP)
        path = pairing.signins_file(self.home)
        path.write_text("{not json")
        self.assertTrue(pairing.signin_revoked(self.home, "n-phone"))
        self.assertTrue(pairing.signin_revoked(self.home, "n-anything"))
        self.assertEqual(self.owner(_LAPTOP_IP)[0], 401)
        pairing.note_signin(self.home, "n-laptop", now=10.0 ** 9)
        self.assertEqual(path.read_text(), "{not json")
        with self.assertRaises(OSError):
            pairing.revoke_signin(self.home, "n-phone")
        path.write_text('{"signins": []}')
        self.assertTrue(pairing.signin_revoked(self.home, "n-phone"))

    def test_a_failed_sign_in_save_leaves_a_retry_that_finishes(self):
        dev = self.pair_from(_PHONE_IP)
        self.owner(_PHONE_IP)
        with patch.object(pairing, "_save_signins", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                pairing.revoke_signin(self.home, "n-phone")
        self.assertTrue(pairing.load_devices(self.home)[dev["device_id"]]["revoked_at"])
        self.assertFalse(pairing.signin_revoked(self.home, "n-phone"))
        status, reply = self.revoke(_LAPTOP_IP, "tailnet:n-phone")
        self.assertEqual(status, 200, reply)
        self.assertTrue(pairing.signin_revoked(self.home, "n-phone"))

    def test_keys_are_resynced_on_every_revoke(self):
        self.owner(_PHONE_IP)
        with patch("quiver.remote.sshkeys.sync") as sync:
            pairing.revoke_signin(self.home, "n-phone")
        sync.assert_called_once()

    def test_a_paired_phone_revoking_its_own_node_must_confirm(self):
        dev = self.pair_from(_PHONE_IP)
        self.owner(_PHONE_IP)
        auth = {"Authorization": f"Bearer {self._credential(dev)}",
                "Content-Type": "application/json"}
        status, data = self.srv.request("POST", "/v1/devices/revoke",
                                        {"device_id": "tailnet:n-phone"}, headers=auth)
        self.assertEqual((status, json.loads(data)["error"]["code"]), (409, "confirm_this_device"))
        status, data = self.srv.request(
            "POST", "/v1/devices/revoke",
            {"device_id": "tailnet:n-phone", "confirm_this_device": True}, headers=auth)
        self.assertEqual(status, 200, data)
        self.assertTrue(json.loads(data)["this_device"])

    def test_an_owner_node_revoking_a_device_paired_from_it_must_confirm(self):
        dev = self.pair_from(_PHONE_IP)
        status, reply = self.revoke(_PHONE_IP, dev["device_id"])
        self.assertEqual((status, reply["error"]["code"]), (409, "confirm_this_device"))
        status, reply = self.revoke(_PHONE_IP, dev["device_id"], confirm_this_device=True)
        self.assertEqual(status, 200, reply)
        self.assertTrue(reply["this_device"])

    def _credential(self, dev):
        rows = pairing._load_requests(self.home)
        return next(r["credential"] for r in rows if r.get("device_id") == dev["device_id"])


class TestNoteSignin(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.home = Path(tempfile.mkdtemp())

    def test_seen_at_most_every_touch_interval(self):
        pairing.note_signin(self.home, "n1", "one", now=1000.0)
        pairing.note_signin(self.home, "n1", "renamed", now=1000.0 + pairing.SIGNIN_TOUCH_S - 1)
        row = pairing.load_signins(self.home)["n1"]
        self.assertEqual((row["first_seen_at"], row["last_seen_at"], row["node_name"]),
                         (1000.0, 1000.0, "one"))
        pairing.note_signin(self.home, "n1", "renamed", now=1000.0 + pairing.SIGNIN_TOUCH_S)
        row = pairing.load_signins(self.home)["n1"]
        self.assertEqual((row["first_seen_at"], row["node_name"]), (1000.0, "renamed"))

    def test_revoked_rows_and_empty_ids_are_left_alone(self):
        pairing.note_signin(self.home, "", "x")
        self.assertEqual(pairing.load_signins(self.home), {})
        pairing.note_signin(self.home, "n1", "one", now=1.0)
        pairing.revoke_signin(self.home, "n1")
        pairing.note_signin(self.home, "n1", "one", now=10_000.0)
        self.assertEqual(pairing.load_signins(self.home)["n1"]["last_seen_at"], 1.0)

    def test_a_parallel_write_is_seen_under_the_lock(self):
        pairing.note_signin(self.home, "n1", "one", now=1000.0)
        # The unlocked read is stale (another request is mid-write); the
        # one under the lock sees the fresh row and writes nothing.
        fresh = pairing._read_signins(self.home)
        with patch.object(pairing, "_read_signins", side_effect=[{}, fresh]), \
                patch.object(pairing, "_save_signins") as save:
            pairing.note_signin(self.home, "n1", "one", now=1001.0)
        save.assert_not_called()

    def test_file_is_private(self):
        pairing.note_signin(self.home, "n1", "one")
        self.assertEqual(pairing.signins_file(self.home).stat().st_mode & 0o777, 0o600)

    def test_rows_sort_newest_first(self):
        pairing.note_signin(self.home, "old", now=1.0)
        pairing.note_signin(self.home, "new", now=5.0)
        ids = [r["id"] for r in devices_mod.signin_rows(self.home, None)]
        self.assertEqual(ids, ["tailnet:new", "tailnet:old"])


class TestCli(unittest.TestCase):
    def test_revoke_tailnet_id(self):
        with patch.object(commands.pairing, "revoke_signin", return_value=True) as rev:
            rc, out, _ = _run(["devices", "--revoke=tailnet:n-phone"])
        self.assertEqual(rc, 0)
        rev.assert_called_once_with(None, "n-phone")
        self.assertIn("revoked tailnet:n-phone", out)

    def test_listing_shows_sign_ins(self):
        rows = {"n-phone": {"node_name": "iphone.ts.net", "last_seen_at": 5.0}}
        with patch.object(commands.pairing, "load_signins", return_value=rows), \
                patch.object(commands.pairing, "load_devices", return_value={}):
            rc, out, _ = _run(["devices"])
        self.assertEqual(rc, 0)
        self.assertIn("tailnet:n-phone", out)
        self.assertIn("iphone.ts.net", out)


if __name__ == "__main__":
    unittest.main()
