import unittest
from unittest.mock import patch

from quiver.reports.batching import SummaryInput, build_summary_batches, compact_transcript
from quiver.reports.transcripts import NormalizedMessage, NormalizedTranscript
from quiver.sessions.models import Session


def item(session_id, project, timestamp, chars=20):
    session = Session(timestamp, "Codex", project, session_id=session_id, tool_name="codex")
    transcript = NormalizedTranscript(
        "codex",
        session_id,
        project,
        [NormalizedMessage("human", "x" * chars), NormalizedMessage("assistant", "done")],
    )
    return SummaryInput(session, transcript, transcript.digest, project)


class ReportBatchingTest(unittest.TestCase):
    def test_batches_are_chronological_and_project_isolated(self):
        batches = build_summary_batches(
            [item("b", "/b", 3), item("a2", "/a", 2), item("a1", "/a", 1)]
        )
        self.assertEqual([batch.project_root for batch in batches], ["/a", "/b"])
        self.assertEqual([entry.session.session_id for entry in batches[0].inputs], ["a1", "a2"])

    def test_count_and_character_limits_split_batches(self):
        inputs = [item(str(index), "/repo", index, chars=20) for index in range(5)]
        by_count = build_summary_batches(inputs, max_sessions=2, max_chars=10_000)
        self.assertEqual([len(batch.inputs) for batch in by_count], [2, 2, 1])
        by_chars = build_summary_batches(inputs[:2], max_sessions=25, max_chars=30)
        self.assertEqual([len(batch.inputs) for batch in by_chars], [1, 1])

    def test_dedicated_inputs_always_receive_one_call(self):
        dedicated = item("large", "/repo", 1)
        batches = build_summary_batches([], [dedicated])
        self.assertEqual(len(batches), 1)
        self.assertTrue(batches[0].dedicated)
        self.assertEqual(batches[0].inputs, (dedicated,))

    def test_thousand_sessions_scale_by_project_batches(self):
        inputs = [item(str(index), f"/repo-{index // 100}", index, chars=1) for index in range(1000)]
        batches = build_summary_batches(inputs)
        self.assertEqual(len(batches), 40)
        self.assertTrue(all(len(batch.inputs) == 25 for batch in batches))

    def test_compaction_keeps_context_edges_and_tool_outcome(self):
        source = NormalizedTranscript(
            "codex",
            "large",
            "/repo",
            [
                NormalizedMessage("human", "BEGIN " + "a" * 300),
                NormalizedMessage("assistant", "middle " + "b" * 300),
                NormalizedMessage("tool", "TESTS PASSED " + "c" * 30, kind="tool"),
                NormalizedMessage("assistant", "d" * 300 + " END"),
            ],
        )
        original_digest = source.digest
        compacted = compact_transcript(source, 300)
        self.assertLessEqual(len(compacted.normalized_text), 300)
        self.assertIn("BEGIN", compacted.normalized_text)
        self.assertIn("END", compacted.normalized_text)
        self.assertIn("TESTS PASSED", compacted.normalized_text)
        self.assertEqual(source.digest, original_digest)
        self.assertNotEqual(compacted.digest, original_digest)


if __name__ == "__main__":
    unittest.main()
