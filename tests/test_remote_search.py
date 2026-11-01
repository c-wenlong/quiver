"""remote/search.py and GET /v1/search: FTS index hits, then metadata."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from unittest.mock import patch

from swe.remote import search
from swe.sessions import search_index
from tests.test_remote_hub import _Server, _session
from tests.test_search_index import write_index


def _row(key, sid, title, cwd="/work/x", harness="claude", ago=0.0, remote=False):
    row = {"key": key, "id": sid, "title": title, "cwd": cwd, "harness": harness,
           "agent": "Claude Code", "last_active_at": time.time() - ago}
    if remote:
        row["remote"] = True
    return row


class MetadataMatchTest(unittest.TestCase):
    def test_plain_case_insensitive_text(self):
        rows = [_row("k1", "s1", "Fix the LEDGER cursor"), _row("k2", "s2", "call foo( here")]

        def find(query):
            # Metadata only: without a home the index under the real
            # HOME would be searched too.
            return search.search(rows, query, use_index=False)["results"]

        self.assertEqual([r["key"] for r in find("ledger c")], ["k1"])
        # Regex syntax is just text: no pattern compiles, nothing can stall.
        self.assertEqual([r["key"] for r in find("foo(")], ["k2"])
        self.assertEqual(find("(a|aa)+$"), [])


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

    def test_title_matches_lead_newest_first_and_keep_their_snippet(self):
        write_index(self.home)
        out = search.search(self.rows, "ledger", home=self.home)
        self.assertTrue(out["indexed"])
        keys = [r["key"] for r in out["results"]]
        # All three titles hold "ledger": the title tier, newest first.
        self.assertEqual(keys, ["claude:far@m2", "claude:other", "claude:sess-alpha"])
        alpha = out["results"][2]
        self.assertEqual(alpha["source"], "metadata")
        self.assertIn("ledger", alpha["snippet"].lower())
        self.assertIsNone(out["results"][1]["snippet"])

    def test_title_match_beats_a_newer_transcript_mention(self):
        write_index(self.home)
        rows = self.rows + [_row("claude:old", "old", "Settings page redesign", ago=90 * 86400)]
        keys = [r["key"] for r in search.search(rows, "settings page", home=self.home)["results"]]
        self.assertEqual(keys, ["claude:old", "codex:sess-beta"])

    def test_phrase_match_does_not_hide_sessions_with_the_words_apart(self):
        write_index(self.home)
        rows = self.rows + [_row("droid:sess-gamma", "sess-gamma", "Gamma title", harness="droid")]
        out = search.search(rows, "scan offsets", home=self.home)["results"]
        # sess-alpha has the phrase; sess-gamma only has both words apart.
        self.assertEqual([r["key"] for r in out], ["claude:sess-alpha", "droid:sess-gamma"])
        self.assertTrue(all(r["source"] == "index" for r in out))
        self.assertTrue(all(r["snippet"] for r in out))

    def test_words_apart_across_title_and_folder(self):
        rows = [_row("k1", "s1", "Ledger fix", cwd="/work/quiver"), _row("k2", "s2", "Ledger quiver")]
        keys = [r["key"] for r in search.search(rows, "quiver ledger", home=self.home)["results"]]
        # k2's title holds both words; k1 has them across title and folder.
        self.assertEqual(keys, ["k2", "k1"])

    def test_recent_session_leads_within_a_tier(self):
        rows = [_row("old", "s1", "ledger", ago=60 * 86400), _row("new", "s2", "ledger", ago=86400)]
        keys = [r["key"] for r in search.search(rows, "ledger", home=self.home)["results"]]
        self.assertEqual(keys, ["new", "old"])

    def test_strong_old_transcript_match_beats_a_weak_new_one(self):
        rows = [_row("claude:a", "a", "x", ago=30 * 86400), _row("claude:b", "b", "y", ago=0)]
        hit = lambda sid, score: search_index.SearchHit(  # noqa: E731
            session_id=sid, agent="claude", title="", cwd="", updated_at="", score=score,
            role="user", ts="", snippet="s")
        with patch.object(search_index, "available", return_value=True), \
                patch.object(search_index, "search_passes", return_value=[[hit("a", 10.0), hit("b", 1.0)]]), \
                patch.object(search_index, "fill_snippets", side_effect=lambda home, hits: hits):
            keys = [r["key"] for r in search.search(rows, "zzz", home=self.home)["results"]]
        # a: 1.0 x ~0.41 for 30 days; b: 0.1 x 1.0 for today.
        self.assertEqual(keys, ["claude:a", "claude:b"])

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

    def test_hits_outside_the_catalog_do_not_use_up_the_limit(self):
        write_index(self.home)
        rows = [_row("codex:sess-beta", "sess-beta", "Untitled")]
        hidden = [search_index.SearchHit(session_id=f"gone-{i}", agent="claude", title="", cwd="",
                                         updated_at="", score=100.0 - i, role="user", ts="", snippet="")
                  for i in range(150)]
        real = search_index.search_passes(self.home, "settings page")
        passes = [hidden + real[0]] + real[1:]
        with patch.object(search_index, "search_passes", return_value=passes) as fake:
            out = search.search(rows, "settings page", home=self.home, limit=5)["results"]
        self.assertEqual([r["key"] for r in out], ["codex:sess-beta"])
        self.assertIn("settings", out[0]["snippet"])
        self.assertEqual(fake.call_args.kwargs["limit"], search.INDEX_FETCH)

    def test_recency_weight(self):
        now = 1_000_000_000.0
        self.assertAlmostEqual(search.recency_weight(now, now), 1.0)
        self.assertAlmostEqual(search.recency_weight(now - 14 * 86400, now), 0.65)
        self.assertAlmostEqual(search.recency_weight(None, now), search.RECENCY_FLOOR)
        self.assertAlmostEqual(search.recency_weight("bad", now), search.RECENCY_FLOOR)

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
