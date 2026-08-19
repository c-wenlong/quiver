import tempfile
import unittest
from pathlib import Path

from quiver.reports.followups import FollowUpLedger, stable_follow_up_id
from quiver.reports.store import MalformedReportStateError


class TickingClock:
    def __init__(self):
        self.tick = 0

    def __call__(self):
        self.tick += 1
        return f"2026-07-30T08:00:{self.tick:02d}+00:00"


class FollowUpLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "reports"
        self.clock = TickingClock()
        self.ledger = FollowUpLedger(root=self.root, clock=self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stable_id_and_deduplication_preserve_sources(self):
        first = self.ledger.add(
            "  Add report CLI  ",
            "/repo",
            source_session_ids=["s1"],
            source_report_ids=["r1"],
            context="Initial context",
            blockers=["CLI integration"],
            completion_criteria=["Tests pass"],
        )
        duplicate = self.ledger.add(
            "add   REPORT cli",
            "/repo/",
            source_session_ids=["s2", "s1"],
            source_report_ids=["r2"],
        )
        self.assertEqual(first.id, duplicate.id)
        self.assertEqual(duplicate.source_session_ids, ["s1", "s2"])
        self.assertEqual(duplicate.source_report_ids, ["r1", "r2"])
        self.assertEqual(duplicate.context, "Initial context")
        self.assertEqual(len(self.ledger.list()), 1)
        self.assertEqual(first.id, stable_follow_up_id("Add report CLI", "/repo"))

    def test_edit_keeps_id_and_source_references(self):
        item = self.ledger.add(
            "Add report CLI", "/repo", source_session_ids=["s1"], source_report_ids=["r1"]
        )
        edited = self.ledger.edit(
            item.id,
            text="Wire report CLI",
            context="Use the frozen interfaces",
            blockers=["completion"],
            completion_criteria=["smoke test passes"],
        )
        self.assertEqual(edited.id, item.id)
        self.assertEqual(edited.source_session_ids, ["s1"])
        self.assertEqual(edited.source_report_ids, ["r1"])
        self.assertEqual(edited.text, "Wire report CLI")

    def test_all_lifecycle_transitions(self):
        item = self.ledger.add("Add report CLI", "/repo")
        done = self.ledger.done(item.id)
        self.assertEqual(done.status, "done")
        self.assertIsNotNone(done.completed_at)
        self.assertEqual(self.ledger.list(status="done"), [done])

        reopened = self.ledger.reopen(item.id)
        self.assertEqual(reopened.status, "open")
        self.assertIsNone(reopened.completed_at)

        dismissed = self.ledger.dismiss(item.id)
        self.assertEqual(dismissed.status, "dismissed")
        self.assertIsNotNone(dismissed.dismissed_at)

        reopened_again = self.ledger.reopen(item.id)
        self.assertEqual(reopened_again.status, "open")
        self.assertIsNone(reopened_again.dismissed_at)

    def test_resolution_suggestion_never_closes_item(self):
        item = self.ledger.add("Add report CLI", "/repo")
        suggested = self.ledger.suggest_resolution(item.id, "A later session may have completed this")
        self.assertEqual(suggested.status, "open")
        self.assertTrue(suggested.resolution_suggested)
        self.assertIn("completed", suggested.resolution_suggestion)
        self.assertIsNone(suggested.completed_at)

        done = self.ledger.done(item.id)
        suggested_after_done = self.ledger.suggest_resolution(item.id, "Still appears complete")
        self.assertEqual(suggested_after_done.status, "done")
        self.assertEqual(suggested_after_done.completed_at, done.completed_at)

    def test_list_filters_by_project_and_status(self):
        first = self.ledger.add("First", "/repo-a")
        second = self.ledger.add("Second", "/repo-b")
        self.ledger.done(second.id)
        self.assertEqual(self.ledger.list(status="open"), [first])
        self.assertEqual(self.ledger.list(project_root="/repo-b")[0].id, second.id)
        with self.assertRaises(ValueError):
            self.ledger.list(status="maybe")

    def test_unknown_and_invalid_edits_fail_without_mutation(self):
        item = self.ledger.add("Add report CLI", "/repo")
        before = self.ledger.path.read_bytes()
        with self.assertRaises(KeyError):
            self.ledger.done("missing")
        with self.assertRaises(ValueError):
            self.ledger.edit(item.id, text="  ")
        self.assertEqual(self.ledger.path.read_bytes(), before)

    def test_malformed_ledger_is_preserved(self):
        self.ledger.path.parent.mkdir(parents=True)
        self.ledger.path.write_text("{broken")
        before = self.ledger.path.read_bytes()
        with self.assertRaises(MalformedReportStateError):
            self.ledger.add("Do work", "/repo")
        self.assertEqual(self.ledger.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
