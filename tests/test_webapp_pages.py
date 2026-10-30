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

    def test_tilde_writes_the_home_as_a_tilde(self):
        self.assertEqual(pages.tilde("/h/u", "/h/u/"), "~")
        self.assertEqual(pages.tilde("/h/u/a/b", "/h/u"), "~/a/b")
        self.assertEqual(pages.tilde("/h/user2/a", "/h/u"), "/h/user2/a")
        self.assertEqual(pages.tilde("/elsewhere", "/h/u"), "/elsewhere")
        with patch.object(pages.os, "sep", "\\"):
            self.assertEqual(pages.tilde("C:\\Users\\a\\.quiver", "C:\\Users\\a"),
                             "~\\.quiver")
            self.assertEqual(pages.tilde("C:\\Users\\a", "C:\\Users\\a\\"), "~")

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

    def test_a_broken_page_does_not_stop_the_others(self):
        """One page that won't parse and one that throws are logged and
        skipped; the pages around them still register."""
        import shutil
        import subprocess
        folder = Path(tempfile.mkdtemp())
        (folder / "a.js").write_text('registerPage("a", () => "a");\n')
        (folder / "b.js").write_text('registerPage("b", () => {;\n')        # syntax error
        (folder / "c.js").write_text('throw new Error("boom");\n')         # throws on load
        (folder / "d.js").write_text('const label = "d";\nregisterPage("d", () => label);\n')
        with patch.object(pages, "STATIC", folder):
            body = pages.bundle(".js").decode()
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not installed")
        script = ("const PAGES = {}; function registerPage(id, r) { PAGES[id] = r; }\n"
                  "const logged = []; console.error = (...a) => logged.push(String(a[0]));\n"
                  + body + "\nprocess.stdout.write(Object.keys(PAGES).join(',') + '|' + logged.length);")
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout, "a,d|2", out.stderr)

    def test_js_string_round_trips(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not installed")
        for text in ('say "hi"', "it's", "back\\slash", "two\nlines\r\n", "tab\t",
                     "sep\u2028x\u2029", "`${template}`", "</script>"):
            # Bytes, not text mode: text mode would fold the \r\n we test.
            out = subprocess.run([node, "-e", "process.stdout.write(" + pages._js_string(text) + ")"],
                                 capture_output=True, timeout=30)
            self.assertEqual(out.stdout.decode("utf-8"), text)
        self.assertIn('registerPage("x"', pages._js_string('registerPage("x", r)'))

    def test_each_script_gets_its_own_scope(self):
        """Two pages reusing private names both register their own render."""
        import shutil
        import subprocess
        folder = Path(tempfile.mkdtemp())
        for page in ("one", "two"):
            (folder / f"{page}.js").write_text(
                '"use strict";\nconst label = "%s";\n'
                "function render() { return label; }\n"
                'registerPage("%s", render);\n' % (page, page))
        with patch.object(pages, "STATIC", folder):
            body = pages.bundle(".js").decode()
            self.assertNotIn("(function", pages.bundle(".css").decode())
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not installed")
        script = ("const PAGES = {}; function registerPage(id, r) { PAGES[id] = r; }\n" + body
                  + "\nprocess.stdout.write(PAGES.one() + ',' + PAGES.two());")
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout, "one,two", out.stderr)


class TestStylesheets(unittest.TestCase):
    """The web app's stylesheets against its own rules."""

    STATIC = Path(pages.__file__).resolve().parent.parent / "static"

    def sheets(self):
        return {p.name: p.read_text(encoding="utf-8")
                for p in [self.STATIC / "styles.css", *sorted((self.STATIC / "pages").glob("*.css"))]}

    def test_every_variable_used_is_defined(self):
        """A var() with no definition renders the property's initial
        value (chat's approval toggle had black text in dark mode)."""
        import re
        scripts = [p.read_text(encoding="utf-8") for p in [*self.STATIC.glob("*.js"), *self.STATIC.glob("pages/*.js")]]
        defined = set()
        for text in [*self.sheets().values(), *scripts]:  # scripts set some inline, as style="--c: …"
            defined |= set(re.findall(r"(--[a-z0-9-]+)\s*:", text))
        missing = sorted({f"{name}: {v}" for name, text in self.sheets().items()
                          for v in re.findall(r"var\((--[a-z0-9-]+)\)?", text) if v not in defined})
        self.assertEqual(missing, [])

    def test_no_all_caps_labels(self):
        """Sentence case only, as the Mac app."""
        self.assertEqual([n for n, t in self.sheets().items() if "uppercase" in t], [])


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
