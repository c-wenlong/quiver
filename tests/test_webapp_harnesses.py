"""The Harnesses page's data: every registered harness, archived included."""

import json
import unittest
from unittest.mock import patch

from quiver.webapp import harnesses

from tests.test_remote_hub import HubBase

REGISTRY = {
    "claude": {"command": "claude", "aliases": ["cc"], "tags": ["agentic"]},
    "codex": {"command": "codex"},
    "ollama": {"command": "ollama"},
}
ARCHIVE = {"ollama": {"reason": "not a harness", "archived_at": "2026-10-06T10:34:07",
                      "usage": "unknown"}}


class _Registry:
    def setUp(self):
        self._patches = [
            patch("quiver.harness.registry.load_registry_readonly", return_value=dict(REGISTRY)),
            patch("quiver.harness.stars.load_stars", return_value=["claude"]),
            patch("quiver.harness.archive.load_archive", return_value=dict(ARCHIVE)),
            patch("quiver.sessions.usage.session_counts", return_value={"claude": 12, "codex": 3}),
            patch("quiver.harness.tools.is_installed", side_effect=lambda cmd: cmd != "codex"),
        ]
        for p in self._patches:
            p.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for p in reversed(self._patches):
            p.stop()


class TestRows(_Registry, unittest.TestCase):
    def test_every_harness_with_state_counts_and_no_limits(self):
        rows = {r["name"]: r for r in harnesses.rows()}
        self.assertEqual(list(rows), ["claude", "codex", "ollama"])
        self.assertEqual(rows["claude"]["state"], "starred")
        self.assertEqual(rows["claude"]["aliases"], ["cc"])
        self.assertEqual(rows["claude"]["sessions_100d"], 12)
        self.assertFalse(rows["codex"]["installed"])
        self.assertEqual(rows["ollama"]["state"], "archived")
        self.assertEqual(rows["ollama"]["archived"]["reason"], "not a harness")
        self.assertTrue(all(r["rate_limit"] is None for r in rows.values()))


class TestRoute(_Registry, HubBase):
    def test_needs_a_device_and_returns_the_rows(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/harnesses")[0], 401)
        status, data = self.srv.request("GET", "/v1/app/harnesses", headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertEqual(len(body["harnesses"]), 3)
        self.assertEqual(body["window_days"], 100)
        self.assertEqual(body["machine"], self.srv.state.machine["name"])

    def test_an_unreadable_registry_is_a_503(self):
        with patch("quiver.webapp.harnesses.rows", side_effect=ValueError("bad json")):
            status, data = self.srv.request("GET", "/v1/app/harnesses", headers=self.auth())
        self.assertEqual(status, 503)
        self.assertIn("bad json", data.decode())

    def test_page_script_is_served(self):
        self.assertEqual(self.srv.request("GET", "/app/manage.js")[0], 200)


if __name__ == "__main__":
    unittest.main()
