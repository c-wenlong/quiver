"""Tests for preparing and launching follow-up work."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quiver.reports.models import FollowUp
from quiver.reports.work import (
    FollowUpWorkError,
    build_follow_up_prompt,
    choose_work_action,
    launch_follow_up_work,
    prepare_follow_up_work,
)
from quiver.sessions.models import Session


REGISTRY = {
    "claude": {"command": "claude", "aliases": ["cc"]},
    "codex": {"command": "codex", "aliases": ["cx"]},
    "custom": {"command": "custom-agent", "aliases": ["ca"]},
}


class FollowUpWorkTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.child = self.root / "child"
        self.child.mkdir()
        self.follow_up = FollowUp(
            id="fu_1",
            text="Fix the report cache invalidation",
            project_root=str(self.root),
            source_session_ids=["old", "new"],
            source_report_ids=["daily-1"],
            context="A digest mismatch leaves stale output.",
            blockers=["Need a reproducible fixture"],
            completion_criteria=["Changed sessions are recomputed", "Tests pass"],
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_prompted_and_direct_modes(self):
        self.assertEqual(choose_work_action(None, lambda _: "resume"), "resume")
        self.assertEqual(choose_work_action(None, lambda _: "n"), "new")
        self.assertEqual(choose_work_action("NEW", lambda _: self.fail("prompted")), "new")
        with self.assertRaises(FollowUpWorkError):
            choose_work_action("other")
        with self.assertRaises(FollowUpWorkError):
            choose_work_action(None, lambda _: "")

    def test_resume_uses_newest_referenced_session(self):
        sessions = [
            Session(100, "Claude", str(self.root), session_id="old", tool_name="claude"),
            Session(300, "Codex", str(self.child), session_id="new", tool_name="codex"),
            Session(999, "Codex", str(self.root), session_id="unrelated", tool_name="codex"),
        ]
        plan = prepare_follow_up_work(
            self.follow_up, mode="resume", session_loader=lambda: sessions
        )
        self.assertEqual(plan.action, "resume")
        self.assertEqual(plan.source_session_id, "new")
        self.assertEqual(plan.cwd, str(self.child))
        self.assertEqual(plan.launch_args, ("codex", "--resume", "new"))
        self.assertEqual(plan.prompt, "")

    def test_resume_rejects_unsupported_without_falling_back(self):
        sessions = [
            Session(300, "Gemini", str(self.root), session_id="new", tool_name="gemini")
        ]
        with self.assertRaisesRegex(FollowUpWorkError, "does not support"):
            prepare_follow_up_work(
                self.follow_up, mode="resume", session_loader=lambda: sessions
            )

    def test_resume_rejects_missing_source_and_invalid_semantics(self):
        no_sources = FollowUp(id="fu_2", text="Task", project_root=str(self.root))
        with self.assertRaisesRegex(FollowUpWorkError, "no source sessions"):
            prepare_follow_up_work(no_sources, mode="resume", session_loader=lambda: [])
        with self.assertRaisesRegex(FollowUpWorkError, "only valid"):
            prepare_follow_up_work(
                self.follow_up,
                mode="resume",
                harness="codex",
                session_loader=lambda: [],
            )

    def test_resume_rejects_source_outside_project_or_unavailable(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Session(
                300, "Codex", other, session_id="new", tool_name="codex"
            )
            with self.assertRaisesRegex(FollowUpWorkError, "outside the follow-up project"):
                prepare_follow_up_work(
                    self.follow_up, mode="resume", session_loader=lambda: [outside]
                )
        missing = Session(
            300, "Codex", str(self.root / "missing"), session_id="new", tool_name="codex"
        )
        with self.assertRaisesRegex(FollowUpWorkError, "unavailable"):
            prepare_follow_up_work(
                self.follow_up, mode="resume", session_loader=lambda: [missing]
            )

    def test_resume_does_not_fall_back_when_newest_source_is_unsafe(self):
        with tempfile.TemporaryDirectory() as other:
            sessions = [
                Session(100, "Codex", str(self.root), session_id="old", tool_name="codex"),
                Session(300, "Codex", other, session_id="new", tool_name="codex"),
            ]
            with self.assertRaisesRegex(FollowUpWorkError, "outside"):
                prepare_follow_up_work(
                    self.follow_up, mode="resume", session_loader=lambda: sessions
                )

    def test_invalid_project_paths_are_rejected(self):
        for path in ("", str(self.root / "missing")):
            follow_up = FollowUp(id="fu_bad", text="Task", project_root=path)
            with self.assertRaises(FollowUpWorkError):
                prepare_follow_up_work(
                    follow_up, mode="new", harness="codex", registry_loader=lambda: REGISTRY
                )
        file_path = self.root / "file"
        file_path.write_text("x")
        follow_up = FollowUp(id="fu_file", text="Task", project_root=str(file_path))
        with self.assertRaisesRegex(FollowUpWorkError, "not a directory"):
            prepare_follow_up_work(
                follow_up, mode="new", harness="codex", registry_loader=lambda: REGISTRY
            )

    def test_new_requires_and_resolves_registered_harness(self):
        with self.assertRaisesRegex(FollowUpWorkError, "requires a harness"):
            prepare_follow_up_work(self.follow_up, mode="new")
        with self.assertRaisesRegex(FollowUpWorkError, "unknown harness"):
            prepare_follow_up_work(
                self.follow_up, mode="new", harness="missing", registry_loader=lambda: REGISTRY
            )

        for requested, canonical in (("claude", "claude"), ("cx", "codex"), ("ca", "custom")):
            with self.subTest(harness=requested):
                plan = prepare_follow_up_work(
                    self.follow_up,
                    mode="new",
                    harness=requested,
                    registry_loader=lambda: REGISTRY,
                )
                self.assertEqual(plan.action, "new")
                self.assertEqual(plan.cwd, str(self.root))
                self.assertEqual(plan.launch_args, (canonical, plan.prompt))

    def test_prompt_contains_context_and_manual_status_instruction(self):
        prompt = build_follow_up_prompt(self.follow_up)
        for value in (
            self.follow_up.text,
            self.follow_up.context,
            self.follow_up.blockers[0],
            self.follow_up.completion_criteria[0],
            "new",
            "daily-1",
            "mark it done manually",
        ):
            self.assertIn(value, prompt)

    def test_launch_receives_exact_args_and_cwd(self):
        plan = prepare_follow_up_work(
            self.follow_up,
            mode="new",
            harness="claude",
            registry_loader=lambda: REGISTRY,
        )
        calls = []

        def launch(args, cwd):
            calls.append((args, cwd))
            return 17

        self.assertEqual(launch_follow_up_work(plan, launch), 17)
        self.assertEqual(calls, [(plan.launch_args, str(self.root))])
        self.assertEqual(self.follow_up.status, "open")


if __name__ == "__main__":
    unittest.main()
