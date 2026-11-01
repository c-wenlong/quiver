"""`swe mcp add` / `swe mcp remove`: hub writes, harness fan-out,
conflicts, secret indirection, and hub-only inventory rows."""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

MCP_MODULE = "swe.mcp"
PROJECT_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def _mcp_env(home: pathlib.Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(PROJECT_SRC) if not existing else f"{PROJECT_SRC}{os.pathsep}{existing}"
    )
    return env


class McpAddRemoveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        cfg = self.home / ".swe" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "tools.json").write_text(
            json.dumps({"claude": {"aliases": ["cc"]}, "codex": {"aliases": ["cx"]}})
            + "\n"
        )
        (self.home / ".swe" / "mcp.json").write_text('{"mcpServers": {}}\n')
        (self.home / ".claude.json").write_text('{"mcpServers": {}}\n')
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text('model = "x"\n')

    def tearDown(self):
        self.tmp.cleanup()

    def run_mcp(self, *args, stdin=""):
        return subprocess.run(
            [sys.executable, "-m", MCP_MODULE, *args],
            env=_mcp_env(self.home),
            input=stdin,
            capture_output=True,
            text=True,
        )

    def hub(self):
        return json.loads((self.home / ".swe" / "mcp.json").read_text())["mcpServers"]

    def claude(self):
        return json.loads((self.home / ".claude.json").read_text())["mcpServers"]

    def codex(self):
        return (self.home / ".codex" / "config.toml").read_text()

    def test_add_stdio_hub_only(self):
        r = self.run_mcp("add", "gh", "--", "npx", "-y", "gh-mcp")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        entry = self.hub()["gh"]
        self.assertEqual(entry["command"], "npx")
        self.assertEqual(entry["args"], ["-y", "gh-mcp"])
        self.assertEqual(self.claude(), {})

    def test_args_after_double_dash_are_verbatim(self):
        r = self.run_mcp("add", "s", "--", "cmd", "--x=y", "a,b")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hub()["s"]["args"], ["--x=y", "a,b"])

    def test_add_to_harnesses_resolves_and_converts(self):
        self.run_mcp("secret", "set", "TOK", stdin="tok-val\n")
        r = self.run_mcp(
            "add", "srv", "--env=TOK=${TOK}", "--to=claude,codex",
            "--", "srv-bin", "run",
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hub()["srv"]["env"]["TOK"], "${TOK}")
        self.assertEqual(self.claude()["srv"]["env"]["TOK"], "tok-val")
        self.assertIn("[mcp_servers.srv]", self.codex())
        self.assertIn("[mcp_servers.srv.env]", self.codex())
        self.assertIn('TOK = "tok-val"', self.codex())
        self.assertIn('command = "srv-bin"', self.codex())

    def test_literal_credential_warns_not_refuses(self):
        r = self.run_mcp("add", "s", "--env=API_KEY=literal", "--", "c")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("API_KEY looks like a credential", r.stderr)
        self.assertEqual(self.hub()["s"]["env"]["API_KEY"], "literal")

    def test_unresolved_ref_with_to_refuses_everything(self):
        r = self.run_mcp("add", "s", "--env=TOK=${MISSING}",
                         "--to=claude", "--", "c")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no value for MISSING", r.stdout + r.stderr)
        self.assertNotIn("s", self.hub())
        self.assertNotIn("s", self.claude())

    def test_conflict_refuses_without_force(self):
        self.run_mcp("add", "s", "--", "c")
        before = self.hub()["s"]
        r = self.run_mcp("add", "s", "--", "other")
        self.assertEqual(r.returncode, 1)
        self.assertIn("already exists", r.stdout + r.stderr)
        self.assertEqual(self.hub()["s"], before)

    def test_force_replaces(self):
        self.run_mcp("add", "s", "--", "c")
        r = self.run_mcp("add", "s", "--force", "--", "other", "a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hub()["s"]["command"], "other")

    def test_url_with_headers_and_sse(self):
        r = self.run_mcp(
            "add", "remote", "--url=https://x.example/mcp",
            "--transport=sse", "--header=Authorization=Bearer ${TOK}",
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        entry = self.hub()["remote"]
        self.assertEqual(entry["url"], "https://x.example/mcp")
        self.assertEqual(entry["headers"]["Authorization"], "Bearer ${TOK}")

    def test_bad_name_and_mode_conflicts(self):
        for args in (
            ("add", "-bad", "--", "c"),
            ("add", "_bad", "--", "c"),
            ("add", "s", "--url=https://x", "--", "c"),
            ("add", "s"),
            ("add", "s", "--url=https://x", "--env=A=B"),
            ("add", "s", "--header=A=B", "--", "c"),
        ):
            r = self.run_mcp(*args)
            self.assertEqual(r.returncode, 1, f"{args}: {r.stdout}{r.stderr}")

    def test_json_shape(self):
        r = self.run_mcp("add", "s", "--env=A=B", "--to=claude", "--json", "--", "c")
        doc = json.loads(r.stdout)
        self.assertEqual(doc["data"]["name"], "s")
        self.assertEqual(doc["data"]["hub"], "added")
        self.assertEqual(doc["data"]["targets"],
                         [{"name": "claude", "status": "added"}])
        self.assertNotIn("A=B", r.stdout)

    def test_remove(self):
        self.run_mcp("add", "s", "--to=claude", "--", "c")
        r = self.run_mcp("remove", "s", "--from=claude,quiver", "--json")
        doc = json.loads(r.stdout)
        self.assertEqual(sorted(doc["data"]["removed"]), ["claude", "swe"])
        self.assertNotIn("s", self.hub())
        self.assertNotIn("s", self.claude())

    def test_remove_absent_and_nothing_removed(self):
        r = self.run_mcp("remove", "ghost", "--from=claude", "--json")
        self.assertEqual(r.returncode, 1)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["data"]["removed"], [])
        self.assertEqual(doc["data"]["absent"], ["claude"])

    def test_remove_all(self):
        self.run_mcp("add", "s", "--to=claude", "--", "c")
        r = self.run_mcp("remove", "s", "--from=all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hub(), {})
        self.assertEqual(self.claude(), {})

    def test_command_tail_keeps_flag_like_words(self):
        # The split at `--` happens before --json/--to parsing, so words
        # shaped like flags stay part of the command verbatim.
        r = self.run_mcp("add", "x", "--", "tool", "--json", "--to=y", "a,b")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hub()["x"]["args"], ["--json", "--to=y", "a,b"])
        self.assertNotIn('"schema"', r.stdout)  # plain output, not JSON

    def _in_home(self):
        """Run in-process cmd_* against the throwaway HOME."""
        from unittest import mock

        from swe.mcp import cli

        return mock.patch.multiple(
            cli,
            MCP_SOURCE_FILE=self.home / ".swe" / "mcp.json",
        ), mock.patch.dict(os.environ, {"HOME": str(self.home)})

    def test_to_without_a_backend_refuses_before_writing(self):
        from unittest import mock
        from swe.mcp import cli

        env_patch, home_patch = self._in_home()
        with env_patch, home_patch, \
             mock.patch.object(cli, "get_tool_saver", return_value=None):
            code = cli.cmd_add(["s", "--to=claude", "--", "c"])
        self.assertEqual(code, 1)
        self.assertNotIn("s", self.hub())

    def test_remove_without_a_backend_refuses(self):
        from unittest import mock
        from swe.mcp import cli

        self.run_mcp("add", "s", "--to=claude", "--", "c")
        before = self.claude()
        env_patch, home_patch = self._in_home()
        with env_patch, home_patch, \
             mock.patch.object(cli, "get_tool_saver", return_value=None):
            code = cli.cmd_remove(["s", "--from=claude"])
        self.assertEqual(code, 1)
        self.assertEqual(self.claude(), before)

    def test_hub_only_rows_only_when_listing_everything(self):
        self.run_mcp("add", "only-hub", "--", "c")
        self.run_mcp("add", "shared", "--to=claude", "--", "c")
        all_rows = json.loads(self.run_mcp("list", "--json").stdout)["data"]["servers"]
        self.assertIn("only-hub", [s["name"] for s in all_rows])
        one = json.loads(
            self.run_mcp("list", "claude", "--json").stdout)["data"]["servers"]
        self.assertNotIn("only-hub", [s["name"] for s in one])
        self.assertTrue(all(s["in_hub"] is True for s in one
                            if s["name"] == "shared"))

    def test_error_branches(self):
        cases = [
            # bad flag, bare value flag, missing = in a pair, misuse
            ["add", "s", "--bogus", "--", "c"],
            ["add", "s", "--env", "--", "c"],          # bare --env
            ["add", "s", "--env=NOEQUAL", "--", "c"],
            ["add", "s", "--transport=sse", "--", "c"],
            ["add", "s", "--url=https://x", "--transport=grpc"],
            ["add", "s", "--to=nosuch", "--", "c"],
            ["add", "s", "--to=bad/../x", "--", "c"],
            ["add", "s", "--json", "--url=https://x", "--header=BROKEN"],
            ["remove", "s"],                          # --from required
            ["remove", "s", "--from=nosuch"],
            ["remove", "s", "--from=bad/../x"],
            ["remove", "s", "--bogus", "--from=claude"],
            ["remove", "s", "extra", "--from=claude"],
            ["add"],
            ["add", "s", "extra", "--", "c"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                r = self.run_mcp(*argv)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.hub(), {})
        self.assertEqual(self.claude(), {})

    def test_plain_output_paths(self):
        self.run_mcp("add", "s", "--to=claude", "--", "c")
        r = self.run_mcp("remove", "s", "--from=quiver")
        self.assertIn("Removed s from: swe", r.stdout)
        r = self.run_mcp("remove", "s", "--from=codex")
        self.assertIn("not in: codex", r.stdout)
        # Adding again while claude still holds it needs --force.
        r = self.run_mcp("add", "s", "--to=claude", "--", "c")
        self.assertEqual(r.returncode, 1)
        r = self.run_mcp("add", "s", "--to=claude", "--force", "--", "c2")
        self.assertIn("replaced in claude", r.stdout)
        self.assertEqual(self.hub()["s"]["command"], "c2")

    def test_remove_accepts_existing_names_with_spaces_or_underscore(self):
        hub = self.home / ".swe" / "mcp.json"
        hub.write_text(json.dumps(
            {"mcpServers": {"my server": {"command": "x"}, "_local": {"command": "y"}}}))
        r = self.run_mcp("remove", "my server", "--from=quiver")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("my server", r.stdout)
        r = self.run_mcp("remove", "_local", "--from=quiver")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.hub(), {})

    def test_sse_refuses_a_format_that_cannot_express_it(self):
        cfg = self.home / ".swe" / "config"
        tools = json.loads((cfg / "tools.json").read_text())
        tools["opencode"] = {"aliases": ["oc"]}
        (cfg / "tools.json").write_text(json.dumps(tools))
        r = self.run_mcp("add", "r", "--url=https://x/sse", "--transport=sse",
                         "--to=opencode")
        self.assertEqual(r.returncode, 1)
        self.assertIn("can't use an SSE", r.stdout + r.stderr)
        self.assertNotIn("r", self.hub())

    def test_sse_survives_to_a_capable_format(self):
        r = self.run_mcp("add", "r", "--url=https://x/sse", "--transport=sse",
                         "--to=claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.claude()["r"]["type"], "sse")
        self.assertEqual(self.hub()["r"]["type"], "sse")

    def test_sync_of_an_sse_hub_server_keeps_the_transport(self):
        hub = self.home / ".swe" / "mcp.json"
        hub.write_text(json.dumps({"mcpServers": {
            "r": {"url": "https://x/sse", "type": "sse"}}}))
        r = self.run_mcp("sync", "quiver", "claude", "--no-interactive")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.claude()["r"]["type"], "sse")

    def test_hub_only_row_in_inventory(self):
        self.run_mcp("add", "only-hub", "--", "c")
        self.run_mcp("add", "shared", "--to=claude", "--", "c")
        r = self.run_mcp("list", "--json")
        rows = {s["name"]: s for s in json.loads(r.stdout)["data"]["servers"]}
        self.assertEqual(rows["only-hub"]["in_hub"], True)
        self.assertEqual(rows["only-hub"]["harnesses"], [])
        self.assertEqual(rows["only-hub"]["command"], "c")
        self.assertEqual(rows["shared"]["in_hub"], True)


if __name__ == "__main__":
    unittest.main()


class HttpTransportRoundTripTest(unittest.TestCase):
    """An explicit and an implicit http read the same; sse stays distinct."""

    def test_strict_sees_no_change_for_http_either_way(self):
        from swe.mcp.formats import get_conversion_issues

        plain = {"url": "https://a/mcp"}
        explicit = {"url": "https://a/mcp", "type": "http"}
        self.assertEqual(get_conversion_issues(plain, "standard", "droid"), [])
        self.assertEqual(get_conversion_issues(explicit, "droid", "standard"), [])

    def test_sse_survives_standard_to_droid(self):
        from swe.mcp.formats import get_conversion_issues, get_format_handler

        sse = {"url": "https://a/sse", "type": "sse"}
        self.assertEqual(get_conversion_issues(sse, "standard", "droid"), [])
        canonical = get_format_handler("standard").parse(sse)
        self.assertEqual(get_format_handler("droid").emit(canonical)["type"], "sse")
