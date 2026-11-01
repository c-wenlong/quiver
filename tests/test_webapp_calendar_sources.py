"""The Calendar's file activity sources (``webapp/pages/_activity.py``),
ported from the Mac's ``FileActivitySources.swift``: file listing,
parsing, the trivial-session drop, the blocks they make, and the route
drawing them. Every log here is made up."""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from swe.ledger.models import LedgerRecord, UsageEvent
from swe.ledger.store import LedgerStore
from swe.webapp import sessions as sessions_mod
from swe.webapp.pages import _activity
from swe.webapp.pages import calendar as cal

from tests.test_remote_hub import HubBase

# Shared with app/mac/Tests/SWECoreTests/ActivityParityTests.swift.
FIXTURES = Path(__file__).resolve().parent.parent / "app/mac/Tests/SWECoreTests/Fixtures/activity"
# The Nix build's source tree carries src/ and tests/ but not app/mac/.
NEEDS_FIXTURES = unittest.skipUnless(FIXTURES.is_dir(), "app/mac/ is not in this source tree")
# Each source's (listing, parser), as _activity.SOURCES pairs them.
PARSERS = {s.harness: (s._files, s._parse) for s in _activity.SOURCES}


def _utc(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def _write(path: Path, *lines: str, newline: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if newline else ""), encoding="utf-8")
    return path


def _cursor_db(user_data: Path) -> Path:
    """The shared fixture's ``state.vscdb``, built under ``user_data``."""
    db = user_data / _activity.CURSOR_DB
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.executescript((FIXTURES / "cursor" / "state.sql").read_text(encoding="utf-8"))
    con.close()
    return db


def _blocks(harness: str, root: Path, account: str, lo: float, hi: float, gap: float):
    """One source's per-session blocks, the way the Mac provider reports
    them: listing, parse, then ``ActivityBlocks.make``."""
    files, parse = PARSERS[harness]
    sessions = _activity.FileIndex().sessions(lambda: files(str(root)), lambda p: parse(p, account))
    return cal.file_pieces([(harness, s) for s in sessions], lo, hi, timezone.utc, gap)


@NEEDS_FIXTURES
class TestParity(unittest.TestCase):
    """The same made-up logs and expected blocks the Swift suite reads."""

    def test_every_source_matches_the_shared_expectation(self):
        expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
        lo, hi = expected["range"]
        for harness in [h for h in expected["sources"] if h != "cursor"]:
            source = expected["sources"][harness]
            root = FIXTURES / source["root"]
            prefix = str(root) + os.sep
            got = sorted(
                ({"session": p["session"][len(prefix):] if p["session"].startswith(prefix) else p["session"],
                  "cwd": p["cwd"], "start": p["start"], "end": p["end"], "responses": p["responses"],
                  "account": p["account"]}
                 for p in _blocks(harness, root, source["account"], lo, hi, expected["gap"])),
                key=lambda b: (b["session"], b["start"]))
            with self.subTest(harness=harness):
                self.assertEqual(len(got), len(source["blocks"]), got)
                for actual, want in zip(got, source["blocks"]):
                    self.assertEqual(actual["session"], want["session"])
                    self.assertEqual(actual["cwd"], want["cwd"])
                    self.assertEqual(actual["account"], source["account"])
                    self.assertAlmostEqual(actual["start"], want["start"], places=3)
                    self.assertAlmostEqual(actual["end"], want["end"], places=3)
                    self.assertEqual(actual["responses"], want["responses"])


    def test_cursor_matches_the_shared_expectation(self):
        expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
        lo, hi = expected["range"]
        user_data = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, user_data, True)
        _cursor_db(user_data)
        sessions = _activity.FileIndex().sessions(
            lambda: _activity.cursor_files([str(user_data)]), _activity.parse_cursor)
        got = sorted(cal.file_pieces([("cursor", s) for s in sessions], lo, hi, timezone.utc,
                                     expected["gap"]),
                     key=lambda p: (p["session"], p["start"]))
        want = expected["sources"]["cursor"]["blocks"]
        self.assertEqual(len(got), len(want), got)
        for actual, block in zip(got, want):
            self.assertEqual((actual["session"], actual["cwd"], actual["account"]),
                             (block["session"], block["cwd"], str(user_data)))
            self.assertAlmostEqual(actual["start"], block["start"], places=3)
            self.assertAlmostEqual(actual["end"], block["end"], places=3)
            self.assertEqual(actual["responses"], block["responses"])


class TestCursor(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_created_at_reads_like_swifts_text_column(self):
        read = _activity.cursor_created_at
        self.assertEqual([read(1786352400000), read(1786352400000.0), read("1786352400000"),
                          read(True), read(0), read(-5), read(None), read("soon"),
                          read(" 1786352400000"), read("1_786_352_400_000"), read("0x10"),
                          read("0x1p1024"), read("-0x1p1024")],
                         [1786352400.0, 1786352400.0, 1786352400.0, 0.001, None, None, None,
                          None, None, None, 0.016, float("inf"), None])

    def test_the_composer_splits_like_swift(self):
        comp = _activity.cursor_composer
        self.assertEqual([comp("bubbleId:c:b"), comp("bubbleId:c::b"), comp("bubbleId::b"),
                          comp("bubbleId:c:"), comp("bubbleId:task-1:b"), comp("other:c:b")],
                         ["c", "c", None, None, None, None])

    def test_the_bubble_read_seeks_the_key_index(self):
        con = sqlite3.connect(self.dir / "s.vscdb")
        con.execute("CREATE TABLE cursorDiskKV (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
        plan = [row[3] for row in con.execute("EXPLAIN QUERY PLAN " + _activity.CURSOR_BUBBLES)]
        con.close()
        self.assertTrue(any(d.startswith("SEARCH") and "INDEX" in d for d in plan), plan)

    @NEEDS_FIXTURES
    def test_extra_profiles_add_their_desktop_folder_once(self):
        profiles = [{"id": "c1", "vendor": "cursor", "isDefault": False},
                    {"id": "c2", "vendor": "cursor", "isDefault": True},
                    {"id": "x1", "vendor": "claude", "isDefault": False}]
        dirs = _activity.cursor_user_data_dirs(self.dir, profiles)
        self.assertEqual(dirs, [str(self.dir / "Library/Application Support/Cursor"),
                                str(self.dir / ".swe/accounts/cursor/c1/desktop")])
        _cursor_db(self.dir / "a")
        files = _activity.cursor_files([str(self.dir / "a"), str(self.dir / "a")])
        self.assertEqual(list(files), [str(self.dir / "a" / _activity.CURSOR_DB)])

    @NEEDS_FIXTURES
    def test_the_wal_folds_into_the_stamp(self):
        db = _cursor_db(self.dir)
        before = _activity.cursor_stamp(str(db))
        Path(str(db) + "-wal").write_bytes(b"x" * 10)
        after = _activity.cursor_stamp(str(db))
        self.assertEqual(after[1], before[1] + 10)

    def test_a_live_wal_database_is_read_through_its_wal(self):
        db = self.dir / _activity.CURSOR_DB
        db.parent.mkdir(parents=True)
        writer = sqlite3.connect(db)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE cursorDiskKV (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
        writer.commit()
        main_stamp = os.stat(db).st_mtime_ns

        def write(bubble, at):
            writer.execute("INSERT INTO cursorDiskKV VALUES (?, ?)",
                           (f"bubbleId:c1:{bubble}", json.dumps({"createdAt": at})))
            writer.commit()

        index = _activity.FileIndex()
        read = lambda: index.sessions(  # noqa: E731
            lambda: _activity.cursor_files([str(self.dir)]), _activity.parse_cursor, force=True)
        write("b1", "2026-08-10T08:00:00Z")
        self.assertEqual([s["timestamps"] for s in read()], [[_utc("2026-08-10T08:00:00Z")]])
        # The commit lands in the WAL only: the main file stands still, and
        # the folded stamp still makes the index read it again.
        write("b2", "2026-08-10T08:05:00Z")
        self.assertEqual(os.stat(db).st_mtime_ns, main_stamp)
        self.assertEqual([len(s["timestamps"]) for s in read()], [2])

    def test_a_broken_database_drops_out_of_the_index(self):
        db = self.dir / _activity.CURSOR_DB
        db.parent.mkdir(parents=True)
        db.write_text("not sqlite")
        index = _activity.FileIndex()
        self.assertEqual(index.sessions(lambda: _activity.cursor_files([str(self.dir)]),
                                        _activity.parse_cursor), [])


class TestReaders(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_hidden_files_and_folders_are_not_listed(self):
        root = self.dir / "sessions"
        _write(root / "p" / "a.jsonl", "{}")
        _write(root / "p" / ".b.jsonl", "{}")
        _write(root / ".hidden" / "c.jsonl", "{}")
        _write(root / "p" / "d.JSONL", "{}")
        _write(root / "p" / "e.jsonl.bak", "{}")
        (root / "p" / "dir.jsonl").mkdir()
        self.assertEqual(sorted(_activity.jsonl_files(str(root))), [str(root / "p" / "a.jsonl")])

    def test_a_missing_root_lists_nothing(self):
        self.assertEqual(_activity.jsonl_files(str(self.dir / "absent")), {})

    def test_droid_takes_the_first_string_cwd_and_the_stem(self):
        path = _write(self.dir / "s" / "u1.jsonl",
                      '{"type":"session_start","cwd":7}',
                      '{"type":"session_start","cwd":""}',
                      '{"type":"session_start","cwd":"/later"}',
                      '{"type":"message","timestamp":"2026-08-10T08:00:00Z"}')
        [session] = _activity.parse_droid(str(path), "/f")
        # A JSON string, even empty, is the folder; a number is not.
        self.assertEqual((session["session"], session["cwd"], session["account"]), ("u1", "", "/f"))

    def test_a_log_with_no_message_times_is_no_session(self):
        path = _write(self.dir / "s" / "u2.jsonl", '{"type":"session","cwd":"/w"}',
                      '{"type":"message","timestamp":"yesterday"}',
                      '{"type":"message","timestamp":1786320000}')
        self.assertEqual(_activity.parse_pi(str(path), "/p"), [])

    def test_nan_lines_are_broken_like_jsonserialization_reads_them(self):
        path = _write(self.dir / "s" / "u3.jsonl",
                      '{"type":"message","timestamp":"2026-08-10T08:00:00Z","x":NaN}',
                      '{"type":"message","timestamp":"2026-08-10T08:01:00Z"}')
        [session] = _activity.parse_pi(str(path), "/p")
        self.assertEqual(session["timestamps"], [_utc("2026-08-10T08:01:00Z")])

    def test_an_unreadable_file_stays_out_and_is_retried(self):
        index = _activity.FileIndex()
        calls = []

        def parse(path):
            calls.append(path)
            if len(calls) == 1:
                raise OSError("busy")
            return [{"session": "s", "account": "/a", "cwd": None, "timestamps": [1.0]}]

        files = lambda: {"/x": (1.0, 1)}  # noqa: E731
        self.assertEqual(index.sessions(files, parse), [])
        self.assertEqual(len(index.sessions(files, parse, force=True)), 1)
        # An unchanged stamp keeps the parsed sessions; a vanished file drops them.
        self.assertEqual(len(index.sessions(files, parse, force=True)), 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(index.sessions(lambda: {}, parse, force=True), [])

    def test_files_are_listed_again_only_after_the_refresh_window(self):
        clock = [100.0]
        index = _activity.FileIndex(now=lambda: clock[0])
        listings = []

        def files():
            listings.append(clock[0])
            return {"/x": (clock[0], 1)}

        def parse(_path):
            return [{"session": "s", "account": "/a", "cwd": None, "timestamps": [clock[0]]}]

        index.sessions(files, parse)
        clock[0] += _activity.REFRESH_S - 1
        self.assertEqual(index.sessions(files, parse)[0]["timestamps"], [100.0])
        self.assertEqual(index.sessions(files, parse, force=True)[0]["timestamps"], [clock[0]])
        clock[0] += _activity.REFRESH_S
        index.sessions(files, parse)
        self.assertEqual(listings, [100.0, 129.0, 159.0])


class TestFreebuff(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def parse(self, body: str):
        path = _write(self.dir / "chats" / "c1" / "run-state.json", body)
        return _activity.parse_freebuff(str(path), "/m")

    def test_the_state_level_initial_cwd_is_the_third_fallback(self):
        [session] = self.parse('{"sessionState":{"initialCwd":"/fallback",'
                               '"mainAgentState":{"messageHistory":[{"sentAt":1786349100123}]}}}')
        self.assertEqual((session["session"], session["cwd"]), ("c1", "/fallback"))

    def test_a_bool_sent_at_counts_as_as_nsnumber_reads_it(self):
        [session] = self.parse('{"sessionState":{"mainAgentState":{"messageHistory":'
                               '[{"sentAt":true},{"sentAt":1786349100123}]}}}')
        self.assertEqual(session["timestamps"], [0.001, 1786349100.123])

    def test_malformed_files_are_no_session(self):
        for body in ("not json", "[]", '{"sessionState":[]}', '{"sessionState":{"mainAgentState":{}}}',
                     '{"sessionState":{"mainAgentState":{"messageHistory":{}}}}',
                     '{"sessionState":{"mainAgentState":{"messageHistory":[{"sentAt":NaN}]}}}',
                     '{"sessionState":{"mainAgentState":{"messageHistory":[{"x":1}]}}}'):
            self.assertEqual(self.parse(body), [], body)

    def test_an_unreadable_file_is_no_session(self):
        self.assertEqual(_activity.parse_freebuff(str(self.dir / "missing.json"), "/m"), [])


class TestFilePieces(unittest.TestCase):
    def test_days_split_responses_and_counts_stay_zero(self):
        session = {"session": "s", "account": "/a", "cwd": "/w",
                   "timestamps": [_utc("2026-08-10T23:50:00Z"), _utc("2026-08-11T00:10:00Z")]}
        [piece] = cal.file_pieces([("pi", session)], 0, 1e12, timezone.utc)
        self.assertEqual(piece["days"]["2026-08-10"]["responses"], 1)
        self.assertEqual(piece["days"]["2026-08-11"]["responses"], 1)
        self.assertEqual((piece["tokens"], piece["cost_usd"], piece["unpriced_tokens"]), (0, 0.0, 0))

    def test_an_endless_stamp_never_touches_a_range(self):
        session = {"session": "s", "account": "/a", "cwd": None,
                   "timestamps": [100.0, float("inf")]}
        pieces = cal.file_pieces([("pi", session)], 0, 1e12, timezone.utc)
        self.assertEqual([(p["start"], p["end"]) for p in pieces], [(100.0, 100.0)])


class TestTrivialDrop(unittest.TestCase):
    """``Ledger.filterTrivialActivitySessions``: judged on the session's
    whole ledger history under its own account; no rows means it stays."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        self.store = LedgerStore(self.home / "l.sqlite")
        self.addCleanup(self.store.close)
        self.n = 0

    def add(self, harness, account, session, ts, out, inp=1000):
        self.n += 1
        self.store.upsert(LedgerRecord(key=f"k{self.n}", event=UsageEvent(
            harness=harness, account=account, session=session, cwd="/w", model="m",
            ts=ts, input=inp, output=out)))

    def pi(self, name):
        return str(_write(self.home / ".pi/agent/sessions/p" / name,
                          '{"type":"session","cwd":"/work/pi"}',
                          '{"type":"message","timestamp":"2026-08-10T08:32:00Z"}'))

    def droid(self, name):
        _write(self.home / ".factory/sessions/p" / f"{name}.jsonl",
               '{"type":"session_start","cwd":"/work/app"}',
               '{"type":"message","timestamp":"2026-08-10T08:37:16Z"}')

    def read(self):
        stats = self.store.activity_session_stats(list(_activity.TRIVIAL_FILTERED))
        return sorted((h, s["session"]) for h, s in _activity.read(self.home, stats,
                                                                  sessions_mod.is_trivial))

    def test_a_probe_drops_and_real_history_outside_the_range_stays(self):
        probe, real, quiet = self.pi("1_a.jsonl"), self.pi("2_b.jsonl"), self.pi("3_c.jsonl")
        pi_home = str(self.home / ".pi")
        self.add("pi", pi_home, probe, _utc("2026-08-10T08:32:00Z"), 10)
        for day in ("05", "06", "10"):
            self.add("pi", pi_home, real, _utc(f"2026-08-{day}T10:00:00Z"), 10)
        # Usage under another account says nothing about this one.
        self.add("pi", "/other-account", quiet, _utc("2026-08-10T08:32:00Z"), 10)
        self.assertEqual(self.read(), [("pi", real), ("pi", quiet)])

    def test_droid_needs_only_tokens(self):
        self.droid("11111111-2222-3333-4444-555555555555")
        self.droid("66666666-7777-8888-9999-000000000000")
        factory = str(self.home / ".factory")
        self.add("droid", factory, "11111111-2222-3333-4444-555555555555",
                 _utc("2026-08-10T08:37:16Z"), 5)
        self.add("droid", factory, "66666666-7777-8888-9999-000000000000",
                 _utc("2026-08-10T08:37:16Z"), 0, inp=0)
        self.assertEqual(self.read(), [("droid", "11111111-2222-3333-4444-555555555555")])


class TestRoute(HubBase):
    @NEEDS_FIXTURES
    def test_file_sources_draw_blocks_with_time_and_responses(self):
        home = self.srv.home
        pi = _write(home / ".pi/agent/sessions/--tmp-pi--/1_a.jsonl",
                    '{"type":"session","cwd":"/tmp/pi-proj"}',
                    '{"type":"message","timestamp":"2026-10-20T09:00:00Z"}',
                    '{"type":"message","timestamp":"2026-10-20T09:20:00Z"}')
        _write(home / ".factory/sessions/-tmp-app/u1.jsonl",
               '{"type":"session_start","cwd":"/tmp/droid-proj"}',
               '{"type":"message","timestamp":"2026-10-21T10:00:00Z"}')
        cursor_app = home / "Library/Application Support/Cursor"
        con = sqlite3.connect(_cursor_db(cursor_app))
        con.execute("INSERT INTO cursorDiskKV VALUES ('bubbleId:comp-9:b1', ?)",
                    (json.dumps({"createdAt": "2026-10-23T12:00:00Z"}),))
        con.commit()
        con.close()
        sent = int(_utc("2026-10-22T11:00:00Z") * 1000)
        _write(home / ".config/manicode/projects/fb/chats/chat-1/run-state.json",
               json.dumps({"sessionState": {"mainAgentState": {
                   "cwd": "/tmp/fb-proj", "messageHistory": [{"sentAt": sent}]}}}))
        q = "/v1/app/calendar?mode=week&anchor=2026-10-20&tz=UTC&first_weekday=1"
        status, data = self.srv.request("GET", q, headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        blocks = {b["harness"]: b for b in body["blocks"]}
        self.assertEqual(set(blocks), {"pi", "droid", "freebuff", "cursor"})
        self.assertEqual((blocks["cursor"]["project"], blocks["cursor"]["account_label"],
                          blocks["cursor"]["sessions"][0]["id"]),
                         ("No folder", "Cursor", "comp-9"))
        self.assertEqual((blocks["freebuff"]["project"], blocks["freebuff"]["account"],
                          blocks["freebuff"]["sessions"][0]["id"]),
                         ("fb-proj", str(home / ".config/manicode"), "chat-1"))
        self.assertEqual((blocks["pi"]["project"], blocks["pi"]["responses"], blocks["pi"]["tokens"]),
                         ("pi-proj", 2, 0))
        self.assertEqual(blocks["pi"]["account"], str(home / ".pi"))
        self.assertEqual(blocks["pi"]["sessions"][0]["id"], str(pi))
        self.assertEqual(blocks["droid"]["sessions"][0]["id"], "u1")
        # A session the listing lacks opens a stand-in row named for its harness.
        row = body["sessions"][blocks["droid"]["sessions"][0]["row"]]
        self.assertEqual((row["harness"], row["agent"]), ("droid", "Factory Droid"))
        self.assertEqual([e["harness"] for e in body["legend"]], ["cursor", "droid", "freebuff", "pi"])
        self.assertEqual(body["legend"][3]["total_s"], 1200)

    def test_the_trivial_drops_totals_are_read_once_per_scan(self):
        q = "/v1/app/calendar?mode=week&anchor=2026-10-20&tz=UTC"
        real = LedgerStore.activity_session_stats
        with patch.object(LedgerStore, "activity_session_stats", autospec=True,
                          side_effect=real) as stats:
            for _ in range(2):
                self.assertEqual(self.srv.request("GET", q, headers=self.auth())[0], 200)
        self.assertEqual(stats.call_count, 1)


if __name__ == "__main__":
    unittest.main()
