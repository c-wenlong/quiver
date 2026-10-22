"""Pairing: one-time tokens, device credentials, revocation."""

import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.remote import pairing


class TestPairing(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_exchange_round_trip(self):
        token = pairing.mint_pair_token(self.tmp)
        cred = pairing.exchange(self.tmp, token, "iphone")
        self.assertIsNotNone(cred)
        self.assertIn("credential", cred)
        hit = pairing.device_for_credential(self.tmp, cred["credential"])
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], cred["device_id"])

    def test_token_is_single_use(self):
        token = pairing.mint_pair_token(self.tmp)
        self.assertIsNotNone(pairing.exchange(self.tmp, token))
        self.assertIsNone(pairing.exchange(self.tmp, token))

    def test_bad_or_expired_token_rejected(self):
        self.assertIsNone(pairing.exchange(self.tmp, "nope"))
        token = pairing.mint_pair_token(self.tmp)
        # Backdate the expiry by rewriting the tokens file.
        path = pairing.tokens_file(self.tmp)
        import json
        data = json.loads(path.read_text())
        data["tokens"][0]["expires_at"] = time.time() - 1
        path.write_text(json.dumps(data))
        self.assertIsNone(pairing.exchange(self.tmp, token))

    def test_devices_file_is_private(self):
        token = pairing.mint_pair_token(self.tmp)
        pairing.exchange(self.tmp, token)
        mode = stat.S_IMODE(os.stat(pairing.devices_file(self.tmp)).st_mode)
        self.assertEqual(mode & 0o777, 0o600)

    def test_revoked_device_fails_auth(self):
        token = pairing.mint_pair_token(self.tmp)
        cred = pairing.exchange(self.tmp, token)
        self.assertTrue(pairing.revoke_device(self.tmp, cred["device_id"]))
        self.assertIsNone(
            pairing.device_for_credential(self.tmp, cred["credential"])
        )

    def test_pair_payload_and_url(self):
        payload = pairing.pair_payload("100.1.2.3", 8477, "tok")
        self.assertIn("quiver://pair", payload)
        self.assertIn("token=tok", payload)
        url = pairing.pair_url("100.1.2.3", 8477, "tok")
        self.assertIn("#pair=tok", url)

    def test_pair_payload_carries_name(self):
        payload = pairing.pair_payload("h", 1, "tok", "kai's phone")
        self.assertIn("name=kai%27s%20phone", payload)
        url = pairing.pair_url("h", 1, "tok", "iphone 13")
        self.assertIn("name=iphone%2013", url)

    def test_concurrent_exchange_spend_token_once(self):
        # Two racing requests for one token: exactly one mints a device.
        import threading
        token = pairing.mint_pair_token(self.tmp)
        results = []
        lock = threading.Lock()

        def race():
            out = pairing.exchange(self.tmp, token, "phone")
            with lock:
                results.append(out)

        threads = [threading.Thread(target=race) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wins = [r for r in results if r is not None]
        self.assertEqual(len(wins), 1)
        self.assertEqual(len(pairing.load_devices(self.tmp)), 1)

    def test_concurrent_mints_keep_every_token(self):
        import json
        import threading
        tokens = []
        lock = threading.Lock()

        def mint():
            with lock:
                tokens.append(pairing.mint_pair_token(self.tmp))

        threads = [threading.Thread(target=mint) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        saved = {
            t["token"]
            for t in json.loads(
                pairing.tokens_file(self.tmp).read_text()
            )["tokens"]
        }
        self.assertEqual(set(tokens), saved)


if __name__ == "__main__":
    unittest.main()


_PEER = {
    "node_name": "iphone-13.taild6a23f.ts.net",
    "node_short": "iphone-13",
    "node_id": "nABC",
    "login": "kai@github",
    "ip": "100.66.1.2",
    "tagged": False,
}


class TestPairRequests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _request(self, peer=None, name=""):
        return pairing.request_pair(self.tmp, dict(peer or _PEER), name)

    def test_request_shape_and_pending_poll(self):
        r = self._request(name="my phone")
        self.assertEqual(len(r["code"]), 6)
        self.assertTrue(r["code"].isdigit())
        self.assertEqual(r["expires_in_s"], pairing.REQUEST_TTL_S)
        self.assertEqual(r["node"]["name"], "iphone-13.taild6a23f.ts.net")
        out = pairing.poll_request(self.tmp, r["request_id"], r["poll_secret"])
        self.assertEqual(out["status"], "pending")
        self.assertGreater(out["expires_in_s"], 0)

    def test_request_defaults_name_to_node(self):
        r = self._request()
        row = [
            x for x in pairing._load_requests(self.tmp)
            if x["request_id"] == r["request_id"]
        ][0]
        self.assertEqual(row["name"], "iphone-13")

    def test_poll_bad_secret_and_unknown_id(self):
        r = self._request()
        self.assertIsNone(
            pairing.poll_request(self.tmp, r["request_id"], "wrong")
        )
        self.assertIsNone(
            pairing.poll_request(self.tmp, "nope", r["poll_secret"])
        )

    def test_approve_delivers_credential_once(self):
        r = self._request()
        res = pairing.approve_request(self.tmp, r["code"])
        self.assertIsNotNone(res)
        self.assertTrue(res["device_id"])
        out = pairing.poll_request(self.tmp, r["request_id"], r["poll_secret"])
        self.assertEqual(out["status"], "approved")
        self.assertEqual(out["device_id"], res["device_id"])
        self.assertTrue(out["credential"])
        # Delivered once; the row is gone after.
        self.assertIsNone(
            pairing.poll_request(self.tmp, r["request_id"], r["poll_secret"])
        )
        hit = pairing.device_for_credential(self.tmp, out["credential"])
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], res["device_id"])
        row = hit[1]
        self.assertEqual(row["paired_via"], "tailscale")
        self.assertEqual(row["tailscale_node"], "nABC")
        self.assertEqual(row["tailscale_login"], "kai@github")

    def test_approve_wrong_code_and_lockout(self):
        r = self._request()
        for _ in range(pairing.MAX_CODE_ATTEMPTS):
            self.assertIsNone(
                pairing.approve_request(
                    self.tmp, "000000" if r["code"] != "000000" else "999999",
                    request_id=r["request_id"],
                )
            )
        out = pairing.poll_request(self.tmp, r["request_id"], r["poll_secret"])
        self.assertEqual(out["status"], "denied")

    def test_wrong_code_without_id_does_not_bump(self):
        r = self._request()
        self.assertIsNone(pairing.approve_request(self.tmp, "zzzzzz"))
        self.assertIsNotNone(pairing.approve_request(self.tmp, r["code"]))

    def test_self_approval_refused(self):
        r = self._request()
        with self.assertRaises(pairing.SelfApproval):
            pairing.approve_request(
                self.tmp, r["code"], approver_node_id="nABC"
            )
        # other node approves fine
        self.assertIsNotNone(
            pairing.approve_request(
                self.tmp, r["code"], approver_node_id="nOTHER"
            )
        )

    def test_deny(self):
        r = self._request()
        self.assertTrue(pairing.deny_request(self.tmp, r["code"]))
        self.assertEqual(
            pairing.poll_request(
                self.tmp, r["request_id"], r["poll_secret"]
            )["status"],
            "denied",
        )

    def test_expiry(self):
        import json
        r = self._request()
        path = pairing.requests_file(self.tmp)
        data = json.loads(path.read_text())
        data["requests"][0]["expires_at"] = time.time() - 1
        path.write_text(json.dumps(data))
        self.assertIsNone(
            pairing.poll_request(self.tmp, r["request_id"], r["poll_secret"])
        )

    def test_same_node_replaces_pending(self):
        r1 = self._request()
        r2 = self._request()
        self.assertNotEqual(r1["request_id"], r2["request_id"])
        self.assertIsNone(
            pairing.poll_request(
                self.tmp, r1["request_id"], r1["poll_secret"]
            )
        )
        self.assertIsNotNone(
            pairing.poll_request(
                self.tmp, r2["request_id"], r2["poll_secret"]
            )
        )

    def test_max_pending_raises_pairbusy(self):
        for i in range(pairing.MAX_PENDING):
            self._request(peer={**_PEER, "node_id": f"n{i}"})
        with self.assertRaises(pairing.PairBusy):
            self._request(peer={**_PEER, "node_id": "nX"})

    def test_list_requests_hides_secrets(self):
        self._request(name="ph")
        rows = pairing.list_requests(self.tmp)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("code", rows[0])
        self.assertNotIn("poll_secret_hash", rows[0])
        self.assertIn("expires_in_s", rows[0])

    def test_requests_file_is_private(self):
        self._request()
        mode = stat.S_IMODE(
            os.stat(pairing.requests_file(self.tmp)).st_mode
        )
        self.assertEqual(mode & 0o777, 0o600)


    def test_approve_second_pending_by_code_only(self):
        r1 = self._request(peer={**_PEER, "node_id": "n1"})
        r2 = self._request(peer={**_PEER, "node_id": "n2"})
        res = pairing.approve_request(self.tmp, r2["code"])
        self.assertIsNotNone(res)
        self.assertEqual(
            pairing.poll_request(
                self.tmp, r2["request_id"], r2["poll_secret"]
            )["status"],
            "approved",
        )
        self.assertEqual(
            pairing.poll_request(
                self.tmp, r1["request_id"], r1["poll_secret"]
            )["status"],
            "pending",
        )

    def test_denied_survives_list_and_polls_once(self):
        r = self._request()
        self.assertTrue(pairing.deny_request(self.tmp, r["code"]))
        self.assertEqual(pairing.list_requests(self.tmp), [])
        out = pairing.poll_request(
            self.tmp, r["request_id"], r["poll_secret"]
        )
        self.assertEqual(out["status"], "denied")

    def test_approve_extends_poll_window(self):
        import json
        r = self._request()
        # Nearly expire the row, then approve: the credential must
        # still be collectable.
        path = pairing.requests_file(self.tmp)
        data = json.loads(path.read_text())
        data["requests"][0]["expires_at"] = time.time() + 1
        path.write_text(json.dumps(data))
        res = pairing.approve_request(self.tmp, r["code"])
        self.assertIsNotNone(res)
        rows = pairing._load_requests(self.tmp)
        self.assertGreater(rows[0]["expires_at"], time.time() + 60)

    def test_token_exchange_still_works(self):
        token = pairing.mint_pair_token(self.tmp)
        cred = pairing.exchange(self.tmp, token, "legacy")
        self.assertIsNotNone(cred)
        self.assertIsNotNone(
            pairing.device_for_credential(self.tmp, cred["credential"])
        )
