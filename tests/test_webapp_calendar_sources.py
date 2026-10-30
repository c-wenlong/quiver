"""The Calendar's file activity sources (``webapp/pages/_activity.py``),
ported from the Mac's ``FileActivitySources.swift``: file listing,
parsing, the trivial-session drop, the blocks they make, and the route
drawing them. Every log here is made up."""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from quiver.ledger.models import LedgerRecord, UsageEvent
from quiver.ledger.store import LedgerStore
from quiver.webapp import sessions as sessions_mod
from quiver.webapp.pages import _activity
from quiver.webapp.pages import calendar as cal

from tests.test_remote_hub import HubBase

# Shared with app/mac/Tests/QuiverCoreTests/ActivityParityTests.swift.
FIXTURES = Path(__file__).resolve().parent.parent / "app/mac/Tests/QuiverCoreTests/Fixtures/activity"
PARSERS = {"pi": _activity.parse_pi, "droid": _activity.parse_droid}


def _utc(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def _write(path: Path, *lines: str, newline: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if newline else ""), encoding="utf-8")
    return path


def _blocks(harness: str, root: Path, account: str, lo: float, hi: float, gap: float):
    """One source's per-session blocks, the way the Mac provider reports
    them: listing, parse, then ``ActivityBlocks.make``."""
    index = _activity.FileIndex()
    sessions = index.sessions(lambda: _activity.jsonl_files(str(root)),
                              lambda p: PARSERS[harness](p, account))
    return cal.file_pieces([(harness, s) for s in sessions], lo, hi, timezone.utc, gap)


class TestParity(unittest.TestCase):
    """The same made-up logs and expected blocks the Swift suite reads."""

    def test_every_source_matches_the_shared_expectation(self):
        expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
        lo, hi = expected["range"]
        for harness in PARSERS:
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
    def test_pi_and_droid_draw_blocks_with_time_and_responses(self):
        home = self.srv.home
        pi = _write(home / ".pi/agent/sessions/--tmp-pi--/1_a.jsonl",
                    '{"type":"session","cwd":"/tmp/pi-proj"}',
                    '{"type":"message","timestamp":"2026-10-20T09:00:00Z"}',
                    '{"type":"message","timestamp":"2026-10-20T09:20:00Z"}')
        _write(home / ".factory/sessions/-tmp-app/u1.jsonl",
               '{"type":"session_start","cwd":"/tmp/droid-proj"}',
               '{"type":"message","timestamp":"2026-10-21T10:00:00Z"}')
        q = "/v1/app/calendar?mode=week&anchor=2026-10-20&tz=UTC&first_weekday=1"
        status, data = self.srv.request("GET", q, headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        blocks = {b["harness"]: b for b in body["blocks"]}
        self.assertEqual(set(blocks), {"pi", "droid"})
        self.assertEqual((blocks["pi"]["project"], blocks["pi"]["responses"], blocks["pi"]["tokens"]),
                         ("pi-proj", 2, 0))
        self.assertEqual(blocks["pi"]["account"], str(home / ".pi"))
        self.assertEqual(blocks["pi"]["sessions"][0]["id"], str(pi))
        self.assertEqual(blocks["droid"]["sessions"][0]["id"], "u1")
        # A session the listing lacks opens a stand-in row named for its harness.
        row = body["sessions"][blocks["droid"]["sessions"][0]["row"]]
        self.assertEqual((row["harness"], row["agent"]), ("droid", "Factory Droid"))
        self.assertEqual([e["harness"] for e in body["legend"]], ["droid", "pi"])
        self.assertEqual(body["legend"][1]["total_s"], 1200)

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
