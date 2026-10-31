"""Owner auto-approve (#522): opt-in, off by default, and strict.

A pair request is approved without a code only when the setting is on,
the hub knows its owner's tailnet login, and the requesting node is an
untagged node of that same login. Every other case stays pending.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from quiver.remote import commands, pairing

from tests.test_remote_hub import _Server

_OWNER = "dev@github"
# The hub machine's own tailnet node.
_HUB_NODE = "nHUB"
_PHONE = {
    "node_name": "iphone.tail0000.ts.net",
    "node_short": "iphone",
    "node_id": "nPHONE",
    "login": _OWNER,
    "display_name": "Dev",
    "tagged": False,
}


class TestRefusal(unittest.TestCase):
    """``owner_auto_approve_refusal``: one test per reason to refuse."""

    def refusal(self, peer=None, owner=_OWNER, enabled=True, own=_HUB_NODE):
        return pairing.owner_auto_approve_refusal(
            dict(_PHONE) if peer is None else peer, owner, enabled, own)

    def test_allows_the_owners_untagged_node(self):
        self.assertIsNone(self.refusal())

    def test_off(self):
        self.assertEqual(self.refusal(enabled=False), "off")

    def test_no_owner_login(self):
        self.assertEqual(self.refusal(owner=""), "no_owner")
        self.assertEqual(self.refusal(owner=None), "no_owner")

    def test_no_peer(self):
        self.assertEqual(
            pairing.owner_auto_approve_refusal(None, _OWNER, True), "no_peer")

    def test_tagged_node(self):
        # Even when a tagged record still names the owner's login.
        self.assertEqual(self.refusal({**_PHONE, "tagged": True}), "tagged")

    def test_unknown_tag_state(self):
        peer = dict(_PHONE)
        del peer["tagged"]
        self.assertEqual(self.refusal(peer), "tagged")
        self.assertEqual(self.refusal({**_PHONE, "tagged": None}), "tagged")
        self.assertEqual(self.refusal({**_PHONE, "tagged": 0}), "tagged")

    def test_no_login(self):
        self.assertEqual(self.refusal({**_PHONE, "login": ""}), "no_login")
        self.assertEqual(self.refusal({**_PHONE, "login": None}), "no_login")

    def test_other_login(self):
        self.assertEqual(
            self.refusal({**_PHONE, "login": "friend@github"}), "other_login")
        # Exact match only: no case folding.
        self.assertEqual(
            self.refusal({**_PHONE, "login": "Dev@github"}), "other_login")

    def test_no_node_id(self):
        self.assertEqual(self.refusal({**_PHONE, "node_id": ""}), "no_node")

    def test_unknown_hub_node(self):
        self.assertEqual(self.refusal(own=""), "no_own_node")
        self.assertEqual(self.refusal(own=None), "no_own_node")

    def test_the_hub_machines_own_node(self):
        """Any local user's process reaches the hub as the hub's node."""
        self.assertEqual(
            self.refusal({**_PHONE, "node_id": _HUB_NODE}), "own_node")


class TestSetting(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_off_by_default(self):
        self.assertFalse(pairing.auto_approve_owner(self.home))

    def test_round_trip_and_private(self):
        self.assertTrue(pairing.set_auto_approve_owner(self.home, True))
        self.assertTrue(pairing.auto_approve_owner(self.home))
        path = pairing.settings_file(self.home)
        self.assertEqual(path.name, "pairing.json")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        pairing.set_auto_approve_owner(self.home, False)
        self.assertFalse(pairing.auto_approve_owner(self.home))

    def test_only_a_literal_true_counts(self):
        path = pairing.settings_file(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        for raw in ('{"auto_approve_owner": "true"}',
                    '{"auto_approve_owner": 1}', "[true]", "not json"):
            path.write_text(raw)
            self.assertFalse(pairing.auto_approve_owner(self.home), raw)


class TestAutoApproveRequest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_approves_the_matching_row_like_a_code_would(self):
        r = pairing.request_pair(self.home, dict(_PHONE), name="phone")
        res = pairing.auto_approve_request(
            self.home, r["request_id"], dict(_PHONE))
        self.assertIsNotNone(res)
        row = pairing.load_devices(self.home)[res["device_id"]]
        self.assertEqual(row["approved_via"], "owner_auto")
        self.assertEqual(row["paired_via"], "tailscale")
        self.assertEqual(row["tailscale_login"], _OWNER)
        polled = pairing.poll_request(
            self.home, r["request_id"], r["poll_secret"])
        self.assertEqual(polled["status"], "approved")
        self.assertTrue(polled["credential"])

    def test_refuses_a_row_from_another_node_or_login(self):
        r = pairing.request_pair(self.home, dict(_PHONE))
        self.assertIsNone(pairing.auto_approve_request(
            self.home, r["request_id"], {**_PHONE, "node_id": "nOTHER"}))
        self.assertIsNone(pairing.auto_approve_request(
            self.home, r["request_id"], {**_PHONE, "login": "x@y"}))
        self.assertIsNone(pairing.auto_approve_request(
            self.home, "nope", dict(_PHONE)))
        self.assertEqual(len(pairing.list_requests(self.home)), 1)

    def test_refuses_a_row_already_decided(self):
        r = pairing.request_pair(self.home, dict(_PHONE))
        pairing.deny_request(self.home, r["code"], r["request_id"])
        self.assertIsNone(pairing.auto_approve_request(
            self.home, r["request_id"], dict(_PHONE)))

    def _pair_by_code(self):
        r = pairing.request_pair(self.home, dict(_PHONE))
        return pairing.approve_request(self.home, r["code"])["device_id"]

    def test_refuses_a_node_whose_newest_device_was_revoked(self):
        """A lost phone on the owner's own login must not walk back in."""
        pairing.revoke_device(self.home, self._pair_by_code())
        r = pairing.request_pair(self.home, dict(_PHONE))
        self.assertIsNone(pairing.auto_approve_request(
            self.home, r["request_id"], dict(_PHONE)))
        self.assertEqual(len(pairing.list_requests(self.home)), 1)

    def test_a_code_approval_after_a_revoke_lifts_it(self):
        pairing.revoke_device(self.home, self._pair_by_code())
        self._pair_by_code()  # the owner typed the code: a newer live row
        r = pairing.request_pair(self.home, dict(_PHONE))
        self.assertIsNotNone(pairing.auto_approve_request(
            self.home, r["request_id"], dict(_PHONE)))

    def test_refuses_a_node_whose_sign_in_was_revoked(self):
        pairing.note_signin(self.home, _PHONE["node_id"])
        self.assertTrue(pairing.revoke_signin(self.home, _PHONE["node_id"]))
        r = pairing.request_pair(self.home, dict(_PHONE))
        self.assertIsNone(pairing.auto_approve_request(
            self.home, r["request_id"], dict(_PHONE)))
        # And it stays revoked: only a typed code lifts a sign-in revoke.
        self.assertTrue(pairing.signin_revoked(self.home, _PHONE["node_id"]))

    def test_refuses_when_the_sign_ins_file_is_damaged(self):
        path = pairing.signins_file(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json")
        r = pairing.request_pair(self.home, dict(_PHONE))
        self.assertIsNone(pairing.auto_approve_request(
            self.home, r["request_id"], dict(_PHONE)))

    def test_a_code_approval_is_marked_too(self):
        r = pairing.request_pair(self.home, dict(_PHONE))
        res = pairing.approve_request(self.home, r["code"])
        row = pairing.load_devices(self.home)[res["device_id"]]
        self.assertEqual(row["approved_via"], "code")


class TestHubRoute(unittest.TestCase):
    """``POST /v1/pair/request`` end to end."""

    _JSON = {"Content-Type": "application/json"}

    def setUp(self):
        self.srv = _Server(sessions=[])
        self.addCleanup(self.srv.stop)
        self.peer = dict(_PHONE)
        self.srv.state.whois = lambda ip: dict(self.peer)
        self.srv.state.owner_login = _OWNER
        self.srv.state.own_node_id = _HUB_NODE
        pairing.set_auto_approve_owner(self.srv.home, True)
        self.err = io.StringIO()
        quiet = redirect_stderr(self.err)
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def request(self, headers=None):
        status, data = self.srv.request(
            "POST", "/v1/pair/request", body={"name": "phone"},
            headers=dict(self._JSON) if headers is None else headers)
        self.assertEqual(status, 201, data)
        return json.loads(data)

    def poll(self, r):
        status, data = self.srv.request(
            "GET",
            f"/v1/pair/request/{r['request_id']}?secret={r['poll_secret']}")
        self.assertEqual(status, 200, data)
        return json.loads(data)

    def assert_pending(self, r):
        self.assertNotIn("auto_approved", r)
        self.assertEqual(self.poll(r)["status"], "pending")
        self.assertEqual(pairing.load_devices(self.srv.home), {})

    def test_owner_device_pairs_without_a_code(self):
        r = self.request()
        self.assertTrue(r["auto_approved"])
        polled = self.poll(r)
        self.assertEqual(polled["status"], "approved")
        self.assertTrue(polled["credential"])
        (row,) = pairing.load_devices(self.srv.home).values()
        self.assertEqual(row["approved_via"], "owner_auto")
        self.assertIn("pair: auto-approved (owner device) phone", self.err.getvalue())
        self.assertIn("login dev@github", self.err.getvalue())

    def test_off_by_default(self):
        pairing.settings_file(self.srv.home).unlink()
        self.assert_pending(self.request())

    def test_turned_off(self):
        pairing.set_auto_approve_owner(self.srv.home, False)
        self.assert_pending(self.request())

    def test_unknown_owner(self):
        self.srv.state.owner_login = ""
        self.assert_pending(self.request())

    def test_tagged_node(self):
        self.peer = {**_PHONE, "tagged": True}
        self.assert_pending(self.request())

    def test_a_request_from_the_hub_machine_itself(self):
        self.peer = {**_PHONE, "node_id": _HUB_NODE}
        self.assert_pending(self.request())

    def test_unknown_hub_node(self):
        self.srv.state.own_node_id = ""
        self.assert_pending(self.request())

    def test_other_login(self):
        self.peer = {**_PHONE, "login": "friend@github"}
        self.assert_pending(self.request())

    def test_no_login(self):
        self.peer = {**_PHONE, "login": ""}
        self.assert_pending(self.request())

    def test_needs_a_json_body(self):
        # A cross-site form can post text/plain without a preflight.
        self.assert_pending(self.request(headers={"Content-Type": "text/plain"}))

    def test_refuses_a_foreign_origin(self):
        self.assert_pending(self.request(headers={
            **self._JSON, "Origin": "https://evil.example"}))

    def test_the_log_line_escapes_the_requesters_name(self):
        status, data = self.srv.request(
            "POST", "/v1/pair/request",
            body={"name": "ph\none\n2026-01-01 pair: forged\x1b[2J"},
            headers=dict(self._JSON))
        self.assertEqual(status, 201, data)
        log = self.err.getvalue()
        pair_lines = [ln for ln in log.splitlines() if "pair:" in ln]
        self.assertEqual(len(pair_lines), 1, log)
        self.assertNotIn("\x1b", log)
        self.assertIn("\\x0a", pair_lines[0])

    def test_a_manual_approval_is_logged_too(self):
        pairing.set_auto_approve_owner(self.srv.home, False)
        r = self.request()
        approver = {**_PHONE, "node_id": "nMAC", "node_name": "mac"}
        self.srv.state.whois = lambda ip: dict(approver)
        status, data = self.srv.request(
            "POST", "/v1/pair/requests/approve",
            body={"code": r["code"], "request_id": r["request_id"]},
            headers={**self._JSON, "X-Quiver-Pair": "1"})
        self.assertEqual(status, 200, data)
        self.assertIn("pair: approved with code phone", self.err.getvalue())


def _run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = commands.cmd_remote(list(args))
    return rc, out.getvalue(), err.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        home = tempfile.mkdtemp()
        env = patch.dict(os.environ, {"HOME": home})
        env.start()
        self.addCleanup(env.stop)

    def test_toggle_and_show(self):
        rc, out, _ = _run(["devices", "--requests"])
        self.assertEqual(rc, 0)
        self.assertIn("owner auto-approve: ", out)
        self.assertIn("off", out)
        rc, out, _ = _run(["devices", "--auto-approve=on"])
        self.assertEqual(rc, 0)
        self.assertIn("on", out)
        self.assertTrue(pairing.auto_approve_owner())
        rc, out, _ = _run(["devices", "--requests", "--json"])
        self.assertTrue(json.loads(out)["data"]["auto_approve_owner"])
        rc, out, _ = _run(["devices", "--auto-approve=off", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["data"], {"auto_approve_owner": False})
        self.assertFalse(pairing.auto_approve_owner())

    def test_rejects_other_values(self):
        for bad in ("yes", "true", "1", ""):
            rc, out, err = _run(["devices", f"--auto-approve={bad}", "--json"])
            self.assertEqual(rc, 1, bad)
            self.assertEqual(out, "")
            self.assertIn("on or off", err)
        self.assertFalse(pairing.auto_approve_owner())

    def test_refuses_other_actions_alongside(self):
        """`--auto-approve=off --revoke=<id>` must not report success
        while the device stays active."""
        r = pairing.request_pair(None, dict(_PHONE))
        did = pairing.approve_request(None, r["code"])["device_id"]
        for extra in ([f"--revoke={did}"], ["--requests"],
                      ["--approve=123456"], ["--deny=123456"],
                      ["--approve=123456", "--request-id=x"]):
            rc, out, err = _run(["devices", "--auto-approve=on", *extra,
                                 "--json"])
            self.assertEqual(rc, 1, extra)
            self.assertEqual(out, "")
            self.assertIn("can't be combined", err)
        self.assertFalse(pairing.auto_approve_owner())
        self.assertFalse(pairing.load_devices()[did].get("revoked_at"))

    def test_bare_flag_needs_a_value(self):
        rc, _, err = _run(["devices", "--auto-approve"])
        self.assertEqual(rc, 1)
        self.assertIn("--auto-approve=", err)


if __name__ == "__main__":
    unittest.main()
