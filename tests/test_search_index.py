"""Read path of the app's FTS5 session-search index.

``~/.swe/data/search/search.sqlite`` is written by the SWE app;
``sessions/search_index.py`` only reads it. These tests build the exact
frozen schema into a throwaway HOME, then check availability gating, the
phrase/AND/OR ladder, agent and limit filters, orphan suppression, the
``swe session --search`` union and the MCP ``search`` parameter.
"""

import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from swe.sessions import search_index
from swe.sessions.models import Session
from tests.test_json_output import decode, run_swe


_DDL = """\
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL,
  session_id TEXT NOT NULL, file_path TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
  cwd TEXT, cwd_key TEXT, created_at TEXT, updated_at TEXT, message_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE files(path TEXT PRIMARY KEY, dev INTEGER, ino INTEGER, byte_offset INTEGER NOT NULL,
  mtime_ms REAL NOT NULL, size_bytes INTEGER, session_row_id INTEGER,
  state TEXT NOT NULL DEFAULT 'current', fail_count INTEGER NOT NULL DEFAULT 0, failed_mtime_ms REAL);
CREATE TABLE messages(id INTEGER PRIMARY KEY, session_row_id INTEGER NOT NULL, role TEXT NOT NULL, ts TEXT);
CREATE INDEX messages_session ON messages(session_row_id);
CREATE VIRTUAL TABLE messages_fts USING fts5(user_text, assistant_text, tool_text, identifiers,
  tokenize="unicode61 tokenchars '_.-/+'", detail=full);
"""

# (session_row_id, agent, session_id, title, cwd)
_SESSIONS = [
    (1, "claude", "sess-alpha", "Fix the ledger cursor", "/work/proj"),
    (2, "codex", "sess-beta", "Dark mode", "/work/other"),
    (3, "droid", "sess-gamma", "Gamma title", "/work/gamma"),
]

# (message id, session_row_id, role, fts column, text). id 5's
# session_row_id 999 has no sessions row — an orphan that must never hit.
_MESSAGES = [
    (1, 1, "user", "user_text", "the ledger cursor holds scan offsets"),
    (2, 1, "assistant", "assistant_text", "fixed the ledger retry loop"),
    (3, 2, "user", "user_text", "add dark mode to the settings page"),
    (4, 3, "user", "user_text", "the C++ parser chokes on foo-bar inside Ledger.swift"),
    (5, 999, "user", "user_text", "ledger ghost row"),
    # Same session as 4, non-contiguous "scan ... offsets": a real phrase
    # rung must skip it while the AND rung would take it.
    (6, 3, "assistant", "assistant_text", "scan the index then read offsets later"),
]


def write_index(home: Path, schema_version: str = "1") -> Path:
    """Create a fixture index under ``home`` with the frozen schema."""
    path = search_index.index_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_DDL)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
            (schema_version,),
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('index_incarnation', 'test-uuid')"
        )
        for row_id, agent, sid, title, cwd in _SESSIONS:
            conn.execute(
                "INSERT INTO sessions(id, agent, session_id, file_path, title,"
                " cwd, cwd_key, updated_at, message_count)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id,
                    agent,
                    sid,
                    str(home / "transcripts" / f"{sid}.jsonl"),
                    title,
                    cwd,
                    cwd,
                    f"2026-10-0{row_id}T10:00:00Z",
                    1,
                ),
            )
        for msg_id, srid, role, column, text in _MESSAGES:
            conn.execute(
                "INSERT INTO messages(id, session_row_id, role, ts)"
                " VALUES (?, ?, ?, ?)",
                (msg_id, srid, role, f"2026-10-0{srid if srid < 10 else 1}T10:00:00Z"),
            )
            cols = {"user_text": "", "assistant_text": "", "tool_text": "",
                    "identifiers": ""}
            cols[column] = text
            conn.execute(
                "INSERT INTO messages_fts(rowid, user_text, assistant_text,"
                " tool_text, identifiers) VALUES (?, ?, ?, ?, ?)",
                (msg_id, cols["user_text"], cols["assistant_text"],
                 cols["tool_text"], cols["identifiers"]),
            )
        conn.commit()
    finally:
        conn.close()
    return path


class TempHomeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)


class AvailableTest(TempHomeTest):
    def test_absent_index_is_unavailable(self):
        self.assertFalse(search_index.available(self.home))

    def test_empty_directory_is_unavailable(self):
        search_index.index_path(self.home).parent.mkdir(parents=True)
        self.assertFalse(search_index.available(self.home))

    def test_a_non_sqlite_file_is_unavailable(self):
        path = search_index.index_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not a database")
        self.assertFalse(search_index.available(self.home))

    def test_a_wrong_schema_version_is_unavailable(self):
        write_index(self.home, schema_version="2")
        self.assertFalse(search_index.available(self.home))

    def test_a_missing_schema_version_is_unavailable(self):
        write_index(self.home)
        conn = sqlite3.connect(search_index.index_path(self.home))
        conn.execute("DELETE FROM meta WHERE key = 'schema_version'")
        conn.commit()
        conn.close()
        self.assertFalse(search_index.available(self.home))

    def test_no_messages_fts_is_unavailable(self):
        path = search_index.index_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
        conn.commit()
        conn.close()
        self.assertFalse(search_index.available(self.home))

    def test_a_valid_index_is_available(self):
        write_index(self.home)
        self.assertTrue(search_index.available(self.home))


class SearchTest(TempHomeTest):
    def setUp(self):
        super().setUp()
        write_index(self.home)

    def ids(self, hits):
        return {h.session_id for h in hits}

    def test_a_single_term_hits_its_session(self):
        hits = search_index.search(self.home, "cursor")
        self.assertEqual(self.ids(hits), {"sess-alpha"})
        self.assertGreater(hits[0].score, 0)

    def test_orphaned_messages_never_match(self):
        hits = search_index.search(self.home, "ghost")
        self.assertEqual(hits, [])
        hits = search_index.search(self.home, "ledger")
        self.assertEqual(self.ids(hits), {"sess-alpha"})

    def test_a_phrase_prefers_contiguous_terms(self):
        hits = search_index.search(self.home, "scan offsets")
        self.assertEqual(self.ids(hits), {"sess-alpha"})

    def test_scattered_terms_hit_via_and_before_or(self):
        # "scan" and "index" share no contiguous position but do share
        # msg 6, so the AND rung answers first; an OR rung would also
        # return sess-alpha's lone "scan".
        hits = search_index.search(self.home, "scan index")
        self.assertEqual(self.ids(hits), {"sess-gamma"})

    def test_terms_in_different_sessions_fall_back_to_or(self):
        # "cursor" lives only in sess-alpha, "settings" only in sess-beta;
        # no single message holds both, so phrase and AND run dry first.
        hits = search_index.search(self.home, "cursor settings")
        self.assertEqual(self.ids(hits), {"sess-alpha", "sess-beta"})

    def test_and_semantics_require_both_terms_in_one_row(self):
        # "ledger" and "mode" never share a row: AND yields nothing, OR
        # yields every row holding either term.
        hits = search_index.search(self.home, "ledger mode")
        self.assertEqual(self.ids(hits), {"sess-alpha", "sess-beta"})

    def test_dashed_terms_are_quoted_not_parsed_as_operators(self):
        hits = search_index.search(self.home, "foo-bar")
        self.assertEqual(self.ids(hits), {"sess-gamma"})

    def test_symbol_terms_like_cxx_match(self):
        hits = search_index.search(self.home, "C++")
        self.assertEqual(self.ids(hits), {"sess-gamma"})

    def test_dotted_identifier_terms_match(self):
        hits = search_index.search(self.home, "Ledger.swift")
        self.assertEqual(self.ids(hits), {"sess-gamma"})

    def test_a_term_with_only_punctuation_returns_nothing(self):
        self.assertEqual(search_index.search(self.home, "?!@#"), [])

    def test_agent_filter_narrows_to_named_harnesses(self):
        hits = search_index.search(self.home, "ledger", agents=["codex"])
        self.assertEqual(hits, [])
        hits = search_index.search(self.home, "dark", agents=["codex"])
        self.assertEqual(self.ids(hits), {"sess-beta"})

    def test_limit_caps_the_hits(self):
        hits = search_index.search(self.home, "cursor settings", limit=1)
        self.assertEqual(len(hits), 1)

    def test_hits_carry_role_ts_and_marked_snippet(self):
        hits = search_index.search(self.home, "ledger")
        hit = hits[0]
        self.assertIn("ledger", hit.snippet.casefold())
        self.assertIn("\uE000", hit.marked_snippet)
        self.assertIn("\uE001", hit.marked_snippet)
        self.assertIn(hit.role, ("user", "assistant"))
        self.assertTrue(hit.ts)
        self.assertEqual(hit.session_id, "sess-alpha")
        self.assertEqual(hit.agent, "claude")
        self.assertEqual(hit.cwd, "/work/proj")
        self.assertTrue(hit.file_path.endswith("sess-alpha.jsonl"))

    def test_a_missing_index_returns_empty(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = Path(tmp.name)
        self.assertEqual(search_index.search(empty, "ledger"), [])


class MergeHitsTest(TempHomeTest):
    """``merge_hits``: the one FTS union ``swe session`` and MCP share."""

    def _parsed(self):
        return [Session(timestamp=1.0, agent="Claude Code", path="/work/proj",
                        title="t", session_id="sess-alpha", tool_name="claude")]

    def test_no_index_leaves_the_listing_alone(self):
        sessions = self._parsed()
        self.assertEqual(search_index.merge_hits(self.home, "ledger", sessions), (None, {}))
        self.assertEqual(len(sessions), 1)

    def test_hits_join_parsed_sessions_and_add_the_rest(self):
        write_index(self.home)
        sessions = self._parsed()
        ids, best = search_index.merge_hits(self.home, "offsets", sessions)
        self.assertEqual(ids, {"sess-alpha", "sess-gamma"})
        self.assertEqual(set(best), {"sess-alpha", "sess-gamma"})
        self.assertIn("offsets", best["sess-alpha"].snippet.lower())
        # sess-gamma was only in the index: it joins the listing.
        self.assertEqual([s.session_id for s in sessions], ["sess-alpha", "sess-gamma"])

    def test_the_agent_filter_widens_aliases(self):
        write_index(self.home)
        ids, _ = search_index.merge_hits(self.home, "dark", [], agent_filter="cx")
        self.assertEqual(ids, {"sess-beta"})


class MatchKeysTest(unittest.TestCase):
    def test_plain_ids_match_themselves(self):
        self.assertEqual(search_index.match_keys("abc"), frozenset({"abc"}))

    def test_a_path_id_matches_its_stem(self):
        keys = search_index.match_keys("/pi/sessions/chat-1.jsonl")
        self.assertIn("chat-1", keys)
        self.assertIn("/pi/sessions/chat-1.jsonl", keys)

    def test_a_rollout_stem_matches_its_thread_uuid(self):
        uuid = "0199aaaa-bbbb-cccc-dddd-eeeeffff0001"
        keys = search_index.match_keys(f"rollout-2026-09-01T10-00-00-{uuid}")
        self.assertIn(uuid, keys)

    def test_an_empty_id_has_no_keys(self):
        self.assertEqual(search_index.match_keys(""), frozenset())


class ToolNamesForTest(unittest.TestCase):
    def test_no_filter_means_no_sql_filter(self):
        self.assertIsNone(search_index.tool_names_for(None))

    def test_an_alias_maps_to_its_tool_name(self):
        self.assertEqual(search_index.tool_names_for("cc"), ["claude"])

    def test_an_unknown_name_passes_through(self):
        self.assertEqual(search_index.tool_names_for("nope"), ["nope"])


# ---------------------------------------------------------------------------
# swe session --search e2e (real CLI subprocess, throwaway HOME)
# ---------------------------------------------------------------------------

SID_META = "11111111-2222-3333-4444-555555555555"
SID_FTS = "22222222-3333-4444-5555-666666666666"
SID_GHOST = "33333333-4444-5555-6666-777777777777"


def _write_jsonl(path: Path, records: list[dict], mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(rec) + "\n" for rec in records))
    os.utime(path, (mtime, mtime))


def _add_claude(home: Path, session_id: str, prompt: str, cwd: str = "/work/proj") -> Path:
    path = home / ".claude" / "projects" / "-work-proj" / f"{session_id}.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "user", "cwd": cwd,
             "message": {"role": "user", "content": prompt}},
            {"type": "assistant", "message": {
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn"}},
        ],
        1_790_000_000,
    )
    return path


def _e2e_index(home: Path, ghost_file: Path) -> None:
    """An index naming one listed session and one the parsers never see."""
    path = search_index.index_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_DDL)
        conn.execute(
            "INSERT INTO meta VALUES ('schema_version', '1'),"
            " ('index_incarnation', 'e2e')"
        )
        conn.execute(
            "INSERT INTO sessions(id, agent, session_id, file_path, title,"
            " cwd, updated_at) VALUES (1, 'claude', ?, ?, ?, '/work/proj',"
            " '2026-10-05T10:00:00Z')",
            (SID_FTS, str(home / ".claude/projects/-work-proj" / f"{SID_FTS}.jsonl"),
             "refactor the retry loop"),
        )
        conn.execute(
            "INSERT INTO sessions(id, agent, session_id, file_path, title,"
            " cwd, updated_at) VALUES (2, 'claude', ?, ?, ?, '/work/ghost',"
            " '2026-10-04T10:00:00Z')",
            (SID_GHOST, str(ghost_file), "ghost title from index"),
        )
        conn.executemany(
            "INSERT INTO messages(id, session_row_id, role, ts)"
            " VALUES (?, ?, ?, ?)",
            [
                (1, 1, "user", "2026-10-05T10:00:00Z"),
                (2, 2, "user", "2026-10-04T10:00:00Z"),
            ],
        )
        conn.executemany(
            "INSERT INTO messages_fts(rowid, user_text) VALUES (?, ?)",
            [
                (1, "the zebra migration plan worked"),
                (2, "zebra ghost content"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


class SessionSearchE2ETest(unittest.TestCase):
    """``swe session --search`` unions metadata matches with FTS hits."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        _add_claude(self.home, SID_META, "fix the login bug")
        _add_claude(self.home, SID_FTS, "refactor the retry loop")
        # A transcript parse_claude never lists: subagents/ is not one of
        # the project files it scans, so this session exists on disk only
        # for the index to name.
        self.ghost_file = (
            self.home / ".claude" / "projects" / "-work-proj"
            / "subagents" / f"{SID_GHOST}.jsonl"
        )
        _write_jsonl(
            self.ghost_file,
            [{"type": "user", "cwd": "/work/ghost",
              "message": {"role": "user", "content": "the ghost prompt"}}],
            1_780_000_000,
        )

    def sessions(self, *args):
        result = run_swe(self.home, "session", "--json", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return decode(result)["data"]["sessions"]

    def test_without_an_index_only_metadata_matches(self):
        rows = self.sessions("--search=zebra")
        self.assertEqual(rows, [])
        rows = self.sessions("--search=login")
        self.assertEqual([r["id"] for r in rows], [SID_META])
        self.assertNotIn("evidence", rows[0])

    def test_fts_hits_join_the_metadata_match(self):
        _e2e_index(self.home, self.ghost_file)
        rows = self.sessions("--search=zebra")
        self.assertEqual({r["id"] for r in rows}, {SID_FTS, SID_GHOST})
        by_id = {r["id"]: r for r in rows}
        ev = by_id[SID_FTS]["evidence"]
        self.assertIn("zebra", ev["snippet"])
        self.assertEqual(ev["role"], "user")
        self.assertTrue(ev["ts"])
        # The ghost file was resolved from the transcript the index
        # recorded: title and cwd come from re-parsing that file.
        ghost = by_id[SID_GHOST]
        self.assertEqual(ghost["harness"], "claude")
        self.assertEqual(ghost["agent"], "Claude Code")
        self.assertEqual(ghost["title"], "the ghost prompt")
        self.assertEqual(ghost["cwd"], "/work/ghost")
        self.assertIn("evidence", ghost)

    def test_a_metadata_hit_still_appears_with_no_evidence(self):
        _e2e_index(self.home, self.ghost_file)
        rows = self.sessions("--search=login")
        self.assertEqual([r["id"] for r in rows], [SID_META])
        self.assertNotIn("evidence", rows[0])


# ---------------------------------------------------------------------------
# MCP get_recent_sessions(search=...)
# ---------------------------------------------------------------------------


class _FakeFastMCP:
    def __init__(self, name):
        self.name = name

    def tool(self):
        return lambda fn: fn


def _fake_mcp_modules():
    fastmcp = types.ModuleType("mcp.server.fastmcp")
    fastmcp.FastMCP = _FakeFastMCP
    server = types.ModuleType("mcp.server")
    server.fastmcp = fastmcp
    pkg = types.ModuleType("mcp")
    pkg.server = server
    return {"mcp": pkg, "mcp.server": server, "mcp.server.fastmcp": fastmcp}


class McpSearchTest(TempHomeTest):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(sys.modules, _fake_mcp_modules())
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop("swe.mcp.server", None)
        self.addCleanup(lambda: sys.modules.pop("swe.mcp.server", None))
        import swe.mcp.server as server

        self.server = server
        self.home_patch = patch.dict(os.environ, {"HOME": str(self.home)})
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def _sessions(self):
        return [
            Session(
                timestamp=1_790_000_000_000.0,
                agent="Claude Code",
                path="/work/proj",
                title="fix the login bug",
                session_id=SID_META,
                tool_name="claude",
            ),
            Session(
                timestamp=1_780_000_000_000.0,
                agent="Claude Code",
                path="/work/proj",
                title="refactor the retry loop",
                session_id=SID_FTS,
                tool_name="claude",
            ),
        ]

    def test_without_an_index_search_is_metadata_only(self):
        with patch.object(
            self.server, "get_all_sessions", return_value=self._sessions()
        ):
            out = self.server.get_recent_sessions(search="zebra")
            self.assertIn("No sessions", out)
            out = self.server.get_recent_sessions(search="login")
            self.assertIn("fix the login bug", out)
            self.assertNotIn("refactor", out)

    def test_with_an_index_fts_hits_carry_a_match_line(self):
        ghost = self.home / "ghost.jsonl"
        _write_jsonl(
            ghost,
            [{"type": "user", "cwd": "/work/ghost",
              "message": {"role": "user", "content": "the ghost prompt"}}],
            1_780_000_000,
        )
        _e2e_index(self.home, ghost)
        with patch.object(
            self.server, "get_all_sessions", return_value=self._sessions()
        ):
            out = self.server.get_recent_sessions(search="zebra")
            self.assertIn("refactor the retry loop", out)
            self.assertIn("the ghost prompt", out)
            self.assertIn("match:", out)
            self.assertIn("zebra", out)

    def test_the_search_argument_is_optional(self):
        with patch.object(
            self.server, "get_all_sessions", return_value=self._sessions()
        ):
            out = self.server.get_recent_sessions(limit=5)
            self.assertIn("fix the login bug", out)
            self.assertIn("refactor the retry loop", out)


class SearchPassesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        write_index(self.home)

    def test_every_rung_without_snippets(self):
        passes = search_index.search_passes(self.home, "scan offsets")
        self.assertEqual([[h.session_id for h in p] for p in passes],
                         [["sess-alpha"], ["sess-alpha", "sess-gamma"], ["sess-alpha", "sess-gamma"]])
        self.assertTrue(all(h.snippet == "" and h.fts and h.rowid for p in passes for h in p))

    def test_one_word_has_one_rung(self):
        self.assertEqual(len(search_index.search_passes(self.home, "ledger")), 1)

    def test_fill_snippets(self):
        hits = search_index.search_passes(self.home, "scan offsets")[1]
        filled = search_index.fill_snippets(self.home, hits)
        self.assertTrue(all("offsets" in h.snippet for h in filled))
        self.assertTrue(all("\ue000" in h.marked_snippet for h in filled))

    def test_missing_index(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = Path(tmp.name)
        self.assertEqual(search_index.search_passes(empty, "ledger"), [])
        self.assertEqual(search_index.fill_snippets(empty, []), [])


if __name__ == "__main__":
    unittest.main()
