"""remote/search.py and GET /v1/search: FTS index hits, then metadata."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from quiver.remote import search
from tests.test_remote_hub import _Server, _session
from tests.test_search_index import write_index


def _row(key, sid, title, cwd="/work/x", harness="claude", ago=0.0, remote=False):
    row = {"key": key, "id": sid, "title": title, "cwd": cwd, "harness": harness,
           "agent": "Claude Code", "last_active_at": time.time() - ago}
    if remote:
        row["remote"] = True
    return row


class CompileQueryTest(unittest.TestCase):
    def test_regex_is_case_insensitive(self):
        self.assertTrue(search.compile_query("LEDGER.*cursor").search("fix the ledger cursor"))

    def test_invalid_regex_matches_literally(self):
        pattern = search.compile_query("foo(")
        self.assertTrue(pattern.search("call foo( here"))

    def test_nested_quantifier_is_literal(self):
        # (a+)+b would backtrack 2^n on a run of a's; it must be plain text.
        pattern = search.compile_query("(a+)+b")
        self.assertIsNone(pattern.search("a" * 40))
        self.assertTrue(pattern.search("x (a+)+b y"))


class SearchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.rows = [
            _row("claude:sess-alpha", "sess-alpha", "Fix the ledger cursor", ago=300),
            _row("codex:sess-beta", "sess-beta", "Dark mode", harness="codex", ago=10),
            _row("claude:other", "other", "Ledger notes", ago=5),
            _row("claude:far@m2", "far", "ledger on the satellite", remote=True, ago=1),
        ]

    def test_without_index_matches_metadata_newest_first(self):
        out = search.search(self.rows, "ledger", home=self.home)
        self.assertFalse(out["indexed"])
        self.assertEqual([r["key"] for r in out["results"]],
                         ["claude:far@m2", "claude:other", "claude:sess-alpha"])
        self.assertTrue(all(r["source"] == "metadata" for r in out["results"]))

    def test_index_hits_come_first_with_snippets(self):
        write_index(self.home)
        out = search.search(self.rows, "ledger", home=self.home)
        self.assertTrue(out["indexed"])
        first = out["results"][0]
        self.assertEqual(first["key"], "claude:sess-alpha")
        self.assertEqual(first["source"], "index")
        self.assertIn("ledger", first["snippet"].lower())
        # Metadata-only matches follow, without repeating the index hit.
        keys = [r["key"] for r in out["results"]]
        self.assertEqual(keys.count("claude:sess-alpha"), 1)
        self.assertIn("claude:other", keys)

    def test_transcript_only_match_needs_the_index(self):
        # "dark mode to the settings" is only in sess-beta's transcript.
        self.assertEqual(search.search(self.rows, "settings page", home=self.home)["results"], [])
        write_index(self.home)
        keys = [r["key"] for r in search.search(self.rows, "settings page", home=self.home)["results"]]
        self.assertEqual(keys, ["codex:sess-beta"])

    def test_hits_outside_the_catalog_are_dropped(self):
        write_index(self.home)
        # sess-gamma is in the index but not in the rows.
        keys = [r["key"] for r in search.search(self.rows, "parser chokes", home=self.home)["results"]]
        self.assertEqual(keys, [])

    def test_empty_query_and_limit(self):
        self.assertEqual(search.search(self.rows, "  ", home=self.home)["results"], [])
        self.assertEqual(len(search.search(self.rows, "e", home=self.home, limit=1)["results"]), 1)


class SearchRouteTest(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[_session(sid="sess-alpha", path="/work/proj")])
        self.addCleanup(self.srv.stop)

    def test_needs_a_paired_device(self):
        status, _ = self.srv.request("GET", "/v1/search?q=test")
        self.assertEqual(status, 401)

    def test_returns_catalog_rows(self):
        cred = self.srv.pair()
        hdr = {"Authorization": f"Bearer {cred['credential']}"}
        status, data = self.srv.request("GET", "/v1/search?q=TES&limit=5", headers=hdr)
        self.assertEqual(status, 200)
        body = json.loads(data)
        self.assertEqual([r["key"] for r in body["results"]], ["claude:sess-alpha"])
        self.assertEqual(body["results"][0]["session"]["title"], "test")
