"""Harnesses this machine neither registered nor installed stay out of the report.

quiver knows where nine harnesses read instructions and where a dozen keep
skills, but a machine that uninstalled one should not be told about it on
every `swe init`, nor warned about it by `swe doctor`.
"""

import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from quiver.harness.drift import check_code_vs_data
from quiver.init import commands as init_commands


def _home(tmp: str, registry: dict | None) -> Path:
    home = Path(tmp)
    (home / ".quiver").mkdir()
    (home / ".claude").mkdir()
    if registry is not None:
        cfg = home / ".quiver" / "config"
        cfg.mkdir()
        (cfg / "harness.json").write_text(json.dumps(registry))
    return home


def _init(home: Path) -> str:
    out = StringIO()
    with mock.patch.object(Path, "home", staticmethod(lambda: home)), redirect_stdout(out):
        init_commands.cmd_init(["--check", "--full"])
    return re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue())


class InitUnregisteredTest(unittest.TestCase):
    def test_uninstalled_and_unregistered_target_is_not_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _init(_home(tmp, {"claude": {}}))
            self.assertIn("~/.claude/CLAUDE.md", out)
            self.assertNotIn("~/.gemini/GEMINI.md", out)
            self.assertNotRegex(out, r"Instructions\s.*skipped")

    def test_registered_but_uninstalled_target_still_shows_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _init(_home(tmp, {"claude": {}, "gemini": {}}))
            self.assertRegex(out, r"skipped\s+~/\.gemini/GEMINI\.md")

    def test_registry_alias_counts_as_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _init(_home(tmp, {"qwen-code": {}}))
            self.assertRegex(out, r"skipped\s+~/\.qwen/QWEN\.md")

    def test_installed_but_unregistered_target_is_still_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp, {})
            (home / ".codex").mkdir()
            out = _init(home)
            self.assertIn("~/.codex/AGENTS.md", out)


class DoctorUnregisteredTest(unittest.TestCase):
    TABLE = (("ghost", Path(".ghost/plugins")),)

    def test_absent_root_for_unregistered_harness_is_quiet_with_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                check_code_vs_data({}, plugin_roots=self.TABLE, home=Path(tmp)), []
            )

    def test_root_on_disk_for_unregistered_harness_still_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".ghost/plugins").mkdir(parents=True)
            findings = check_code_vs_data({}, plugin_roots=self.TABLE, home=Path(tmp))
            self.assertEqual(len(findings), 1)
            self.assertIn("no such harness", findings[0].message)


if __name__ == "__main__":
    unittest.main()
