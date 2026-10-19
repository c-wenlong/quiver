"""`swe doctor`'s setup checks: hub, AGENTS.md, registry, unlinked."""

import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from quiver.harness.doctor import setup_findings


class SetupFindingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def hub(self):
        quiver = self.home / ".quiver"
        quiver.mkdir(exist_ok=True)
        return quiver

    def findings(self, registry, states=None):
        (self.home / "harness.json").write_text(json.dumps(registry))
        with mock.patch("quiver.harness.registry.HARNESS_FILE",
                        self.home / "harness.json"), \
             mock.patch("quiver.init.layout.link_states",
                        return_value=states or {}):
            return setup_findings(self.home)

    def test_no_hub_reports_only_hub(self):
        got = self.findings({})
        self.assertEqual([f.check for f in got], ["setup.hub"])
        self.assertEqual(got[0].action, "setup")

    def test_hub_without_agents_md(self):
        self.hub()
        got = self.findings({})
        self.assertEqual([f.check for f in got], ["setup.agents-md"])
        self.assertEqual(got[0].fix, "swe setup --apply")

    def test_empty_registry_reports_registry_not_unlinked(self):
        (self.hub() / "AGENTS.md").write_text("# shared\n")
        got = self.findings({})
        self.assertEqual([f.check for f in got], ["setup.registry"])
        self.assertEqual(got[0].action, "discover")

    def test_one_unlinked_one_linked(self):
        (self.hub() / "AGENTS.md").write_text("# shared\n")
        registry = {"claude": {"command": "claude", "state": "active"},
                    "codex": {"command": "codex", "state": "active"}}
        states = {"claude": {"agents": "create", "skills": "linked"},
                  "codex": {"agents": "linked", "skills": "linked"}}
        got = self.findings(registry, states)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].check, "setup.unlinked")
        self.assertEqual(got[0].harness, "claude")
        self.assertEqual(got[0].action, "init")
        self.assertIn("the shared AGENTS.md", got[0].message)

    def test_archived_and_keep_are_ignored(self):
        (self.hub() / "AGENTS.md").write_text("# shared\n")
        registry = {"old": {"command": "old", "state": "archived"},
                    "keep": {"command": "keep"}}
        states = {"old": {"agents": "create"}, "keep": {"agents": "keep"}}
        self.assertEqual(self.findings(registry, states), [])

    def test_json_carries_action(self):
        self.assertEqual(self.findings({})[0].to_json()["action"], "setup")

    def test_warnings_keep_exit_zero(self):
        from quiver.harness.commands import cmd_doctor

        env = mock.Mock(node="/usr/bin/node", npm="/usr/bin/npm",
                        node_version="22", npm_version="10",
                        global_prefix="/usr/local",
                        global_bin="/usr/local/bin", global_bin_on_path=True)
        (self.hub() / "AGENTS.md").write_text("# shared\n")
        (self.home / "harness.json").write_text("{}")
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"HOME": str(self.home)}), \
             mock.patch("quiver.harness.commands.load_registry", return_value={}), \
             mock.patch("quiver.harness.path_health.probe_node_env", return_value=env), \
             mock.patch("quiver.harness.path_health.preferred_npm_bin", return_value="/usr/bin/npm"), \
             mock.patch("quiver.harness.path_health.nvm_bin_dirs", return_value=[]), \
             mock.patch("quiver.harness.path_health.find_off_path_tools", return_value=[]), \
             mock.patch("quiver.harness.commands.shutil.which", return_value="/usr/bin/npm"), \
             mock.patch("quiver.harness.registry.HARNESS_FILE", self.home / "harness.json"), \
             contextlib.redirect_stdout(out):
            self.assertEqual(cmd_doctor(["--json"]), 0)
        doc = json.loads(out.getvalue())
        checks = {f["check"] for f in doc["data"]["findings"]}
        self.assertIn("setup.registry", checks)


if __name__ == "__main__":
    unittest.main()
