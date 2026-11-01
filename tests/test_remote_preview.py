"""preview.py and GET /v1/sessions/{id}/preview: Preview on page files."""

import http.client
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from swe.remote import catalog as catalog_mod
from swe.remote import files
from swe.remote import preview
from swe.remote import protocol
from tests.test_remote_hub import _Server, _session

PAGE = b"<!doctype html><title>mock</title><p>hi</p>"


class _Tmp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        # realpath: macOS /var is a symlink to /private/var.
        self.base = Path(os.path.realpath(tmp))
        self.proj = self.base / "proj"
        (self.proj / "out").mkdir(parents=True)
        (self.proj / "out" / "page.html").write_bytes(PAGE)
        (self.base / "secret.html").write_bytes(b"<p>outside</p>")
        (self.base / "secret.txt").write_text("outside")

    def code(self, rel, cwd=None):
        with self.assertRaises(files.FilesError) as cm:
            preview.read(str(cwd or self.proj), rel)
        return cm.exception.status, cm.exception.code


class TestRead(_Tmp):
    def test_reads_page(self):
        got = preview.read(str(self.proj), "out/page.html")
        self.assertEqual(got.body, PAGE)
        self.assertEqual(got.content_type, "text/html; charset=utf-8")
        self.assertEqual(got.path, "out/page.html")

    def test_types(self):
        (self.proj / "a.HTM").write_text("<p>x</p>")
        (self.proj / "logo.svg").write_text("<svg/>")
        self.assertEqual(preview.read(str(self.proj), "a.HTM").content_type,
                         "text/html; charset=utf-8")
        self.assertEqual(preview.read(str(self.proj), "logo.svg").content_type,
                         "image/svg+xml")

    def test_dotdot(self):
        self.assertEqual(self.code("../secret.html"), (422, "bad_path"))
        self.assertEqual(self.code("out/../../secret.html"),
                         (422, "bad_path"))
        self.assertEqual(self.code("out\\..\\..\\secret.html"),
                         (422, "bad_path"))

    def test_absolute(self):
        self.assertEqual(self.code(str(self.base / "secret.html")),
                         (422, "bad_path"))
        self.assertEqual(self.code(str(self.proj / "out" / "page.html")),
                         (422, "bad_path"))

    def test_empty_nul_and_long(self):
        self.assertEqual(self.code(""), (422, "bad_path"))
        self.assertEqual(self.code("out/page.html\x00.txt"),
                         (422, "bad_path"))
        self.assertEqual(self.code("a" * 5000 + ".html"), (422, "bad_path"))

    def test_symlink_out(self):
        os.symlink(self.base / "secret.html", self.proj / "link.html")
        self.assertEqual(self.code("link.html"), (403, "outside_folder"))

    def test_folder_symlink_out(self):
        os.symlink(self.base, self.proj / "up")
        self.assertEqual(self.code("up/secret.html"), (403, "outside_folder"))

    def test_symlink_inside_ok(self):
        os.symlink(self.proj / "out" / "page.html", self.proj / "alias.html")
        self.assertEqual(preview.read(str(self.proj), "alias.html").body, PAGE)

    def test_link_between_page_types_uses_target_type(self):
        (self.proj / "logo.svg").write_text("<svg/>")
        os.symlink(self.proj / "out" / "page.html", self.proj / "alias.svg")
        os.symlink(self.proj / "logo.svg", self.proj / "alias.html")
        self.assertEqual(preview.read(str(self.proj), "alias.svg").content_type,
                         "text/html; charset=utf-8")
        self.assertEqual(
            preview.read(str(self.proj), "alias.html").content_type,
            "image/svg+xml",
        )

    def test_link_to_other_type_inside(self):
        (self.proj / ".env").write_text("TOKEN=x")
        os.symlink(self.proj / ".env", self.proj / "env.html")
        self.assertEqual(self.code("env.html"), (415, "unsupported_type"))

    def test_wrong_extension(self):
        (self.proj / "notes.txt").write_text("x")
        self.assertEqual(self.code("notes.txt"), (415, "unsupported_type"))
        self.assertEqual(self.code("out"), (415, "unsupported_type"))
        self.assertEqual(self.code(".html/x"), (415, "unsupported_type"))

    def test_oversize(self):
        big = self.proj / "big.html"
        big.write_bytes(b"x" * (preview.MAX_BYTES + 1))
        self.assertEqual(self.code("big.html"), (413, "too_large"))
        big.write_bytes(b"x" * preview.MAX_BYTES)
        self.assertEqual(len(preview.read(str(self.proj), "big.html").body),
                         preview.MAX_BYTES)

    def test_missing(self):
        self.assertEqual(self.code("nope.html"), (404, "not_found"))
        self.assertEqual(self.code("nope/page.html"), (404, "not_found"))

    def test_folder_named_like_a_page(self):
        (self.proj / "dir.html").mkdir()
        self.assertEqual(self.code("dir.html"), (422, "not_a_file"))

    def test_fifo_refused_without_blocking(self):
        os.mkfifo(self.proj / "pipe.html")
        self.assertEqual(self.code("pipe.html"), (422, "not_a_file"))

    def test_swapped_for_link_after_check(self):
        # The check passes on a real file; the open then meets a link.
        target = self.proj / "out" / "page.html"
        real_confine = files.confine

        def confine(root, rel):
            got = real_confine(root, rel)
            target.unlink()
            os.symlink(self.base / "secret.html", target)
            return got

        with patch.object(files, "confine", confine):
            self.assertEqual(self.code("out/page.html"),
                             (403, "outside_folder"))

    def test_folder_swapped_for_link_after_check(self):
        real_confine = files.confine

        def confine(root, rel):
            got = real_confine(root, rel)
            shutil.rmtree(self.proj / "out")
            os.symlink(self.base, self.proj / "out")
            return got

        (self.base / "page.html").write_bytes(b"<p>outside</p>")
        with patch.object(files, "confine", confine):
            self.assertEqual(self.code("out/page.html"), (404, "not_found"))

    def test_missing_cwd(self):
        self.assertEqual(self.code("a.html", cwd=self.base / "gone"),
                         (422, "cwd_missing"))


class TestRoute(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.base = Path(os.path.realpath(tmp))
        self.proj = self.base / "proj"
        (self.proj / "out").mkdir(parents=True)
        (self.proj / "out" / "page.html").write_bytes(PAGE)
        (self.proj / "logo.svg").write_text("<svg/>")
        (self.base / "secret.html").write_bytes(b"<p>outside</p>")
        self.srv = _Server(sessions=[
            _session(sid="s1", path=str(self.proj)),
            _session(sid="s3", path=""),
        ])
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {cred['credential']}"}

    def get(self, key, rel, headers=None):
        path = (f"/v1/sessions/{quote(key, safe='')}/preview"
                f"?path={quote(rel, safe='')}")
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                          timeout=5)
        try:
            conn.request("GET", path,
                         headers=self.hdr if headers is None else headers)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def error(self, key, rel, headers=None):
        status, hdrs, data = self.get(key, rel, headers)
        self.assertTrue(hdrs["Content-Type"].startswith("application/json"))
        return status, json.loads(data)["error"]["code"]

    def test_serves_page(self):
        status, hdrs, data = self.get("claude:s1", "out/page.html")
        self.assertEqual(status, 200)
        self.assertEqual(data, PAGE)
        self.assertEqual(hdrs["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(hdrs["Content-Length"], str(len(PAGE)))
        self.assertEqual(hdrs["Cache-Control"], "no-store")
        self.assertEqual(hdrs["X-Content-Type-Options"], "nosniff")
        self.assertIn("Last-Modified", hdrs)
        csp = hdrs["Content-Security-Policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertIn("sandbox", csp)
        self.assertNotIn("script-src", csp)

    def test_serves_svg(self):
        status, hdrs, data = self.get("claude:s1", "logo.svg")
        self.assertEqual(status, 200)
        self.assertEqual(hdrs["Content-Type"], "image/svg+xml")
        self.assertIn("Content-Security-Policy", hdrs)

    def test_requires_auth(self):
        self.assertEqual(self.error("claude:s1", "out/page.html", {}),
                         (401, "unauthorized"))
        bad = {"Authorization": "Bearer nope"}
        self.assertEqual(self.error("claude:s1", "out/page.html", bad),
                         (401, "unauthorized"))

    def test_unknown_session(self):
        self.assertEqual(self.error("claude:nope", "out/page.html"),
                         (404, "unknown_session"))

    def test_traversal(self):
        self.assertEqual(self.error("claude:s1", "../secret.html"),
                         (422, "bad_path"))

    def test_absolute(self):
        self.assertEqual(
            self.error("claude:s1", str(self.base / "secret.html")),
            (422, "bad_path"),
        )

    def test_missing_path_arg(self):
        self.assertEqual(self.error("claude:s1", ""), (422, "bad_path"))

    def test_symlink_out(self):
        os.symlink(self.base / "secret.html", self.proj / "link.html")
        self.assertEqual(self.error("claude:s1", "link.html"),
                         (403, "outside_folder"))

    def test_wrong_extension(self):
        (self.proj / "notes.md").write_text("x")
        self.assertEqual(self.error("claude:s1", "notes.md"),
                         (415, "unsupported_type"))

    def test_oversize(self):
        (self.proj / "big.svg").write_bytes(b"x" * (preview.MAX_BYTES + 1))
        self.assertEqual(self.error("claude:s1", "big.svg"),
                         (413, "too_large"))

    def test_no_cwd(self):
        self.assertEqual(self.error("claude:s3", "a.html"), (422, "bad_cwd"))

    def test_satellite_session_unavailable(self):
        home = self.srv.home
        catalog_mod.upsert_machine(home, {"id": "sat1", "name": "studio"})
        catalog_mod.merge_remote_rows(home, "sat1", [{
            "key": "claude:r1", "cwd": str(self.proj), "status": "idle",
        }])
        self.srv.state.catalog.refresh()
        key = protocol.remote_key("sat1", "claude:r1")
        self.assertEqual(self.error(key, "out/page.html"),
                         (422, "preview_unavailable"))


if __name__ == "__main__":
    unittest.main()
