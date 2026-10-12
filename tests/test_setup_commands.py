"""cmd_setup: the non-interactive print path and argument refusals."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from quiver.harness.discover import HarnessFinding
from quiver.mcp.discover import MergeResult, McpFinding
from quiver.setup.commands import cmd_setup
from quiver.skills.symlinks import SkillsSymlinkHint


def _finding(name="tool", confidence="high", status="new"):
    return HarnessFinding(name=name, command=name, path=f"/h/.{name}",
                          confidence=confidence, source="catalog",
                          status=status, description="", tags=(), aliases=())


def _mcp(name="srv", status="new"):
    return McpFinding(name=name, tools=("claude",), status=status,
                      source_tool="claude", server={"command": "npx"})


def _hint(action="symlink"):
    return SkillsSymlinkHint(label="skills", path=Path("/x"), action=action,
                             command="ln -s", reason="diverged")


class SetupCommandTest(unittest.TestCase):
    def _run(self, args, findings=(), mcp=(), hints=(), apply_map=None):
        """Run cmd_setup with discovery mocked and stdin forced non-tty."""
        from quiver.setup import commands as sc
        merge = MergeResult(added=[], updated=[], orphaned=[], pruned=[])
        mocks = {
            "apply_findings": Mock(return_value=[]),
            "apply_mcp_findings": Mock(return_value=merge),
            "apply_skills_symlink_hints": Mock(return_value=[]),
        }
        if apply_map:
            mocks.update(apply_map)
        out = io.StringIO()
        with patch.object(sc, "discover_harnesses", lambda: list(findings)), \
                patch.object(sc, "discover_mcp_servers", lambda **k: list(mcp)), \
                patch.object(sc, "skills_symlink_hints", lambda **k: list(hints)), \
                patch.object(sc, "apply_findings", mocks["apply_findings"]), \
                patch.object(sc, "apply_mcp_findings", mocks["apply_mcp_findings"]), \
                patch.object(sc, "apply_skills_symlink_hints",
                             mocks["apply_skills_symlink_hints"]), \
                patch("sys.stdin.isatty", return_value=False), \
                redirect_stdout(out):
            code = cmd_setup(args)
        return code, out.getvalue(), mocks

    def test_nothing_to_do_is_clean(self):
        code, out, _ = self._run([])
        self.assertEqual(code, 0)
        self.assertIn("quiver setup", out)
        self.assertIn("No new high-confidence harnesses", out)
        self.assertIn("No new MCP servers", out)
        self.assertIn("Nothing to apply", out)

    def test_dry_run_lists_findings_and_hints(self):
        code, out, _ = self._run(
            [],
            findings=[_finding("kiro")],
            mcp=[_mcp("linear")],
            hints=[_hint("create_shared"), _hint("manual"), _hint("ok")],
        )
        self.assertEqual(code, 0)
        self.assertIn("kiro", out)
        self.assertIn("linear", out)
        self.assertIn("diverged", out)
        self.assertIn("Dry-run only", out)

    def test_apply_runs_all_three_apply_functions(self):
        apply_mcp = Mock(return_value=MergeResult(added=["linear"], updated=[],
                                                  orphaned=[], pruned=[]))
        apply_skills = Mock(return_value=["linked"])
        apply_h = Mock(return_value=["kiro"])
        code, out, _ = self._run(
            ["--apply"],
            findings=[_finding("kiro")],
            mcp=[_mcp("linear")],
            hints=[_hint("symlink")],
            apply_map={"apply_findings": apply_h,
                       "apply_mcp_findings": apply_mcp,
                       "apply_skills_symlink_hints": apply_skills},
        )
        self.assertEqual(code, 0)
        apply_h.assert_called_once()
        apply_mcp.assert_called_once()
        apply_skills.assert_called_once()
        self.assertIn("Applied:", out)

    def test_unknown_flag_is_refused(self):
        code, out, _ = self._run(["--bogus"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown setup option", out)

    def test_section_plus_json_is_refused(self):
        code, out, _ = self._run(["skills", "--json"])
        self.assertEqual(code, 1)
        self.assertIn("interactive", out)

    def test_quick_plus_json_is_refused(self):
        code, out, _ = self._run(["--quick", "--json"])
        self.assertEqual(code, 1)

    def test_unknown_section_is_refused(self):
        code, out, _ = self._run(["bogus-section"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown setup section", out)

    def test_help_runs(self):
        code, _, _ = self._run(["--help"])
        self.assertEqual(code, 0)

    def test_interactive_prompt_declined_stays_dry(self):
        from quiver.setup import commands as sc
        with patch.object(sc, "discover_harnesses", lambda: [_finding()]), \
                patch.object(sc, "discover_mcp_servers", lambda **k: []), \
                patch.object(sc, "skills_symlink_hints", lambda **k: []), \
                patch("sys.stdin.isatty", return_value=True), \
                patch.object(sc, "read_line", return_value="n"):
            out = io.StringIO()
            with redirect_stdout(out):
                code = cmd_setup(["--non-interactive"])
        self.assertEqual(code, 0)
        self.assertIn("Dry-run only", out.getvalue())

    def test_interactive_prompt_eof_returns_130(self):
        from quiver.setup import commands as sc
        with patch.object(sc, "discover_harnesses", lambda: [_finding()]), \
                patch.object(sc, "discover_mcp_servers", lambda **k: []), \
                patch.object(sc, "skills_symlink_hints", lambda **k: []), \
                patch("sys.stdin.isatty", return_value=True), \
                patch.object(sc, "read_line", side_effect=EOFError):
            out = io.StringIO()
            with redirect_stdout(out):
                code = cmd_setup(["--non-interactive"])
        self.assertEqual(code, 130)

    def test_interactive_accept_applies(self):
        from quiver.setup import commands as sc
        apply_h = Mock(return_value=["kiro"])
        merge = MergeResult(added=["linear"], updated=[], orphaned=[], pruned=[])
        apply_m = Mock(return_value=merge)
        apply_s = Mock(return_value=["linked"])
        with patch.object(sc, "discover_harnesses", lambda: [_finding("kiro")]), \
                patch.object(sc, "discover_mcp_servers", lambda **k: [_mcp("linear")]), \
                patch.object(sc, "skills_symlink_hints", lambda **k: [_hint()]), \
                patch.object(sc, "apply_findings", apply_h), \
                patch.object(sc, "apply_mcp_findings", apply_m), \
                patch.object(sc, "apply_skills_symlink_hints", apply_s), \
                patch("sys.stdin.isatty", return_value=True), \
                patch.object(sc, "read_line", return_value="y"):
            out = io.StringIO()
            with redirect_stdout(out):
                code = cmd_setup(["--non-interactive"])
        self.assertEqual(code, 0)
        apply_h.assert_called_once()
        self.assertIn("kiro", out.getvalue())


if __name__ == "__main__":
    unittest.main()
