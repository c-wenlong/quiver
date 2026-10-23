"""The MCP servers page: swe's inventory in process, redacted for the
browser, the managed servers folder beside it, and health only on request."""

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quiver.webapp.pages import mcp

from tests.test_remote_hub import HubBase

SECRET = "lin_api_SECRETVALUE1234567890"


def _pairs(servers_root="/srv/mcp"):
    """Two inventory rows as server_inventory returns them."""
    linear = {
        "name": "linear", "transport": "stdio", "command": "node",
        "args": [f"{servers_root}/custom/linear-mcp/build/index.js", "--token", "abc"],
        "url": None, "env_keys": ["LINEAR_API_KEY"], "header_keys": [],
        "harnesses": ["claude", "codex"], "conflict": True,
        "variants": [["claude"], ["codex"]], "secrets_missing": ["LINEAR_API_KEY"],
        "in_hub": True,
    }
    notion = {
        "name": "notion", "transport": "http", "command": None, "args": [],
        "url": "https://mcp.notion.com/mcp?key=abc", "env_keys": [],
        "header_keys": ["Authorization"], "harnesses": ["claude"], "conflict": False,
        "variants": [["claude"]], "secrets_missing": [], "in_hub": False,
    }
    return [(linear, {"command": "node", "env": {"LINEAR_API_KEY": SECRET}}),
            (notion, {"type": "http", "url": "https://mcp.notion.com/mcp"})]


def _ctx(home=None):
    return SimpleNamespace(home=home, machine={"name": "box"})


class TestRedaction(unittest.TestCase):
    def test_secret_flags_and_values(self):
        self.assertEqual(
            mcp.summary("npx", ["-y", "@x/server", "--api-key", "sk-abcdefghijklmnop", "--token=xyz"], None),
            "npx -y @x/server --api-key … --token=…")

    def test_header_keeps_its_name(self):
        self.assertEqual(mcp.summary("srv", ["-H", "Authorization: Bearer x"], None), "srv -H Authorization:…")
        self.assertEqual(mcp.summary("srv", ["--header=X-Key: y"], None), "srv --header=X-Key:…")

    def test_a_flag_and_value_in_one_argument_split(self):
        self.assertEqual(mcp.summary("srv", ["--access-token abc def"], None), "srv --access-token …")

    def test_env_assignment_and_key_like_words(self):
        self.assertEqual(mcp.summary("env API_KEY=123 node", ["AbCdEfGhIjKlMnOpQr"], None), "env API_KEY=… node …")
        # An upper-case variable name or a lower-case package is not a key.
        self.assertEqual(mcp.summary("x", ["CLOUDFLARE_R2_ACCESS_KEY_ID", "@modelcontextprotocol/server"], None),
                         "x CLOUDFLARE_R2_ACCESS_KEY_ID @modelcontextprotocol/server")

    def test_quoted_command_line_is_redacted_word_by_word(self):
        self.assertEqual(mcp.summary("bash", ["-c", "'curl --token abc https://h/x?k=v'"], None),
                         "bash -c 'curl --token … https://h/x?k=…'")
        self.assertEqual(mcp.summary('node "/srv/my server/index.js"', [], None), 'node "/srv/my server/index.js"')

    def test_url_parts(self):
        self.assertEqual(mcp.redact_url("https://u:p@host.com/s/AbCdEf1234567890xyzQ/mcp?token=abc&flag#frag"),
                         "https://…@host.com/s/…/mcp?token=…&…#…")
        self.assertEqual(mcp.redact_url("https://h/sk%2Dproj%2Dabcdefghijklmnop"), "https://h/…")
        self.assertEqual(mcp.redact_url("https://mcp.notion.com/mcp"), "https://mcp.notion.com/mcp")
        self.assertEqual(mcp.summary(None, [], "https://h/x?k=v"), "https://h/x?k=…")

    def test_url_argument_with_trailing_text(self):
        self.assertEqual(mcp.summary("x", ['"https://h/p?a=1 rest"'], None), 'x "https://h/p?a=… …"')

    def test_unclosed_quote_splits_every_space(self):
        self.assertEqual(mcp.shell_words('a "b --token c'), ["a", "b", "--token", "c"])


class TestGit(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(mcp.parse_git("## main...origin/main [ahead 1, behind 2]\n M a\n"),
                         mcp.git_state("main", 1, 2, True))
        self.assertEqual(mcp.parse_git("## main\n"), mcp.git_state("main"))
        self.assertEqual(mcp.parse_git("## HEAD (no branch)\n"), mcp.git_state(None))
        self.assertEqual(mcp.parse_git("## No commits yet on dev\n?? x\n"), mcp.git_state("dev", 0, 0, True))
        self.assertFalse(mcp.parse_git("")["is_repo"])

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_reads_a_checkout_and_ignores_an_enclosing_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "trunk", str(repo)], check=True)
            (repo / "f").write_text("x")
            state = mcp.read_git(repo)
            self.assertEqual((state["branch"], state["has_changes"], state["is_repo"]), ("trunk", True, True))
            inner = repo / "inner"
            inner.mkdir()
            self.assertFalse(mcp.read_git(inner)["is_repo"])


class TestFolders(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def _server(self, rel, marker="package.json"):
        path = self.root / rel
        path.mkdir(parents=True)
        (path / marker).write_text("{}")
        return path

    def test_scan_groups_and_order(self):
        self._server("zeta")
        self._server("custom/Linear-mcp")
        self._server("custom/fathom-mcp", "server.py")
        self._server("Alpha/one")
        (self.root / ".hidden").mkdir()
        (self.root / "notes.txt").write_text("x")
        found = [(f["group"], f["name"]) for f in mcp.scan(self.root)]
        self.assertEqual(found, [(None, "zeta"), ("Alpha", "one"), ("custom", "fathom-mcp"),
                                 ("custom", "Linear-mcp")])

    def test_missing_root_is_empty_and_a_file_is_an_error(self):
        self.assertEqual(mcp.scan(self.root / "nope"), [])
        (self.root / "file").write_text("x")
        result = mcp.folder_scan(self.root / "file", [])
        self.assertIn("is a file, not a folder", result["error"])

    def test_usage_matches_paths_under_a_folder_only(self):
        hn = self._server("mcp-hn")
        self._server("mcp-hnx")
        servers = [
            {"name": "hn", "command": f"node {hn}/index.js", "args": []},
            {"name": "quoted", "command": f'node "{hn}/a b.js"', "args": []},
            {"name": "via-file", "command": "x", "args": [f"file://{hn}/run"]},
            {"name": "relative", "command": "node", "args": ["mcp-hn/index.js"]},
        ]
        result = mcp.folder_scan(self.root, servers)
        used = {f["name"]: f["used_by"] for f in result["items"]}
        self.assertEqual(used, {"mcp-hn": ["hn", "quoted", "via-file"], "mcp-hnx": []})
        self.assertTrue(all(not f["git"]["is_repo"] for f in result["items"]))


class TestPayload(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        root = self.home / ".quiver" / "mcp" / "servers" / "custom" / "linear-mcp"
        root.mkdir(parents=True)
        (root / "package.json").write_text("{}")
        self.servers_root = self.home / ".quiver" / "mcp" / "servers"

    def test_rows_carry_a_summary_not_the_command_line(self):
        with patch.object(mcp, "inventory", return_value=(["claude", "codex"], _pairs(self.servers_root))), \
                patch.object(mcp, "probe_all") as probe:
            body = mcp.get(_ctx(self.home), {})
        probe.assert_not_called()
        self.assertFalse(body["health"])
        self.assertEqual(body["harnesses"], ["claude", "codex"])
        linear = body["servers"][0]
        for key in ("command", "args", "url"):
            self.assertNotIn(key, linear)
        self.assertTrue(linear["summary"].endswith("index.js --token …"))
        self.assertEqual(body["servers"][1]["summary"], "https://mcp.notion.com/mcp?key=…")
        self.assertNotIn("health", linear)
        self.assertNotIn(SECRET, json.dumps(body))
        self.assertNotIn("abc", json.dumps(body))
        folder = body["folders"]["items"][0]
        self.assertEqual((folder["name"], folder["group"], folder["used_by"]), ("linear-mcp", "custom", ["linear"]))
        self.assertEqual(body["machine"], "box")

    def test_health_only_when_asked(self):
        with patch.object(mcp, "inventory", return_value=(["claude"], _pairs())), \
                patch("quiver.mcp.cli.probe_server_health",
                      side_effect=[("ok", None), ("error", "url unreachable")]) as probe:
            body = mcp.get(_ctx(self.home), {"health": "1"})
        self.assertEqual(probe.call_count, 2)
        self.assertTrue(body["health"])
        self.assertEqual([(s["health"], s["detail"]) for s in body["servers"]],
                         [("ok", None), ("error", "url unreachable")])

    def test_an_unreadable_inventory_still_lists_folders(self):
        with patch.object(mcp, "inventory", side_effect=ValueError("bad config")):
            body = mcp.get(_ctx(self.home), {"health": "1"})
        self.assertEqual(body["error"], "bad config")
        self.assertEqual(body["servers"], [])
        self.assertFalse(body["health"])
        self.assertEqual(body["folders"]["items"][0]["used_by"], [])

    def test_a_slow_probe_reads_unknown_at_the_deadline(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def slow(name, cfg):
            if name == "notion":
                release.wait(5)
            return "ok", None

        with patch("quiver.mcp.cli.probe_server_health", side_effect=slow):
            results = mcp.probe_all(_pairs(), deadline=0.3)
        self.assertEqual(results, [("ok", None), ("unknown", "check timed out")])

    def test_inventory_is_swe_mcp_list(self):
        tool_data = {
            "claude": {"linear": {"command": "node", "args": ["a.js"], "env": {"LINEAR_API_KEY": SECRET}}},
            "cursor": {"linear": {"command": "node", "args": ["b.js"]}},
        }
        with patch("quiver.harness.registry.load_registry_readonly", return_value={"claude": {}, "cursor": {}}), \
                patch("quiver.mcp.cli.get_mcp_tools", return_value={"claude": {}, "cursor": {}}), \
                patch("quiver.mcp.cli._display_tools", side_effect=lambda tools: tools), \
                patch("quiver.mcp.cli.get_tool_servers", side_effect=lambda name: tool_data[name]), \
                patch("quiver.mcp.cli.get_hub_servers", return_value={}), \
                patch("quiver.mcp.inventory.load_secrets", return_value={}):
            harnesses, pairs = mcp.inventory()
        self.assertEqual(harnesses, ["claude", "cursor"])
        row = pairs[0][0]
        self.assertEqual((row["name"], row["variants"], row["env_keys"]),
                         ("linear", [["claude"], ["cursor"]], ["LINEAR_API_KEY"]))
        self.assertNotIn(SECRET, json.dumps(mcp.page_row(row)))


class TestRoute(HubBase):
    def test_needs_a_device_and_returns_the_inventory(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/mcp")[0], 401)
        with patch.object(mcp, "inventory", return_value=(["claude"], _pairs())), \
                patch("quiver.mcp.cli.probe_server_health") as probe:
            status, data = self.srv.request("GET", "/v1/app/mcp", headers=self.auth())
        self.assertEqual(status, 200, data)
        probe.assert_not_called()
        body = json.loads(data)
        self.assertEqual([s["name"] for s in body["servers"]], ["linear", "notion"])
        self.assertEqual(body["machine"], self.srv.state.machine["name"])
        self.assertFalse(body["folders"]["exists"])

    def test_health_query_probes(self):
        with patch.object(mcp, "inventory", return_value=(["claude"], _pairs())), \
                patch("quiver.mcp.cli.probe_server_health", return_value=("ok", None)):
            status, data = self.srv.request("GET", "/v1/app/mcp?health=1", headers=self.auth())
        self.assertEqual(status, 200, data)
        self.assertEqual({s["health"] for s in json.loads(data)["servers"]}, {"ok"})

    def test_page_script_is_bundled(self):
        status, body = self.srv.request("GET", "/app/pages.js")
        self.assertEqual(status, 200)
        self.assertIn(b'registerPage("mcp"', body)


if __name__ == "__main__":
    unittest.main()
