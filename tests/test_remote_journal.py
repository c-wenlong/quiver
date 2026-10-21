"""Journal: seq numbering, replay, ring trim."""

import json
import tempfile
import unittest
from pathlib import Path

from quiver.remote import protocol
from quiver.remote.journal import Journal


class TestJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.journal = Journal(Path(self.tmp))

    def _append(self, key, n):
        return self.journal.append(
            key, protocol.event("session.status", key, "m", {"n": n})
        )

    def test_seq_is_per_session_and_monotonic(self):
        e1 = self._append("a:x", 1)
        e2 = self._append("a:x", 2)
        e3 = self._append("a:y", 3)
        self.assertEqual((e1["seq"], e2["seq"], e3["seq"]), (1, 2, 1))

    def test_read_since_filters(self):
        for i in range(5):
            self._append("a:x", i)
        events, truncated = self.journal.read_since("a:x", 3)
        self.assertFalse(truncated)
        self.assertEqual([e["seq"] for e in events], [4, 5])
        self.assertEqual([e["data"]["n"] for e in events], [3, 4])

    def test_seq_survives_new_journal(self):
        self._append("a:x", 1)
        journal2 = Journal(Path(self.tmp))
        self.assertEqual(journal2.last_seq("a:x"), 1)

    def test_ring_trim_keeps_newest(self):
        # Force a trim by writing a file past the byte threshold.
        key = "a:big"
        path = self.journal.dir / f"{protocol.key_slug(key)}.jsonl"
        with open(path, "w") as fh:
            for i in range(5000):
                fh.write(json.dumps({"seq": i + 1, "type": "t",
                                     "data": "x" * 300}) + "\n")
        self._append(key, 1)  # triggers _maybe_trim
        events, truncated = self.journal.read_since(key, 0)
        self.assertGreater(truncated or len(events) <= 4000, 0)
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs[-1], 5001)  # newest survived
        self.assertLess(len(events), 5001)  # ring was trimmed

    def test_missing_file_reads_empty(self):
        events, truncated = self.journal.read_since("ghost", 5)
        self.assertEqual(events, [])
        self.assertFalse(truncated)

    def test_truncated_when_client_behind_ring(self):
        key = "a:x"
        path = self.journal.dir / f"{protocol.key_slug(key)}.jsonl"
        with open(path, "w") as fh:
            for i in range(10, 15):
                fh.write(json.dumps({"seq": i, "type": "t"}) + "\n")
        events, truncated = self.journal.read_since(key, 2)
        self.assertTrue(truncated)
        self.assertEqual([e["seq"] for e in events], [10, 11, 12, 13, 14])

    def test_read_since_limit_and_corrupt_lines(self):
        key = "a:y"
        path = self.journal.dir / f"{protocol.key_slug(key)}.jsonl"
        with open(path, "w") as fh:
            fh.write("not json\n")
            fh.write(json.dumps([1, 2]) + "\n")          # non-dict
            fh.write(json.dumps({"type": "t"}) + "\n")   # no seq
            for i in range(1, 5):
                fh.write(json.dumps({"seq": i, "type": "t"}) + "\n")
        events, _ = self.journal.read_since(key, 0, limit=2)
        self.assertEqual([e["seq"] for e in events], [1, 2])

    def test_append_oserror_returns_event(self):
        from unittest.mock import patch
        j = Journal(Path(self.tmp) / "nowhere")
        with patch("builtins.open", side_effect=OSError("read-only")):
            ev = j.append("k", protocol.event("t", "k", "m", {}))
        self.assertEqual(ev["seq"], 1)


if __name__ == "__main__":
    unittest.main()
