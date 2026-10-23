"""ledger/store.py and scan.py: dedup rules and incremental reads."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from quiver.ledger.models import LedgerRecord, UsageEvent
from quiver.ledger.scan import scan
from quiver.ledger.store import LedgerStore, ledger_path_for


def _rec(key, total, msg=None, sidechain=False, ts=1000.0):
    ev = UsageEvent(harness="claude", account="/h", session="s", cwd=None,
                    model="m", ts=ts, input=total)
    return LedgerRecord(key=key, event=ev, msg=msg, sidechain=sidechain)


def _inputs(store):
    return dict(store.db.execute("SELECT key, input FROM events"))


class TestUpsert(unittest.TestCase):
    def setUp(self):
        self.store = LedgerStore(Path(tempfile.mkdtemp()) / "l.sqlite")
        self.addCleanup(self.store.close)

    def test_larger_total_wins_and_tie_keeps_first(self):
        self.store.upsert(_rec("k", 5))
        self.store.upsert(_rec("k", 9))
        self.store.upsert(_rec("k", 3))
        self.assertEqual(_inputs(self.store), {"k": 9})
        first = _rec("k2", 4)
        first.event.session = "first"
        self.store.upsert(first)
        second = _rec("k2", 4)
        second.event.session = "second"
        self.store.upsert(second)
        row = self.store.db.execute("SELECT session FROM events WHERE key='k2'").fetchone()
        self.assertEqual(row[0], "first")

    def test_main_copy_beats_sidechain_on_key_and_message_id(self):
        self.store.upsert(_rec("a", 100, msg="m1", sidechain=True))
        self.store.upsert(_rec("b", 10, msg="m1", sidechain=False))
        self.assertEqual(_inputs(self.store), {"b": 10})
        # A later sidechain replay of the same message never displaces it.
        self.store.upsert(_rec("c", 500, msg="m1", sidechain=True))
        self.assertEqual(_inputs(self.store), {"b": 10})

    def test_two_main_copies_with_different_request_ids_both_count(self):
        self.store.upsert(_rec("m1:r1", 10, msg="m1"))
        self.store.upsert(_rec("m1:r2", 20, msg="m1"))
        self.assertEqual(_inputs(self.store), {"m1:r1": 10, "m1:r2": 20})


def _line(mid, req, inp, ts="2026-10-03T10:00:00Z"):
    return json.dumps({
        "timestamp": ts, "sessionId": "s", "requestId": req,
        "message": {"id": mid, "model": "claude-opus-5",
                    "usage": {"input_tokens": inp, "output_tokens": 1}},
    }) + "\n"


class TestScan(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.proj = self.home / ".claude" / "projects" / "p"
        self.proj.mkdir(parents=True)
        self.store = LedgerStore(ledger_path_for(self.home))
        self.addCleanup(self.store.close)

    def test_appends_are_read_on_and_partial_lines_wait(self):
        f = self.proj / "a.jsonl"
        f.write_text(_line("m1", "r1", 5) + '{"partial')
        scan(self.store, self.home)
        self.assertEqual(_inputs(self.store), {"claude:m1:r1": 5})
        with open(f, "a") as fh:
            fh.write('": 1}\n' + _line("m2", "r2", 7))
        result = scan(self.store, self.home)
        self.assertEqual(result.files_read, 1)
        self.assertEqual(_inputs(self.store), {"claude:m1:r1": 5, "claude:m2:r2": 7})
        self.assertEqual(scan(self.store, self.home).files_read, 0)

    def test_rows_outlive_deleted_logs(self):
        f = self.proj / "a.jsonl"
        f.write_text(_line("m1", "r1", 5))
        scan(self.store, self.home)
        f.unlink()
        scan(self.store, self.home)
        self.assertEqual(_inputs(self.store), {"claude:m1:r1": 5})

    def test_replaced_file_is_read_from_the_start(self):
        f = self.proj / "a.jsonl"
        f.write_text(_line("m1", "r1", 5) + _line("m2", "r2", 6))
        scan(self.store, self.home)
        f.unlink()
        f.write_text(_line("m3", "r3", 1))
        os.utime(f, (1, 1))
        scan(self.store, self.home)
        self.assertIn("claude:m3:r3", _inputs(self.store))

    def test_profile_homes_and_codex(self):
        prof = self.home / ".quiver" / "accounts" / "claude" / "p1" / "home" / "projects" / "x"
        prof.mkdir(parents=True)
        (prof / "b.jsonl").write_text(_line("m9", "r9", 3))
        rollout = self.home / ".codex" / "sessions" / "2026" / "10" / "03"
        rollout.mkdir(parents=True)
        (rollout / "rollout-1.jsonl").write_text(json.dumps({
            "timestamp": "2026-10-03T10:00:00Z", "type": "event_msg",
            "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 4, "output_tokens": 2}}}}) + "\n")
        scan(self.store, self.home)
        keys = set(_inputs(self.store))
        self.assertIn("claude:m9:r9", keys)
        self.assertIn("codex:rollout-1:2026-10-03T10:00:00Z", keys)


if __name__ == "__main__":
    unittest.main()
