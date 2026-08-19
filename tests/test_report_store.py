import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.reports.models import ExclusionRecord, Report, ReportManifest, SessionSummary
from quiver.reports.store import MalformedReportStateError, ReportStore


NOW = "2026-07-30T08:00:00+00:00"


class ReportStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "reports"
        self.store = ReportStore(self.root, clock=lambda: NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def summary(self, digest="digest-1"):
        return SessionSummary(
            session_id="session-1",
            digest=digest,
            project_root="/repo",
            source_tool="codex",
            summary="Implemented the report store.",
            blockers=["integration pending"],
        )

    def manifest(self):
        return ReportManifest(
            report_id="daily-2026-07-29",
            cadence="daily",
            period_start="2026-07-29",
            period_end="2026-07-29",
            generated_at=NOW,
            source_session_ids=["session-1"],
            summary_digests={"session-1": "digest-1"},
        )

    def test_summary_cache_lookup_and_digest_invalidation(self):
        path = self.store.save_session_summary(self.summary())
        self.assertTrue(path.exists())
        self.assertEqual(
            self.store.get_session_summary("session-1", "digest-1", "codex").summary,
            "Implemented the report store.",
        )
        self.assertIsNone(self.store.get_session_summary("session-1", "changed", "codex"))

        self.store.save_session_summary(self.summary("changed"))
        self.assertIsNone(self.store.get_session_summary("session-1", "digest-1", "codex"))
        self.assertEqual(
            self.store.get_session_summary("session-1", "changed", "codex").digest,
            "changed",
        )
        self.assertEqual(self.store.get_session_summary("session-1", "changed").digest, "changed")
        self.assertTrue(self.store.invalidate_session_summary("session-1", "codex"))
        self.assertFalse(self.store.invalidate_session_summary("session-1", "codex"))
        self.assertIsNone(self.store.get_session_summary("session-1", "changed", "codex"))

    def test_report_and_manifest_are_timestamped_and_cursor_is_explicit(self):
        markdown_path, manifest_path = self.store.write_report("# Daily\n", self.manifest())
        self.assertIn("20260730T080000_0000", markdown_path.name)
        self.assertEqual(markdown_path.read_text(), "# Daily\n")
        loaded = self.store.load_manifest(manifest_path)
        self.assertEqual(loaded.report_id, "daily-2026-07-29")
        self.assertEqual(loaded.markdown_path, str(markdown_path))
        self.assertIsNone(self.store.get_cursor("daily"))

        cursor = self.store.advance_cursor("daily", "2026-07-29", loaded.report_id)
        self.assertEqual(cursor.through, "2026-07-29")
        self.assertEqual(self.store.get_cursor("daily"), cursor)

    def test_serializable_report_model_can_be_saved(self):
        report = Report(markdown="# Daily\n", manifest=self.manifest())
        restored = Report.from_dict(report.to_dict())
        markdown_path, manifest_path = self.store.save_report(restored)
        self.assertEqual(markdown_path.read_text(), "# Daily\n")
        self.assertEqual(self.store.load_manifest(manifest_path).report_id, report.manifest.report_id)

    def test_daily_and_weekly_cursors_are_independent(self):
        self.store.advance_cursor("daily", "2026-07-29", "daily-1")
        self.store.advance_cursor("weekly", "2026-07-26", "weekly-1")
        self.assertEqual(self.store.get_cursor("daily").report_id, "daily-1")
        self.assertEqual(self.store.get_cursor("weekly").report_id, "weekly-1")

    def test_cursor_cannot_move_backwards(self):
        self.store.advance_cursor("daily", "2026-07-29", "daily-1")
        before = self.store.cursors_file.read_bytes()
        with self.assertRaises(ValueError):
            self.store.advance_cursor("daily", "2026-07-28", "daily-older")
        self.assertEqual(self.store.cursors_file.read_bytes(), before)

    def test_exclusions_deduplicate_by_tool_session_and_digest(self):
        first = ExclusionRecord("s1", "d1", "greeting", source_tool="codex")
        second = ExclusionRecord("s1", "d1", "empty", source_tool="codex")
        changed = ExclusionRecord("s1", "d2", "empty", source_tool="codex")
        self.store.record_exclusions([first])
        self.store.record_exclusions([second, changed])
        records = self.store.load_exclusions()
        self.assertEqual(len(records), 2)
        self.assertEqual({record.digest for record in records}, {"d1", "d2"})

    def test_malformed_state_is_preserved_and_not_overwritten(self):
        self.store.cursors_file.parent.mkdir(parents=True)
        self.store.cursors_file.write_text("{broken")
        before = self.store.cursors_file.read_bytes()
        with self.assertRaises(MalformedReportStateError):
            self.store.advance_cursor("daily", "2026-07-29", "r1")
        self.assertEqual(self.store.cursors_file.read_bytes(), before)

        summary_path = self.store._summary_path("session-1", "codex")
        summary_path.parent.mkdir(parents=True)
        summary_path.write_text("[]")
        with self.assertRaises(MalformedReportStateError):
            self.store.save_session_summary(self.summary())
        self.assertEqual(summary_path.read_text(), "[]")

        summary_path.write_text('{"session_id": "session-1"}')
        with self.assertRaises(MalformedReportStateError):
            self.store.save_session_summary(self.summary())
        self.assertEqual(summary_path.read_text(), '{"session_id": "session-1"}')

    def test_atomic_replace_failure_preserves_existing_cache(self):
        path = self.store.save_session_summary(self.summary())
        before = path.read_bytes()
        with patch("quiver.reports.store.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.save_session_summary(self.summary("new"))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_manifest_failure_removes_unpaired_markdown(self):
        manifest = self.manifest()
        with patch("quiver.reports.store._atomic_write_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.write_report("# Daily\n", manifest)
        self.assertEqual(list((self.root / "daily").glob("*.md")), [])

    def test_malformed_manifest_raises_without_modification(self):
        path = self.root / "daily" / "bad.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"report_id": "missing-fields"}))
        before = path.read_bytes()
        with self.assertRaises(MalformedReportStateError):
            self.store.load_manifest(path)
        self.assertEqual(path.read_bytes(), before)

    def test_report_filename_does_not_trust_manifest_identifiers(self):
        manifest = self.manifest()
        manifest.report_id = "../../outside"
        markdown_path, manifest_path = self.store.write_report("safe", manifest)
        self.assertEqual(markdown_path.parent, self.root / "daily")
        self.assertEqual(manifest_path.parent, self.root / "daily")
        self.assertNotIn("..", markdown_path.name)


if __name__ == "__main__":
    unittest.main()
