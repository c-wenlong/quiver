"""`swe mcp doctor`'s local checks folded into `swe doctor`, with mcp-sync."""

import json
import sys
import unittest
from pathlib import Path

from quiver.harness import doctor_fix
from quiver.harness.doctor import _mcp_local_problem

try:  # `unittest discover -s tests` puts tests/ itself on sys.path
    from _tempdir import temp_dir
    from test_json_output import decode, run_swe, write_registry
except ImportError:  # `python -m unittest tests.test_doctor_fix_mcp`
    from tests._tempdir import temp_dir
    from tests.test_json_output import decode, run_swe, write_registry


class LocalProblemTest(unittest.TestCase):
    def test_local_checks_only(self):
        self.assertIsNone(_mcp_local_problem({"command": sys.executable}))
        self.assertEqual(_mcp_local_problem({"command": "/nope/bin/x"}), "binary not found")
        self.assertIn("missing env", _mcp_local_problem(
            {"command": sys.executable, "env": {"TOKEN": ""}}))
        # An unreachable URL is swe mcp doctor's to find: no network here.
        self.assertIsNone(_mcp_local_problem({"url": "https://192.0.2.1/mcp"}))
        self.assertEqual(_mcp_local_problem({"url": "ftp://x"}), "invalid URL scheme")
        # Neither command nor url: unknown to swe mcp doctor, never a
        # copy fit to replace anything.
        self.assertIsNone(_mcp_local_problem({}))
        self.assertEqual(_mcp_local_problem({}, strict=True), "no command or url")


class McpSyncTest(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        write_registry(self.home, {"claude": {"command": "claude"}})
        (self.home / ".quiver" / "AGENTS.md").write_text("# shared\n")
        self.claude_json = self.home / ".claude.json"
        self.claude_json.write_text(json.dumps({
            "theme": "dark",
            "mcpServers": {
                "tool": {"type": "stdio", "command": "/nope/bin/tool", "args": []},
                "other": {"type": "stdio", "command": "/nope/bin/other", "args": []},
                "fine": {"type": "stdio", "command": sys.executable, "args": []},
            }}))
        (self.home / ".quiver" / "mcp.json").write_text(json.dumps({
            "mcpServers": {"tool": {"command": sys.executable, "args": ["-V"]}}}))

    def mcp(self, data):
        return {f["message"]: f for f in data["findings"] if f["check"] == "mcp.server"}

    def test_only_a_server_the_hub_holds_healthy_gets_the_sync(self):
        found = self.mcp(decode(run_swe(self.home, "doctor", "--json"))["data"])
        self.assertEqual(set(found), {"claude: tool: binary not found",
                                      "claude: other: binary not found"})
        tool = found["claude: tool: binary not found"]
        self.assertEqual(tool["action"], "mcp-sync")
        self.assertEqual((tool["remedies"][0]["tool"], tool["remedies"][0]["server"]),
                         ("claude", "tool"))
        self.assertNotIn("action", found["claude: other: binary not found"])

    def test_the_batch_never_overwrites(self):
        before = self.claude_json.read_text()
        data = decode(run_swe(self.home, "doctor", "--fix", "--json"))["data"]
        self.assertNotIn("mcp-sync", [s["action"] for s in data["ran"]])
        self.assertIn("mcp-sync", [a for f in data["skipped"] for a in f["actions"]])
        self.assertEqual(self.claude_json.read_text(), before)

    def test_a_click_replaces_only_that_entry_after_a_backup(self):
        done = run_swe(self.home, "doctor", "--fix=mcp.server",
                       "--message=claude: tool: binary not found",
                       "--action=mcp-sync", "--json")
        data = decode(done)["data"]
        self.assertEqual([(s["action"], s["error"]) for s in data["ran"]],
                         [("mcp-sync", None)], done.stderr)
        after = json.loads(self.claude_json.read_text())
        self.assertEqual(after["mcpServers"]["tool"]["command"], sys.executable)
        self.assertEqual(after["mcpServers"]["other"]["command"], "/nope/bin/other")
        self.assertEqual(after["theme"], "dark")
        backups = list((self.home / ".quiver" / "backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertIn("/nope/bin/tool", backups[0].read_text())
        self.assertIn({"check": "mcp.server", "message": "claude: tool: binary not found"},
                      data["fixed"])

    def fix(self, message):
        return run_swe(self.home, "doctor", "--fix=mcp.server", f"--message={message}",
                       "--action=mcp-sync", "--json")

    def test_a_stale_click_writes_nothing(self):
        found = self.mcp(decode(run_swe(self.home, "doctor", "--json"))["data"])
        self.assertEqual(found["claude: tool: binary not found"]["action"], "mcp-sync")
        # The entry works again before the click lands.
        data = json.loads(self.claude_json.read_text())
        data["mcpServers"]["tool"]["command"] = sys.executable
        self.claude_json.write_text(json.dumps(data))
        before = self.claude_json.read_text()
        done = self.fix("claude: tool: binary not found")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.claude_json.read_text(), before)
        self.assertFalse((self.home / ".quiver" / "backups").exists())
        self.assertIn("mcp-sync", doctor_fix.CLICK)

    def hub(self, entry):
        (self.home / ".quiver" / "mcp.json").write_text(json.dumps({"mcpServers": {"tool": entry}}))

    def test_a_hub_copy_that_cannot_run_offers_nothing(self):
        for entry in ({}, {"command": sys.executable, "env": {"TOKEN": "${NOT_STORED}"}},
                      {"command": "/nope/bin/also"}):
            self.hub(entry)
            found = self.mcp(decode(run_swe(self.home, "doctor", "--json"))["data"])
            self.assertNotIn("action", found["claude: tool: binary not found"], entry)

    def test_a_symlinked_config_backs_up_its_contents(self):
        real = self.home / "dotfiles-claude.json"
        real.write_text(self.claude_json.read_text())
        self.claude_json.unlink()
        self.claude_json.symlink_to(real)
        done = self.fix("claude: tool: binary not found")
        self.assertEqual(decode(done)["data"]["ran"][0]["error"], None, done.stderr)
        (backup,) = (self.home / ".quiver" / "backups").iterdir()
        self.assertFalse(backup.is_symlink())
        self.assertIn("/nope/bin/tool", backup.read_text())
        self.assertEqual(json.loads(real.read_text())["mcpServers"]["tool"]["command"],
                         sys.executable)

    def test_an_unreadable_config_says_so(self):
        self.claude_json.write_text("{ not json")
        data = decode(run_swe(self.home, "doctor", "--json"))["data"]
        checks = [f["check"] for f in data["findings"] if f["check"].startswith("mcp.")]
        self.assertEqual(checks, ["mcp.unchecked"])

    def test_the_copied_command_quotes_the_name(self):
        data = json.loads(self.claude_json.read_text())
        data["mcpServers"]["my tool"] = {"type": "stdio", "command": "/nope/x", "args": []}
        self.claude_json.write_text(json.dumps(data))
        self.hub({"command": sys.executable})
        hub = json.loads((self.home / ".quiver" / "mcp.json").read_text())
        hub["mcpServers"]["my tool"] = {"command": sys.executable}
        (self.home / ".quiver" / "mcp.json").write_text(json.dumps(hub))
        found = self.mcp(decode(run_swe(self.home, "doctor", "--json"))["data"])
        self.assertIn("'--only=my tool'", found["claude: my tool: binary not found"]["fix"])


if __name__ == "__main__":
    unittest.main()
