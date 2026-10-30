"""The Inbox page's data: swe gh inbox --all, cached, and its route;
and its write, marking read, against a fake ``gh`` on PATH."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quiver.github import gh
from quiver.webapp.pages import PageError
from quiver.webapp.pages import _github as G
from quiver.webapp.pages import inbox

from tests.test_remote_hub import HubBase
from tests.test_webapp_repositories import FakeGh

NOTE = {"id": "1", "repo": "o/a", "title": "CI failed", "type": "CheckSuite",
        "reason": "ci_activity", "unread": True, "updated_at": "2026-10-23T18:29:12Z",
        "url": "https://github.com/o/a"}


class TestInbox(unittest.TestCase):
    def setUp(self):
        p = patch.object(G, "CACHE", G.TtlCache())
        p.start()
        self.addCleanup(p.stop)
        self.ctx = SimpleNamespace(home=None, machine={"name": "hub-box"})

    def test_rows_read_included_without_a_probe(self):
        reply = {"status": "ok", "notifications": [NOTE]}
        with patch("quiver.github.cli._fetch_inbox", return_value=reply) as fetch, \
                patch.object(gh, "probe_env") as probe:
            body = inbox.get(self.ctx, {})
            inbox.get(self.ctx, {})
        fetch.assert_called_once_with(50, fetch_all=True)
        probe.assert_not_called()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["notifications"], [NOTE])
        self.assertEqual(body["machine"], "hub-box")
        self.assertIn("fetched_at", body)

    def test_a_failure_is_classified_by_the_probe(self):
        failed = {"status": "upstream-error", "notifications": [], "error": "502"}
        with patch("quiver.github.cli._fetch_inbox", return_value=failed), \
                patch.object(gh, "probe_env", return_value=gh.GhEnv(installed=True)):
            body = inbox.get(self.ctx, {})
        self.assertEqual((body["status"], body["github"]["status"]), ("auth-required", "auth-required"))
        self.assertEqual(body["notifications"], [])
        G.CACHE.clear()
        ok_env = gh.GhEnv(installed=True, authenticated=True)
        with patch("quiver.github.cli._fetch_inbox", return_value=failed), \
                patch.object(gh, "probe_env", return_value=ok_env):
            body = inbox.get(self.ctx, {})
        # gh works, GitHub failed: the page says "GitHub returned …".
        self.assertEqual((body["status"], body["error"]), ("upstream-error", "502"))


class TestRoute(HubBase):
    def test_needs_a_device(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/inbox")[0], 401)
        with patch.object(G, "CACHE", G.TtlCache()), \
                patch("quiver.github.cli._fetch_inbox",
                      return_value={"status": "ok", "notifications": [NOTE]}):
            status, data = self.srv.request("GET", "/v1/app/inbox", headers=self.auth())
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["notifications"][0]["id"], "1")



def _raw(nid, unread=True, title="CI failed"):
    """One notification as GitHub's API returns it."""
    return {"id": nid, "unread": unread, "reason": "ci_activity",
            "updated_at": "2026-10-23T18:29:12Z",
            "subject": {"title": title, "type": "CheckSuite", "url": None},
            "repository": {"full_name": "o/a", "html_url": "https://github.com/o/a"}}


class TestMarkRead(unittest.TestCase):
    def setUp(self):
        p = patch.object(G, "CACHE", G.TtlCache())
        p.start()
        self.addCleanup(p.stop)
        self.ctx = SimpleNamespace(home=Path(tempfile.mkdtemp()), machine={"name": "hub-box"})
        self.gh = FakeGh(self, notes=[_raw("1"), _raw("2"), _raw("3", unread=False)])
        inbox.get(self.ctx, {})  # what the page lists

    def refused(self, status, code, body):
        with self.assertRaises(PageError) as caught:
            inbox.post(self.ctx, "read", body)
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code),
                         caught.exception.message)
        self.assertEqual(self.gh.writes(), [])
        return caught.exception

    def test_one_row_is_marked_and_the_answer_carries_the_rows(self):
        body = inbox.post(self.ctx, "read", {"ids": ["1"]})
        self.assertEqual(self.gh.writes(), [["api", "--method", "PATCH", "notifications/threads/1"]])
        self.assertEqual((body["all"], body["marked"], body["failed"]), (False, ["1"], []))
        unread = {n["id"]: n["unread"] for n in body["notifications"]}
        self.assertEqual(unread, {"1": False, "2": True, "3": False})
        self.assertEqual(body["machine"], "hub-box")
        # The cached listing was dropped: the next read asks GitHub again.
        listings = lambda: [c for c in self.gh.calls() if c[:1] == ["api"] and "--method" not in c]
        before = len(listings())
        again = inbox.get(self.ctx, {})
        self.assertEqual(len(listings()), before + 1)
        self.assertFalse(next(n for n in again["notifications"] if n["id"] == "1")["unread"])

    def test_all_marks_everything_with_one_put(self):
        body = inbox.post(self.ctx, "read", {"all": True})
        writes = self.gh.writes()
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][:4], ["api", "--method", "PUT", "notifications"])
        self.assertRegex(writes[0][5], r"^last_read_at=\d{4}-\d\d-\d\dT")
        self.assertEqual(body["marked"], ["*"])
        self.assertFalse(any(n["unread"] for n in body["notifications"]))

    def test_a_partial_failure_names_the_ids_github_refused(self):
        self.gh.update(fail_ids=["2"])
        body = inbox.post(self.ctx, "read", {"ids": ["1", "2", "1"]})
        self.assertEqual(body["marked"], ["1"])
        self.assertEqual([f["id"] for f in body["failed"]], ["2"])
        self.assertIn("500", body["failed"][0]["error"])
        unread = {n["id"]: n["unread"] for n in body["notifications"]}
        self.assertEqual((unread["1"], unread["2"]), (False, True))
        self.assertEqual(len(self.gh.writes()), 2)  # the repeated id is sent once

    def test_several_rows_at_once_all_stay_read(self):
        body = inbox.post(self.ctx, "read", {"ids": ["1", "2"]})
        self.assertEqual(sorted(body["marked"]), ["1", "2"])
        again = inbox.get(self.ctx, {})
        self.assertEqual([n["unread"] for n in again["notifications"]], [False, False, False])

    def test_reads_run_off_the_write_lock_and_writes_under_it(self):
        # The probe and the two listings are reads; holding WRITE_LOCK
        # over them stalled every other GitHub write on the hub. The
        # marks and the cache drop still need it.
        seen = {}

        def spy(owner, name):
            real = getattr(owner, name)

            def call(*a, **k):
                seen.setdefault(name, []).append(G.WRITE_LOCK.locked())
                return real(*a, **k)
            return patch.object(owner, name, side_effect=call)

        with spy(G, "probe"), spy(inbox, "_listing"), spy(inbox, "_unread_listing"), \
                spy(inbox.ghcli, "mark_threads_read"), spy(inbox.ghcli, "mark_all_read"), \
                spy(inbox, "_drop"):
            inbox.post(self.ctx, "read", {"ids": ["1"]})
            inbox.post(self.ctx, "read", {"all": True})
        for read in ("probe", "_listing", "_unread_listing"):
            self.assertEqual(set(seen[read]), {False}, read)
        for write in ("mark_threads_read", "mark_all_read", "_drop"):
            self.assertEqual(set(seen[write]), {True}, write)

    def test_ids_must_be_ones_the_inbox_lists(self):
        err = self.refused(404, "unknown_notification", {"ids": ["1", "999"]})
        self.assertIn("999", err.message)

    def test_bad_shapes(self):
        for body, code in (({}, "nothing_to_mark"), ({"ids": []}, "nothing_to_mark"),
                           ({"ids": "1"}, "bad_ids"), ({"ids": ["1; rm -rf"]}, "bad_ids"),
                           ({"ids": ["../threads"]}, "bad_ids"), ({"ids": [True]}, "bad_ids"),
                           ({"ids": [{"id": 1}]}, "bad_ids"), ({"all": "yes"}, "bad_all"),
                           ({"ids": ["1"], "all": True}, "ids_and_all"),
                           ({"ids": ["9" * 5000]}, "bad_ids"),
                           ({"ids": [str(i) for i in range(51)]}, "too_many_ids")):
            with self.subTest(body=body):
                self.refused(422, code, body)

    def test_unknown_action_signed_out_and_a_failed_mark_all(self):
        with self.assertRaises(PageError) as caught:
            inbox.post(self.ctx, "unread", {"ids": ["1"]})
        self.assertEqual(caught.exception.status, 404)
        self.gh.update(signed_out=True)
        err = self.refused(409, "gh_auth_required", {"ids": ["1"]})
        self.assertIn("gh auth login", err.message)
        self.gh.update(signed_out=False, api_error="HTTP 502: Bad Gateway")
        G.CACHE.clear()
        with self.assertRaises(PageError) as caught:
            inbox.post(self.ctx, "read", {"all": True})
        self.assertEqual((caught.exception.status, caught.exception.code), (502, "mark_failed"))
        self.assertIn("502", caught.exception.message)


class TestReadRoute(HubBase):
    def setUp(self):
        super().setUp()
        p = patch.object(G, "CACHE", G.TtlCache())
        p.start()
        self.addCleanup(p.stop)

    def test_needs_a_device_and_a_same_site_origin(self):
        fake = FakeGh(self, notes=[_raw("1")])
        body = {"ids": ["1"]}
        self.assertEqual(self.srv.request("POST", "/v1/app/inbox/read", body=body)[0], 401)
        auth = self.auth()
        status, data = self.srv.request("POST", "/v1/app/inbox/read", body=body,
                                        headers={**auth, "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("bad_origin", data.decode())
        self.assertEqual(fake.writes(), [])
        status, data = self.srv.request(
            "POST", "/v1/app/inbox/read", body=body,
            headers={**auth, "Origin": f"http://127.0.0.1:{self.srv.port}"})
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["marked"], ["1"])
        self.assertEqual(fake.writes(), [["api", "--method", "PATCH", "notifications/threads/1"]])
        status, _ = self.srv.request("POST", "/v1/app/inbox/read", body={"ids": ["7"]}, headers=auth)
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
