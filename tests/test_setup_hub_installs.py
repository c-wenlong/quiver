"""`swe setup --hub`: the hub's MCP servers and plugins reach each harness
with no manual step (#524).

No network: the hubs are local fixture repositories, and ``claude`` and
``codex`` are either a stubbed ``run`` or small fake CLIs on PATH that
write the files the real ones do.
"""

import copy
import io
import json
import shutil
import subprocess
import sys
import tomllib
import unittest
from contextlib import ExitStack, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from swe.console import strip_ansi
from swe.init import hub as hub_repo
from swe.mcp import cli as mcp_cli
from swe.mcp.secrets import set_secret
from tests._tempdir import temp_dir
from tests.test_init_hub import _git
from tests.test_setup_hub import _HubHarness

GIT = shutil.which("git")
SRC = Path(__file__).resolve().parents[1] / "src"

HUB_SERVERS = {
    "context7": {"command": "npx", "args": ["-y", "@upstash/context7-mcp"]},
    "github": {"url": "https://api.githubcopilot.com/mcp/",
               "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"}},
}


def _write_hub_mcp(home: Path, servers=None) -> None:
    path = home / ".swe" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": copy.deepcopy(servers or HUB_SERVERS)}))


class _McpHome(unittest.TestCase):
    """A home with Claude Code and Codex installed, and the MCP module's
    import-time paths pointed into it."""

    def setUp(self):
        self.home = temp_dir(self)
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        self.claude_json = self.home / ".claude.json"
        self.codex_toml = self.home / ".codex" / "config.toml"
        mcp_map = copy.deepcopy(mcp_cli.MCP_CONFIG_MAP)
        mcp_map["claude"]["path"] = self.claude_json
        mcp_map["codex"]["path"] = self.codex_toml
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(Path, "home", staticmethod(lambda: self.home)))
        stack.enter_context(patch.dict(mcp_cli.MCP_CONFIG_MAP, mcp_map))
        stack.enter_context(patch.object(mcp_cli, "MCP_SOURCE_FILE",
                                         self.home / ".swe" / "mcp.json"))
        stack.enter_context(patch.object(mcp_cli, "load_registry", lambda: {}))
        # Nothing on PATH counts as installed: only the folders made above.
        stack.enter_context(patch("swe.init.layout.shutil.which", return_value=None))

    def claude_servers(self) -> dict:
        return json.loads(self.claude_json.read_text()).get("mcpServers", {})

    def codex_servers(self) -> dict:
        return tomllib.loads(self.codex_toml.read_text()).get("mcp_servers", {})


class McpTargetsTest(_McpHome):
    def test_installed_harnesses_with_a_known_mcp_config(self):
        self.assertEqual(hub_repo.mcp_targets(self.home), ["claude", "codex"])

    def test_an_archived_harness_is_left_out(self):
        config = self.home / ".swe" / "config"
        config.mkdir(parents=True)
        (config / "harness.json").write_text(json.dumps({"codex": {"state": "archived"}}))
        self.assertEqual(hub_repo.mcp_targets(self.home), ["claude"])

    def test_a_harness_that_is_not_installed_is_left_out(self):
        shutil.rmtree(self.home / ".codex")
        self.assertEqual(hub_repo.mcp_targets(self.home), ["claude"])


class SyncHubMcpTest(_McpHome):
    def test_servers_reach_claude_and_codex_and_a_missing_secret_skips_only_its_server(self):
        _write_hub_mcp(self.home)
        rows = hub_repo.sync_hub_mcp(self.home)
        fix = "no value for GITHUB_TOKEN; set it with: swe mcp secret set GITHUB_TOKEN"
        self.assertEqual(sorted((r["harness"], r["name"], r["skipped"], r["error"], r["needs"])
                                for r in rows), [
            ("claude", "context7", None, None, []),
            ("claude", "github", fix, None, ["GITHUB_TOKEN"]),
            ("codex", "context7", None, None, []),
            ("codex", "github", fix, None, ["GITHUB_TOKEN"]),
        ])
        self.assertEqual(self.claude_servers()["context7"]["command"], "npx")
        self.assertNotIn("github", self.claude_servers())
        self.assertEqual(self.codex_servers()["context7"]["command"], "npx")
        self.assertNotIn("github", self.codex_servers())
        # A literal ${GITHUB_TOKEN} never reaches a harness.
        self.assertNotIn("GITHUB_TOKEN", self.claude_json.read_text() + self.codex_toml.read_text())

    def test_with_the_secret_set_both_sync_and_no_row_carries_its_value(self):
        _write_hub_mcp(self.home)
        set_secret("GITHUB_TOKEN", "ghp_secretvalue", home=self.home)
        rows = hub_repo.sync_hub_mcp(self.home)
        self.assertTrue(all(r["skipped"] is None and r["error"] is None for r in rows), rows)
        self.assertEqual(len(rows), 4)
        self.assertIn("github", self.claude_servers())
        self.assertNotIn("ghp_secretvalue", json.dumps(rows))

    def test_a_server_the_harness_already_has_is_left_as_it_is(self):
        _write_hub_mcp(self.home, {"context7": HUB_SERVERS["context7"]})
        self.claude_json.write_text(json.dumps(
            {"mcpServers": {"context7": {"command": "mine"}}, "other": 1}))
        rows = hub_repo.sync_hub_mcp(self.home)
        claude = [r for r in rows if r["harness"] == "claude"]
        self.assertEqual(len(claude), 1)
        self.assertIn("already has a server by this name", claude[0]["skipped"])
        self.assertEqual(self.claude_servers()["context7"], {"command": "mine"})
        self.assertEqual(json.loads(self.claude_json.read_text())["other"], 1)

    def test_one_broken_harness_config_is_a_row_and_the_others_still_sync(self):
        _write_hub_mcp(self.home, {"context7": HUB_SERVERS["context7"]})
        self.claude_json.write_text("{not json")
        with redirect_stderr(io.StringIO()):
            rows = hub_repo.sync_hub_mcp(self.home)
        by_harness = {r["harness"]: r for r in rows}
        self.assertTrue(by_harness["claude"]["error"])
        self.assertIsNone(by_harness["codex"]["error"])
        self.assertIn("context7", self.codex_servers())
        self.assertEqual(self.claude_json.read_text(), "{not json")

    def test_a_hub_with_no_servers_does_nothing(self):
        self.assertEqual(hub_repo.sync_hub_mcp(self.home), [])
        self.assertFalse(self.claude_json.exists())

    def test_plain_mcp_sync_still_refuses_on_a_missing_secret(self):
        _write_hub_mcp(self.home)
        out = io.StringIO()
        with patch("sys.stdout", out):
            code = mcp_cli._sync(["swe", "claude", "--no-interactive"], None)
        self.assertEqual(code, 1)
        self.assertIn("Not syncing: no value for GITHUB_TOKEN (used by github)", out.getvalue())
        self.assertFalse(self.claude_json.exists())


def _market(hub: Path, plugins=("git-helpers",)) -> Path:
    """A ``starter`` marketplace in the hub folder ``hub``."""
    market = hub / "plugins" / "starter"
    (market / ".claude-plugin").mkdir(parents=True)
    (market / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
        {"name": "starter", "plugins": [{"name": p, "source": f"./{p}"} for p in plugins]}))
    for plugin in plugins:
        (market / plugin / ".claude-plugin").mkdir(parents=True)
        (market / plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": plugin, "version": "0.1.0"}))
    return market


class InstallHubPluginsTest(unittest.TestCase):
    """The harness CLIs are a stubbed ``run`` that writes what they write."""

    def setUp(self):
        self.home = temp_dir(self)
        self.market = _market(self.home / ".swe", ("git-helpers", "lint"))
        plugins = self.home / ".claude" / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "known_marketplaces.json").write_text(json.dumps(
            {"starter": {"source": {"source": "directory", "path": str(self.market)}}}))
        self.installed = plugins / "installed_plugins.json"
        self.installed.write_text(json.dumps({"version": 2, "plugins": {}}))
        (self.home / ".codex").mkdir()
        self.codex_toml = self.home / ".codex" / "config.toml"
        self.codex_toml.write_text(
            f'[marketplaces.starter]\nsource_type = "local"\nsource = "{self.market}"\n')
        self.ran = []
        self.fail = set()

    def run_cli(self, argv, **_kw):
        self.ran.append(argv)
        harness, ref = argv[0], argv[-1]
        if (harness, ref) in self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "network is down")
        name, market = ref.split("@")
        if harness == "claude":
            copy_dir = self.home / ".claude" / "plugins" / "cache" / market / name / "0.1.0"
            copy_dir.mkdir(parents=True)
            data = json.loads(self.installed.read_text())
            data["plugins"][ref] = [{"scope": "user", "installPath": str(copy_dir),
                                     "version": "0.1.0"}]
            self.installed.write_text(json.dumps(data))
        else:
            (self.home / ".codex" / "plugins" / "cache" / market / name / "0.1.0").mkdir(parents=True)
            with self.codex_toml.open("a") as fh:
                fh.write(f'\n[plugins."{ref}"]\nenabled = true\n')
        return subprocess.CompletedProcess(argv, 0, "installed", "")

    def rows(self):
        return [(r["harness"], r["name"], r["marketplace"], r["skipped"], r["error"])
                for r in hub_repo.install_hub_plugins(self.home, run=self.run_cli)]

    def test_every_listed_plugin_is_installed_in_every_harness_that_registered_it(self):
        self.assertEqual(self.rows(), [
            ("claude", "git-helpers", "starter", None, None),
            ("claude", "lint", "starter", None, None),
            ("codex", "git-helpers", "starter", None, None),
            ("codex", "lint", "starter", None, None),
        ])
        self.assertEqual(self.ran, [
            ["claude", "plugin", "install", "git-helpers@starter"],
            ["claude", "plugin", "install", "lint@starter"],
            ["codex", "plugin", "add", "git-helpers@starter"],
            ["codex", "plugin", "add", "lint@starter"],
        ])

    def test_an_installed_plugin_is_skipped_without_running_anything(self):
        self.run_cli(["claude", "plugin", "install", "git-helpers@starter"])
        self.ran.clear()
        rows = self.rows()
        self.assertIn(("claude", "git-helpers", "starter", "already installed", None), rows)
        self.assertNotIn(["claude", "plugin", "install", "git-helpers@starter"], self.ran)

    def test_one_failing_plugin_is_reported_and_the_rest_still_install(self):
        self.fail.add(("claude", "git-helpers@starter"))
        rows = self.rows()
        failed = [r for r in rows if r[4]]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0][:2], ("claude", "git-helpers"))
        self.assertIn("network is down", failed[0][4])
        self.assertEqual(len([r for r in rows if not r[3] and not r[4]]), 3)

    def test_a_marketplace_a_harness_did_not_register_installs_nothing_there(self):
        self.codex_toml.write_text("")
        self.assertEqual({r[0] for r in self.rows()}, {"claude"})

    def test_a_missing_cli_is_a_row(self):
        def missing(argv, **_kw):
            raise FileNotFoundError(argv[0])
        rows = hub_repo.install_hub_plugins(self.home, run=missing)
        self.assertEqual({r["error"] for r in rows}, {"claude is not on PATH", "codex is not on PATH"})


PLUGIN_ROWS = [
    {"harness": "claude", "name": "git-helpers", "marketplace": "starter",
     "skipped": None, "error": None},
    {"harness": "codex", "name": "git-helpers", "marketplace": "starter",
     "skipped": None, "error": None},
    {"harness": "codex", "name": "lint", "marketplace": "starter",
     "skipped": "already installed", "error": None},
    {"harness": "claude", "name": "lint", "marketplace": "starter",
     "skipped": None, "error": "claude plugin install lint@starter failed with exit status 1:\nboom"},
]
FIX = "no value for GITHUB_TOKEN; set it with: swe mcp secret set GITHUB_TOKEN"
MCP_ROWS = [
    {"harness": "claude", "name": "context7", "skipped": None, "error": None, "needs": []},
    {"harness": "codex", "name": "context7", "skipped": None, "error": None, "needs": []},
    {"harness": "claude", "name": "github", "skipped": FIX, "error": None, "needs": ["GITHUB_TOKEN"]},
    {"harness": "codex", "name": "github", "skipped": FIX, "error": None, "needs": ["GITHUB_TOKEN"]},
]


@unittest.skipUnless(GIT, "git not installed")
class SetupHubReportsInstallsTest(_HubHarness):
    def setUp(self):
        super().setUp()
        for name, rows in (("install_hub_plugins", PLUGIN_ROWS), ("sync_hub_mcp", MCP_ROWS)):
            patcher = patch(f"swe.init.hub.{name}", return_value=copy.deepcopy(rows))
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def _hub(self):
        from tests.test_init_hub import _hub_repo
        return _hub_repo(self.scratch)

    def test_json_carries_the_rows_and_a_failed_plugin_does_not_fail_setup(self):
        src = self._hub()
        for extra in ([], ["--apply"]):
            with self.subTest(extra=extra):
                shutil.rmtree(self.home / ".swe", ignore_errors=True)
                code, out, err = self._run_json([*extra, f"--hub={src}", "--non-interactive"])
                self.assertEqual(code, 0, err)
                cloned = json.loads(out)["data"]["hub"]["cloned"]
                self.assertEqual(set(cloned), {"url", "path", "marketplaces", "plugins", "mcp"})
                self.assertEqual(cloned["plugins"], PLUGIN_ROWS)
                self.assertEqual(cloned["mcp"], MCP_ROWS)
        self.install_hub_plugins.assert_called_with(self.home)
        self.sync_hub_mcp.assert_called_with(self.home)

    def test_text_mode_says_what_was_installed_and_what_waits_on_a_secret(self):
        code, out, _ = self._run(["--apply", f"--hub={self._hub()}"])
        self.assertEqual(code, 0, out)
        out = strip_ansi(out)
        self.assertIn("✓ git-helpers@starter installed in claude, codex", out)
        self.assertIn("· lint@starter already installed in codex", out)
        # The CLI's own words stay on the line, folded onto it.
        self.assertIn("! lint@starter not installed in claude: claude plugin install "
                      "lint@starter failed with exit status 1: boom\n", out)
        self.assertIn("✓ context7 synced to claude, codex", out)
        self.assertIn(f"! github not synced: {FIX}", out)
        self.assertIn("then: swe mcp sync swe claude codex --only=github", out)

    def test_the_dry_run_says_what_hub_would_do(self):
        shutil.rmtree(self.home / ".swe", ignore_errors=True)
        _, out, _ = self._run([])
        self.assertIn("installs its plugins and syncs its MCP servers", out)
        self.install_hub_plugins.assert_not_called()
        self.sync_hub_mcp.assert_not_called()


_FAKE_CLAUDE = r'''
import json, os, sys
from pathlib import Path
home = Path(os.environ["HOME"])
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("2.0.0 (Claude Code)"); sys.exit(0)
plugins = home / ".claude" / "plugins"
plugins.mkdir(parents=True, exist_ok=True)
if args[:3] == ["plugin", "marketplace", "add"]:
    folder = args[-1]
    name = json.loads((Path(folder) / ".claude-plugin" / "marketplace.json").read_text())["name"]
    known = plugins / "known_marketplaces.json"
    data = json.loads(known.read_text()) if known.exists() else {}
    data[name] = {"source": {"source": "directory", "path": folder}}
    known.write_text(json.dumps(data))
elif args[:2] == ["plugin", "install"]:
    ref = args[2]
    if ref.startswith("broken@"):
        print("plugin broken not found", file=sys.stderr); sys.exit(1)
    name, market = ref.split("@")
    copy = plugins / "cache" / market / name / "0.1.0"
    copy.mkdir(parents=True, exist_ok=True)
    installed = plugins / "installed_plugins.json"
    data = json.loads(installed.read_text()) if installed.exists() else {"plugins": {}}
    data["plugins"][ref] = [{"scope": "user", "installPath": str(copy), "version": "0.1.0"}]
    installed.write_text(json.dumps(data))
'''

_FAKE_CODEX = r'''
import json, os, sys
from pathlib import Path
home = Path(os.environ["HOME"])
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("codex-cli 0.200.0"); sys.exit(0)
config = home / ".codex" / "config.toml"
if args[:3] == ["plugin", "marketplace", "add"]:
    folder = args[-1]
    name = json.loads((Path(folder) / ".claude-plugin" / "marketplace.json").read_text())["name"]
    with config.open("a") as fh:
        fh.write(f'\n[marketplaces.{name}]\nsource_type = "local"\nsource = "{folder}"\n')
elif args[:2] == ["plugin", "add"]:
    ref = args[2]
    if ref.startswith("broken@"):
        print("plugin broken not found", file=sys.stderr); sys.exit(1)
    name, market = ref.split("@")
    (home / ".codex" / "plugins" / "cache" / market / name / "0.1.0").mkdir(parents=True, exist_ok=True)
    with config.open("a") as fh:
        fh.write(f'\n[plugins."{ref}"]\nenabled = true\n')
'''


@unittest.skipUnless(GIT, "git not installed")
class SetupHubEndToEndTest(unittest.TestCase):
    """The real ``swe setup --hub --apply --json`` in a child process, with
    fake ``claude`` and ``codex`` on PATH, so every import-time path is the
    child's own HOME."""

    def setUp(self):
        self.scratch = temp_dir(self).resolve()
        self.home = self.scratch / "home"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text("")
        self.bin = self.scratch / "bin"
        self.bin.mkdir()
        (self.bin / "git").symlink_to(GIT)
        for name, body in (("claude", _FAKE_CLAUDE), ("codex", _FAKE_CODEX)):
            script = self.bin / name
            script.write_text(f"#!{sys.executable}\n{body}")
            script.chmod(0o755)
        self.src = self.scratch / "hub-src"
        _market(self.src, ("git-helpers", "broken"))
        (self.src / "AGENTS.md").write_text("# demo hub\n")
        (self.src / "mcp.json").write_text(json.dumps({"mcpServers": HUB_SERVERS}))
        _git("init", "-q", "-b", "main", cwd=self.src)
        _git("add", "-A", cwd=self.src)
        _git("commit", "-q", "-m", "hub", cwd=self.src)

    def test_a_fresh_home_gets_the_servers_and_plugins_with_no_other_step(self):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "NO_COLOR": "1",
               "PYTHONPATH": str(SRC), "GIT_CONFIG_NOSYSTEM": "1",
               "TMPDIR": str(self.scratch)}
        run = subprocess.run(
            [sys.executable, "-m", "swe.cli", "setup", f"--hub={self.src}", "--apply",
             "--json"], capture_output=True, text=True, env=env, timeout=180)
        self.assertEqual(run.returncode, 0, run.stderr)
        cloned = json.loads(run.stdout)["data"]["hub"]["cloned"]
        self.assertEqual([(m["harness"], m["error"]) for m in cloned["marketplaces"]],
                         [("claude", None), ("codex", None)])
        plugins = {(r["harness"], r["name"]): r for r in cloned["plugins"]}
        for harness in ("claude", "codex"):
            self.assertEqual((plugins[harness, "git-helpers"]["skipped"],
                              plugins[harness, "git-helpers"]["error"]), (None, None))
            self.assertIn("plugin broken not found", plugins[harness, "broken"]["error"])
        mcp = {(r["harness"], r["name"]): r for r in cloned["mcp"]}
        self.assertEqual(set(mcp), {("claude", "context7"), ("claude", "github"),
                                    ("codex", "context7"), ("codex", "github")})
        self.assertIsNone(mcp["claude", "context7"]["skipped"])
        self.assertEqual(mcp["codex", "github"]["needs"], ["GITHUB_TOKEN"])

        claude = json.loads((self.home / ".claude.json").read_text())["mcpServers"]
        self.assertEqual(sorted(claude), ["context7"])
        codex = tomllib.loads((self.home / ".codex" / "config.toml").read_text())
        self.assertEqual(sorted(codex["mcp_servers"]), ["context7"])
        self.assertTrue(codex["plugins"]["git-helpers@starter"]["enabled"])
        installed = json.loads((self.home / ".claude" / "plugins" / "installed_plugins.json")
                               .read_text())["plugins"]
        self.assertEqual(sorted(installed), ["git-helpers@starter"])
        self.assertTrue((self.home / ".claude" / "CLAUDE.md").is_symlink())


if __name__ == "__main__":
    unittest.main()
