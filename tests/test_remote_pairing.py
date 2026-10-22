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
        payload = pairing.pair_payload("h", 1, "tok", "dev's phone")
        self.assertIn("name=dev%27s%20phone", payload)
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
