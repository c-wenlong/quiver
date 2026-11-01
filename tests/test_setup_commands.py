"""cmd_setup: the non-interactive print path and argument refusals."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from swe.harness.discover import HarnessFinding
from swe.mcp.discover import MergeResult, McpFinding
from swe.setup.commands import cmd_setup
from swe.skills.symlinks import SkillsSymlinkHint


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


class _SetupHarness(unittest.TestCase):
    def setUp(self):
        # --apply now links into the home it finds, so every run gets its own.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        home_patch = patch.object(Path, "home", staticmethod(lambda: self.home))
        home_patch.start()
        self.addCleanup(home_patch.stop)
        # PATH is stubbed so the developer's own installs cannot leak in: a
        # binary that resolves is install evidence (#68), and every harness
        # in these tests is meant to be absent until its dir is made by hand.
        which_patch = patch("swe.init.layout.shutil.which", return_value=None)
        which_patch.start()
        self.addCleanup(which_patch.stop)

    def _run(self, args, findings=(), mcp=(), hints=(), apply_map=None):
        """Run cmd_setup with discovery mocked and stdin forced non-tty."""
        from swe.setup import commands as sc
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

class SetupCommandTest(_SetupHarness):
    def test_nothing_to_do_is_clean(self):
        code, out, _ = self._run([])
        self.assertEqual(code, 0)
        self.assertIn("swe setup", out)
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
        from swe.setup import commands as sc
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
        from swe.setup import commands as sc
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
        from swe.setup import commands as sc
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


class SetupLinksTest(_SetupHarness):
    """--apply links AGENTS.md and skills; --adopt starts AGENTS.md from a file."""

    def _json(self, args):
        code, out, _ = self._run(args + ["--json"])
        self.assertEqual(code, 0, out)
        return json.loads(out)["data"]

    def test_apply_on_a_blank_home_seeds_and_links(self):
        (self.home / ".claude").mkdir()
        applied = self._json(["--apply"])["applied"]

        agents = self.home / ".swe" / "AGENTS.md"
        self.assertTrue(agents.is_file())
        self.assertTrue((self.home / ".swe" / ".gitignore").is_file())
        claude_md = self.home / ".claude" / "CLAUDE.md"
        self.assertTrue(claude_md.is_symlink())
        self.assertEqual(claude_md.resolve(), agents.resolve())
        rows = applied["links"]["rows"]
        self.assertIn({"kind": "instructions", "harness": "claude",
                       "path": str(claude_md), "result": "linked", "detail": ""}, rows)
        self.assertIsNone(applied["links"]["adopted"])

    def test_apply_leaves_a_real_instruction_file_alone(self):
        (self.home / ".claude").mkdir()
        claude_md = self.home / ".claude" / "CLAUDE.md"
        claude_md.write_text("my own rules\n")
        applied = self._json(["--apply"])["applied"]

        self.assertFalse(claude_md.is_symlink())
        self.assertEqual(claude_md.read_text(), "my own rules\n")
        results = {r["path"]: r["result"] for r in applied["links"]["rows"]}
        self.assertEqual(results[str(claude_md)], "blocked")

    def test_preview_offers_real_instruction_files_to_adopt(self):
        (self.home / ".claude").mkdir()
        claude_md = self.home / ".claude" / "CLAUDE.md"
        claude_md.write_text("my own rules\n")
        preview = self._json([])["instructions"]

        self.assertFalse(preview["agents_file_exists"])
        self.assertFalse(preview["agents_file_customised"])
        self.assertEqual(preview["adopt_candidates"],
                         [{"harness": "claude", "path": str(claude_md)}])
        self.assertFalse((self.home / ".swe" / "AGENTS.md").exists(),
                         "the preview must not write")

    def test_adopt_starts_agents_md_from_the_file_and_links_it(self):
        (self.home / ".claude").mkdir()
        claude_md = self.home / ".claude" / "CLAUDE.md"
        claude_md.write_text("my own rules\n")
        applied = self._json(["--apply", f"--adopt={claude_md}"])["applied"]

        agents = self.home / ".swe" / "AGENTS.md"
        self.assertEqual(agents.read_text(), "my own rules\n")
        self.assertTrue(claude_md.is_symlink())
        self.assertEqual(claude_md.read_text(), "my own rules\n")
        self.assertEqual(applied["links"]["adopted"], str(claude_md))
        backups = list((self.home / ".swe" / "backups").rglob("*CLAUDE.md*"))
        self.assertTrue(backups, "the original is backed up before it becomes a link")

    def test_adopt_is_refused_once_agents_md_has_your_own_text(self):
        (self.home / ".swe").mkdir()
        (self.home / ".swe" / "AGENTS.md").write_text("already mine\n")
        source = self.home / "rules.md"
        source.write_text("other rules\n")
        code, out, mocks = self._run(["--apply", f"--adopt={source}"])

        self.assertEqual(code, 1)
        self.assertIn("already has your own text", out)
        mocks["apply_findings"].assert_not_called()
        self.assertEqual((self.home / ".swe" / "AGENTS.md").read_text(), "already mine\n")

    def test_starred_sets_exactly_the_ticked_harnesses(self):
        from swe.harness import registry
        reg = {"claude": {"command": "claude"}, "codex": {"command": "codex"},
               "droid": {"command": "droid", "state": "starred", "pin": 1}}
        saved = {}
        with patch.object(registry, "load_registry", lambda: saved.get("reg", reg)), \
                patch.object(registry, "save_registry",
                             lambda tools: saved.__setitem__("reg", tools)):
            applied = self._json(["--apply", "--starred=codex,claude,qwen-code,nonesuch"])["applied"]
        final = saved["reg"]
        self.assertEqual(applied["starred"], {"starred": ["codex", "claude", "qwen-code"],
                                              "registered": ["qwen-code"],
                                              "unknown": ["nonesuch"]})
        self.assertEqual(final["codex"]["state"], "starred")
        self.assertEqual(final["codex"]["pin"], 1)
        self.assertEqual(final["claude"]["pin"], 2)
        self.assertEqual(final["qwen-code"]["command"], "qwen")
        self.assertNotEqual(final["droid"].get("state"), "starred")

    def test_a_linking_failure_exits_nonzero_and_keeps_what_was_applied(self):
        (self.home / ".swe" / ".linkignore").mkdir(parents=True)
        code, out, mocks = self._run(["--apply", "--json"])

        self.assertEqual(code, 1)
        applied = json.loads(out)["data"]["applied"]
        self.assertIn("cannot read", applied["links"]["error"])
        self.assertEqual(applied["harness"], [])
        mocks["apply_findings"].assert_not_called()  # nothing new was found

    def test_an_apply_failure_still_emits_the_json_envelope(self):
        # Regression: a filesystem error inside the apply calls escaped as a
        # traceback before emit(), so the --json runs most likely to fail
        # were exactly the ones that never produced a SetupApplied reply.
        code, out, _ = self._run(
            ["--apply", "--json"],
            findings=[_finding("kiro")],
            mcp=[_mcp("linear")],
            apply_map={
                "apply_findings": Mock(return_value=(["kiro"], [])),
                "apply_mcp_findings": Mock(side_effect=OSError("disk full")),
            },
        )

        self.assertEqual(code, 1)
        applied = json.loads(out)["data"]["applied"]
        # The step that succeeded is still reported, the failed one is
        # named in errors, and the links block is present as always.
        self.assertEqual(applied["harness"], ["kiro"])
        self.assertEqual(applied["mcp"], [])
        self.assertIn("disk full", " ".join(applied["errors"]))
        self.assertIn("mcp", applied["errors"][0])
        self.assertIn("rows", applied["links"])

    def test_a_discovery_failure_still_emits_the_json_envelope(self):
        # A crash before any apply step ran (unwritable config dir, corrupt
        # registry) must still answer --json with a well-formed envelope.
        from swe.setup import commands as sc

        for extra, key in ((["--apply"], "applied"), ([], "instructions")):
            with self.subTest(extra=extra):
                with patch.object(
                    sc, "discover_harnesses",
                    Mock(side_effect=OSError("config dir gone")),
                ), patch("sys.stdin.isatty", return_value=False):
                    out = io.StringIO()
                    with redirect_stdout(out):
                        code = cmd_setup(extra + ["--json"])
                self.assertEqual(code, 1)
                payload = json.loads(out.getvalue())["data"]
                self.assertIn("config dir gone", str(payload[key]))

    def test_starred_failure_lands_in_errors_not_a_traceback(self):
        # Happy path must not invent an errors key. The registry and stars
        # live under module-level paths that Path.home() cannot redirect,
        # so load/save are mocked — without this the test would rewrite
        # the developer's real ~/.swe/config/harness.json.
        from swe.harness import registry

        saved = {}
        with patch.object(registry, "load_registry", lambda: {}), \
                patch.object(registry, "save_registry",
                             lambda tools: saved.__setitem__("reg", tools)):
            code, out, _ = self._run(["--apply", "--json", "--starred=claude"])
        self.assertEqual(code, 0)
        self.assertNotIn("errors", json.loads(out)["data"]["applied"])

    def test_starred_failure_is_reported_in_errors(self):
        from swe.setup import commands as sc

        with patch.object(
            sc, "_set_starred", Mock(side_effect=OSError("registry locked"))
        ):
            code, out, _ = self._run(["--apply", "--json", "--starred=claude"])
        self.assertEqual(code, 1)
        applied = json.loads(out)["data"]["applied"]
        self.assertIn("starred: registry locked", applied["errors"])
        self.assertNotIn("starred", applied)

    def test_preview_linking_failure_is_an_error_field_not_a_traceback(self):
        from swe.setup import commands as sc

        err = io.StringIO()
        with patch.object(
            sc, "run_init", Mock(side_effect=OSError("linkignore unreadable"))
        ), redirect_stderr(err):
            code, out, _ = self._run(["--json"])
        # A failed preview is a failed run: exit 1 and a stderr line, so a
        # caller that only reads failures (the app throws on nonzero exit)
        # still sees what went wrong.
        self.assertEqual(code, 1)
        instructions = json.loads(out)["data"]["instructions"]
        self.assertEqual(instructions["error"], "linkignore unreadable")
        self.assertIn("linkignore unreadable", err.getvalue())

    def test_eof_and_interrupt_still_propagate_through_every_guard(self):
        # The widened guards report Exception but must never swallow
        # Ctrl-D / Ctrl-C.
        import contextlib

        from swe.setup import commands as sc

        none = MergeResult(added=[], updated=[], orphaned=[], pruned=[])
        quiet = {
            "discover_harnesses": lambda: [],
            "discover_mcp_servers": lambda **k: [],
            "skills_symlink_hints": lambda **k: [],
            "apply_findings": Mock(return_value=([], [])),
            "apply_mcp_findings": Mock(return_value=none),
            "apply_skills_symlink_hints": Mock(return_value=[]),
        }
        cases = [
            # (extra patches, argv, exception that must reach the caller)
            ({"discover_harnesses": Mock(side_effect=EOFError)},
             ["--json"], EOFError),
            # Non-JSON discovery failures re-raise too: only the JSON
            # contract owes the app an envelope instead of a traceback.
            ({"discover_harnesses": Mock(side_effect=OSError)},
             [], OSError),
            ({"discover_harnesses": lambda: [_finding()],
              "apply_findings": Mock(side_effect=EOFError)},
             ["--apply", "--json"], EOFError),
            ({"_set_starred": Mock(side_effect=KeyboardInterrupt)},
             ["--apply", "--json", "--starred=claude"], KeyboardInterrupt),
            ({"run_init": Mock(side_effect=KeyboardInterrupt)},
             ["--apply", "--json"], KeyboardInterrupt),
            ({"run_init": Mock(side_effect=EOFError)},
             ["--json"], EOFError),
        ]
        for extra, argv, exc_type in cases:
            with self.subTest(argv=argv, extra=sorted(extra)):
                patches = dict(quiet)
                patches.update(extra)
                stack = [
                    patch.object(sc, key, mock) for key, mock in patches.items()
                ]
                stack.append(patch("sys.stdin.isatty", return_value=False))
                with contextlib.ExitStack() as es:
                    for p in stack:
                        es.enter_context(p)
                    with self.assertRaises(exc_type):
                        cmd_setup(argv)

    def test_interactive_accept_apply_failure_reports_instead_of_traceback(self):
        from swe.setup import commands as sc

        with patch.object(sc, "discover_harnesses", lambda: [_finding("kiro")]), \
                patch.object(sc, "discover_mcp_servers", lambda **k: []), \
                patch.object(sc, "skills_symlink_hints", lambda **k: []), \
                patch.object(
                    sc, "apply_findings",
                    Mock(side_effect=OSError("disk full")),
                ), \
                patch("sys.stdin.isatty", return_value=True), \
                patch.object(sc, "read_line", return_value="y"):
            out = io.StringIO()
            with redirect_stdout(out):
                code = cmd_setup(["--non-interactive"])
        self.assertEqual(code, 1)
        self.assertIn("stopped part way", out.getvalue())
        self.assertIn("disk full", out.getvalue())

    def test_interactive_accept_eof_still_propagates(self):
        # The apply-step guard re-raises Ctrl-D/Ctrl-C instead of
        # reporting it as an apply failure.
        from swe.setup import commands as sc

        with patch.object(sc, "discover_harnesses", lambda: [_finding("kiro")]), \
                patch.object(sc, "discover_mcp_servers", lambda **k: []), \
                patch.object(sc, "skills_symlink_hints", lambda **k: []), \
                patch.object(
                    sc, "apply_findings", Mock(side_effect=KeyboardInterrupt),
                ), \
                patch("sys.stdin.isatty", return_value=True), \
                patch.object(sc, "read_line", return_value="y"):
            with redirect_stdout(io.StringIO()):
                self.assertRaises(KeyboardInterrupt, cmd_setup,
                                  ["--non-interactive"])

    def test_an_apply_failure_exits_1_and_says_so_in_text_mode(self):
        code, out, _ = self._run(
            ["--apply"],
            mcp=[_mcp("linear")],
            apply_map={
                "apply_mcp_findings": Mock(side_effect=OSError("disk full")),
            },
        )

        self.assertEqual(code, 1)
        self.assertIn("disk full", out)
        self.assertIn("run swe setup --apply again", out)

    def test_text_apply_reports_links_and_a_linking_failure(self):
        (self.home / ".claude").mkdir()
        claude_md = self.home / ".claude" / "CLAUDE.md"
        claude_md.write_text("my own rules\n")
        code, out, _ = self._run(["--apply", f"--adopt={claude_md}"])
        self.assertEqual(code, 0)
        self.assertIn("link(s)", out)
        self.assertIn("AGENTS.md started from", out)

        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "AGENTS.md").write_text("other rules\n")
        code, out, _ = self._run(["--apply"])
        self.assertEqual(code, 0)
        self.assertIn("left alone", out)

        (self.home / ".swe" / ".linkignore").unlink()
        (self.home / ".swe" / ".linkignore").mkdir()
        code, out, _ = self._run(["--apply"])
        self.assertEqual(code, 1)
        self.assertIn("Nothing was linked", out)

    def test_preview_leaves_out_files_adopt_would_refuse(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "CLAUDE.md").write_bytes(b"\xff\xfe not text")
        preview = self._json([])["instructions"]
        self.assertEqual(preview["adopt_candidates"], [])

    def test_starred_needs_apply(self):
        code, out, _ = self._run(["--starred=claude"])
        self.assertEqual(code, 1)
        self.assertIn("--starred only works with --apply", out)

    def test_adopt_needs_apply(self):
        source = self.home / "rules.md"
        source.write_text("rules\n")
        code, out, _ = self._run([f"--adopt={source}"])
        self.assertEqual(code, 1)
        self.assertIn("--adopt only works with --apply", out)


if __name__ == "__main__":
    unittest.main()
