import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from quiver.reports.runners import (
    REPORT_ENV_MARKER,
    REPORT_ENV_OUTPUT_KIND,
    REPORT_ENV_RUN_ID,
    RunnerError,
    RunnerOutputError,
    RunnerSpec,
    RunnerTimeoutError,
    UnsupportedHarnessError,
    build_argv,
    run_structured,
)


SUMMARY = {
    "kind": "session_summary_batch",
    "project_path": "/work/project",
    "sessions": [
        {
            "session_id": "s1",
            "objective": "Fix the build",
            "outcome": "Tests pass",
            "status": "completed",
            "changes": ["Updated parser"],
            "decisions": [],
            "blockers": [],
            "follow_ups": [],
            "context": "No further action",
        }
    ],
    "project_summary": "Build fixed",
}


class RunnerArgvTest(unittest.TestCase):
    def test_claude_argv_is_non_interactive_and_read_only(self):
        spec = RunnerSpec("Claude", "claude-sonnet", ("--verbose",))
        self.assertEqual(
            build_argv(spec),
            [
                "claude",
                "--print",
                "--output-format",
                "json",
                "--model",
                "claude-sonnet",
                "--no-session-persistence",
                "--permission-mode",
                "plan",
                "--verbose",
            ],
        )

    def test_codex_argv_is_non_interactive_and_read_only(self):
        spec = RunnerSpec("codex", "gpt-5", ("--config", "model_reasoning=medium"))
        self.assertEqual(
            build_argv(spec),
            [
                "codex",
                "exec",
                "--model",
                "gpt-5",
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--json",
                "--config",
                "model_reasoning=medium",
                "-",
            ],
        )

    def test_unsupported_harness(self):
        with self.assertRaisesRegex(UnsupportedHarnessError, "droid"):
            build_argv(RunnerSpec("droid", "model"))

    def test_cannot_override_safety_flags(self):
        with self.assertRaisesRegex(ValueError, "managed by Quiver"):
            build_argv(RunnerSpec("codex", "gpt-5", ("--sandbox=danger-full-access",)))

    def test_claude_rejects_option_terminator(self):
        with self.assertRaisesRegex(ValueError, "terminators"):
            build_argv(RunnerSpec("claude", "sonnet", ("--", "ignored")))

    def test_codex_rejects_option_terminator(self):
        with self.assertRaisesRegex(ValueError, "terminators"):
            build_argv(RunnerSpec("codex", "gpt-5", ("--", "ignored")))

    def test_claude_rejects_positional_argument(self):
        with self.assertRaisesRegex(ValueError, "positional argument"):
            build_argv(RunnerSpec("claude", "sonnet", ("unexpected",)))

    def test_codex_rejects_extra_stdin_marker(self):
        with self.assertRaisesRegex(ValueError, "terminators"):
            build_argv(RunnerSpec("codex", "gpt-5", ("-",)))

    def test_useful_option_value_pairs_remain_supported(self):
        claude_argv = build_argv(
            RunnerSpec("claude", "sonnet", ("--effort", "medium"))
        )
        codex_argv = build_argv(
            RunnerSpec("codex", "gpt-5", ("--config", "model_reasoning=medium"))
        )

        self.assertEqual(claude_argv[-2:], ["--effort", "medium"])
        self.assertEqual(codex_argv[-3:], ["--config", "model_reasoning=medium", "-"])

    def test_managed_flags_cannot_be_repeated_after_safety_flags(self):
        for harness, args in (
            ("claude", ("--no-session-persistence",)),
            ("codex", ("--ephemeral",)),
        ):
            with self.subTest(harness=harness):
                with self.assertRaisesRegex(ValueError, "managed by Quiver"):
                    build_argv(RunnerSpec(harness, "model", args))


class RunStructuredTest(unittest.TestCase):
    def completed(self, stdout, *, returncode=0, stderr=""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    @patch("quiver.reports.runners.subprocess.run")
    def test_passes_prompt_on_stdin_and_marks_environment(self, run):
        run.return_value = self.completed(json.dumps(SUMMARY))

        result = run_structured(
            RunnerSpec("claude", "sonnet", timeout_seconds=42, cwd="/work/project"),
            "summarize this",
            output_kind="session_summary_batch",
        )

        self.assertEqual(result.data, SUMMARY)
        self.assertEqual(result.attempts, 1)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["input"], "summarize this")
        self.assertEqual(kwargs["timeout"], 42)
        self.assertEqual(kwargs["cwd"], "/work/project")
        self.assertTrue(kwargs["text"])
        self.assertFalse(kwargs["check"])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["env"][REPORT_ENV_MARKER], "1")
        self.assertEqual(kwargs["env"][REPORT_ENV_OUTPUT_KIND], "session_summary_batch")
        self.assertRegex(kwargs["env"][REPORT_ENV_RUN_ID], r"^[0-9a-f]{32}$")

    @patch("quiver.reports.runners.subprocess.run")
    def test_extracts_claude_result_json(self, run):
        run.return_value = self.completed(json.dumps({"result": json.dumps(SUMMARY)}))
        result = run_structured(
            RunnerSpec("claude", "sonnet"), "prompt", output_kind="session_summary_batch"
        )
        self.assertEqual(result.data, SUMMARY)

    @patch("quiver.reports.runners.subprocess.run")
    def test_extracts_codex_jsonl_agent_message(self, run):
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(SUMMARY)},
        }
        run.return_value = self.completed(
            json.dumps({"type": "thread.started"}) + "\n" + json.dumps(event) + "\n"
        )
        result = run_structured(
            RunnerSpec("codex", "gpt-5"), "prompt", output_kind="session_summary_batch"
        )
        self.assertEqual(result.data, SUMMARY)

    @patch("quiver.reports.runners.subprocess.run")
    def test_rejects_malformed_json_without_retry(self, run):
        run.return_value = self.completed("not json")
        with self.assertRaisesRegex(RunnerOutputError, "malformed"):
            run_structured(
                RunnerSpec("codex", "gpt-5"),
                "prompt",
                output_kind="session_summary_batch",
            )
        self.assertEqual(run.call_count, 1)

    @patch("quiver.reports.runners.subprocess.run")
    def test_rejects_invalid_schema(self, run):
        invalid = dict(SUMMARY, sessions=[])
        run.return_value = self.completed(json.dumps(invalid))
        with self.assertRaisesRegex(RunnerOutputError, "non-empty"):
            run_structured(
                RunnerSpec("claude", "sonnet"),
                "prompt",
                output_kind="session_summary_batch",
            )

    @patch("quiver.reports.runners.subprocess.run")
    def test_validates_final_report_and_user_owned_follow_up_status(self, run):
        report = {
            "kind": "final_report",
            "markdown": "# Work Completed\n\n- Fixed parser",
            "follow_up_suggestions": [
                {
                    "action": "create",
                    "follow_up_id": "",
                    "text": "Run CI",
                    "project_path": "/work/project",
                    "evidence": ["session:s1"],
                    "status": "done",
                }
            ],
        }
        run.return_value = self.completed(json.dumps(report))
        with self.assertRaisesRegex(RunnerOutputError, "user-owned status"):
            run_structured(
                RunnerSpec("claude", "sonnet"),
                "prompt",
                output_kind="final_report",
            )

    @patch("quiver.reports.runners.subprocess.run")
    def test_timeout_retries_once_then_stops(self, run):
        run.side_effect = subprocess.TimeoutExpired(["codex"], 1)
        with self.assertRaisesRegex(RunnerTimeoutError, "2 attempts"):
            run_structured(
                RunnerSpec("codex", "gpt-5", timeout_seconds=1),
                "prompt",
                output_kind="session_summary_batch",
                sleep=Mock(),
            )
        self.assertEqual(run.call_count, 2)

    @patch("quiver.reports.runners.subprocess.run")
    def test_transient_failure_retries_once_and_succeeds(self, run):
        run.side_effect = [
            self.completed("", returncode=1, stderr="429 rate limit"),
            self.completed(json.dumps(SUMMARY)),
        ]
        result = run_structured(
            RunnerSpec("claude", "sonnet"),
            "prompt",
            output_kind="session_summary_batch",
            sleep=Mock(),
        )
        self.assertEqual(result.attempts, 2)
        self.assertEqual(run.call_count, 2)

    @patch("quiver.reports.runners.subprocess.run")
    def test_second_transient_failure_stops(self, run):
        run.return_value = self.completed("", returncode=1, stderr="service unavailable")
        with self.assertRaises(RunnerError):
            run_structured(
                RunnerSpec("claude", "sonnet"),
                "prompt",
                output_kind="session_summary_batch",
                sleep=Mock(),
            )
        self.assertEqual(run.call_count, 2)

    @patch("quiver.reports.runners.subprocess.run")
    def test_claude_stdout_auth_error_is_actionable(self, run):
        run.return_value = self.completed(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "api_error_status": 401,
                    "result": "Failed to authenticate. OAuth access token has been revoked.",
                }
            ),
            returncode=1,
        )

        with self.assertRaises(RunnerError) as raised:
            run_structured(
                RunnerSpec("claude", "sonnet"),
                "prompt",
                output_kind="session_summary_batch",
                sleep=Mock(),
            )

        message = str(raised.exception)
        self.assertIn("OAuth access token has been revoked", message)
        self.assertIn("claude auth login", message)
        self.assertNotIn("no diagnostic output", message)
        self.assertEqual(run.call_count, 1)

    @patch("quiver.reports.runners.subprocess.run")
    def test_error_redacts_tokens_and_sensitive_argument_values(self, run):
        run.return_value = self.completed(
            "",
            returncode=1,
            stderr="request used sk-ant-abcdefghijklmnopqrstuvwxyz and hunter2",
        )
        spec = RunnerSpec("claude", "sonnet", ("--api-key", "hunter2"))
        with self.assertRaises(RunnerError) as raised:
            run_structured(spec, "prompt", output_kind="session_summary_batch")
        message = str(raised.exception)
        self.assertNotIn("sk-ant-", message)
        self.assertNotIn("hunter2", message)
        self.assertIn("[REDACTED]", message)


if __name__ == "__main__":
    unittest.main()
