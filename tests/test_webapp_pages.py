"""Web app page plugins: dispatch of /v1/app/<page> and the page bundles."""

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.webapp import pages

from tests.test_remote_hub import HubBase


def _page(get=None, post=None):
    mod = types.SimpleNamespace()
    if get:
        mod.get = get
    if post:
        mod.post = post
    return mod


class TestLoad(unittest.TestCase):
    def test_only_plain_names_of_real_modules(self):
        for bad in ("", "_private", "Upper", "a-b", "../x", "nope", "__init__"):
            self.assertIsNone(pages.load(bad), bad)

    def test_bundle_concatenates_in_name_order(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "b.js").write_text("B")
        (folder / "a.js").write_text("A")
        (folder / "a.css").write_text("CSS")
        with patch.object(pages, "STATIC", folder):
            body = pages.bundle(".js").decode()
            self.assertLess(body.index("A"), body.index("B"))
            self.assertNotIn("CSS", body)
            self.assertIn("/* a.js */", body)
            self.assertEqual(pages.bundle(".none"), b"")


class TestLedgerAccess(HubBase):
    def test_pages_share_the_ledger(self):
        from quiver.webapp.api import WebApp
        app = WebApp(self.srv.home, download_prices=False)
        self.addCleanup(app.close)
        with app.ledger() as (store, catalog, scan_ok):
            self.assertTrue(scan_ok)
            self.assertEqual(store.models(), [])
            self.assertIsNotNone(catalog)


class TestDispatch(HubBase):
    def test_get_passes_context_and_flat_args(self):
        seen = {}

        def get(ctx, args):
            seen["ctx"], seen["args"] = ctx, args
            return {"ok": True}
        with patch.object(pages, "load", return_value=_page(get=get)):
            self.assertEqual(self.srv.request("GET", "/v1/app/demo")[0], 401)
            status, data = self.srv.request("GET", "/v1/app/demo?x=1&x=2&y=", headers=self.auth())
        self.assertEqual((status, json.loads(data)), (200, {"ok": True}))
        self.assertEqual(seen["args"], {"x": "1"})
        self.assertIs(seen["ctx"].state, self.srv.state)
        self.assertEqual(seen["ctx"].machine, self.srv.state.machine)
        self.assertIsNotNone(seen["ctx"].device_id)

    def test_unknown_page_and_errors(self):
        status, _ = self.srv.request("GET", "/v1/app/nope", headers=self.auth())
        self.assertEqual(status, 404)

        def refuse(ctx, args):
            raise pages.PageError(409, "busy", "try later")

        def broken(ctx, args):
            raise OSError("disk gone")
        with patch.object(pages, "load", return_value=_page(get=refuse)):
            status, data = self.srv.request("GET", "/v1/app/demo", headers=self.auth())
        self.assertEqual(status, 409)
        self.assertIn("try later", data.decode())
        with patch.object(pages, "load", return_value=_page(get=broken)):
            status, _ = self.srv.request("GET", "/v1/app/demo", headers=self.auth())
        self.assertEqual(status, 503)

    def test_post_needs_a_device_an_action_and_a_good_origin(self):
        calls = []

        def post(ctx, action, body):
            calls.append((action, body))
            return {"done": action}
        hdr = {**self.auth(), "Content-Type": "application/json"}
        with patch.object(pages, "load", return_value=_page(post=post)):
            self.assertEqual(self.srv.request("POST", "/v1/app/demo/star", {"name": "x"})[0], 401)
            self.assertEqual(self.srv.request("POST", "/v1/app/demo", {}, headers=hdr)[0], 404)
            bad = {**hdr, "Origin": "https://evil.example"}
            self.assertEqual(self.srv.request("POST", "/v1/app/demo/star", {}, headers=bad)[0], 403)
            status, data = self.srv.request("POST", "/v1/app/demo/star", {"name": "x"}, headers=hdr)
        self.assertEqual((status, json.loads(data)), (200, {"done": "star"}))
        self.assertEqual(calls, [("star", {"name": "x"})])

    def test_page_without_post_is_404(self):
        with patch.object(pages, "load", return_value=_page(get=lambda c, a: {})):
            status, _ = self.srv.request("POST", "/v1/app/demo/x", {}, headers=self.auth())
        self.assertEqual(status, 404)

    def test_bundles_are_served(self):
        for path, ctype in (("/app/pages.js", "javascript"), ("/app/pages.css", "css")):
            status, _ = self.srv.request("GET", path)
            self.assertEqual(status, 200, path)
        status, body = self.srv.request("GET", "/app/")
        self.assertIn(b"/app/pages.js", body)


if __name__ == "__main__":
    unittest.main()
