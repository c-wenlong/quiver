"""Unit tests for session family engines."""

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.sessions.engines.common import (
    clean_title,
    extract_user_text,
    parse_iso_ts,
    path_from_encoded_dir,
    strip_file_uri,
)
from swe.sessions.engines.json_engine import JsonParserConfig, parse_json_store
from swe.sessions.models import Session
from swe.sessions.engines.jsonl_engine import (
    JsonlParserConfig,
    first_user_title,
    parse_jsonl_projects,
)
from swe.sessions.engines.sqlite_engine import SqliteParserConfig, parse_sqlite


class CommonHelpersTest(unittest.TestCase):
    def test_parse_iso_ts_units(self):
        self.assertAlmostEqual(parse_iso_ts(1_700_000_000), 1_700_000_000_000, delta=1)
        self.assertAlmostEqual(parse_iso_ts(1_700_000_000_000), 1_700_000_000_000, delta=1)
        self.assertGreater(parse_iso_ts("2026-07-01T12:00:00Z"), 0)

    def test_parse_iso_ts_iso_strings_exact(self):
        """Exact epoch-milliseconds for every offset-bearing spelling.

        Characterization written ahead of the Python 3.10 -> 3.11 floor
        bump; confirmed identical on real 3.10.17 and 3.12.8 before
        being committed.  Naive input is deliberately absent here, see
        ``test_parse_iso_ts_naive_uses_local_time``.
        """
        base_ms = 1785542400000.0  # 2026-08-01T00:00:00Z, via calendar.timegm
        cases = [
            ("Z bare", "2026-08-01T00:00:00Z", base_ms),
            ("explicit UTC offset", "2026-08-01T00:00:00+00:00", base_ms),
            ("negative offset", "2026-07-31T20:00:00-04:00", base_ms),
            ("ms with Z", "2026-08-01T00:00:00.123Z", 1785542400123.0),
            ("us with offset", "2026-08-01T00:00:00.123456+00:00", 1785542400123.456),
        ]
        for label, raw, expected in cases:
            with self.subTest(label=label, raw=raw):
                self.assertEqual(parse_iso_ts(raw), expected)

    def test_parse_iso_ts_naive_uses_local_time(self):
        """A naive string is read as LOCAL time, not UTC.

        Unlike ``harness.rate_limits._parse_iso8601_to_epoch``, which
        forces UTC on a naive value, this function lets ``.timestamp()``
        apply the machine timezone.  The expected number is therefore
        machine-dependent, so this test pins TZ=UTC for the duration
        rather than hardcoding a literal that would be wrong on any
        runner outside UTC.  Do not "fix" the number: fix the TZ.
        """
        try:
            with patch.dict(os.environ, {"TZ": "UTC"}):
                time.tzset()
                self.assertEqual(parse_iso_ts("2026-08-01T00:00:00"), 1785542400000.0)
        finally:
            # patch.dict has restored the real TZ by now; this tzset call
            # has to come AFTER it, or the process keeps UTC and every
            # later local-time-sensitive test becomes order-dependent.
            time.tzset()

    def test_parse_iso_ts_numeric_unit_detection(self):
        """The magnitude of a number decides how it is scaled.

        Boundaries are exclusive, so a value sitting exactly on 1e12
        is scaled as seconds rather than treated as milliseconds. That
        is current behaviour, pinned here so the floor bump cannot move
        it unnoticed; it is not an endorsement.
        """
        cases = [
            ("seconds", 1_700_000_000, 1_700_000_000_000.0),
            ("seconds as float", 1_700_000_000.5, 1_700_000_000_500.0),
            ("milliseconds", 1_700_000_000_000, 1_700_000_000_000.0),
            ("nanoseconds", 170_000_000_000_000_000, 170_000_000_000.0),
            ("below the seconds cutoff", 500, 500.0),
            ("negative passes through", -100, -100.0),
            ("exactly 1e12 scales as seconds", 1e12, 1e15),
            ("digit string", "1700000000", 1_700_000_000_000.0),
            ("one-dot numeric string", "1700000000.5", 1_700_000_000_500.0),
        ]
        for label, raw, expected in cases:
            with self.subTest(label=label, raw=raw):
                self.assertEqual(parse_iso_ts(raw), expected)

    def test_parse_iso_ts_unreadable_values_are_zero(self):
        """Anything unreadable becomes 0.0 rather than raising."""
        cases = [
            ("None", None),
            ("empty string", ""),
            ("whitespace", "   "),
            ("garbage text", "not-a-date"),
            ("two-dot numeric string", "1700000000.123.456"),
            ("malformed fraction", "2026-08-01T00:00:00.abc+00:00"),
            ("empty list", []),
            ("empty dict", {}),
        ]
        for label, raw in cases:
            with self.subTest(label=label, raw=raw):
                self.assertEqual(parse_iso_ts(raw), 0.0)

    def test_parse_iso_ts_booleans_are_not_guarded(self):
        """``True`` becomes 1.0 because bool is an int subclass.

        Documented, not endorsed: no isinstance-bool guard exists here,
        so a boolean silently becomes a timestamp-shaped number. Pinned
        so the floor bump is not blamed if someone later adds a guard.
        """
        self.assertEqual(parse_iso_ts(True), 1.0)
        self.assertEqual(parse_iso_ts(False), 0.0)

    def test_clean_title_and_user_text(self):
        self.assertEqual(clean_title("<task>hi there</task>"), "hi there")
        self.assertEqual(extract_user_text([{"type": "text", "text": "hello"}]), "hello")
        self.assertEqual(extract_user_text("plain"), "plain")

    def test_path_helpers(self):
        self.assertEqual(path_from_encoded_dir("-Users-foo-bar"), "/Users/foo/bar")
        self.assertEqual(strip_file_uri("file:///Users/test%20x"), "/Users/test x")


class SqliteEngineTest(unittest.TestCase):
    def test_parse_sqlite_basic(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE sessions (id TEXT, cwd TEXT, title TEXT, updated_at TEXT)"
            )
            conn.execute(
                "INSERT INTO sessions VALUES ('s1', '/tmp/p', 'Hello', '2026-07-01T10:00:00Z')"
            )
            conn.commit()
            conn.close()

            sessions = parse_sqlite(
                SqliteParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    db_path=str(db),
                    query="SELECT id, cwd, title, updated_at FROM sessions",
                    session_id=0,
                    path=1,
                    title=2,
                    updated=3,
                )
            )
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].path, "/tmp/p")
            self.assertEqual(sessions[0].title, "Hello")
            self.assertEqual(sessions[0].tool_name, "demo")

    def test_malformed_query_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            sqlite3.connect(db).close()

            sessions = parse_sqlite(
                SqliteParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    db_path=str(db),
                    query="SELECT missing FROM nowhere",
                )
            )

            self.assertEqual(sessions, [])

    def test_missing_paths_are_skipped_and_enrich_failures_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE sessions (id TEXT, cwd TEXT)")
            conn.executemany(
                "INSERT INTO sessions VALUES (?, ?)",
                [("missing", ""), ("kept", "/tmp/work")],
            )
            conn.commit()
            conn.close()

            def broken_enrich(_conn, _row, _fields):
                raise RuntimeError("optional side table is unavailable")

            sessions = parse_sqlite(
                SqliteParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    db_path=str(db),
                    query="SELECT id, cwd FROM sessions",
                    session_id=0,
                    path=1,
                    enrich=broken_enrich,
                )
            )

            self.assertEqual([session.session_id for session in sessions], ["kept"])


class JsonlEngineTest(unittest.TestCase):
    def test_nested_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "-Users-test"
            proj.mkdir()
            fp = proj / "abc.jsonl"
            fp.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_start", "cwd": "/Users/test"}),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {"role": "user", "content": "hi engine"},
                            }
                        ),
                    ]
                )
            )
            sessions = parse_jsonl_projects(
                JsonlParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    base_dir=tmp,
                    path_from_event=lambda d: d.get("cwd") or "",
                    path_from_project_dir=lambda n: path_from_encoded_dir(n),
                    title_from_event=lambda d: (
                        (d.get("message") or {}).get("content")
                        if d.get("type") == "message"
                        and (d.get("message") or {}).get("role") == "user"
                        else ""
                    ),
                    one_session_per_file=True,
                )
            )
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].path, "/Users/test")
            self.assertIn("hi", sessions[0].title)

    # --- tail title override -------------------------------------------

    @staticmethod
    def _tail_config(tmp, **overrides):
        kwargs = dict(
            tool_name="demo",
            agent="Demo",
            base_dir=tmp,
            path_from_event=lambda d: d.get("cwd") or "",
            title_from_event=first_user_title,
            tail_title_from_event=lambda d: (
                (2, d.get("customTitle") or "", "rename")
                if d.get("type") == "custom-title"
                else (1, d.get("aiTitle") or "", "auto")
                if d.get("type") == "ai-title"
                else None
            ),
            one_session_per_file=True,
        )
        kwargs.update(overrides)
        return JsonlParserConfig(**kwargs)

    @staticmethod
    def _write_session(tmp, lines):
        proj = Path(tmp) / "proj"
        proj.mkdir(exist_ok=True)
        (proj / "abc.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")

    def test_tail_rename_beats_first_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, [
                {"type": "session_start", "cwd": "/Users/test"},
                {"role": "user", "content": "first prompt text"},
                {"role": "assistant", "content": "reply"},
                {"type": "custom-title", "customTitle": "Renamed by user"},
                {"role": "user", "content": "second prompt"},
            ])
            sessions = parse_jsonl_projects(self._tail_config(tmp))
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].title, "Renamed by user")
            self.assertEqual(sessions[0].title_source, "rename")

    def test_tail_falls_back_to_first_prompt_without_title_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, [
                {"type": "session_start", "cwd": "/Users/test"},
                {"role": "user", "content": "first prompt text"},
                {"role": "user", "content": "second prompt"},
            ])
            sessions = parse_jsonl_projects(self._tail_config(tmp))
            self.assertEqual(sessions[0].title, "first prompt text")
            self.assertEqual(sessions[0].title_source, "")

    def test_tail_latest_rename_wins_and_outranks_later_ai_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, [
                {"type": "session_start", "cwd": "/Users/test"},
                {"role": "user", "content": "first prompt text"},
                {"type": "custom-title", "customTitle": "Old name"},
                {"type": "custom-title", "customTitle": "New name"},
                {"type": "ai-title", "aiTitle": "Auto title after rename"},
            ])
            sessions = parse_jsonl_projects(self._tail_config(tmp))
            self.assertEqual(sessions[0].title, "New name")
            self.assertEqual(sessions[0].title_source, "rename")

    def test_tail_ai_title_used_when_no_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, [
                {"type": "session_start", "cwd": "/Users/test"},
                {"role": "user", "content": "first prompt text"},
                {"type": "ai-title", "aiTitle": "Auto title"},
            ])
            sessions = parse_jsonl_projects(self._tail_config(tmp))
            self.assertEqual(sessions[0].title, "Auto title")
            self.assertEqual(sessions[0].title_source, "auto")

    def test_tail_window_only_reads_the_end_of_large_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            filler = [{"role": "assistant", "content": "x" * 200}] * 50
            self._write_session(tmp, [
                {"type": "session_start", "cwd": "/Users/test"},
                {"role": "user", "content": "first prompt text"},
                {"type": "custom-title", "customTitle": "Buried early rename"},
                *filler,
                {"type": "custom-title", "customTitle": "Recent rename"},
                *filler[:3],
            ])
            small = parse_jsonl_projects(self._tail_config(tmp, tail_bytes=2048))
            self.assertEqual(small[0].title, "Recent rename")
            # A window too small to reach any title event falls back cleanly.
            tiny = parse_jsonl_projects(self._tail_config(tmp, tail_bytes=64))
            self.assertEqual(tiny[0].title, "first prompt text")

    def test_tail_ignores_malformed_lines_and_callback_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            (proj / "abc.jsonl").write_text(
                json.dumps({"type": "session_start", "cwd": "/Users/test"}) + "\n"
                + json.dumps({"role": "user", "content": "first prompt text"}) + "\n"
                + json.dumps({"type": "custom-title", "customTitle": "Good"}) + "\n"
                + "{broken json\n"
                + json.dumps({"type": "explode"}) + "\n"
            )

            def boom(d):
                if d.get("type") == "explode":
                    raise ValueError("bad event")
                if d.get("type") == "custom-title":
                    return (2, d.get("customTitle") or "", "rename")
                return None

            sessions = parse_jsonl_projects(
                self._tail_config(tmp, tail_title_from_event=boom)
            )
            self.assertEqual(sessions[0].title, "Good")

    def test_title_from_event_may_return_a_source_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, [
                {"type": "session_start", "cwd": "/Users/test", "title": "Set by hand",
                 "manual": True},
                {"role": "user", "content": "first prompt text"},
            ])

            def title_from_event(d):
                if d.get("type") == "session_start" and d.get("title"):
                    return d["title"], ("rename" if d.get("manual") else "auto")
                return first_user_title(d)

            sessions = parse_jsonl_projects(
                self._tail_config(tmp, title_from_event=title_from_event,
                                  tail_title_from_event=None)
            )
            self.assertEqual(sessions[0].title, "Set by hand")
            self.assertEqual(sessions[0].title_source, "rename")

    def test_index_get_title_may_return_a_source_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            (proj / "index.jsonl").write_text(
                json.dumps({"id": "s1", "cwd": "/Users/test", "title": "Named"}) + "\n"
                + json.dumps({"id": "s2", "cwd": "/Users/test", "title": None}) + "\n"
            )
            (proj / "s2.jsonl").write_text(
                json.dumps({"role": "user", "content": "prompt for s2"}) + "\n"
            )
            sessions = parse_jsonl_projects(JsonlParserConfig(
                tool_name="demo", agent="Demo", base_dir=tmp, mode="index_jsonl",
                get_title=lambda e: ((e.get("title") or ""), "rename"),
                title_from_event=first_user_title,
            ))
            by_id = {s.session_id: s for s in sessions}
            self.assertEqual(by_id["s1"].title, "Named")
            self.assertEqual(by_id["s1"].title_source, "rename")
            self.assertEqual(by_id["s2"].title, "prompt for s2")
            self.assertEqual(by_id["s2"].title_source, "")

    def test_malformed_lines_are_skipped_and_project_path_is_a_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "project"
            proj.mkdir()
            (proj / "abc.jsonl").write_text(
                "{not json}\n"
                + json.dumps({"role": "user", "content": "meaningful request"})
                + "\n"
            )

            sessions = parse_jsonl_projects(
                JsonlParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    base_dir=tmp,
                    path_from_event=lambda _data: (_ for _ in ()).throw(ValueError()),
                    path_from_project_dir=lambda _name: "/tmp/fallback",
                    title_from_event=first_user_title,
                )
            )

            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].path, "/tmp/fallback")
            self.assertEqual(sessions[0].title, "meaningful request")

    def test_index_mode_uses_side_session_file_after_bad_index_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "project"
            proj.mkdir()
            (proj / "index.jsonl").write_text(
                "{bad}\n" + json.dumps({"id": "s1", "cwd": "/tmp/work"}) + "\n"
            )
            (proj / "s1.jsonl").write_text(
                json.dumps({"role": "user", "content": "side-file title"}) + "\n"
            )

            sessions = parse_jsonl_projects(
                JsonlParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    base_dir=tmp,
                    mode="index_jsonl",
                    title_from_event=first_user_title,
                )
            )

            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].session_id, "s1")
            self.assertEqual(sessions[0].title, "side-file title")

    def test_required_path_excludes_unattributed_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "project"
            proj.mkdir()
            (proj / "abc.jsonl").write_text(json.dumps({"type": "event"}) + "\n")

            sessions = parse_jsonl_projects(
                JsonlParserConfig(tool_name="demo", agent="Demo", base_dir=tmp)
            )

            self.assertEqual(sessions, [])

    def test_first_user_title_supports_top_level_and_nested_messages(self):
        cases = [
            ({"role": "user", "content": "top level"}, "top level"),
            (
                {"type": "message", "message": {"role": "user", "content": "nested"}},
                "nested",
            ),
            ({"role": "assistant", "content": "ignore"}, ""),
        ]
        for event, expected in cases:
            with self.subTest(event=event):
                self.assertEqual(first_user_title(event), expected)


class JsonEngineTest(unittest.TestCase):
    def test_index_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "sessions.json"
            index.write_text(
                json.dumps(
                    [
                        {
                            "sessionId": "x1",
                            "title": "Explore",
                            "workspaceDirectory": "/tmp/w",
                            "dateCreated": "2026-07-10T10:00:00.000Z",
                        }
                    ]
                )
            )
            sessions = parse_json_store(
                JsonParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    mode="index",
                    index_path=str(index),
                    index_items=lambda data: data if isinstance(data, list) else [],
                    get_id=lambda e, _f: e.get("sessionId") or "",
                    get_path=lambda e, _f: e.get("workspaceDirectory") or "",
                    get_title=lambda e, _f: e.get("title") or "",
                    get_ts=lambda e, _f: parse_iso_ts(e.get("dateCreated")),
                )
            )
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].title, "Explore")
            self.assertEqual(sessions[0].path, "/tmp/w")

    def test_files_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "t1.json").write_text(
                json.dumps({"id": "t1", "cwd": "/tmp/a", "title": "one"})
            )
            sessions = parse_json_store(
                JsonParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    mode="files",
                    base_dir=tmp,
                    file_glob="*.json",
                    get_id=lambda e, _f: e.get("id") or "",
                    get_path=lambda e, _f: e.get("cwd") or "",
                    get_title=lambda e, _f: e.get("title") or "",
                    get_ts=lambda e, f: 0,
                )
            )
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].session_id, "t1")

    def test_files_mode_skips_malformed_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bad.json").write_text("{bad")
            (Path(tmp) / "good.json").write_text(
                json.dumps({"id": "good", "cwd": "/tmp/work"})
            )

            sessions = parse_json_store(
                JsonParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    mode="files",
                    base_dir=tmp,
                )
            )

            self.assertEqual([session.session_id for session in sessions], ["good"])

    def test_nested_dirs_uses_parent_fallback_when_summary_is_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "encoded-project" / "session-1"
            session_dir.mkdir(parents=True)
            (session_dir / "summary.json").write_text("{bad")

            sessions = parse_json_store(
                JsonParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    mode="nested_dirs",
                    base_dir=tmp,
                    path_from_parent=lambda _name: "/tmp/fallback",
                )
            )

            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].session_id, "session-1")
            self.assertEqual(sessions[0].path, "/tmp/fallback")

    def test_project_map_uses_map_key_when_primary_file_is_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "sessions" / "hash-1"
            session_dir.mkdir(parents=True)
            (session_dir / "summary.json").write_text("{bad")
            index = Path(tmp) / "projects.json"
            index.write_text(json.dumps({"projects": {"/tmp/work": "hash-1"}}))

            sessions = parse_json_store(
                JsonParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    mode="project_map",
                    index_path=str(index),
                    session_dir_from_item=lambda _path, key: str(
                        Path(tmp) / "sessions" / key
                    ),
                )
            )

            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].path, "/tmp/work")


class JsonlSessionDirsModeTest(unittest.TestCase):
    """mode="session_dirs": base/<project>/<session_id>/(jsonl files)."""

    def _config(self, tmp, **overrides):
        kwargs = dict(
            tool_name="demo",
            agent="Demo",
            base_dir=str(Path(tmp) / "base"),
            mode="session_dirs",
            require_path=False,
        )
        kwargs.update(overrides)
        return JsonlParserConfig(**kwargs)

    def _seed(self, tmp, proj="projA", sid="s1", records=None):
        sess_dir = Path(tmp) / "base" / proj / sid
        sess_dir.mkdir(parents=True)
        records = records if records is not None else [
            {"type": "session_start", "cwd": "/work/a"},
            {"role": "user", "content": "hi there"},
        ]
        (sess_dir / "chat.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records) + "\n")
        return sess_dir

    def test_session_dirs_finds_primary_and_project_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp)
            sessions = parse_jsonl_projects(self._config(
                tmp,
                path_from_event=lambda e: e.get("cwd") or "",
                path_from_project_dir=lambda name: f"/from/{name}",
            ))
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].session_id, "s1")
            self.assertEqual(sessions[0].path, "/work/a")

    def test_session_dirs_uses_project_fallback_without_cwd_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, records=[{"role": "user", "content": "hi"}])
            sessions = parse_jsonl_projects(self._config(
                tmp, path_from_project_dir=lambda name: f"/from/{name}",
            ))
            self.assertEqual(sessions[0].path, "/from/projA")

    def test_session_dirs_chats_subdir_and_enrich(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess_dir = Path(tmp) / "base" / "projB" / "chats" / "s9"
            sess_dir.mkdir(parents=True)
            (sess_dir / "t.jsonl").write_text('{"role":"user","content":"q"}\n')

            def enrich(fields, _dir, proj_name):
                fields["title"] = "enriched-" + proj_name
                fields["title_source"] = "auto"

            sessions = parse_jsonl_projects(self._config(
                tmp, chats_subdir="chats", enrich_session=enrich,
            ))
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].session_id, "s9")
            self.assertEqual(sessions[0].title, "enriched-projB")
            self.assertEqual(sessions[0].title_source, "auto")

    def test_session_dirs_enrich_exception_is_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp)

            def boom(fields, _dir, _proj):
                raise RuntimeError("enrich blew up")

            sessions = parse_jsonl_projects(self._config(
                tmp, enrich_session=boom, require_path=False,
            ))
            self.assertEqual(len(sessions), 1)

    def test_session_dirs_require_path_drops_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, records=[{"role": "user", "content": "x"}])
            sessions = parse_jsonl_projects(self._config(
                tmp, require_path=True,
            ))
            self.assertEqual(sessions, [])

    def test_session_dirs_skips_files_and_empty_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp)
            (Path(tmp) / "base" / "afile.txt").write_text("x")
            (Path(tmp) / "base" / "projA" / "emptydir").mkdir()
            sessions = parse_jsonl_projects(self._config(tmp))
            self.assertEqual(len(sessions), 1)

    def test_session_dirs_primary_files_and_skip_basenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess_dir = self._seed(tmp)
            (sess_dir / "skip.jsonl").write_text('{"cwd":"/skip"}\n')
            sessions = parse_jsonl_projects(self._config(
                tmp, skip_basenames={"skip.jsonl"},
            ))
            self.assertEqual(len(sessions), 1)
            # only the whitelisted name counts
            sessions = parse_jsonl_projects(self._config(
                tmp, primary_files={"chat.jsonl"},
            ))
            self.assertEqual(len(sessions), 1)


class JsonlIndexModeTest(unittest.TestCase):
    """mode="index_jsonl": base/<project>/index.jsonl lists sessions."""

    def _config(self, tmp, **overrides):
        kwargs = dict(
            tool_name="demo",
            agent="Demo",
            base_dir=str(Path(tmp) / "base"),
            mode="index_jsonl",
        )
        kwargs.update(overrides)
        return JsonlParserConfig(**kwargs)

    def test_index_entries_become_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "base" / "proj"
            proj.mkdir(parents=True)
            (proj / "index.jsonl").write_text("\n".join([
                json.dumps({"id": "i1", "cwd": "/w1", "title": "First"}),
                "not json",
                json.dumps(["not", "a", "dict"]),
                json.dumps({"id": "i2", "path": "/w2"}),
                "",
            ]) + "\n")
            sessions = parse_jsonl_projects(self._config(
                tmp,
                get_title=lambda e: (e["id"].upper(), "auto")
                if e["id"] == "i2" else e.get("title", ""),
            ))
            self.assertEqual([s.session_id for s in sessions], ["i1", "i2"])
            self.assertEqual(sessions[0].title, "First")
            self.assertEqual(sessions[1].title, "I2")
            self.assertEqual(sessions[1].title_source, "auto")

    def test_index_extractor_exceptions_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "base" / "proj"
            proj.mkdir(parents=True)
            (proj / "index.jsonl").write_text(
                json.dumps({"id": "i1", "cwd": "/w", "title": "T"}) + "\n")

            def boom(_e):
                raise ValueError("bad entry")

            sessions = parse_jsonl_projects(self._config(
                tmp,
                get_id=boom, get_path=boom, get_title=boom, get_ts=boom,
                require_path=False,
            ))
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].title, "")
            # timestamp falls back to the index file's mtime
            self.assertGreater(sessions[0].timestamp, 0)
            self.assertEqual(sessions[0].path, "")

    def test_index_session_file_enriches_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "base" / "proj"
            proj.mkdir(parents=True)
            (proj / "i1.jsonl").write_text(
                '{"type":"rename","name":"From File"}\n')
            (proj / "index.jsonl").write_text(
                json.dumps({"id": "i1", "cwd": "/w"}) + "\n")
            sessions = parse_jsonl_projects(self._config(
                tmp,
                title_from_event=lambda e: (
                    (e["name"], "rename") if e.get("type") == "rename" else ""
                ),
            ))
            self.assertEqual(sessions[0].title, "From File")
            self.assertEqual(sessions[0].title_source, "rename")

    def test_missing_index_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "base" / "proj").mkdir(parents=True)
            self.assertEqual(parse_jsonl_projects(self._config(tmp)), [])


class JsonlGlobModeTest(unittest.TestCase):
    def test_glob_mode_honours_skip_and_primary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            (base / "deep").mkdir(parents=True)
            (base / "one.jsonl").write_text('{"cwd":"/w1"}\n')
            (base / "deep" / "two.jsonl").write_text('{"cwd":"/w2"}\n')
            (base / "skip.jsonl").write_text('{"cwd":"/w3"}\n')
            sessions = parse_jsonl_projects(JsonlParserConfig(
                tool_name="demo", agent="Demo", base_dir=str(base),
                mode="glob", skip_basenames={"skip.jsonl"},
                path_from_event=lambda e: e.get("cwd") or "",
            ))
            self.assertEqual({s.session_id for s in sessions}, {"one", "two"})
            sessions = parse_jsonl_projects(JsonlParserConfig(
                tool_name="demo", agent="Demo", base_dir=str(base),
                mode="glob", primary_files={"one.jsonl"},
                path_from_event=lambda e: e.get("cwd") or "",
            ))
            self.assertEqual([s.session_id for s in sessions], ["one"])

    def test_custom_mode_and_its_failure(self):
        marker = Session(timestamp=1, agent="Demo", path="/w",
                         session_id="c1", tool_name="demo")
        sessions = parse_jsonl_projects(JsonlParserConfig(
            tool_name="demo", agent="Demo", base_dir="/nope",
            custom=lambda: [marker],
        ))
        self.assertEqual(sessions, [marker])

        def boom():
            raise RuntimeError("custom broke")

        sessions = parse_jsonl_projects(JsonlParserConfig(
            tool_name="demo", agent="Demo", base_dir="/nope", custom=boom,
        ))
        self.assertEqual(sessions, [])


class JsonEngineEdgeTest(unittest.TestCase):
    def test_index_dict_items_and_tuple_normalisation(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "sessions.json"
            index.write_text(json.dumps({
                "k1": {"cwd": "/w1", "title": "One"},
                "k2": "not-a-dict-value",
            }))
            sessions = parse_json_store(
                JsonParserConfig(
                    tool_name="demo", agent="Demo", mode="index",
                    index_path=str(index), require_path=False,
                )
            )
            self.assertEqual([s.session_id for s in sessions], ["k1", "k2"])
            self.assertEqual(sessions[0].path, "/w1")
            self.assertEqual(sessions[1].path, "")

    def test_extractor_exceptions_are_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "i.json"
            index.write_text(json.dumps([{"id": "x"}]))

            def boom(_e, _f):
                raise RuntimeError("nope")

            sessions = parse_json_store(
                JsonParserConfig(
                    tool_name="demo", agent="Demo", mode="index",
                    index_path=str(index), require_path=False,
                    get_id=boom, get_path=boom, get_title=boom, get_ts=boom,
                )
            )
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].title, "")
            # timestamp falls back to the index file's mtime
            self.assertGreater(sessions[0].timestamp, 0)

    def test_enrich_and_include_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "i.json"
            index.write_text(json.dumps([
                {"id": "keep", "cwd": "/w", "kind": "chat"},
                {"id": "drop", "cwd": "/w", "kind": "draft"},
            ]))

            def enrich(fields, entry, _f):
                fields["title"] = "E:" + entry["id"]

            sessions = parse_json_store(
                JsonParserConfig(
                    tool_name="demo", agent="Demo", mode="index",
                    index_path=str(index),
                    include=lambda e, _f: e.get("kind") == "chat",
                    enrich=enrich,
                )
            )
            self.assertEqual([s.session_id for s in sessions], ["keep"])
            self.assertEqual(sessions[0].title, "E:keep")

    def test_files_mode_skip_basenames_and_include(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "skipme.json").write_text(json.dumps({"id": "s"}))
            (Path(tmp) / "keep.json").write_text(
                json.dumps({"id": "k", "cwd": "/w", "flag": True}))
            (Path(tmp) / "drop.json").write_text(
                json.dumps({"id": "d", "cwd": "/w"}))
            sessions = parse_json_store(
                JsonParserConfig(
                    tool_name="demo", agent="Demo", mode="files",
                    base_dir=tmp, skip_basenames={"skipme.json"},
                    include=lambda e, _f: bool(e.get("flag")),
                )
            )
            self.assertEqual([s.session_id for s in sessions], ["k"])

    def test_missing_index_returns_empty(self):
        sessions = parse_json_store(
            JsonParserConfig(tool_name="demo", agent="Demo", mode="index",
                             index_path="/nope/nothing.json"))
        self.assertEqual(sessions, [])


class EngineCommonHelpersTest(unittest.TestCase):
    """Error and edge branches in sessions/engines/common.py."""

    def test_expand_path_rules(self):
        from swe.sessions.engines.common import expand_path
        self.assertEqual(expand_path(""), "")
        self.assertEqual(expand_path("/abs/path"), "/abs/path")
        self.assertEqual(expand_path("rel/path"), "rel/path")
        expanded = expand_path("~/x")
        self.assertNotEqual(expanded, "~/x")
        self.assertTrue(expanded.endswith("/x"))

    def test_open_sqlite_ro(self):
        from swe.sessions.engines.common import open_sqlite_ro
        self.assertIsNone(open_sqlite_ro("/definitely/not/a/db.sqlite"))
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "d.sqlite"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE t (a)")
            conn.commit()
            conn.close()
            ro = open_sqlite_ro(str(db))
            self.assertIsNotNone(ro)
            ro.close()
            # mode=ro connects lazily; garbage only fails when read
            notadb = Path(tmp) / "notdb.sqlite"
            notadb.write_text("garbage")
            lazy = open_sqlite_ro(str(notadb))
            self.assertIsNotNone(lazy)
            with self.assertRaises(sqlite3.DatabaseError):
                lazy.execute("SELECT 1 FROM sqlite_master").fetchall()
            lazy.close()

    def test_path_from_encoded_dir(self):
        from swe.sessions.engines.common import path_from_encoded_dir
        self.assertEqual(path_from_encoded_dir("-Users-a-b"), "/Users/a/b")
        self.assertEqual(path_from_encoded_dir("noprefix"), "/noprefix")
        self.assertEqual(path_from_encoded_dir("-"), "")
        self.assertEqual(path_from_encoded_dir(""), "")

    def test_strip_file_uri(self):
        from swe.sessions.engines.common import strip_file_uri
        self.assertEqual(strip_file_uri("file:///a%20b/c"), "/a b/c")
        self.assertEqual(strip_file_uri("/plain"), "/plain")
        self.assertEqual(strip_file_uri(42), "")

    def test_dig(self):
        from swe.sessions.engines.common import dig
        d = {"a": {"b": {"c": 7}}}
        self.assertEqual(dig(d, "a", "b", "c"), 7)
        self.assertEqual(dig(d, "a", "x"), None)
        self.assertEqual(dig(d, "a", "x", default="dflt"), "dflt")
        self.assertEqual(dig({"a": "str"}, "a", "b"), None)
        self.assertEqual(dig(None, "a"), None)

    def test_extract_user_text_shapes(self):
        from swe.sessions.engines.common import extract_user_text
        self.assertEqual(extract_user_text("plain"), "plain")
        self.assertEqual(extract_user_text(["  ", {"type": "text", "text": "blk"}]), "blk")
        self.assertEqual(extract_user_text([{"text": "untyped"}]), "untyped")
        self.assertEqual(extract_user_text({"text": "top"}), "top")
        self.assertEqual(extract_user_text({"content": {"text": "nested"}}), "nested")
        self.assertEqual(extract_user_text({"content": [{"text": "deep"}]}), "deep")
        self.assertEqual(extract_user_text(None), "")
        self.assertEqual(extract_user_text({}), "")

    def test_parse_iso_ts_edges(self):
        from swe.sessions.engines.common import parse_iso_ts
        self.assertEqual(parse_iso_ts(None), 0.0)
        self.assertEqual(parse_iso_ts(float("nan")), 0.0)
        self.assertEqual(parse_iso_ts(float("inf")), 0.0)
        self.assertEqual(parse_iso_ts(1e15), 1e9)   # ns -> ms
        self.assertEqual(parse_iso_ts(2e12), 2e12)  # already ms
        self.assertEqual(parse_iso_ts(2e9), 2e12)   # seconds -> ms
        self.assertEqual(parse_iso_ts(5), 5.0)
        from datetime import datetime
        expected = datetime.fromisoformat("2026-07-10T10:00:00+00:00").timestamp() * 1000
        self.assertEqual(parse_iso_ts("2026-07-10T10:00:00Z"), expected)
        self.assertEqual(parse_iso_ts("garbage"), 0.0)
        self.assertEqual(parse_iso_ts("1777032754"), 1777032754000.0)


if __name__ == "__main__":
    unittest.main()
