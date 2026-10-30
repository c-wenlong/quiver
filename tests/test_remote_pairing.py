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

    def test_exchange_caps_name_length(self):
        token = pairing.mint_pair_token(self.tmp)
        cred = pairing.exchange(self.tmp, token, "y" * 100_000 + "  ")
        want = "y" * pairing.MAX_NAME_LEN
        self.assertEqual(cred["name"], want)
        self.assertEqual(
            pairing.load_devices(self.tmp)[cred["device_id"]]["name"], want
        )

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


    def test_pair_url_brackets_an_ipv6_host(self):
        self.assertTrue(pairing.pair_url("fd7a::1", 8477, "t").startswith(
            "http://[fd7a::1]:8477/#pair="))
        self.assertTrue(pairing.pair_url("[::1]", 8477, "t").startswith(
            "http://[::1]:8477/"))
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
            # Only the append is locked: the mints themselves must race
            # for the test to prove none is lost.
            tok = pairing.mint_pair_token(self.tmp)
            with lock:
                tokens.append(tok)

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



_PEER = {
    "node_name": "test-iphone.tail0000.ts.net",
    "node_short": "test-iphone",
    "node_id": "nABC",
    "login": "dev@github",
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
        self.assertEqual(r["node"]["name"], "test-iphone.tail0000.ts.net")
        out = pairing.poll_request(self.tmp, r["request_id"], r["poll_secret"])
        self.assertEqual(out["status"], "pending")
        self.assertGreater(out["expires_in_s"], 0)

    def test_request_defaults_name_to_node(self):
        r = self._request()
        row = [
            x for x in pairing._load_requests(self.tmp)
            if x["request_id"] == r["request_id"]
        ][0]
        self.assertEqual(row["name"], "test-iphone")

    def test_request_caps_name_length(self):
        r = self._request(name="  " + "x" * 100_000)
        row = [
            x for x in pairing._load_requests(self.tmp)
            if x["request_id"] == r["request_id"]
        ][0]
        self.assertEqual(row["name"], "x" * pairing.MAX_NAME_LEN)

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
        self.assertEqual(row["tailscale_login"], "dev@github")

    def _expire_all(self):
        rows = pairing._load_requests(self.tmp)
        for row in rows:
            row["expires_at"] = time.time() - 1
        pairing._save_requests(self.tmp, rows)

    def test_poll_drops_an_uncollected_credential_once_expired(self):
        r = self._request()
        other = self._request(
            peer={**_PEER, "node_id": "nOTHER"}, name="other")
        pairing.approve_request(self.tmp, r["code"])
        self._expire_all()
        # Any poll — even for a different, unknown request — clears
        # the expired rows and the plaintext credential with them.
        self.assertIsNone(
            pairing.poll_request(self.tmp, "nope", other["poll_secret"]))
        self.assertEqual(pairing._load_requests(self.tmp), [])
        self.assertNotIn(
            "credential", pairing.requests_file(self.tmp).read_text())

    def test_prune_requests_drops_expired_rows_only(self):
        old = self._request()
        live = self._request(
            peer={**_PEER, "node_id": "nOTHER"}, name="other")
        pairing.approve_request(self.tmp, old["code"])
        rows = pairing._load_requests(self.tmp)
        for row in rows:
            if row["request_id"] == old["request_id"]:
                row["expires_at"] = time.time() - 1
        pairing._save_requests(self.tmp, rows)
        # Both rows are on disk until prune_requests runs.
        self.assertEqual(len(pairing._load_requests(self.tmp)), 2)
        pairing.prune_requests(self.tmp)
        rows = pairing._load_requests(self.tmp)
        self.assertEqual(
            [x["request_id"] for x in rows], [live["request_id"]])
        self.assertNotIn(
            "credential", pairing.requests_file(self.tmp).read_text())

    def test_prune_requests_without_a_file_writes_nothing(self):
        pairing.prune_requests(self.tmp)
        self.assertFalse(pairing.requests_file(self.tmp).exists())

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

    def test_wrong_code_without_id_counts_a_strike(self):
        r = self._request()
        self.assertIsNone(pairing.approve_request(self.tmp, "zzzzzz"))
        self.assertIsNotNone(pairing.approve_request(self.tmp, r["code"]))

    def test_wrong_codes_without_id_lock_out_every_pending_row(self):
        a = self._request()
        b = self._request(peer={**_PEER, "node_id": "nOTHER"}, name="b")
        for _ in range(pairing.MAX_CODE_ATTEMPTS):
            self.assertIsNone(pairing.approve_request(self.tmp, "zzzzzz"))
        for r in (a, b):
            out = pairing.poll_request(
                self.tmp, r["request_id"], r["poll_secret"])
            self.assertEqual(out["status"], "denied")
        # Denied rows no longer take the right code either.
        self.assertIsNone(pairing.approve_request(self.tmp, a["code"]))

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

    def test_codes_never_duplicate(self):
        seq = iter([123456, 123456, 654321])
        with patch.object(
            pairing.secrets, "randbelow", lambda n: next(seq)
        ):
            r1 = self._request(peer={**_PEER, "node_id": "n1"})
            r2 = self._request(peer={**_PEER, "node_id": "n2"})
        self.assertEqual(r1["code"], "123456")
        self.assertEqual(r2["code"], "654321")

    def test_row_cap_counts_all_states(self):
        # Resolved rows still occupy the file — the total cap stops a
        # spammer from churning it open.
        with patch.object(pairing, "MAX_REQUEST_ROWS", 3):
            r = self._request(peer={**_PEER, "node_id": "n1"})
            self._request(peer={**_PEER, "node_id": "n2"})
            self.assertTrue(
                pairing.deny_request(self.tmp, r["code"])
            )
            # 1 denied + 1 pending = 2 live rows; a third fits, a
            # fourth does not — even though pending < MAX_PENDING.
            self._request(peer={**_PEER, "node_id": "n3"})
            with self.assertRaises(pairing.PairBusy):
                self._request(peer={**_PEER, "node_id": "nX"})


class TestClientMeta(unittest.TestCase):
    SAFARI_IPHONE = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.5 Mobile/15E148 Safari/604.1"
    )

    def test_iphone_safari(self):
        meta = pairing.client_meta(self.SAFARI_IPHONE)
        self.assertEqual(meta["kind"], "iphone")
        self.assertEqual(meta["os"], "iOS 18.5")
        self.assertEqual(meta["client"], "Safari 18.5")

    def test_ipad(self):
        ua = self.SAFARI_IPHONE.replace("iPhone; CPU iPhone", "iPad; CPU")
        meta = pairing.client_meta(ua)
        self.assertEqual(meta["kind"], "ipad")
        self.assertEqual(meta["os"], "iPadOS 18.5")

    def test_android_chrome(self):
        meta = pairing.client_meta(
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.6422.165 Mobile Safari/537.36"
        )
        self.assertEqual(meta["kind"], "android")
        self.assertEqual(meta["os"], "Android 14")
        self.assertEqual(meta["client"], "Chrome 125.0.6422.165")

    def test_mac_safari(self):
        meta = pairing.client_meta(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.4 Safari/605.1.15"
        )
        self.assertEqual(meta["kind"], "mac")
        self.assertEqual(meta["os"], "macOS 10.15.7")
        self.assertEqual(meta["client"], "Safari 17.4")

    def test_windows_edge(self):
        meta = pairing.client_meta(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36 Edg/125.0.2535.51"
        )
        self.assertEqual(meta["kind"], "windows")
        self.assertEqual(meta["os"], "Windows NT 10.0")
        self.assertEqual(meta["client"], "Edge 125.0.2535.51")

    def test_cfnetwork_app_gets_darwin_os(self):
        # Quiver Remote's URLSession UA: product token + Darwin, no
        # platform name. The product picks the iOS table.
        meta = pairing.client_meta(
            "QuiverRemote/1.0 CFNetwork/3826.500.131 Darwin/25.0.0"
        )
        self.assertEqual(meta["client"], "QuiverRemote 1.0")
        self.assertEqual(meta["os"], "iOS 26")

    def test_mac_app_product_gets_macos(self):
        # The Mac app's URLSession UA shares Darwin but its product is
        # Quiver/x, not QuiverRemote — it must not get an iOS version.
        meta = pairing.client_meta(
            "Quiver/1.0 CFNetwork/3826.500.131 Darwin/25.0.0"
        )
        self.assertEqual(meta["client"], "Quiver 1.0")
        self.assertEqual(meta["os"], "macOS 26")

    def test_oversized_header_is_bounded(self):
        # Crafted input for the old greedy Version/...Safari/ match on
        # the unauthenticated /v1/pair path: capped, then parsed.
        meta = pairing.client_meta("Mozilla/5.0 Version/" + "9" * 65000)
        self.assertEqual(meta["kind"], "browser")
        self.assertNotIn("client", meta)

    def test_cli_and_empty(self):
        self.assertEqual(
            pairing.client_meta("curl/8.4.0")["client"], "curl 8.4.0"
        )
        meta = pairing.client_meta("curl/8.4.0")
        self.assertEqual(meta["kind"], "unknown")
        self.assertEqual(pairing.client_meta(""), {})
        self.assertEqual(pairing.client_meta(None), {})

    def test_clean_device_meta(self):
        out = pairing.clean_device_meta({
            "kind": " IPHONE!! ", "model": "iPhone16,2",
            "os": "iOS 18.5", "client": "Quiver Remote 1.0",
            "bogus": "drop me", "os2": "drop me too",
            "node_name": "not a client field",
        })
        self.assertEqual(
            out,
            {
                "kind": "iphone", "model": "iPhone16,2",
                "os": "iOS 18.5", "client": "Quiver Remote 1.0",
            },
        )

    def test_clean_device_meta_drops_garbage(self):
        self.assertEqual(pairing.clean_device_meta(None), {})
        self.assertEqual(pairing.clean_device_meta("str"), {})
        self.assertEqual(
            pairing.clean_device_meta({"kind": "!!!", "os": 12}), {}
        )
        long_val = "x" * 200
        out = pairing.clean_device_meta({"model": long_val})
        self.assertEqual(len(out["model"]), 120)


class TestDeviceMetadata(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_exchange_stores_meta(self):
        token = pairing.mint_pair_token(self.tmp)
        cred = pairing.exchange(
            self.tmp, token, "phone",
            meta={"kind": "iphone", "client": "Quiver Web",
                  "os": "iOS 18.5", "node_name": "test-iphone.ts.net",
                  "display_name": "nope", "credential_hash": "nope"},
        )
        row = pairing.load_devices(self.tmp)[cred["device_id"]]
        self.assertEqual(row["paired_via"], "token")
        self.assertEqual(row["kind"], "iphone")
        self.assertEqual(row["os"], "iOS 18.5")
        self.assertEqual(row["node_name"], "test-iphone.ts.net")
        # Only metadata keys pass through — display_name is rename-only
        # and credential_hash is the mint's own.
        self.assertNotIn("display_name", row)
        self.assertNotEqual(row["credential_hash"], "nope")

    def test_exchange_without_meta_matches_old_rows(self):
        token = pairing.mint_pair_token(self.tmp)
        cred = pairing.exchange(self.tmp, token, "phone")
        row = pairing.load_devices(self.tmp)[cred["device_id"]]
        self.assertEqual(row["paired_via"], "token")
        for key in ("kind", "client", "os", "model", "node_name"):
            self.assertNotIn(key, row)

    def test_approve_mints_meta_and_node_name(self):
        r = pairing.request_pair(
            self.tmp, dict(_PEER), "phone",
            meta={"kind": "iphone", "client": "Quiver Remote 1.0",
                  "os": "iOS 18.5", "model": "iPhone16,2",
                  "node_name": "ignored"},
        )
        res = pairing.approve_request(self.tmp, r["code"])
        self.assertIsNotNone(res)
        row = pairing.load_devices(self.tmp)[res["device_id"]]
        self.assertEqual(row["kind"], "iphone")
        self.assertEqual(row["model"], "iPhone16,2")
        self.assertEqual(row["client"], "Quiver Remote 1.0")
        # node_name comes from the resolved peer, never the client body.
        self.assertEqual(row["node_name"], _PEER["node_name"])

    def test_touch_backfills_only_missing(self):
        token = pairing.mint_pair_token(self.tmp)
        cred = pairing.exchange(
            self.tmp, token, "phone",
            meta={"kind": "iphone", "os": "iOS 17"},
        )
        did = cred["device_id"]
        pairing.touch_device(
            self.tmp, did,
            meta={"kind": "mac", "os": "macOS 15",
                  "client": "Safari 17.4"},
        )
        row = pairing.load_devices(self.tmp)[did]
        # Pair-time values win; only absent keys get filled.
        self.assertEqual(row["kind"], "iphone")
        self.assertEqual(row["os"], "iOS 17")
        self.assertEqual(row["client"], "Safari 17.4")

    def test_device_view_hides_credential(self):
        token = pairing.mint_pair_token(self.tmp)
        cred = pairing.exchange(self.tmp, token, "phone")
        view = pairing.device_view(
            cred["device_id"],
            pairing.load_devices(self.tmp)[cred["device_id"]],
        )
        self.assertEqual(view["id"], cred["device_id"])
        self.assertFalse(view["revoked"])
        self.assertNotIn("credential_hash", view)
        self.assertNotIn("credential", view)

    def test_rename_via_update_device(self):
        token = pairing.mint_pair_token(self.tmp)
        cred = pairing.exchange(self.tmp, token, "phone")
        did = cred["device_id"]
        self.assertTrue(
            pairing.update_device(self.tmp, did, display_name="Dev's")
        )
        row = pairing.load_devices(self.tmp)[did]
        self.assertEqual(row["display_name"], "Dev's")
        # Clearing removes the field, back to the paired name.
        self.assertTrue(
            pairing.update_device(self.tmp, did, display_name=None)
        )
        self.assertNotIn(
            "display_name", pairing.load_devices(self.tmp)[did]
        )


if __name__ == "__main__":
    unittest.main()
