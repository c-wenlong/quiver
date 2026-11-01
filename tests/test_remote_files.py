"""files.py and GET /v1/sessions/{id}/files: the prompt box's @ menu."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from swe.remote import catalog as catalog_mod
from swe.remote import files
from swe.remote import protocol
from tests.test_remote_hub import _Server, _session

HAS_GIT = shutil.which("git") is not None


def _touch(root: Path, rel: str, mtime: float | None = None) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _paths(body: dict) -> list[str]:
    return [e["path"] for e in body["entries"]]


class _Tmp(unittest.TestCase):
    def setUp(self):
        files.clear_cache()
        self.addCleanup(files.clear_cache)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        # realpath: macOS /var is a symlink to /private/var.
        self.base = Path(os.path.realpath(tmp))
        self.root = self.base / "proj"
        self.root.mkdir()


class TestRanking(_Tmp):
    def test_name_tiers_lead_path_matches(self):
        _touch(self.root, "src/swe/remote/hub.py")
        _touch(self.root, "src/github/notes.md")
        _touch(self.root, "docs/hubris.txt")
        _touch(self.root, "h/u/b/deep.txt")
        got = _paths(files.search(str(self.root), "hub"))
        # exact stem, then name prefix, then path substring, then the
        # letters spread over the path.
        self.assertEqual(got[:2], ["src/swe/remote/hub.py",
                                   "docs/hubris.txt"])
        self.assertLess(got.index("src/github/notes.md"),
                        got.index("h/u/b/deep.txt"))

    def test_subsequence_on_name_beats_path(self):
        _touch(self.root, "a/remote_hub_client.py")
        _touch(self.root, "rh/x/client.py")
        got = _paths(files.search(str(self.root), "rhc"))
        self.assertEqual(got[0], "a/remote_hub_client.py")
        self.assertIn("rh/x/client.py", got)

    def test_ties_prefer_short_then_recent(self):
        _touch(self.root, "a/config.py", mtime=1000)
        _touch(self.root, "bb/config.py", mtime=2000)
        _touch(self.root, "cc/config.py", mtime=3000)
        got = _paths(files.search(str(self.root), "config.py"))
        self.assertEqual(got, ["a/config.py", "cc/config.py",
                               "bb/config.py"])

    def test_case_insensitive_and_at_prefix(self):
        _touch(self.root, "README.md")
        self.assertEqual(
            _paths(files.search(str(self.root), "@readme"))[0], "README.md"
        )

    def test_directories_listed_with_kind(self):
        _touch(self.root, "src/app/main.py")
        body = files.search(str(self.root), "app")
        kinds = {e["path"]: e["kind"] for e in body["entries"]}
        self.assertEqual(kinds["src/app"], "dir")
        self.assertEqual(kinds["src/app/main.py"], "file")
        self.assertEqual(_paths(body)[0], "src/app")

    def test_empty_query_lists_recent_files(self):
        _touch(self.root, "old.txt", mtime=1000)
        _touch(self.root, "sub/new.txt", mtime=5000)
        _touch(self.root, "mid.txt", mtime=3000)
        body = files.search(str(self.root), "", limit=2)
        self.assertEqual(_paths(body), ["sub/new.txt", "mid.txt"])
        self.assertTrue(all(e["kind"] == "file" for e in body["entries"]))

    def test_limit_clamped(self):
        for i in range(5):
            _touch(self.root, f"f{i}.txt")
        self.assertEqual(len(files.search(str(self.root), "f", 2)["entries"]), 2)
        self.assertEqual(len(files.search(str(self.root), "f", 0)["entries"]), 1)
        with patch.object(files, "MAX_LIMIT", 3):
            self.assertEqual(
                len(files.search(str(self.root), "f", 99)["entries"]), 3
            )

    def test_span_finds_tightest_match(self):
        self.assertEqual(files._span("a111b111c_a_b_c.py", "abc"), 5)
        self.assertEqual(files._span("a__b__c.py", "abc"), 7)
        self.assertIsNone(files._span("acb", "abc"))
        _touch(self.root, "a__b__c.py")
        _touch(self.root, "a111b111c_a_b_c.py")
        self.assertEqual(_paths(files.search(str(self.root), "abc")),
                         ["a111b111c_a_b_c.py", "a__b__c.py"])

    def test_concurrent_misses_share_one_build(self):
        _touch(self.root, "one.txt")
        real_build = files._build
        started, release = threading.Event(), threading.Event()
        calls = []

        def slow_build(root):
            calls.append(root)
            started.set()
            release.wait(5)
            return real_build(root)

        results = []

        def keystroke(q):
            results.append(files.search(str(self.root), q))

        with patch.object(files, "_build", slow_build):
            first = threading.Thread(target=keystroke, args=("o",))
            first.start()
            self.assertTrue(started.wait(5))
            # The second keystroke must wait on the first's build, not
            # arrive after it and hit the cache: note when it waits.
            waiting = threading.Event()

            class _NotedDone(threading.Event):
                def wait(self, timeout=None):
                    waiting.set()
                    return super().wait(timeout)

            (pending,) = files._building.values()
            pending.done = _NotedDone()
            second = threading.Thread(target=keystroke, args=("on",))
            second.start()
            self.assertTrue(waiting.wait(5))
            release.set()
            first.join(5)
            second.join(5)
            self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 2)
        for body in results:
            self.assertEqual(_paths(body), ["one.txt"])

    def test_listing_cached_between_keystrokes(self):
        _touch(self.root, "one.txt")
        files.search(str(self.root), "o")
        _touch(self.root, "two.txt")
        self.assertNotIn("two.txt", _paths(files.search(str(self.root), "t")))
        files.clear_cache()
        self.assertIn("two.txt", _paths(files.search(str(self.root), "t")))


class TestWalk(_Tmp):
    def test_skips_heavy_and_hidden_dirs(self):
        _touch(self.root, "src/main.py")
        _touch(self.root, ".env")
        for d in ("node_modules/pkg", ".venv/lib", "__pycache__", ".git",
                  "build", "dist", ".cache"):
            _touch(self.root, f"{d}/main.py")
        body = files.search(str(self.root), "main")
        self.assertEqual(body["source"], "walk")
        self.assertEqual(_paths(body), ["src/main.py"])
        self.assertIn(".env", _paths(files.search(str(self.root), ".env")))

    def test_file_cap_truncates(self):
        for i in range(10):
            _touch(self.root, f"f{i}.txt")
        with patch.object(files, "MAX_FILES", 4):
            body = files.search(str(self.root), "", limit=50)
        self.assertTrue(body["truncated"])
        self.assertEqual(len(body["entries"]), 4)

    def test_dir_cap_truncates(self):
        for i in range(6):
            _touch(self.root, f"d{i}/f.txt")
        with patch.object(files, "MAX_DIRS", 3):
            body = files.search(str(self.root), "f.txt")
        self.assertTrue(body["truncated"])
        self.assertLess(len(body["entries"]), 6)


class TestBudget(_Tmp):
    def test_folder_read_stops_at_deadline_before_sorting(self):
        for i in range(600):
            _touch(self.root, f"f{i:03}.txt")
        fd = files.open_dir(str(self.root), "")
        try:
            items, cut = files._read_dir(fd, time.monotonic() - 1)
        finally:
            os.close(fd)
        self.assertTrue(cut)
        self.assertLess(len(items), 600)

    def test_folder_read_capped(self):
        for i in range(10):
            _touch(self.root, f"f{i}.txt")
        fd = files.open_dir(str(self.root), "")
        try:
            with patch.object(files, "MAX_FILES", 4):
                items, cut = files._read_dir(fd, time.monotonic() + 60)
        finally:
            os.close(fd)
        self.assertTrue(cut)
        self.assertEqual(len(items), 4)

    def test_git_collection_stops_at_deadline(self):
        rels = [f"f{i:03}.txt" for i in range(600)]
        for rel in rels:
            _touch(self.root, rel)
        entries, cut = files._from_git(
            str(self.root), rels, time.monotonic() - 1
        )
        self.assertTrue(cut)
        self.assertLess(len(entries), 600)


class TestWalkSwap(_Tmp):
    def test_folder_swapped_for_outside_link_is_not_followed(self):
        outside = self.base / "outside"
        _touch(outside, "secret.txt")
        _touch(self.root, "a/inner.txt")

        def swap(rel_dir):
            if rel_dir == "a":
                os.rename(self.root / "a", self.base / "a_moved")
                os.symlink(outside, self.root / "a")

        with patch.object(files, "_before_open", swap):
            body = files.search(str(self.root), "", limit=50)
        self.assertEqual(body["source"], "walk")
        got = _paths(body)
        self.assertNotIn("a/secret.txt", got)
        self.assertNotIn("a/inner.txt", got)

    def test_nested_swap_above_queued_folder_is_not_followed(self):
        outside = self.base / "outside"
        _touch(outside, "b/secret.txt")
        _touch(self.root, "a/b/inner.txt")

        def swap(rel_dir):
            if rel_dir == "a/b":
                os.rename(self.root / "a", self.base / "a_moved")
                os.symlink(outside, self.root / "a")

        with patch.object(files, "_before_open", swap):
            got = _paths(files.search(str(self.root), "", limit=50))
        self.assertNotIn("a/b/secret.txt", got)


@unittest.skipUnless(HAS_GIT, "git not installed")
class TestGit(_Tmp):
    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True,
                       capture_output=True)

    def setUp(self):
        super().setUp()
        self._git("init", "-q")

    def test_gitignored_files_excluded(self):
        (self.root / ".gitignore").write_text("secret.txt\nlogs/\n")
        _touch(self.root, "tracked.py")
        _touch(self.root, "untracked.py")
        _touch(self.root, "secret.txt")
        _touch(self.root, "logs/run.txt")
        self._git("add", "tracked.py", ".gitignore")
        body = files.search(str(self.root), "", limit=50)
        self.assertEqual(body["source"], "git")
        got = set(_paths(body))
        self.assertIn("tracked.py", got)
        self.assertIn("untracked.py", got)
        self.assertNotIn("secret.txt", got)
        self.assertNotIn("logs/run.txt", got)
        self.assertEqual(files.search(str(self.root), "secret")["entries"],
                         [])

    def test_subfolder_cwd_lists_relative_to_it(self):
        _touch(self.root, "pkg/inner/mod.py")
        _touch(self.root, "other.py")
        body = files.search(str(self.root / "pkg"), "", limit=50)
        self.assertEqual(_paths(body), ["inner/mod.py"])
        self.assertIn("inner",
                      _paths(files.search(str(self.root / "pkg"), "inner")))

    def test_ignored_only_subfolder_stays_empty(self):
        (self.root / ".gitignore").write_text("*.log\n")
        _touch(self.root, "pkg/debug.log")
        _touch(self.root, "pkg/trace.log")
        body = files.search(str(self.root / "pkg"), "", limit=50)
        self.assertEqual(body["source"], "git")
        self.assertEqual(body["entries"], [])
        self.assertEqual(
            files.search(str(self.root / "pkg"), "log")["entries"], []
        )

    def test_git_cap_truncates(self):
        for i in range(10):
            _touch(self.root, f"f{i}.txt")
        with patch.object(files, "MAX_FILES", 3):
            body = files.search(str(self.root), "", limit=50)
        self.assertEqual(body["source"], "git")
        self.assertTrue(body["truncated"])
        self.assertEqual(len(body["entries"]), 3)

    def test_symlink_out_of_repo_dropped(self):
        outside = self.base / "outside"
        _touch(outside, "passwords.txt")
        _touch(self.root, "real.txt")
        os.symlink(outside / "passwords.txt", self.root / "leak.txt")
        os.symlink(self.root / "real.txt", self.root / "alias.txt")
        got = _paths(files.search(str(self.root), "", limit=50))
        self.assertNotIn("leak.txt", got)
        self.assertIn("alias.txt", got)


class TestContainment(_Tmp):
    def test_symlinks_pointing_outside_refused(self):
        outside = self.base / "outside"
        _touch(outside, "secret/key.pem")
        _touch(self.root, "inside.txt")
        os.symlink(outside / "secret", self.root / "linkdir")
        os.symlink(outside / "secret" / "key.pem", self.root / "key.pem")
        os.symlink(self.root / "inside.txt", self.root / "ok.txt")
        got = _paths(files.search(str(self.root), "", limit=50))
        got += _paths(files.search(str(self.root), "key", limit=50))
        got += _paths(files.search(str(self.root), "linkdir", limit=50))
        for p in got:
            self.assertFalse(p.startswith("linkdir"), p)
            self.assertNotEqual(p, "key.pem")
        self.assertIn("ok.txt", _paths(files.search(str(self.root), "ok")))

    def test_symlinked_dir_inside_is_listed_not_descended(self):
        _touch(self.root, "a/b.txt")
        os.symlink(self.root, self.root / "loop")
        got = _paths(files.search(str(self.root), "", limit=50))
        self.assertNotIn("loop/a/b.txt", got)
        kinds = {e["path"]: e["kind"]
                 for e in files.search(str(self.root), "loop")["entries"]}
        self.assertEqual(kinds.get("loop"), "dir")

    def test_traversal_queries_match_nothing(self):
        _touch(self.root, "a.txt")
        _touch(self.base, "sibling.txt")
        for q in ("../sibling.txt", "..", "a/../../x", "/etc/passwd",
                  "~/.ssh", "@../a"):
            self.assertEqual(files.search(str(self.root), q)["entries"], [],
                             q)

    def test_confine_helper(self):
        _touch(self.root, "a/b.txt")
        _touch(self.base, "outside.txt")
        os.symlink(self.base / "outside.txt", self.root / "out.txt")
        root = files.resolve_root(str(self.root))
        self.assertEqual(files.confine(root, "a/b.txt"),
                         str(self.root / "a" / "b.txt"))
        for rel in ("", "/etc/passwd", "../outside.txt", "a/../../x",
                    "a\\..\\..\\x", "out.txt", "a\x00b"):
            self.assertIsNone(files.confine(root, rel), rel)

    def test_cwd_symlink_resolves(self):
        _touch(self.root, "a.txt")
        link = self.base / "link"
        os.symlink(self.root, link)
        body = files.search(str(link), "a")
        self.assertEqual(body["cwd"], str(link))
        self.assertEqual(_paths(body), ["a.txt"])

    def test_missing_cwd_errors(self):
        with self.assertRaises(files.FilesError) as cm:
            files.search(str(self.base / "gone"), "")
        self.assertEqual(cm.exception.code, "cwd_missing")
        f = _touch(self.root, "file.txt")
        with self.assertRaises(files.FilesError) as cm:
            files.search(str(f), "")
        self.assertEqual(cm.exception.code, "cwd_missing")
        with self.assertRaises(files.FilesError) as cm:
            files.search("relative/path", "")
        self.assertEqual(cm.exception.code, "bad_cwd")


class TestRoute(unittest.TestCase):
    def setUp(self):
        files.clear_cache()
        self.addCleanup(files.clear_cache)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.proj = Path(os.path.realpath(tmp))
        _touch(self.proj, "src/hub.py")
        self.srv = _Server(sessions=[
            _session(sid="s1", path=str(self.proj)),
            _session(sid="s2", path=str(self.proj / "missing")),
            _session(sid="s3", path=""),
        ])
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {cred['credential']}"}

    def get(self, key, query="", headers=None):
        path = f"/v1/sessions/{quote(key, safe='')}/files{query}"
        status, data = self.srv.request(
            "GET", path, headers=self.hdr if headers is None else headers
        )
        return status, json.loads(data)

    def test_requires_auth(self):
        status, body = self.get("claude:s1", "?q=hub", headers={})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthorized")

    def test_lists_files(self):
        status, body = self.get("claude:s1", "?q=hub&limit=5")
        self.assertEqual(status, 200)
        self.assertEqual(body["cwd"], str(self.proj))
        self.assertEqual(body["query"], "hub")
        self.assertEqual(body["entries"][0]["path"], "src/hub.py")
        self.assertEqual(set(body["entries"][0]), {"path", "kind", "mtime"})

    def test_unknown_session(self):
        status, body = self.get("claude:nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "unknown_session")

    def test_missing_cwd(self):
        status, body = self.get("claude:s2")
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["code"], "cwd_missing")

    def test_no_cwd(self):
        status, body = self.get("claude:s3")
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["code"], "bad_cwd")

    def test_non_utf8_git_name_skipped(self):
        bad = "bad\udcff.txt"
        real_lstat = os.lstat

        def lstat(path, *a, **kw):
            # Pretend the undecodable name exists; macOS can't create one.
            if str(path).endswith(bad):
                return real_lstat(str(self.proj / "src" / "hub.py"))
            return real_lstat(path, *a, **kw)

        with patch.object(files, "_git_files",
                          return_value=(["src/hub.py", bad], False)), \
                patch.object(files.os, "lstat", lstat):
            status, body = self.get("claude:s1", "?q=bad")
        self.assertEqual(status, 200)
        self.assertEqual(body["entries"], [])

    @unittest.skipIf(sys.platform == "darwin",
                     "APFS refuses names that aren't UTF-8")
    def test_non_utf8_name_on_disk_skipped(self):
        open(os.path.join(os.fsencode(self.proj), b"bad\xff.txt"),
             "w").close()
        status, body = self.get("claude:s1", "?q=bad")
        self.assertEqual(status, 200)
        self.assertEqual(body["entries"], [])

    def test_satellite_session_unavailable(self):
        home = self.srv.home
        catalog_mod.upsert_machine(home, {"id": "sat1", "name": "studio"})
        catalog_mod.merge_remote_rows(home, "sat1", [{
            "key": "claude:r1", "cwd": str(self.proj), "status": "idle",
        }])
        self.srv.state.catalog.refresh()
        key = protocol.remote_key("sat1", "claude:r1")
        status, body = self.get(key, "?q=hub")
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["code"], "files_unavailable")
        self.assertIn("studio", body["error"]["message"])


if __name__ == "__main__":
    unittest.main()
