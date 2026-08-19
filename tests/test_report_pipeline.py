import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from quiver.reports.followups import FollowUpLedger
from quiver.reports.models import SessionSummary
from quiver.reports.pipeline import (
    ApprovedReportPlan,
    ReportApprovalError,
    ReportPipeline,
    ReportWriterError,
    approval_granted,
)
from quiver.reports.runners import RunnerResult, RunnerSpec
from quiver.reports.store import ReportStore
from quiver.reports.transcripts import NormalizedMessage, NormalizedTranscript
from quiver.sessions.models import Session


NOW = "2026-07-30T08:00:00+00:00"


def transcript(session, suffix=""):
    return NormalizedTranscript(
        session.tool_name,
        session.session_id,
        session.path,
        [
            NormalizedMessage("human", f"Implement {session.session_id}{suffix}"),
            NormalizedMessage("assistant", "Implemented and tested it."),
        ],
    )


def parse_session_prompt(prompt):
    payload = prompt.split("<session_data>\n", 1)[1].split("\n</session_data>", 1)[0]
    return json.loads(payload)


class FakeRunner:
    def __init__(self, fail_project=None, fail_writer=False, suggestions=None):
        self.calls = []
        self.prompts = []
        self.fail_project = fail_project
        self.fail_writer = fail_writer
        self.suggestions = suggestions or []

    def __call__(self, spec, prompt, *, output_kind):
        self.calls.append(output_kind)
        self.prompts.append((output_kind, prompt))
        if output_kind == "final_report":
            if self.fail_writer:
                raise RuntimeError("writer unavailable")
            return RunnerResult(
                spec.harness,
                {
                    "kind": "final_report",
                    "markdown": "# Report\n",
                    "follow_up_suggestions": self.suggestions,
                },
                1,
            )
        payload = parse_session_prompt(prompt)
        if payload["project_path"] == self.fail_project:
            raise RuntimeError("worker unavailable")
        summaries = []
        for item in payload["sessions"]:
            summaries.append(
                {
                    "session_id": item["session_id"],
                    "objective": "Implement feature",
                    "outcome": "Completed",
                    "status": "completed",
                    "changes": ["code"],
                    "decisions": [],
                    "blockers": [],
                    "follow_ups": [],
                    "context": "Tests pass",
                }
            )
        return RunnerResult(
            spec.harness,
            {
                "kind": "session_summary_batch",
                "project_path": payload["project_path"],
                "sessions": summaries,
                "project_summary": "Completed work",
            },
            1,
        )


class ReportPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "reports"
        self.store = ReportStore(self.root, clock=lambda: NOW)
        self.ledger = FollowUpLedger(store=self.store, clock=lambda: NOW)
        self.sessions = [
            Session(1, "Codex", "/repo-a", "First", "s1", "codex"),
            Session(2, "Codex", "/repo-a", "Second", "s2", "codex"),
        ]
        self.transcripts = {session.session_id: transcript(session) for session in self.sessions}

    def tearDown(self):
        self.tmp.cleanup()

    def pipeline(self, runner, **kwargs):
        return ReportPipeline(
            RunnerSpec("codex", "cheap"),
            RunnerSpec("codex", "strong"),
            store=self.store,
            ledger=self.ledger,
            transcript_reader=lambda session: self.transcripts[session.session_id],
            runner=runner,
            clock=lambda: NOW,
            **kwargs,
        )

    def preview(self, pipeline, sessions=None, **kwargs):
        return pipeline.preview(
            sessions or self.sessions,
            cadence="daily",
            period_start="2026-07-29",
            period_end="2026-07-29",
            **kwargs,
        )

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_preview_and_cancellation_make_no_model_calls(self, _root):
        runner = FakeRunner()
        pipeline = self.pipeline(runner)
        preview = self.preview(pipeline)
        self.assertEqual(runner.calls, [])
        self.assertEqual(preview.project_batched, 2)
        with self.assertRaises(ReportApprovalError):
            pipeline.run(ApprovedReportPlan(preview, False))
        self.assertEqual(runner.calls, [])

    def test_strict_over_budget_approval_phrase(self):
        preview = Mock(over_budget=True)
        self.assertTrue(approval_granted(preview, "process all"))
        for value in (True, "yes", "Process all", "process all ", False):
            self.assertFalse(approval_granted(preview, value))
        normal = Mock(over_budget=False)
        self.assertTrue(approval_granted(normal, True))
        self.assertTrue(approval_granted(normal, "yes"))

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_preview_marks_call_budget_excess(self, _root):
        other = Session(3, "Codex", "/repo-b", "Third", "s3", "codex")
        self.transcripts["s3"] = transcript(other)
        pipeline = self.pipeline(FakeRunner(), max_summary_calls=1)
        preview = self.preview(pipeline, [*self.sessions, other])
        self.assertEqual(preview.estimated_summary_calls, 2)
        self.assertTrue(preview.over_budget)

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_pipeline_honors_batch_configuration(self, _root):
        pipeline = self.pipeline(FakeRunner(), max_batch_sessions=1, max_batch_chars=10_000)
        preview = self.preview(pipeline)
        self.assertEqual(preview.project_batch_calls, 2)
        self.assertEqual(preview.estimated_summary_calls, 2)

        by_chars = self.pipeline(
            FakeRunner(), max_batch_sessions=25, max_batch_chars=100
        )
        chars_preview = self.preview(by_chars)
        self.assertEqual(chars_preview.project_batch_calls, 2)

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_preview_warns_on_compaction_and_keeps_original_digest(self, _root):
        source = NormalizedTranscript(
            "codex",
            "s1",
            "/repo-a",
            [
                NormalizedMessage("human", "BEGIN " + "a" * 300),
                NormalizedMessage("tool", "COMMAND OK " + "b" * 40, kind="tool"),
                NormalizedMessage("assistant", "c" * 300 + " END"),
            ],
        )
        self.transcripts["s1"] = source
        original_digest = source.digest
        pipeline = self.pipeline(FakeRunner(), max_transcript_chars=240)
        preview = self.preview(pipeline, [self.sessions[0]])
        planned = preview.batches[0].inputs[0]
        self.assertEqual(planned.digest, original_digest)
        self.assertLessEqual(len(planned.transcript.normalized_text), 240)
        self.assertIn("BEGIN", planned.transcript.normalized_text)
        self.assertIn("END", planned.transcript.normalized_text)
        self.assertIn("COMMAND OK", planned.transcript.normalized_text)
        self.assertTrue(any("compacted transcript from" in item for item in preview.warnings))

        pipeline.run(ApprovedReportPlan(preview, True))
        self.transcripts["s1"] = NormalizedTranscript(
            "codex",
            "s1",
            "/repo-a",
            [
                NormalizedMessage("human", "BEGIN " + "a" * 300),
                NormalizedMessage("tool", "CHANGED HIDDEN DETAIL " + "b" * 40, kind="tool"),
                NormalizedMessage("assistant", "c" * 300 + " END"),
            ],
        )
        changed = self.preview(pipeline, [self.sessions[0]])
        self.assertEqual(changed.cached, 0)
        self.assertNotEqual(changed.batches[0].inputs[0].digest, original_digest)

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_cache_reuse_and_one_changed_digest(self, _root):
        first_runner = FakeRunner()
        first = self.pipeline(first_runner)
        first.run(ApprovedReportPlan(self.preview(first), True))
        self.assertEqual(first_runner.calls.count("session_summary_batch"), 1)

        second_runner = FakeRunner()
        second = self.pipeline(second_runner)
        cached_preview = self.preview(second)
        self.assertEqual(cached_preview.cached, 2)
        self.assertEqual(cached_preview.estimated_summary_calls, 0)
        second.run(ApprovedReportPlan(cached_preview, True))
        self.assertEqual(second_runner.calls, ["final_report"])

        self.transcripts["s2"] = transcript(self.sessions[1], " changed")
        third_runner = FakeRunner()
        third = self.pipeline(third_runner)
        changed_preview = self.preview(third)
        self.assertEqual(changed_preview.cached, 1)
        self.assertEqual(changed_preview.estimated_summary_calls, 1)

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_project_isolation_and_dedicated_session(self, _root):
        third = Session(3, "Codex", "/repo-b", "Third", "s3", "codex")
        large = Session(4, "Codex", "/repo-a", "Large", "large", "codex")
        self.transcripts["s3"] = transcript(third)
        self.transcripts["large"] = NormalizedTranscript(
            "codex",
            "large",
            "/repo-a",
            [
                NormalizedMessage("human", "Implement large feature"),
                NormalizedMessage("assistant", "x" * 60_001),
            ],
        )
        pipeline = self.pipeline(FakeRunner())
        preview = self.preview(pipeline, [*self.sessions, third, large])
        self.assertEqual(preview.project_batch_calls, 2)
        self.assertEqual(preview.dedicated, 1)
        self.assertEqual(preview.estimated_summary_calls, 3)
        self.assertTrue(any(batch.dedicated for batch in preview.batches))

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_partial_worker_failure_still_persists_partial_report(self, _root):
        third = Session(3, "Codex", "/repo-b", "Third", "s3", "codex")
        self.transcripts["s3"] = transcript(third)
        runner = FakeRunner(fail_project="/repo-b")
        pipeline = self.pipeline(runner)
        result = pipeline.run(ApprovedReportPlan(self.preview(pipeline, [*self.sessions, third]), True))
        self.assertTrue(result.report.manifest.partial)
        self.assertTrue(any("worker unavailable" in warning for warning in result.warnings))
        self.assertTrue(result.markdown_path.exists())
        self.assertEqual(len(result.summaries), 2)
        self.assertIsNone(self.store.get_cursor("daily"))

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_all_worker_failures_abort_before_writer(self, _root):
        runner = FakeRunner(fail_project="/repo-a")
        pipeline = self.pipeline(runner)

        with self.assertRaisesRegex(ReportWriterError, "all summary batches failed"):
            pipeline.run(ApprovedReportPlan(self.preview(pipeline), True))

        self.assertNotIn("final_report", runner.calls)
        self.assertIsNone(self.store.get_cursor("daily"))

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_complete_worker_run_advances_cursor(self, _root):
        pipeline = self.pipeline(FakeRunner())
        pipeline.run(ApprovedReportPlan(self.preview(pipeline), True))
        cursor = self.store.get_cursor("daily")
        self.assertIsNotNone(cursor)
        self.assertEqual(cursor.through, "2026-07-29")

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_unreadable_session_does_not_advance_cursor(self, _root):
        self.transcripts["s2"] = NormalizedTranscript(
            "codex",
            "s2",
            "/repo-a",
            [],
            readable=False,
            error="transcript unavailable",
        )
        pipeline = self.pipeline(FakeRunner())
        preview = self.preview(pipeline)
        self.assertEqual(preview.unreadable, 1)

        result = pipeline.run(ApprovedReportPlan(preview, True))

        self.assertTrue(result.report.manifest.partial)
        self.assertTrue(any("transcript unavailable" in item for item in result.warnings))
        self.assertIsNone(self.store.get_cursor("daily"))

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_writer_estimate_includes_projected_outputs_and_is_part_of_total(self, _root):
        pipeline = self.pipeline(FakeRunner())
        empty = pipeline.preview(
            [],
            cadence="daily",
            period_start="2026-07-29",
            period_end="2026-07-29",
        )
        preview = self.preview(pipeline)
        self.assertGreater(preview.estimated_writer_input_tokens, empty.estimated_writer_input_tokens)
        self.assertEqual(
            preview.estimated_input_tokens,
            preview.estimated_summary_input_tokens + preview.estimated_writer_input_tokens,
        )

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_1000_cached_summaries_are_estimated_compacted_and_bounded(self, _root):
        sessions = [
            Session(index, "Codex", f"/repo-{index % 10}", f"Task {index}", f"s{index}", "codex")
            for index in range(1_000)
        ]
        self.transcripts.update({item.session_id: transcript(item) for item in sessions})
        cached = {
            item.session_id: SessionSummary(
                session_id=item.session_id,
                digest=self.transcripts[item.session_id].digest,
                project_root=item.path,
                source_tool="codex",
                ended_at=str(item.timestamp),
                title=item.title,
                objective="Implement a substantial cached objective " + "x" * 400,
                outcome="Completed cached work " + "y" * 400,
                summary="Summary " + "z" * 400,
                context="Context " + "c" * 400,
            )
            for item in sessions
        }
        runner = FakeRunner()
        pipeline = self.pipeline(runner, max_estimated_input_tokens=50_000)
        with patch.object(
            self.store,
            "get_session_summary",
            side_effect=lambda session_id, digest, source_tool: cached.get(session_id),
        ), patch.object(pipeline, "_previous_report", return_value="previous " + "p" * 100_000):
            preview = self.preview(pipeline, sessions)

        self.assertEqual(runner.calls, [])
        self.assertEqual(preview.cached, 1_000)
        self.assertEqual(preview.estimated_summary_calls, 0)
        self.assertGreater(preview.estimated_writer_input_tokens, 0)
        self.assertEqual(preview.estimated_input_tokens, preview.estimated_writer_input_tokens)
        self.assertTrue(preview.over_budget)
        self.assertTrue(any("session summaries" in warning for warning in preview.warnings))

        pipeline.run(ApprovedReportPlan(preview, "process all"))
        writer_prompt = next(prompt for kind, prompt in runner.prompts if kind == "final_report")
        self.assertLessEqual(len(writer_prompt), preview.writer_prompt_char_limit)
        self.assertLess(len(writer_prompt), 300_000)

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_generated_list_fields_cannot_bypass_writer_prompt_bound(self, _root):
        runner = FakeRunner()

        def huge_output(spec, prompt, *, output_kind):
            if output_kind == "final_report":
                return runner(spec, prompt, output_kind=output_kind)
            runner.calls.append(output_kind)
            runner.prompts.append((output_kind, prompt))
            payload = parse_session_prompt(prompt)
            return RunnerResult(
                spec.harness,
                {
                    "kind": "session_summary_batch",
                    "project_path": payload["project_path"],
                    "sessions": [
                        {
                            "session_id": item["session_id"],
                            "objective": "objective",
                            "outcome": "outcome",
                            "status": "completed",
                            "changes": ["x" * 1_000_000],
                            "decisions": [],
                            "blockers": [],
                            "follow_ups": [],
                            "context": "context",
                        }
                        for item in payload["sessions"]
                    ],
                    "project_summary": "y" * 1_000_000,
                },
                1,
            )

        pipeline = self.pipeline(huge_output)
        preview = self.preview(pipeline)
        pipeline.run(ApprovedReportPlan(preview, True))
        writer_prompt = next(prompt for kind, prompt in runner.prompts if kind == "final_report")
        self.assertLessEqual(len(writer_prompt), preview.writer_prompt_char_limit)

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_writer_failure_does_not_persist_report_or_cursor(self, _root):
        pipeline = self.pipeline(FakeRunner(fail_writer=True))
        with self.assertRaises(ReportWriterError):
            pipeline.run(ApprovedReportPlan(self.preview(pipeline), True))
        self.assertIsNone(self.store.get_cursor("daily"))
        self.assertEqual(list((self.root / "daily").glob("*")), [])
        self.assertIsNotNone(self.store.get_session_summary("s1", self.transcripts["s1"].digest, "codex"))

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_custom_range_does_not_advance_cursor(self, _root):
        pipeline = self.pipeline(FakeRunner())
        preview = self.preview(pipeline, advance_cursor=False)
        result = pipeline.run(ApprovedReportPlan(preview, True))
        self.assertTrue(result.markdown_path.exists())
        self.assertIsNone(self.store.get_cursor("daily"))

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_follow_up_suggestions_never_close_items(self, _root):
        existing = self.ledger.add("Finish report CLI", "/repo-a")
        runner = FakeRunner(
            suggestions=[
                {
                    "action": "suggest_resolved",
                    "follow_up_id": existing.id,
                    "text": "Appears complete",
                    "project_path": "/repo-a",
                    "evidence": ["s1"],
                },
                {
                    "action": "create",
                    "follow_up_id": "",
                    "text": "Document reports",
                    "project_path": "/repo-a",
                    "evidence": ["s2", "not-a-source-session"],
                },
            ]
        )
        pipeline = self.pipeline(runner)
        pipeline.run(ApprovedReportPlan(self.preview(pipeline), True))
        unchanged = self.ledger.get(existing.id)
        self.assertEqual(unchanged.status, "open")
        self.assertTrue(unchanged.resolution_suggested)
        self.assertEqual(len(self.ledger.list(status="open")), 2)
        created = next(item for item in self.ledger.list(status="open") if item.id != existing.id)
        self.assertEqual(created.source_session_ids, ["s2"])

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_update_context_changes_context_only(self, _root):
        existing = self.ledger.add("Finish report CLI", "/repo-a", context="old")
        runner = FakeRunner(
            suggestions=[
                {
                    "action": "update_context",
                    "follow_up_id": existing.id,
                    "text": "New evidence from the latest session",
                    "project_path": "/repo-a",
                    "evidence": ["s1"],
                }
            ]
        )
        pipeline = self.pipeline(runner)
        pipeline.run(ApprovedReportPlan(self.preview(pipeline), True))
        updated = self.ledger.get(existing.id)
        self.assertEqual(updated.context, "New evidence from the latest session")
        self.assertEqual(updated.status, "open")
        self.assertIsNone(updated.completed_at)
        self.assertIsNone(updated.dismissed_at)

    @patch("quiver.reports.batching.resolve_project_root", side_effect=lambda path: path)
    def test_report_save_failure_leaves_ledger_and_cursor_unchanged(self, _root):
        existing = self.ledger.add("Existing follow-up", "/repo-a", context="unchanged")
        before = self.ledger.path.read_bytes()
        runner = FakeRunner(
            suggestions=[
                {
                    "action": "update_context",
                    "follow_up_id": existing.id,
                    "text": "must not survive",
                    "project_path": "/repo-a",
                    "evidence": ["s1"],
                },
                {
                    "action": "create",
                    "follow_up_id": "",
                    "text": "must not be created",
                    "project_path": "/repo-a",
                    "evidence": ["s2"],
                },
            ]
        )
        pipeline = self.pipeline(runner)
        preview = self.preview(pipeline)
        with patch.object(self.store, "save_report", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                pipeline.run(ApprovedReportPlan(preview, True))
        self.assertEqual(self.ledger.path.read_bytes(), before)
        self.assertIsNone(self.store.get_cursor("daily"))


if __name__ == "__main__":
    unittest.main()
