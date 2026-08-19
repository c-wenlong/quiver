import unittest

from quiver.reports.transcripts import NormalizedMessage, NormalizedTranscript
from quiver.reports.triage import DEDICATED, NOISE, PROJECT_BATCH, classify_transcript


def _transcript(messages, readable=True, error=""):
    return NormalizedTranscript(
        tool_name="test",
        session_id="session-1",
        project_path="/work/project",
        messages=messages,
        readable=readable,
        error=error,
    )


class ReportTriageTest(unittest.TestCase):
    def test_empty_greeting_version_and_exit_are_noise(self):
        empty = classify_transcript(_transcript([]))
        self.assertEqual(empty.classification, NOISE)
        self.assertEqual(empty.reasons, ("empty transcript",))

        for prompt in ("hello", "Version", "/exit"):
            decision = classify_transcript(_transcript([
                NormalizedMessage("human", prompt),
                NormalizedMessage("assistant", "ok"),
            ]))
            self.assertEqual(decision.classification, NOISE, prompt)
            self.assertIn("no substantive human prompt", decision.reasons)

    def test_startup_only_failure_without_work_is_noise(self):
        decision = classify_transcript(_transcript([
            NormalizedMessage("tool", "Authentication failed: weekly rate limit reached")
        ]))
        self.assertEqual(decision.classification, NOISE)
        self.assertEqual(decision.reasons, ("startup-only failure without work",))

    def test_meaningful_single_prompt_edit_is_batched(self):
        decision = classify_transcript(_transcript([
            NormalizedMessage("human", "Fix the Python 3.10 import failure."),
            NormalizedMessage("tool", "Edit: src/parser.py"),
            NormalizedMessage("tool", "pytest: 18 passed"),
            NormalizedMessage("assistant", "Updated the fallback and verified the tests."),
        ]))
        self.assertEqual(decision.classification, PROJECT_BATCH)
        self.assertEqual(decision.activity.substantive_human_turns, 1)
        self.assertTrue(decision.digest)

    def test_task_related_error_is_meaningful(self):
        decision = classify_transcript(_transcript([
            NormalizedMessage("human", "Run the migration and diagnose failures."),
            NormalizedMessage("assistant", "The migration failed because column users.slug is missing."),
        ]))
        self.assertEqual(decision.classification, PROJECT_BATCH)

    def test_prompt_without_any_outcome_is_noise(self):
        decision = classify_transcript(_transcript([
            NormalizedMessage("human", "Refactor the report pipeline.")
        ]))
        self.assertEqual(decision.classification, NOISE)
        self.assertIn("without assistant or tool outcome", decision.reasons[0])

    def test_dedicated_thresholds_are_independent(self):
        base = [
            NormalizedMessage("human", "Do meaningful work"),
            NormalizedMessage("assistant", "Done"),
        ]
        chars = classify_transcript(_transcript([
            NormalizedMessage("human", "x" * 60_001),
            NormalizedMessage("assistant", "done"),
        ]))
        self.assertEqual(chars.classification, DEDICATED)

        turns = classify_transcript(_transcript(
            [item for i in range(10) for item in (
                NormalizedMessage("human", f"Task {i}"),
                NormalizedMessage("assistant", f"Result {i}"),
            )]
        ))
        self.assertEqual(turns.classification, DEDICATED)

        tools = classify_transcript(_transcript(base + [
            NormalizedMessage("tool", f"command {i}") for i in range(20)
        ]))
        self.assertEqual(tools.classification, DEDICATED)

    def test_unreadable_transcript_is_not_silently_classified_as_noise(self):
        decision = classify_transcript(_transcript([], readable=False, error="bad JSON"))
        self.assertEqual(decision.classification, PROJECT_BATCH)
        self.assertIn("bad JSON", decision.reasons[0])

    def test_digest_changes_with_semantic_content(self):
        first = classify_transcript(_transcript([
            NormalizedMessage("human", "Fix A"), NormalizedMessage("assistant", "Done")
        ]))
        second = classify_transcript(_transcript([
            NormalizedMessage("human", "Fix B"), NormalizedMessage("assistant", "Done")
        ]))
        self.assertNotEqual(first.digest, second.digest)


if __name__ == "__main__":
    unittest.main()

