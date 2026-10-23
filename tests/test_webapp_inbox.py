"""The Inbox page's data: swe gh inbox --all, cached, and its route."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from quiver.github import gh
from quiver.webapp.pages import _github as G
from quiver.webapp.pages import inbox

from tests.test_remote_hub import HubBase

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


if __name__ == "__main__":
    unittest.main()
