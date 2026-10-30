"""The MCP servers page: swe's inventory in process, redacted for the
browser, the managed servers folder beside it, and health only on request."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quiver.mcp import cli as mcp_cli
from quiver.webapp.pages import PageError, mcp

from tests.test_remote_hub import HubBase
from tests.test_webapp import app_js_function

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

    def test_swe_credential_words_inside_a_shell_command(self):
        self.assertEqual(mcp.summary("bash", ["-c", "exec server --cookie abcdefghijklmnop"], None),
                         "bash -c exec server --cookie …")
        self.assertEqual(mcp.summary("srv", ["--signature=abc", "--bearer", "abc", "--session-cookies=x"], None),
                         "srv --signature=… --bearer … --session-cookies=…")
        self.assertEqual(mcp.summary("srv", ["--headers", "X-Api: abc"], None), "srv --headers X-Api:…")

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

    def test_the_servers_folder_is_shortened_against_the_hubs_home(self):
        # The folder lives under the hub's home, which need not be the
        # process's: it was always written out in full.
        self._server("mcp-hn")
        result = mcp.folder_scan(self.root, [], home=self.root.parent)
        self.assertEqual(result["root"], "~/" + self.root.name)
        self.assertEqual(result["items"][0]["path"],
                         "~/" + self.root.name + "/mcp-hn")

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


def _no_hub_secrets(case):
    """Keep get()'s secrets join off the real ~/.quiver/mcp.json and
    secrets store, which these cases don't set up."""
    for p in (patch.object(mcp, "_hub_refs", return_value={}),
              patch("quiver.mcp.secrets.load_secrets", return_value={})):
        p.start()
        case.addCleanup(p.stop)


class TestPayload(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        _no_hub_secrets(self)
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

    def test_a_cookie_in_a_wrapped_command_stays_on_the_hub(self):
        row = {"name": "wrapped", "transport": "stdio", "command": "bash",
               "args": ["-c", "exec server --cookie abcdefghijklmnop"], "url": None,
               "env_keys": [], "header_keys": [], "harnesses": ["claude"], "conflict": False,
               "variants": [["claude"]], "secrets_missing": [], "in_hub": False}
        with patch.object(mcp, "inventory", return_value=(["claude"], [(row, {})])):
            body = mcp.get(_ctx(self.home), {})
        self.assertNotIn("abcdefghijklmnop", json.dumps(body))
        self.assertEqual(body["servers"][0]["summary"], "bash -c exec server --cookie …")

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
    def setUp(self):
        super().setUp()
        _no_hub_secrets(self)

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


LINEAR_TOKEN = "lin_api_LIVEVALUE0987654321"
HUB_TOKEN = "ntn_HUBSECRETVALUE5555555555"
REGISTRY = {"claude": {"aliases": ["cc"]}, "cursor": {}, "codex": {}}


class _SyncHome:
    """A real temp HOME holding claude, cursor and codex configs and the
    hub's mcp.json; swe's config paths and registry point into it."""

    def setUp(self):
        super().setUp()
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        cmap = {
            "claude": {"path": self.home / ".claude.json", "key": "mcpServers", "label": "Claude Code"},
            "cursor": {"path": self.home / ".cursor" / "mcp.json", "key": "mcpServers", "label": "Cursor"},
            "codex": {"path": self.home / ".codex" / "config.toml", "key": "mcp_servers",
                      "label": "Codex", "format": "standard", "io_type": "toml_region"},
        }
        self.claude, self.cursor, self.codex = (cmap[n]["path"] for n in ("claude", "cursor", "codex"))
        self.hub = self.home / ".quiver" / "mcp.json"
        self.hub.parent.mkdir(parents=True)
        self.cursor.parent.mkdir()
        self.codex.parent.mkdir()
        self.claude.write_text(json.dumps({"numStartups": 7, "mcpServers": {
            "linear": {"command": "node", "args": ["/srv/linear.js", "--token", LINEAR_TOKEN],
                       "env": {"LINEAR_API_KEY": LINEAR_TOKEN, "DEBUG": "1"}},
        }}))
        self.cursor.write_text(json.dumps({"mcpServers": {
            "linear": {"command": "node", "args": ["/old/linear.js"],
                       "env": {"OLD_KEY": "old-value", "DEBUG": "0"}, "disabled": True},
            "keep": {"command": "keep"},
        }}))
        self.codex.write_text('model = "o3"\n\n[mcp_servers.other]\ncommand = "x"\n')
        self.hub.write_text(json.dumps({"mcpServers": {
            "notion": {"command": "npx", "args": ["notion-mcp"], "env": {"NOTION_TOKEN": "${NOTION_TOKEN}"}},
        }}))
        for patcher in (
            patch.dict(os.environ, {"HOME": str(self.home)}),
            patch.object(mcp_cli, "MCP_CONFIG_MAP", cmap),
            patch.object(mcp_cli, "MCP_SOURCE_FILE", self.hub),
            patch.object(mcp_cli, "_load_registry", return_value=REGISTRY),
            patch("quiver.harness.registry.load_registry_readonly", return_value=REGISTRY),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def snapshot(self):
        return {p: p.read_bytes() if p.exists() else None
                for p in (self.claude, self.cursor, self.codex, self.hub)}

    def set_secret(self, name, value):
        path = self.home / ".quiver" / "secrets" / "mcp" / ".api_keys"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"export {name}={value}\n")


LINEAR = {"server": "linear", "source": "claude", "targets": ["cursor", "codex"], "replace": ["cursor"]}


class TestSync(_SyncHome, unittest.TestCase):
    def post(self, action, **body):
        return mcp.post(_ctx(), action, body)

    def refused(self, action, **body):
        with self.assertRaises(PageError) as caught:
            self.post(action, **body)
        return caught.exception

    def test_preview_shows_the_plan_and_writes_nothing(self):
        before = self.snapshot()
        reply = self.post("preview", **LINEAR)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(reply["report"]["dry_run"])
        plan = {h["harness"]: h for h in reply["harnesses"]}
        self.assertEqual(list(plan), ["cursor", "codex"])
        self.assertEqual((plan["cursor"]["outcome"], plan["codex"]["outcome"]), ("replace", "add"))
        self.assertEqual(plan["cursor"]["path"], "~/.cursor/mcp.json")
        self.assertEqual(plan["cursor"]["env"],
                         {"added": ["LINEAR_API_KEY"], "removed": ["OLD_KEY"], "changed": ["DEBUG"]})
        self.assertEqual(plan["cursor"]["kept"], ["disabled"])
        self.assertTrue(plan["cursor"]["command_changes"])
        self.assertEqual(plan["cursor"]["before"]["summary"], "node /old/linear.js")
        self.assertEqual(reply["files"], ["~/.cursor/mcp.json", "~/.codex/config.toml"])
        text = json.dumps(reply)
        for secret in (LINEAR_TOKEN, "old-value"):
            self.assertNotIn(secret, text)

    def test_sync_writes_exactly_the_previewed_plan(self):
        fingerprint = self.post("preview", **LINEAR)["fingerprint"]
        reply = self.post("sync", **LINEAR, fingerprint=fingerprint)
        self.assertIsNone(reply["error"])
        self.assertFalse(reply["report"]["dry_run"])
        cursor = json.loads(self.cursor.read_text())["mcpServers"]
        self.assertEqual(cursor["linear"]["args"], ["/srv/linear.js", "--token", LINEAR_TOKEN])
        self.assertEqual(cursor["linear"]["env"], {"LINEAR_API_KEY": LINEAR_TOKEN, "DEBUG": "1"})
        self.assertTrue(cursor["linear"]["disabled"])  # the harness's own key survives
        self.assertEqual(cursor["keep"], {"command": "keep"})
        codex = self.codex.read_text()
        self.assertIn("[mcp_servers.linear]", codex)
        self.assertIn('model = "o3"', codex)
        self.assertEqual(json.loads(self.claude.read_text())["numStartups"], 7)
        row = next(r for r in reply["page"]["servers"] if r["name"] == "linear")
        self.assertFalse(row["conflict"])
        self.assertEqual(sorted(row["harnesses"]), ["claude", "codex", "cursor"])
        self.assertNotIn(LINEAR_TOKEN, json.dumps(reply))

    def test_sync_refuses_when_a_config_changed_since_the_preview(self):
        fingerprint = self.post("preview", **LINEAR)["fingerprint"]
        data = json.loads(self.cursor.read_text())
        data["mcpServers"]["linear"]["args"] = ["/newer/linear.js"]
        self.cursor.write_text(json.dumps(data))
        before = self.snapshot()
        error = self.refused("sync", **LINEAR, fingerprint=fingerprint)
        self.assertEqual((error.status, error.code), (409, "plan_changed"))
        self.assertEqual(self.snapshot(), before)
        # The source changing counts too.
        fingerprint = self.post("preview", **LINEAR)["fingerprint"]
        data = json.loads(self.claude.read_text())
        data["mcpServers"]["linear"]["env"]["DEBUG"] = "2"
        self.claude.write_text(json.dumps(data))
        self.assertEqual(self.refused("sync", **LINEAR, fingerprint=fingerprint).code, "plan_changed")
        self.assertEqual(self.refused("sync", **LINEAR, fingerprint="0" * 64).code, "plan_changed")
        self.assertEqual(self.refused("sync", **LINEAR, fingerprint="é" * 64).code, "plan_changed")

    def test_a_secret_with_no_value_refuses_and_writes_nothing(self):
        before = self.snapshot()
        body = {"server": "notion", "source": "quiver", "targets": ["claude"]}
        for action in ("preview", "sync"):
            error = self.refused(action, **body, fingerprint="x")
            self.assertEqual((error.status, error.code), (409, "secrets_missing"))
            self.assertIn("NOTION_TOKEN", error.message)
            self.assertIn("Set NOTION_TOKEN in this server's sheet", error.message)
        self.assertEqual(self.snapshot(), before)

    def test_from_the_hub_the_value_is_written_but_never_sent(self):
        self.set_secret("NOTION_TOKEN", HUB_TOKEN)
        body = {"server": "notion", "source": "quiver", "targets": ["claude", "cursor"]}
        preview = self.post("preview", **body)
        self.assertEqual([h["outcome"] for h in preview["harnesses"]], ["add", "add"])
        reply = self.post("sync", **body, fingerprint=preview["fingerprint"])
        self.assertEqual(json.loads(self.claude.read_text())["mcpServers"]["notion"]["env"],
                         {"NOTION_TOKEN": HUB_TOKEN})
        for answer in (preview, reply):
            self.assertNotIn(HUB_TOKEN, json.dumps(answer))
        self.assertEqual(preview["from"]["env_keys"], ["NOTION_TOKEN"])

    def test_targets_must_still_be_what_the_page_showed(self):
        # cursor has its own linear: adding without approval would replace it.
        error = self.refused("preview", server="linear", source="claude", targets=["cursor"])
        self.assertEqual((error.status, error.code), (409, "target_changed"))
        # codex lacks it: there is nothing of its own to replace.
        error = self.refused("preview", server="linear", source="claude", targets=["codex"], replace=["codex"])
        self.assertEqual((error.status, error.code), (409, "target_changed"))
        # The source already has its own definition: nothing to copy there.
        error = self.refused("preview", server="linear", source="cursor", targets=["cursor"])
        self.assertEqual((error.status, error.code), (409, "target_changed"))
        # cursor's definition is a source too, and replaces claude's with approval.
        reply = self.post("preview", server="linear", source="cursor", targets=["claude", "codex"],
                          replace=["claude"])
        self.assertEqual([h["outcome"] for h in reply["harnesses"]], ["replace", "add"])

    def test_refusals(self):
        cases = [
            ("preview", {"server": "nope", "source": "claude", "targets": ["codex"]}, 404, "unknown_server"),
            ("preview", {"server": "linear", "source": "gemini", "targets": ["codex"]}, 422, "bad_source"),
            ("preview", {"server": "linear", "source": "quiver", "targets": ["codex"]}, 422, "bad_source"),
            ("preview", {"server": "linear", "source": "claude", "targets": ["evil"]}, 422, "unknown_harness"),
            ("preview", {"server": "linear", "source": "claude", "targets": ["../x"]}, 422, "unknown_harness"),
            ("preview", {"server": "linear", "source": "claude", "targets": []}, 422, "no_targets"),
            ("preview", {"server": "linear", "source": "claude", "targets": "codex"}, 422, "bad_targets"),
            ("preview", {"server": "linear", "source": "claude", "targets": ["codex"], "replace": ["cursor"]},
             422, "bad_replace"),
            ("preview", {"server": "", "source": "claude", "targets": ["codex"]}, 422, "bad_server"),
            ("preview", {"server": "linear", "source": 3, "targets": ["codex"]}, 422, "bad_source"),
            ("sync", {"server": "linear", "source": "claude", "targets": ["codex"]}, 422, "no_fingerprint"),
            ("install", {"server": "linear"}, 404, "unknown_action"),
        ]
        before = self.snapshot()
        for action, body, status, code in cases:
            with self.subTest(code=code, body=body):
                error = self.refused(action, **body)
                self.assertEqual((error.status, error.code), (status, code))
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(PageError) as caught:
            mcp.post(_ctx(), "preview", ["linear"])
        self.assertEqual(caught.exception.status, 422)

    def test_a_name_with_a_comma_cannot_sync_alone(self):
        data = json.loads(self.claude.read_text())
        data["mcpServers"]["a,b"] = {"command": "x"}
        self.claude.write_text(json.dumps(data))
        error = self.refused("preview", server="a,b", source="claude", targets=["codex"])
        self.assertEqual((error.status, error.code), (422, "comma_name"))

    def test_only_pattern_matches_the_name_alone(self):
        self.assertEqual(mcp.only_pattern("a*b?[c]"), "a[*]b[?][[]c]")
        data = json.loads(self.claude.read_text())
        data["mcpServers"]["lin*"] = {"command": "star"}
        self.claude.write_text(json.dumps(data))
        body = {"server": "lin*", "source": "claude", "targets": ["codex"]}
        reply = self.post("sync", **body, fingerprint=self.post("preview", **body)["fingerprint"])
        self.assertEqual(reply["report"]["targets"][0]["added"], ["lin*"])
        self.assertNotIn("[mcp_servers.linear]", self.codex.read_text())

    def test_a_save_that_fails_is_reported(self):
        body = {"server": "linear", "source": "claude", "targets": ["codex"]}
        fingerprint = self.post("preview", **body)["fingerprint"]
        with patch.object(mcp_cli, "get_tool_saver", return_value=lambda servers, path: (_ for _ in ()).throw(OSError("disk full"))):
            error = self.refused("sync", **body, fingerprint=fingerprint)
        self.assertEqual(error.status, 409)
        self.assertIn("disk full", error.message)

    def test_sync_writes_the_checked_source_not_a_later_read(self):
        # An edit to the source landing after the fingerprint check must not
        # reach a target: the write uses the snapshot that was checked.
        body = {"server": "linear", "source": "claude", "targets": ["codex"]}
        fingerprint = self.post("preview", **body)["fingerprint"]
        real = mcp_cli.servers_for_source
        tampered = {"linear": {"command": "evil", "args": []}}
        with patch.object(mcp_cli, "servers_for_source", side_effect=[real("claude"), tampered, tampered]):
            self.post("sync", **body, fingerprint=fingerprint)
        codex = self.codex.read_text()
        self.assertIn('command = "node"', codex)
        self.assertNotIn("evil", codex)

    def test_a_target_changed_mid_sync_is_left_alone(self):
        fingerprint = self.post("preview", **LINEAR)["fingerprint"]
        real = mcp_cli.get_tool_servers
        calls = {"cursor": 0}

        def reads(target):
            servers = real(target)
            if target == "cursor":
                calls["cursor"] += 1
                if calls["cursor"] > 1:  # after the check, before its save
                    servers["linear"] = {"command": "someone-else"}
            return servers

        with patch.object(mcp_cli, "get_tool_servers", side_effect=reads):
            reply = self.post("sync", **LINEAR, fingerprint=fingerprint)
        self.assertEqual([t["name"] for t in reply["report"]["targets"]], ["codex"])
        self.assertIn("changed while syncing", reply["error"])
        self.assertEqual(json.loads(self.cursor.read_text())["mcpServers"]["linear"]["args"], ["/old/linear.js"])

    def test_a_save_failing_part_way_keeps_what_was_saved(self):
        fingerprint = self.post("preview", **LINEAR)["fingerprint"]
        real = mcp_cli.get_tool_saver

        def saver(target):
            if target == "cursor":
                return lambda servers, path: (_ for _ in ()).throw(OSError("read-only file system"))
            return real(target)

        with patch.object(mcp_cli, "get_tool_saver", side_effect=saver):
            reply = self.post("sync", **LINEAR, fingerprint=fingerprint)
        # codex (an add) went first and was saved; cursor's save failed.
        self.assertEqual(reply["report"]["targets"],
                         [{"name": "codex", "added": ["linear"], "updated": [], "skipped": [], "pruned": [],
                       "unsupported": []}])
        self.assertIn("Sync stopped at cursor: read-only file system", reply["error"])
        self.assertEqual([(h["harness"], h["outcome"]) for h in reply["harnesses"]],
                         [("cursor", "not_run"), ("codex", "add")])
        self.assertEqual(reply["files"], ["~/.codex/config.toml"])
        self.assertIn("[mcp_servers.linear]", self.codex.read_text())

    def test_sources_and_targets_follow_the_mac_model(self):
        row = {"name": "x", "harnesses": ["claude", "cursor"], "variants": [["claude"], ["cursor"]],
               "conflict": True, "in_hub": True}
        self.assertEqual(mcp.sources(row), ["claude", "cursor", "quiver"])
        self.assertEqual(mcp.sync_targets(row, "claude", ["claude", "cursor", "codex"]), (["codex"], ["cursor"]))
        self.assertEqual(mcp.sync_targets(row, "quiver", ["claude", "cursor", "codex"]),
                         (["codex"], ["claude", "cursor"]))

    def test_rows_say_where_a_definition_is_remote(self):
        hub = json.loads(self.hub.read_text())
        hub["mcpServers"]["remote"] = {"url": "https://mcp.example.com/mcp"}
        self.hub.write_text(json.dumps(hub))
        rows = {r["name"]: r for r in mcp.get(_ctx(), {})["servers"]}
        self.assertEqual(rows["remote"]["remote_in"], ["quiver"])
        self.assertEqual(rows["linear"]["remote_in"], [])

    def test_a_url_server_is_not_offered_to_claude_desktop(self):
        remote = {"name": "r", "transport": "http", "harnesses": ["claude"], "variants": [["claude"]],
                  "conflict": False, "in_hub": True}
        local = {**remote, "transport": "stdio"}
        every = ["claude", "claude-desktop", "codex"]
        desktop = {"path": self.home / "desktop.json", "key": "mcpServers", "local_only": True}
        self.enterContext(patch.dict(mcp_cli.MCP_CONFIG_MAP, {"claude-desktop": desktop}))
        self.assertEqual(mcp.sync_targets({**remote, "remote_in": ["quiver"]}, "quiver", every), (["codex"], ["claude"]))
        self.assertEqual(mcp.sync_targets(local, "quiver", every), (["claude-desktop", "codex"], ["claude"]))
        # Local in claude, remote in the hub: the source's definition decides.
        mixed = {**local, "remote_in": ["quiver"]}
        self.assertEqual(mcp.sync_targets(mixed, "claude", every), (["claude-desktop", "codex"], []))
        self.assertEqual(mcp.sync_targets(mixed, "quiver", every), (["codex"], ["claude"]))


class TestSecrets(_SyncHome, unittest.TestCase):
    """The sheet's secrets: masked in the page, set and read on request."""

    TOKEN = "ntn_abcdefghijklmnopqrst1234"

    def notion(self, page):
        return next(s for s in page["servers"] if s["name"] == "notion")

    def test_the_page_lists_refs_masked_never_the_value(self):
        self.assertEqual(self.notion(mcp.get(_ctx(), {}))["secrets"],
                         [{"name": "NOTION_TOKEN", "set": False, "masked": None}])
        self.set_secret("NOTION_TOKEN", self.TOKEN)
        page = mcp.get(_ctx(), {})
        self.assertEqual(self.notion(page)["secrets"],
                         [{"name": "NOTION_TOKEN", "set": True, "masked": "••••••••1234"}])
        self.assertNotIn(self.TOKEN, json.dumps(page))
        # A harness-only server has no hub definition, so no secrets row.
        self.assertEqual(next(s for s in page["servers"] if s["name"] == "linear")["secrets"], [])

    def test_set_writes_the_store_and_answers_without_the_value(self):
        reply = mcp.post(_ctx(), "secret_set", {"name": "NOTION_TOKEN", "value": f"  {self.TOKEN}\n"})
        self.assertNotIn(self.TOKEN, json.dumps(reply))
        self.assertTrue(self.notion(reply["page"])["secrets"][0]["set"])
        from quiver.mcp.secrets import load_secrets
        self.assertEqual(load_secrets()["NOTION_TOKEN"], self.TOKEN)

    def test_value_answers_one_set_secret(self):
        self.set_secret("NOTION_TOKEN", self.TOKEN)
        self.assertEqual(mcp.post(_ctx(), "secret_value", {"name": "NOTION_TOKEN"}),
                         {"name": "NOTION_TOKEN", "value": self.TOKEN})

    def test_refusals_never_echo_a_value(self):
        self.set_secret("UNRELATED", "zz_secretvalue_0000000000")
        cases = [("secret_value", {"name": "UNRELATED"}, (404, "unknown_secret")),
                 ("secret_set", {"name": "UNRELATED", "value": "x"}, (404, "unknown_secret")),
                 ("secret_value", {"name": "NOTION_TOKEN"}, (404, "secret_not_set")),
                 ("secret_set", {"name": "NOTION_TOKEN", "value": "  "}, (422, "bad_value")),
                 ("secret_set", {"name": "NOTION_TOKEN", "value": "a\nb" + self.TOKEN}, (422, "bad_value")),
                 ("secret_set", {"name": "NOTION_TOKEN", "value": 7}, (422, "bad_value")),
                 ("secret_set", {"name": "", "value": "x"}, (422, "bad_name"))]
        for action, body, want in cases:
            with self.assertRaises(PageError) as caught:
                mcp.post(_ctx(), action, body)
            self.assertEqual((caught.exception.status, caught.exception.code), want, body)
            self.assertNotIn(self.TOKEN, caught.exception.message)
            self.assertNotIn("zz_secretvalue", caught.exception.message)

    def test_mask_matches_the_mac_apps(self):
        from quiver.webapp.pages._secrets import mask
        self.assertEqual(mask(""), "")
        self.assertEqual(mask("short"), "••••••••")
        self.assertEqual(mask("sk-ant-api03-abcdefghijWXYZ"), "sk-ant-••••••••WXYZ")
        self.assertEqual(mask("abcdefghijklmnop"), "••••••••mnop")


class TestSecretsSheetScript(unittest.TestCase):
    """mcp.js's Secrets section under node, with the page's globals stubbed."""

    PRELUDE = """
const registerPage = () => {};
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => "&#" + c.charCodeAt(0) + ";");
const Fmt = {}; const localStorage = { getItem: () => null, setItem() {} };
const document = { getElementById: () => null };
const window = { addEventListener() {} };
let answer; const apiPost = () => new Promise((resolve) => { answer = resolve; });
"""

    def run_js(self, body):
        import shutil
        import subprocess
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not installed")
        source = (Path(mcp.__file__).parent.parent / "static" / "pages" / "mcp.js").read_text()
        script = ("const run = new Function(" + json.dumps(self.PRELUDE + source + "\n" + body) + ");\n"
                  "Promise.resolve(run()).then((out) => process.stdout.write(JSON.stringify(out)));")
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    SERVER = """const s = { name: "proto", harnesses: [], variants: [], in_hub: true, secrets: [
  { name: "constructor", set: false, masked: null },
  { name: "__proto__", set: true, masked: "••••••••abcd" },
  { name: "PLAIN_TOKEN", set: true, masked: "••••••••wxyz" }] };
mcpPage.data = { harnesses: [], servers: [s] };
const st = mcpSheetState(s);
"""

    def test_names_like_object_properties_start_empty(self):
        html = self.run_js(self.SERVER + "return mcpSecretsHtml(s, st);")
        self.assertNotIn("native code", html)
        self.assertRegex(html, r'data-mcp-secret-draft="constructor"[^>]*value=""')
        self.assertIn("••••••••abcd", html)
        self.assertIn('aria-label="Show the value"', html)

    def test_add_sheet_splits_and_names_like_the_app(self):
        got = self.run_js("""return {
  words: [mcpCommandWords('npx -y "@scope/pkg" a\\\\ b'), mcpCommandWords("x 'C:\\\\'"), mcpCommandWords('x "open')],
  names: [mcpSecretName("my-server", "api.key"), mcpSecretName("1x", "Token"), mcpSecretName("a", "__b__")],
  secret: ["GITHUB_TOKEN", "x-api-key", "Authorization", "DEBUG", ""].map(mcpIsSecretName) };""")
        self.assertEqual(got["words"], [["npx", "-y", "@scope/pkg", "a b"], ["x", "C:\\"], None])
        self.assertEqual(got["names"], [mcp.secret_name("my-server", "api.key"), mcp.secret_name("1x", "Token"),
                                        mcp.secret_name("a", "__b__")])
        self.assertEqual(got["secret"], [True, True, True, False, False])

    def test_a_reveal_answering_after_close_stays_hidden(self):
        got = self.run_js(self.SERVER + """
const buttons = { "[data-mcp-secret-reveal]": [{ dataset: { mcpSecretReveal: "PLAIN_TOKEN" } }] };
const sheet = { querySelectorAll: (sel) => buttons[sel] || [] };
mcpBindSecrets(sheet, s, st);
const pending = buttons["[data-mcp-secret-reveal]"][0].onclick();
// The sheet closes while the hub is still answering.
st.revealed = new Map(); st.revealGen++;
answer({ status: 200, data: { name: "PLAIN_TOKEN", value: "the-value" } });
return pending.then(() => ({ revealed: st.revealed.has("PLAIN_TOKEN"), html: mcpSecretsHtml(s, st) }));
""")
        self.assertFalse(got["revealed"])
        self.assertNotIn("the-value", got["html"])


class TestAdd(_SyncHome, unittest.TestCase):
    """Add server: swe mcp add in process, secrets through set_secret."""

    TOKEN = "ghp_addsecretvalue00001234"

    def add(self, **body):
        return mcp.post(_ctx(), "add", body)

    def refused(self, **body):
        before = self.snapshot()
        store = self.home / ".quiver" / "secrets" / "mcp" / ".api_keys"
        had = store.read_bytes() if store.exists() else None
        with self.assertRaises(PageError) as caught:
            self.add(**body)
        self.assertEqual(self.snapshot(), before, "a refusal must write nothing")
        self.assertEqual(store.read_bytes() if store.exists() else None, had, "nor store a secret")
        self.assertNotIn(self.TOKEN, caught.exception.message)
        return caught.exception

    def test_a_command_server_with_a_secret_goes_to_the_hub_and_the_targets(self):
        reply = self.add(name="github", kind="command", command=["npx", "-y", "server-github"],
                         vars=[{"key": "GITHUB_TOKEN", "value": self.TOKEN, "secret": True},
                               {"key": "DEBUG", "value": "1", "secret": False},
                               {"key": "", "value": "ignored", "secret": False}],
                         targets=["claude", "cursor"])
        self.assertEqual((reply["hub"], reply["targets"]),
                         ("added", [{"name": "claude", "status": "added"}, {"name": "cursor", "status": "added"}]))
        self.assertNotIn(self.TOKEN, json.dumps(reply))
        hub = json.loads(self.hub.read_text())["mcpServers"]["github"]
        self.assertEqual(hub, {"command": "npx", "args": ["-y", "server-github"],
                               "env": {"GITHUB_TOKEN": "${GITHUB_GITHUB_TOKEN}", "DEBUG": "1"}})
        from quiver.mcp.secrets import load_secrets
        self.assertEqual(load_secrets()["GITHUB_GITHUB_TOKEN"], self.TOKEN)
        claude = json.loads(self.claude.read_text())["mcpServers"]["github"]
        self.assertEqual(claude["env"]["GITHUB_TOKEN"], self.TOKEN)
        row = next(r for r in reply["page"]["servers"] if r["name"] == "github")
        self.assertEqual(row["secrets"], [{"name": "GITHUB_GITHUB_TOKEN", "set": True, "masked": "••••••••1234"}])

    def test_a_url_server_takes_headers_and_sse(self):
        self.add(name="remote", kind="url", url=" https://mcp.example.com/sse ", transport="sse",
                 vars=[{"key": "X-Team", "value": "core", "secret": False}], targets=[])
        hub = json.loads(self.hub.read_text())["mcpServers"]["remote"]
        self.assertEqual(hub, {"url": "https://mcp.example.com/sse", "type": "sse", "headers": {"X-Team": "core"}})

    def test_replace_needs_force(self):
        body = dict(name="notion", kind="command", command=["npx", "notion-mcp-2"],
                    vars=[{"key": "NOTION_TOKEN", "value": self.TOKEN, "secret": True}], targets=[])
        error = self.refused(**body)
        self.assertEqual((error.status, error.code), (409, "exists"))
        self.assertEqual(self.add(**body, force=True)["hub"], "replaced")

    def test_nothing_is_stored_for_an_add_refused_later(self):
        secret = {"key": "API_KEY", "value": self.TOKEN, "secret": True}
        cmd = {"name": "fresh", "kind": "command", "command": ["npx", "x"]}
        # A ${NAME} the store can't fill, with a harness picked.
        error = self.refused(**cmd, targets=["claude"],
                             vars=[secret, {"key": "OTHER", "value": "${MISSING}", "secret": False}])
        self.assertEqual((error.status, error.code), (409, "add_refused"))
        self.assertIn("MISSING", error.message)
        # A later secret the store can't hold.
        error = self.refused(**cmd, targets=[], vars=[secret, {"key": "B_KEY", "value": "a'b\"c", "secret": True}])
        self.assertEqual((error.status, error.code), (422, "bad_value"))
        # Two keys that fold to one name.
        error = self.refused(**cmd, targets=[], vars=[secret, {"key": "api.key", "value": "x", "secret": True}])
        self.assertEqual((error.status, error.code), (422, "secret_clash"))

    def test_a_failed_add_puts_the_secret_store_back(self):
        from quiver.mcp.secrets import load_secrets, set_secret
        set_secret("KEPT_API_KEY", "old-value-00000000")
        before = load_secrets()
        vars_ = [{"key": "API_KEY", "value": self.TOKEN, "secret": True},
                 {"key": "NEW_KEY", "value": "new-value-00000000", "secret": True}]
        for exc, code in ((mcp_cli.AddRefused("disk said no"), "add_refused"), (OSError("full"), "add_failed")):
            with self.subTest(code=code), patch.object(mcp_cli, "add_server", side_effect=exc):
                with self.assertRaises(PageError) as caught:
                    self.add(name="kept", kind="command", command=["x"], targets=[], vars=vars_)
                self.assertEqual(caught.exception.code, code)
                # The replaced value comes back and the new one is gone.
                self.assertEqual(load_secrets(), before)

    def test_a_failed_secret_write_puts_the_earlier_ones_back(self):
        from quiver.mcp import secrets
        real = secrets.set_secret
        calls = []

        def flaky(name, value, home=None):
            calls.append(name)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(name, value, home)

        vars_ = [{"key": "A_KEY", "value": self.TOKEN, "secret": True},
                 {"key": "B_KEY", "value": "second-value-0000", "secret": True}]
        with patch.object(secrets, "set_secret", side_effect=flaky):
            with self.assertRaises(PageError) as caught:
                self.add(name="flaky", kind="command", command=["x"], targets=[], vars=vars_)
        self.assertEqual(caught.exception.code, "add_failed")
        self.assertEqual(secrets.load_secrets(), {})

    def test_secrets_stay_when_the_hub_was_written_before_a_harness_failed(self):
        from quiver.mcp.secrets import load_secrets
        real = mcp_cli.add_server

        def hub_then_fail(name, canonical, targets, **kw):
            real(name, canonical, [], **kw)
            raise OSError("harness config is read-only")

        with patch.object(mcp_cli, "add_server", side_effect=hub_then_fail):
            with self.assertRaises(PageError):
                self.add(name="half", kind="command", command=["x"], targets=["claude"],
                         vars=[{"key": "API_KEY", "value": self.TOKEN, "secret": True}])
        # The hub's ${HALF_API_KEY} still resolves.
        self.assertEqual(load_secrets()["HALF_API_KEY"], self.TOKEN)

    def test_cleanup_leaves_a_value_that_changed_since(self):
        from quiver.mcp.secrets import load_secrets, set_secret

        def someone_else_then_fail(*a, **kw):
            set_secret("RACE_API_KEY", "written-by-another-0")
            raise OSError("full")

        with patch.object(mcp_cli, "add_server", side_effect=someone_else_then_fail):
            with self.assertRaises(PageError):
                self.add(name="race", kind="command", command=["x"], targets=[],
                         vars=[{"key": "API_KEY", "value": self.TOKEN, "secret": True}])
        self.assertEqual(load_secrets()["RACE_API_KEY"], "written-by-another-0")

    def test_a_secret_another_server_refers_to_is_not_overwritten(self):
        self.add(name="a-b", kind="command", command=["x"], targets=[],
                 vars=[{"key": "TOKEN", "value": "first-value-0000", "secret": True}])
        error = self.refused(name="a_b", kind="command", command=["y"], targets=[],
                             vars=[{"key": "TOKEN", "value": self.TOKEN, "secret": True}])
        self.assertEqual((error.status, error.code), (409, "secret_taken"))
        self.assertIn("a-b", error.message)
        # Replacing the same server may change its own value.
        self.add(name="a-b", kind="command", command=["x"], targets=[], force=True,
                 vars=[{"key": "TOKEN", "value": "second-value-000", "secret": True}])
        from quiver.mcp.secrets import load_secrets
        self.assertEqual(load_secrets()["A_B_TOKEN"], "second-value-000")

    def test_a_bearer_token_is_a_stored_secret_in_the_header(self):
        reply = self.add(name="lin", kind="url", url="https://mcp.linear.app/mcp", transport="http",
                         sign_in="bearer", token=f" {self.TOKEN} ", targets=["claude"], vars=[])
        self.assertNotIn(self.TOKEN, json.dumps(reply))
        hub = json.loads(self.hub.read_text())["mcpServers"]["lin"]
        self.assertEqual(hub, {"url": "https://mcp.linear.app/mcp",
                               "headers": {"Authorization": "Bearer ${LIN_TOKEN}"}})
        from quiver.mcp.secrets import load_secrets
        self.assertEqual(load_secrets()["LIN_TOKEN"], self.TOKEN)
        claude = json.loads(self.claude.read_text())["mcpServers"]["lin"]
        self.assertEqual(claude["headers"]["Authorization"], f"Bearer {self.TOKEN}")

    def test_oauth_writes_no_header(self):
        self.add(name="notion2", kind="url", url="https://mcp.notion.com/mcp", sign_in="oauth", targets=[])
        self.assertEqual(json.loads(self.hub.read_text())["mcpServers"]["notion2"], {"url": "https://mcp.notion.com/mcp"})

    def test_sign_in_refusals(self):
        url = {"name": "r", "kind": "url", "url": "https://x", "targets": []}
        cases = [({"name": "c", "kind": "command", "command": ["x"], "sign_in": "bearer", "token": "t"}, (422, "bad_sign_in")),
                 ({**url, "sign_in": "basic"}, (422, "bad_sign_in")),
                 ({**url, "sign_in": "bearer", "token": "  "}, (422, "bad_value")),
                 ({**url, "sign_in": "bearer", "token": self.TOKEN,
                   "vars": [{"key": "Authorization", "value": "x", "secret": False}]}, (422, "bad_vars"))]
        for body, want in cases:
            error = self.refused(**body)
            self.assertEqual((error.status, error.code), want, body)

    def test_the_page_says_which_harness_takes_what(self):
        caps = mcp.get(_ctx(), {})["caps"]
        self.assertEqual(caps["claude"], {"remote": True, "sse": True})
        self.assertEqual(caps["codex"], {"remote": True, "sse": False})
        desktop = {"path": self.home / "desktop.json", "key": "mcpServers", "local_only": True}
        with patch.dict(mcp_cli.MCP_CONFIG_MAP, {"claude-desktop": desktop}):
            self.assertEqual(mcp._caps(["claude-desktop"]), {"claude-desktop": {"remote": False, "sse": False}})

    def test_secret_names_follow_the_app(self):
        self.assertEqual(mcp.secret_name("my-server", "api.key"), "MY_SERVER_API_KEY")
        self.assertEqual(mcp.secret_name("1x", "Token"), "MCP_1X_TOKEN")
        self.assertEqual(mcp.secret_name("a", "__b__"), "A_B")

    def test_refusals(self):
        cmd = {"kind": "command", "command": ["npx", "x"], "targets": []}
        cases = [
            ({"name": "_bad", **cmd}, (422, "bad_name")),
            ({"name": "ok", "kind": "pipe"}, (422, "bad_kind")),
            ({"name": "ok", "kind": "command", "command": []}, (422, "bad_command")),
            ({"name": "ok", "kind": "url", "url": "  "}, (422, "bad_url")),
            ({"name": "ok", "kind": "url", "url": "https://x", "transport": "ws"}, (422, "bad_transport")),
            ({"name": "ok", **cmd, "targets": ["nope"]}, (422, "add_refused")),
            ({"name": "ok", **cmd, "vars": [{"key": "K", "value": 3}]}, (422, "bad_vars")),
            ({"name": "ok", **cmd, "vars": [{"key": "TOKEN", "value": " ", "secret": True}]}, (422, "bad_value")),
            ({"name": "ok", **cmd, "vars": [{"key": "TOKEN", "value": "a\nb" + self.TOKEN, "secret": True}]},
             (422, "bad_value")),
            ({"name": "linear", **cmd, "targets": ["claude"],
              "vars": [{"key": "TOKEN", "value": self.TOKEN, "secret": True}]}, (409, "exists")),
            ({"name": "ok", **cmd, "force": "yes"}, (422, "bad_force")),
        ]
        for body, want in cases:
            error = self.refused(**body)
            self.assertEqual((error.status, error.code), want, body.get("name"))


class TestRemove(_SyncHome, unittest.TestCase):
    """Remove: swe mcp remove for the places the sheet's confirm listed."""

    def remove(self, **body):
        return mcp.post(_ctx(), "remove", body)

    def refused(self, **body):
        before = self.snapshot()
        with self.assertRaises(PageError) as caught:
            self.remove(**body)
        self.assertEqual(self.snapshot(), before, "a refusal must write nothing")
        return caught.exception

    def test_removes_from_every_place_named_and_keeps_the_rest(self):
        reply = self.remove(server="linear", **{"from": ["claude", "cursor"]})
        self.assertEqual((reply["removed"], reply["absent"]), (["claude", "cursor"], []))
        claude = json.loads(self.claude.read_text())
        self.assertNotIn("linear", claude["mcpServers"])
        self.assertEqual(claude["numStartups"], 7)
        self.assertEqual(list(json.loads(self.cursor.read_text())["mcpServers"]), ["keep"])
        self.assertNotIn("linear", [s["name"] for s in reply["page"]["servers"]])

    def test_the_hub_is_quiver(self):
        reply = self.remove(server="notion", **{"from": ["quiver"]})
        self.assertEqual(reply["removed"], ["quiver"])
        self.assertEqual(json.loads(self.hub.read_text())["mcpServers"], {})

    def test_a_place_left_off_the_confirm_keeps_it(self):
        self.remove(server="linear", **{"from": ["cursor"]})
        self.assertIn("linear", json.loads(self.claude.read_text())["mcpServers"])

    def test_refusals(self):
        cases = [({"server": "nope", "from": ["claude"]}, (404, "unknown_server")),
                 ({"server": "linear", "from": ["codex"]}, (409, "state_changed")),
                 ({"server": "linear", "from": ["quiver"]}, (409, "state_changed")),
                 ({"server": "linear", "from": []}, (422, "bad_from")),
                 ({"server": "linear", "from": "claude"}, (422, "bad_from")),
                 ({"server": "", "from": ["claude"]}, (422, "bad_server"))]
        for body, want in cases:
            error = self.refused(**body)
            self.assertEqual((error.status, error.code), want, body)


class TestSyncRoute(_SyncHome, HubBase):
    def origin(self):
        return f"http://127.0.0.1:{self.srv.port}"

    def test_needs_a_device(self):
        before = self.snapshot()
        status, _ = self.srv.request("POST", "/v1/app/mcp/preview", body=LINEAR)
        self.assertEqual(status, 401)
        status, _ = self.srv.request("POST", "/v1/app/mcp/sync", body={**LINEAR, "fingerprint": "x"})
        self.assertEqual(status, 401)
        self.assertEqual(self.snapshot(), before)

    def test_preview_then_sync_over_the_route(self):
        auth = {**self.auth(), "Origin": self.origin()}
        status, data = self.srv.request("POST", "/v1/app/mcp/preview", body=LINEAR, headers=auth)
        self.assertEqual(status, 200, data)
        fingerprint = json.loads(data)["fingerprint"]
        status, data = self.srv.request("POST", "/v1/app/mcp/sync",
                                        body={**LINEAR, "fingerprint": fingerprint}, headers=auth)
        self.assertEqual(status, 200, data)
        self.assertIn("linear", json.loads(self.cursor.read_text())["mcpServers"])
        self.assertNotIn(LINEAR_TOKEN.encode(), data)
        status, data = self.srv.request("POST", "/v1/app/mcp/sync",
                                        body={**LINEAR, "fingerprint": fingerprint}, headers=auth)
        self.assertEqual(status, 409, data)  # the plan it described has happened
        self.assertIn("target_changed", data.decode())

    def test_add_over_the_route(self):
        token = "ghp_addsecretvalue00001234"
        body = {"name": "routed", "kind": "command", "command": ["echo"],
                "vars": [{"key": "API_KEY", "value": token, "secret": True}], "targets": []}
        status, _ = self.srv.request("POST", "/v1/app/mcp/add", body=body,
                                     headers={**self.auth(), "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, data = self.srv.request("POST", "/v1/app/mcp/add", body=body,
                                        headers={**self.auth(), "Origin": self.origin()})
        self.assertEqual(status, 200, data)
        self.assertNotIn(token.encode(), data)

    def test_secrets_over_the_route(self):
        token = "ntn_abcdefghijklmnopqrst1234"
        auth = {**self.auth(), "Origin": self.origin()}
        status, _ = self.srv.request("POST", "/v1/app/mcp/secret_set", body={"name": "NOTION_TOKEN", "value": token},
                                     headers={**self.auth(), "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, data = self.srv.request("POST", "/v1/app/mcp/secret_set",
                                        body={"name": "NOTION_TOKEN", "value": token}, headers=auth)
        self.assertEqual(status, 200, data)
        self.assertNotIn(token.encode(), data)
        _, data = self.srv.request("GET", "/v1/app/mcp", headers=auth)
        self.assertNotIn(token.encode(), data)
        status, data = self.srv.request("POST", "/v1/app/mcp/secret_value", body={"name": "NOTION_TOKEN"}, headers=auth)
        self.assertEqual((status, json.loads(data)["value"]), (200, token))

    def test_remove_over_the_route(self):
        before = self.snapshot()
        status, _ = self.srv.request("POST", "/v1/app/mcp/remove", body={"server": "linear", "from": ["claude"]},
                                     headers={**self.auth(), "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.snapshot(), before)
        status, data = self.srv.request("POST", "/v1/app/mcp/remove", body={"server": "linear", "from": ["claude"]},
                                        headers={**self.auth(), "Origin": self.origin()})
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["removed"], ["claude"])
        self.assertNotIn(LINEAR_TOKEN.encode(), data)

    def test_a_cross_site_post_is_refused(self):
        before = self.snapshot()
        status, data = self.srv.request("POST", "/v1/app/mcp/preview", body=LINEAR,
                                        headers={**self.auth(), "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("bad_origin", data.decode())
        self.assertEqual(self.snapshot(), before)

    def test_refusal_reaches_the_page(self):
        status, data = self.srv.request("POST", "/v1/app/mcp/preview",
                                        body={"server": "notion", "source": "quiver", "targets": ["claude"]},
                                        headers=self.auth())
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(data)["error"]["code"], "secrets_missing")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestLeavingForgetsSecrets(unittest.TestCase):
    """pages/mcp.js under node: navigating away removes both sheets with
    #panel and fires no close, so the hashchange handler drops the Add
    draft and the values a server's sheet revealed or had typed in."""

    def test_leaving_drops_the_add_draft_and_revealed_values(self):
        page = Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/pages/mcp.js"
        prelude = (
            "const listeners = [];\n"
            "globalThis.window = { addEventListener(t, f) { if (t === 'hashchange') listeners.push(f); } };\n"
            "globalThis.localStorage = { getItem() { return null; }, setItem() {} };\n"
            "globalThis.location = { hash: '#/mcp' };\n"
            "function registerPage() {}\n" + app_js_function("currentRoute"))
        body = (
            "const st = () => ({ revealed: new Map([['TOKEN', 'shh']]), revealGen: 0,"
            " drafts: new Map([['KEY', 'typed']]), replacing: new Set(['KEY']), secretError: 'x' });\n"
            "const fire = (hash) => { location.hash = hash; listeners.forEach((f) => f()); };\n"
            "mcpPage.add = { line: 'npx srv --token=abc' }; mcpPage.sheet = st();\n"
            "fire('#/mcp?x=1');\n"
            "const stayed = [mcpPage.add !== null, mcpPage.sheet.revealed.size];\n"
            "fire('#/overview');\n"
            "const s = mcpPage.sheet;\n"
            "process.stdout.write(JSON.stringify({ stayed, add: mcpPage.add, revealed: s.revealed.size,"
            " drafts: s.drafts.size, replacing: s.replacing.size, gen: s.revealGen, error: s.secretError }));\n")
        src = prelude + page.read_text(encoding="utf-8") + "\n" + body
        done = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"stayed": [True, 1], "add": None, "revealed": 0, "drafts": 0,
                                                  "replacing": 0, "gen": 1, "error": None})


if __name__ == "__main__":
    unittest.main()
