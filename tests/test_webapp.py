"""The web app on the hub: /app/ pages and /v1/app/* data."""

import json
import tempfile
import unittest
from pathlib import Path

from quiver.webapp import static
from quiver.webapp.api import WebApp

from tests.test_remote_hub import HubBase


class TestAssetNames(unittest.TestCase):
    def test_only_flat_known_files(self):
        self.assertTrue(static.is_asset_name("index.html"))
        self.assertTrue(static.is_asset_name("harness-claude-color.svg"))
        for bad in ("../hub.py", "..", "a/b.js", "nope.js", "static.py", "", ".hidden.css"):
            self.assertFalse(static.is_asset_name(bad), bad)


class TestWebAppRoutes(HubBase):
    def setUp(self):
        super().setUp()
        self.srv.state.webapp = WebApp(self.srv.home, download_prices=False)
        proj = self.srv.home / ".claude" / "projects" / "p"
        proj.mkdir(parents=True)
        (proj / "a.jsonl").write_text(json.dumps({
            "timestamp": "2026-10-03T10:00:00Z", "sessionId": "s", "requestId": "r",
            "message": {"id": "m", "model": "claude-opus-5",
                        "usage": {"input_tokens": 1000, "output_tokens": 10}},
        }) + "\n")

    def test_pages_are_public_and_confined(self):
        status, body = self.srv.request("GET", "/app/")
        self.assertEqual(status, 200)
        self.assertIn(b"/app/app.js", body)
        for path in ("/app/app.js", "/app/format.js", "/app/styles.css",
                     "/app/harness-devin.svg"):
            self.assertEqual(self.srv.request("GET", path)[0], 200, path)
        self.assertEqual(self.srv.request("GET", "/app")[0], 301)
        for path in ("/app/../hub.py", "/app/%2e%2e/hub.py", "/app/api.py", "/app/x/y.js"):
            self.assertEqual(self.srv.request("GET", path)[0], 404, path)
        # The PWA's own routes are untouched.
        self.assertEqual(self.srv.request("GET", "/app.js")[0], 200)

    def test_overview_needs_a_device_and_reads_the_ledger(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/overview")[0], 401)
        status, data = self.srv.request(
            "GET", "/v1/app/overview?period=all&tz=UTC", headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertEqual(body["period"], "all")
        self.assertEqual(body["totals"]["tokens"], 1010)
        self.assertGreater(body["totals"]["cost_usd"], 0)
        self.assertEqual(body["harnesses"][0]["harness"], "claude")
        self.assertEqual(body["machine"], self.srv.state.machine["name"])
        self.assertEqual(body["notes"], [])
        # The store is the web app's own, never the Mac app's.
        self.assertTrue((self.srv.home / ".quiver/data/web-ledger/ledger.sqlite").exists())
        self.assertFalse((self.srv.home / ".quiver/data/ledger").exists())


if __name__ == "__main__":
    unittest.main()
