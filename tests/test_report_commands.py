"""Focused tests for report command parsing and zero-session behavior."""

import io
import json
import shlex
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from quiver.reports.commands import (
    _default_period,
    _followup,
    _generate,
    _parse_generate_args,
    _print_preview,
    _warnings_command,
    cmd_report,
)
from quiver.reports.followups import FollowUpLedger
from quiver.reports.models import FollowUp
from quiver.reports.pipeline import ReportApprovalError, ReportWriterError


class ReportCommandsTest(unittest.TestCase):
    @staticmethod
    def _preview(*, over_budget=False):
        return SimpleNamespace(
            total=1,
            period_start="start",
            period_end="end",
            excluded=0,
            cached=0,
            unreadable=0,
            project_batched=1,
            project_batch_calls=1,
            dedicated=0,
            estimated_summary_calls=1,
            estimated_summary_input_tokens=100,
            estimated_writer_input_tokens=50,
            estimated_input_tokens=150,
            over_budget=over_budget,
        )

    def _run_generate(self, pipeline, approval="y"):
        query = Mock()
        query.apply.return_value = [object()]
        patches = (
            patch("quiver.reports.commands.load_resolved_config", return_value={}),
            patch("quiver.reports.commands.validate_config", return_value=[]),
            patch("quiver.reports.commands.report_setup_complete", return_value=True),
            patch(
                "quiver.reports.commands._source_period",
                return_value=(1, 2, "start", "end", True),
            ),
            patch("quiver.reports.commands.SessionQuery", return_value=query),
            patch("quiver.reports.commands.get_all_sessions", return_value=[object()]),
            patch("quiver.reports.commands._pipeline", return_value=pipeline),
        )
        output = io.StringIO()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            with redirect_stdout(output):
                result = _generate("daily", [], input_fn=lambda _prompt: approval)
        return result, output.getvalue()

    def test_generate_flags_are_explained_by_distinct_fields(self):
        parsed = _parse_generate_args([
            "-d", "5", "--here", "--session-harness", "claude",
            "--session-model", "haiku", "--session-arg", "--server=local",
            "--writer-harness", "codex", "--writer-model", "strong",
        ])
        self.assertEqual(parsed.days, 5)
        self.assertTrue(parsed.here)
        self.assertEqual(parsed.session_args, ["--server=local"])
        self.assertEqual(parsed.writer_model, "strong")

    def test_rejects_mixed_date_ranges(self):
        with self.assertRaises(ValueError):
            _parse_generate_args(["-d", "2", "-w", "1"])

    @patch("quiver.reports.commands.get_all_sessions", return_value=[])
    @patch("quiver.reports.commands.report_setup_complete", return_value=True)
    @patch("quiver.reports.commands.load_resolved_config", return_value={})
    @patch("quiver.reports.commands.ReportStore")
    def test_no_sessions_returns_without_pipeline(self, store_cls, _config, _complete, _sessions):
        store_cls.return_value.get_cursor.return_value = None
        output = io.StringIO()
        with redirect_stdout(output):
            result = cmd_report(["daily", "-d", "1"])
        self.assertEqual(result, 0)
        self.assertIn("No sessions found", output.getvalue())

    def test_unknown_command_is_error(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cmd_report(["unknown"]), 1)

    def test_daily_default_ends_at_last_completed_day(self):
        store = SimpleNamespace(get_cursor=lambda _cadence: None)
        _start_ms, _end_ms, start, end = _default_period("daily", store)
        now = datetime.now().astimezone()

        self.assertEqual(
            datetime.fromisoformat(end).date(), now.date() - timedelta(days=1)
        )
        self.assertEqual(datetime.fromisoformat(end).hour, 23)
        self.assertEqual(datetime.fromisoformat(start).date(), now.date() - timedelta(days=1))
        self.assertLess(datetime.fromisoformat(end), now)

    def test_invalid_cursor_falls_back_to_completed_window(self):
        store = SimpleNamespace(
            get_cursor=lambda _cadence: SimpleNamespace(through="not-a-date")
        )

        _start_ms, _end_ms, start, end = _default_period("weekly", store)

        self.assertLess(datetime.fromisoformat(start), datetime.fromisoformat(end))
        self.assertEqual(datetime.fromisoformat(end).weekday(), 6)

    @patch("quiver.reports.commands.load_resolved_config")
    def test_invalid_resolved_config_is_reported_without_traceback(self, load_config):
        load_config.return_value = {
            "report": {
                "max_workers": None,
                "session": {"harness": "codex", "model": "small", "args": []},
                "writer": {"harness": "codex", "model": "strong", "args": []},
            }
        }
        output = io.StringIO()

        with redirect_stdout(output):
            result = cmd_report(["daily"])

        self.assertEqual(result, 1)
        self.assertIn("report.max_workers: must be a positive integer", output.getvalue())

    def test_preview_prints_summary_writer_and_total_token_estimates(self):
        preview = SimpleNamespace(
            total=2,
            period_start="start",
            period_end="end",
            excluded=0,
            cached=0,
            unreadable=0,
            project_batched=2,
            project_batch_calls=1,
            dedicated=0,
            estimated_summary_calls=1,
            estimated_summary_input_tokens=10_000,
            estimated_writer_input_tokens=2_000,
            estimated_input_tokens=12_000,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            _print_preview(preview)

        rendered = output.getvalue()
        self.assertIn("~10,000 summary input tokens", rendered)
        self.assertIn("~2,000 writer input tokens", rendered)
        self.assertIn("~12,000 total", rendered)

    def test_warning_command_is_specific_and_shell_safe(self):
        path = Path("/tmp/report archive/daily manifest.json")
        command = _warnings_command(path)

        self.assertEqual(
            shlex.split(command),
            ["swe", "report", "warnings", str(path)],
        )

    def test_warnings_command_prints_only_the_requested_manifest(self):
        manifest = {
            "report_id": "daily-example",
            "cadence": "daily",
            "period_start": "2026-07-29T00:00:00+08:00",
            "period_end": "2026-07-29T23:59:59.999000+08:00",
            "generated_at": "2026-07-30T00:00:00+00:00",
            "warnings": ["first warning", "second warning"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "specific.json"
            path.write_text(json.dumps(manifest))
            output = io.StringIO()

            with redirect_stdout(output):
                result = cmd_report(["warnings", str(path)])

        self.assertEqual(result, 0)
        rendered = output.getvalue()
        self.assertIn("Warnings for daily-example", rendered)
        self.assertIn("1. first warning", rendered)
        self.assertIn("2. second warning", rendered)

    def test_generate_cancellation_reports_that_no_model_calls_started(self):
        pipeline = Mock()
        pipeline.preview.return_value = self._preview()
        pipeline.run.side_effect = ReportApprovalError("not approved")

        result, output = self._run_generate(pipeline, approval="n")

        self.assertEqual(result, 0)
        self.assertIn("no model calls were started", output)
        pipeline.run.assert_called_once()

    def test_generate_passes_exact_over_budget_acknowledgement(self):
        pipeline = Mock()
        pipeline.preview.return_value = self._preview(over_budget=True)
        pipeline.run.return_value = SimpleNamespace(
            report=SimpleNamespace(markdown="# Daily report"),
            markdown_path=Path("/tmp/report.md"),
            manifest_path=Path("/tmp/report.json"),
            warnings=("one session failed",),
        )

        result, output = self._run_generate(pipeline, approval="process all")

        self.assertEqual(result, 0)
        plan = pipeline.run.call_args.args[0]
        self.assertEqual(plan.approval, "process all")
        self.assertIn("Completed with 1 warning", output)
        self.assertIn("swe report warnings /tmp/report.json", output)

    def test_generate_reports_writer_failure_without_traceback(self):
        pipeline = Mock()
        pipeline.preview.return_value = self._preview()
        pipeline.run.side_effect = ReportWriterError("writer exited")

        result, output = self._run_generate(pipeline)

        self.assertEqual(result, 1)
        self.assertIn("Report failed: writer exited", output)

    def test_followup_lifecycle_routes_to_persistent_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = FollowUpLedger(root=tmp, clock=lambda: "2026-08-01T00:00:00+00:00")
            with patch("quiver.reports.commands.FollowUpLedger", return_value=ledger):
                self.assertEqual(_followup(["add", "Ship", "the", "fix", "--project", tmp]), 0)
                item = ledger.list()[0]
                self.assertEqual(_followup(["edit", item.id, "Ship", "the", "tested", "fix"]), 0)
                self.assertEqual(_followup(["done", item.id]), 0)
                self.assertEqual(_followup(["reopen", item.id]), 0)
                self.assertEqual(_followup(["dismiss", item.id]), 0)

            final = ledger.get(item.id)
            self.assertEqual(final.text, "Ship the tested fix")
            self.assertEqual(final.status, "dismissed")

    def test_followup_work_rejects_conflicting_modes_before_launch(self):
        item = FollowUp(id="fu_test", text="Finish", project_root="/tmp")
        ledger = Mock()
        ledger.get.return_value = item
        with patch("quiver.reports.commands.FollowUpLedger", return_value=ledger), patch(
            "quiver.reports.commands.work_on_follow_up"
        ) as work:
            result = _followup(["work", item.id, "--resume", "--new"])

        self.assertEqual(result, 1)
        work.assert_not_called()


if __name__ == "__main__":
    unittest.main()
