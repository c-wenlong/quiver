import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from quiver.mcp.discover import McpFinding
from quiver.harness.discover import HarnessFinding
from quiver.setup.commands import cmd_setup
from quiver.setup.wizard import (
    StageOutcome,
    _ask_yes_no,
    _stage_harnesses,
    _stage_check,
    _stage_mcp,
    _stage_providers,
    _stage_report,
    _stage_skills,
    backup_file,
    run_setup_wizard,
)
from quiver.skills.symlinks import SkillsSymlinkHint


class SetupWizardTest(unittest.TestCase):
    def test_backup_file_preserves_source_and_uses_timestamped_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "config.json"
            source.write_text('{"before": true}\n')

            backup = backup_file(source, now=datetime(2026, 7, 30, 12, 34, 56))

            self.assertEqual(source.read_text(), '{"before": true}\n')
            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_text(), source.read_text())
            self.assertIn("config.json.bak.20260730_123456", backup.name)

    def test_yes_no_reprompts_then_accepts_default(self):
        answers = iter(["maybe", ""])
        output = io.StringIO()
        with redirect_stdout(output):
            result = _ask_yes_no("Continue?", default=True, input_fn=lambda _prompt: next(answers))
        self.assertTrue(result)
        self.assertIn("Enter yes or no", output.getvalue())

    @patch("quiver.setup.wizard.backup_file")
    @patch("quiver.setup.wizard.apply_findings", return_value=["claude"])
    @patch("quiver.setup.wizard.discover_harnesses")
    def test_harness_stage_confirms_and_applies_high_confidence_findings(
        self, discover, apply, backup
    ):
        discover.return_value = [
            HarnessFinding(
                name="claude",
                command="claude",
                path="/usr/local/bin/claude",
                confidence="high",
                source="catalog",
                status="new",
            )
        ]
        backup.return_value = Path("/tmp/tools.json.bak")

        outcome = _stage_harnesses(
            1,
            1,
            input_fn=lambda _prompt: "",
            home=Path("/Users/test"),
            quick=False,
        )

        self.assertEqual(outcome.status, "changed")
        discover.assert_called_once_with(include_registered=True, include_missing=True)
        apply.assert_called_once()
        self.assertEqual(outcome.backups, ("/tmp/tools.json.bak",))

    def test_full_wizard_runs_sections_in_order(self):
        calls = []

        def stage(key):
            def run(number, total, **_kwargs):
                calls.append((key, number, total))
                return StageOutcome(key, key, "ready", "ok")
            return run

        stages = {
            key: stage(key)
            for key in ("harnesses", "providers", "mcp", "skills", "report", "check")
        }
        with patch.dict("quiver.setup.wizard._STAGES", stages, clear=True):
            result = run_setup_wizard(input_fn=lambda _prompt: "")

        self.assertEqual(result, 0)
        self.assertEqual([key for key, _number, _total in calls], list(stages))
        self.assertTrue(all(total == 6 for _key, _number, total in calls))

    def test_completed_stages_remain_reported_when_user_cancels(self):
        first = Mock(return_value=StageOutcome("harnesses", "Harnesses", "changed", "one"))
        second = Mock(side_effect=KeyboardInterrupt)
        stages = {
            "harnesses": first,
            "providers": second,
            "mcp": Mock(),
            "skills": Mock(),
            "report": Mock(),
            "check": Mock(),
        }
        output = io.StringIO()
        with patch.dict("quiver.setup.wizard._STAGES", stages, clear=True), redirect_stdout(output):
            result = run_setup_wizard()

        self.assertEqual(result, 130)
        self.assertIn("Completed stages remain saved", output.getvalue())
        self.assertIn("Harnesses", output.getvalue())

    def test_failed_stage_is_reported_and_later_stages_continue(self):
        failed = Mock(side_effect=RuntimeError("broken registry"))
        later = Mock(return_value=StageOutcome("providers", "Providers", "ready", "ok"))
        stages = {
            "harnesses": failed,
            "providers": later,
            "mcp": Mock(return_value=StageOutcome("mcp", "MCP", "ready", "ok")),
            "skills": Mock(return_value=StageOutcome("skills", "Skills", "ready", "ok")),
            "report": Mock(return_value=StageOutcome("report", "Report", "ready", "ok")),
            "check": Mock(return_value=StageOutcome("check", "Check", "ready", "ok")),
        }
        output = io.StringIO()
        with patch.dict("quiver.setup.wizard._STAGES", stages, clear=True), redirect_stdout(output):
            result = run_setup_wizard()

        self.assertEqual(result, 0)
        later.assert_called_once()
        self.assertIn("broken registry", output.getvalue())

    @patch("quiver.setup.wizard.discover_provider_keys")
    @patch("quiver.setup.wizard.load_provider_registry", return_value={"openai": {}})
    def test_provider_stage_distinguishes_configured_and_missing_keys(
        self, _registry, discover
    ):
        discover.return_value = [{"name": "openai", "masked": "sk-***"}]
        ready = _stage_providers(
            1, 1, input_fn=lambda _prompt: "", home=Path("/tmp/home"), quick=False
        )
        discover.return_value = [{"name": "openai", "masked": "-"}]
        attention = _stage_providers(
            1, 1, input_fn=lambda _prompt: "", home=Path("/tmp/home"), quick=False
        )

        self.assertEqual(ready.status, "ready")
        self.assertEqual(attention.status, "attention")

    @patch("quiver.setup.wizard.backup_file", return_value=Path("/tmp/mcp.json.bak"))
    @patch("quiver.setup.wizard.apply_mcp_findings", return_value=["notion"])
    @patch("quiver.setup.wizard.discover_mcp_servers")
    def test_mcp_stage_applies_confirmed_findings(self, discover, apply, _backup):
        finding = McpFinding(
            name="notion",
            tools=("claude",),
            status="new",
            source_tool="claude",
            server={"command": "notion-mcp"},
        )
        discover.return_value = [finding]

        outcome = _stage_mcp(
            1, 1, input_fn=lambda _prompt: "", home=Path("/tmp/home"), quick=False
        )

        self.assertEqual(outcome.status, "changed")
        self.assertEqual(outcome.changed, ("notion",))
        apply.assert_called_once_with([finding])

    @patch("quiver.setup.wizard.apply_skills_symlink_hints", return_value=["symlink codex"])
    @patch("quiver.setup.wizard.skills_symlink_hints")
    def test_skills_stage_applies_safe_hint_and_rechecks(self, hints, apply):
        safe = SkillsSymlinkHint(
            label="codex",
            path=Path("/tmp/home/.codex/skills"),
            action="symlink",
            command="ln -s",
            reason="link to shared",
        )
        hints.side_effect = [[safe], []]

        outcome = _stage_skills(
            1, 1, input_fn=lambda _prompt: "", home=Path("/tmp/home"), quick=False
        )

        self.assertEqual(outcome.status, "changed")
        apply.assert_called_once_with([safe], home=Path("/tmp/home"))

    @patch("quiver.setup.wizard.report_setup_complete", return_value=True)
    @patch("quiver.setup.wizard.load_resolved_config", return_value={})
    def test_report_stage_quick_mode_keeps_valid_configuration(self, _load, _complete):
        outcome = _stage_report(
            1, 1, input_fn=lambda _prompt: "", home=Path("/tmp/home"), quick=True
        )

        self.assertEqual(outcome.status, "ready")
        self.assertEqual(outcome.changed, ())

    @patch("quiver.setup.wizard.skills_symlink_hints", return_value=[])
    @patch("quiver.setup.wizard.load_resolved_config", return_value={})
    @patch("quiver.setup.wizard.report_setup_complete", return_value=False)
    @patch("quiver.setup.wizard.check_config", return_value=["broken"])
    @patch("quiver.setup.wizard.discover_provider_keys", return_value=[])
    @patch("quiver.setup.wizard.load_provider_registry", return_value={})
    @patch("quiver.setup.wizard.discover_harnesses", return_value=[])
    def test_check_stage_reports_attention_for_missing_setup(self, *_mocks):
        outcome = _stage_check(
            1, 1, input_fn=lambda _prompt: "", home=Path("/tmp/home"), quick=False
        )

        self.assertEqual(outcome.status, "attention")


class SetupCommandRoutingTest(unittest.TestCase):
    @patch("quiver.setup.commands.run_setup_wizard", return_value=0)
    @patch("quiver.setup.commands.sys.stdin.isatty", return_value=True)
    def test_interactive_setup_routes_section_and_quick_mode(self, _isatty, wizard):
        self.assertEqual(cmd_setup(["report", "--quick"]), 0)
        wizard.assert_called_once_with(section="report", quick=True)

    @patch("quiver.setup.commands.run_setup_wizard")
    @patch("quiver.setup.commands.sys.stdin.isatty", return_value=False)
    def test_section_requires_interactive_terminal(self, _isatty, wizard):
        output = io.StringIO()
        with redirect_stdout(output):
            result = cmd_setup(["mcp"])
        self.assertEqual(result, 1)
        self.assertIn("requires an interactive terminal", output.getvalue())
        wizard.assert_not_called()

    def test_unknown_section_is_actionable(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = cmd_setup(["telepathy"])
        self.assertEqual(result, 1)
        self.assertIn("Sections: harnesses, providers, mcp, skills, report, check", output.getvalue())


if __name__ == "__main__":
    unittest.main()
