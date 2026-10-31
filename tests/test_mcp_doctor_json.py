"""`--json` for `swe mcp list`, `swe mcp status` and `swe doctor`.

The macOS app's MCP and Doctor pages decode stdout whole, so each command
prints one ``{"schema": 1, "data": ...}`` document and nothing else. These
run the real CLI against a throwaway HOME with fixture harness configs.

MCP configs carry API keys in ``env`` and ``headers``, and the hub keeps
``${REF}`` placeholders that resolve from ``~/.quiver/secrets/mcp/.api_keys``.
Nothing here may ever put a secret value on stdout.
"""

import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from quiver.harness.commands import cmd_doctor
from quiver.harness.drift import Finding
from quiver.console import strip_ansi
from quiver.harness.path_health import NodeEnv, OffPathHit

try:  # `unittest discover -s tests` puts tests/ itself on sys.path
    from test_json_output import decode, run_swe, write_registry
except ImportError:  # `python -m unittest tests.test_mcp_doctor_json`
    from tests.test_json_output import decode, run_swe, write_registry

FAKE_KEY = "sk-fake-0123456789abcdefghijklmnopqrstuv"
LITERAL_KEY = "lit-9f8e7d6c5b4a39281706f5e4d3c2b1a0"

REGISTRY = {
    "claude": {"command": "claude", "aliases": ["cc"]},
    "cursor": {"command": "cursor", "aliases": ["cs"]},
}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def write_claude(home: Path, servers: dict) -> None:
    write_json(home / ".claude.json", {"mcpServers": servers})


def write_cursor(home: Path, servers: dict) -> None:
    write_json(home / ".cursor" / "mcp.json", {"mcpServers": servers})


def by_name(result) -> dict:
    return {row["name"]: row for row in decode(result)["data"]["servers"]}


class McpListJsonTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        write_registry(self.home, REGISTRY)
        write_claude(self.home, {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": FAKE_KEY},
            },
            "docs": {
                "type": "http",
                "url": "https://docs.example.com/mcp",
                "headers": {"Authorization": "Bearer " + FAKE_KEY},
            },
            "shared": {"command": "uvx", "args": ["tool"]},
            "clash": {"command": "node", "args": ["a.js"]},
        })
        write_cursor(self.home, {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": FAKE_KEY},
            },
            "shared": {"command": "uvx", "args": ["tool"]},
            "clash": {"command": "node", "args": ["b.js"]},
            "local-only": {"command": "/opt/bin/local-server"},
        })

    def test_one_entry_per_server_name_in_an_envelope(self):
        result = run_swe(self.home, "mcp", "list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = decode(result)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(
            [row["name"] for row in doc["data"]["servers"]],
            ["clash", "docs", "github", "local-only", "shared"])

    def test_stdio_server_carries_command_args_and_key_names(self):
        github = by_name(run_swe(self.home, "mcp", "list", "--json"))["github"]
        self.assertEqual(github["transport"], "stdio")
        self.assertEqual(github["command"], "npx")
        self.assertEqual(
            github["args"], ["-y", "@modelcontextprotocol/server-github"])
        self.assertIsNone(github["url"])
        self.assertEqual(github["env_keys"], ["GITHUB_TOKEN"])
        self.assertEqual(github["header_keys"], [])

    def test_http_server_carries_url_and_header_names(self):
        docs = by_name(run_swe(self.home, "mcp", "list", "--json"))["docs"]
        self.assertEqual(docs["transport"], "http")
        self.assertIsNone(docs["command"])
        self.assertEqual(docs["args"], [])
        self.assertEqual(docs["url"], "https://docs.example.com/mcp")
        self.assertEqual(docs["env_keys"], [])
        self.assertEqual(docs["header_keys"], ["Authorization"])

    def test_harnesses_lists_every_config_the_server_appears_in(self):
        rows = by_name(run_swe(self.home, "mcp", "list", "--json"))
        self.assertEqual(rows["github"]["harnesses"], ["claude", "cursor"])
        self.assertEqual(rows["docs"]["harnesses"], ["claude"])
        self.assertEqual(rows["local-only"]["harnesses"], ["cursor"])

    def test_sse_type_is_reported_as_sse(self):
        write_claude(self.home, {
            "events": {"type": "sse", "url": "https://e.example.com/sse"},
        })
        events = by_name(run_swe(self.home, "mcp", "list", "--json"))["events"]
        self.assertEqual(events["transport"], "sse")
        self.assertEqual(events["url"], "https://e.example.com/sse")

    def test_mcp_remote_wrapper_is_reported_as_wrapped(self):
        write_claude(self.home, {
            "remote": {"command": "npx",
                       "args": ["mcp-remote", "https://r.example.com/mcp"]},
        })
        remote = by_name(run_swe(self.home, "mcp", "list", "--json"))["remote"]
        self.assertEqual(remote["transport"], "wrapped")
        self.assertEqual(remote["args"], ["mcp-remote", "https://r.example.com/mcp"])

    def test_opencode_command_list_splits_into_command_and_args(self):
        write_registry(self.home, {**REGISTRY, "opencode": {"command": "opencode"}})
        write_json(self.home / ".config" / "opencode" / "opencode.json", {
            "mcp": {"notion": {
                "type": "local",
                "command": ["node", "/opt/notion.js"],
                "environment": {"NOTION_TOKEN": FAKE_KEY},
                "enabled": True,
            }},
        })
        notion = by_name(run_swe(self.home, "mcp", "list", "--json"))["notion"]
        self.assertEqual(notion["transport"], "stdio")
        self.assertEqual(notion["command"], "node")
        self.assertEqual(notion["args"], ["/opt/notion.js"])
        self.assertEqual(notion["env_keys"], ["NOTION_TOKEN"])
        self.assertEqual(notion["harnesses"], ["opencode"])

    def test_tool_filter_keeps_working(self):
        result = run_swe(self.home, "mcp", "list", "cs", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = by_name(result)
        self.assertEqual(
            sorted(rows), ["clash", "github", "local-only", "shared"])
        self.assertEqual(rows["github"]["harnesses"], ["cursor"])

    def test_json_flag_may_come_before_the_tool(self):
        rows = by_name(run_swe(self.home, "mcp", "list", "--json", "cs"))
        self.assertEqual(rows["shared"]["harnesses"], ["cursor"])

    def test_no_servers_is_an_empty_list_not_a_sentence(self):
        home = Path(tempfile.mkdtemp())
        write_registry(home, REGISTRY)
        result = run_swe(home, "mcp", "list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = decode(result)["data"]
        self.assertEqual(data["servers"], [])
        self.assertIn("claude", data["harnesses"])

    def test_harnesses_names_every_config_read_even_with_no_servers(self):
        data = decode(run_swe(self.home, "mcp", "list", "--json"))["data"]
        self.assertIn("claude", data["harnesses"])
        self.assertIn("cursor", data["harnesses"])
        # claude-desktop is always read and holds nothing here
        self.assertIn("claude-desktop", data["harnesses"])
        filtered = decode(run_swe(self.home, "mcp", "list", "cs", "--json"))["data"]
        self.assertEqual(filtered["harnesses"], ["cursor"])

    def test_unknown_tool_fails_with_nothing_on_stdout(self):
        result = run_swe(self.home, "mcp", "list", "nope", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("Unknown tool", result.stderr)

    def test_a_server_that_is_not_an_object_is_unknown_not_a_crash(self):
        write_claude(self.home, {"broken": "just a string"})
        broken = by_name(run_swe(self.home, "mcp", "list", "--json"))["broken"]
        self.assertEqual(broken["transport"], "unknown")
        self.assertIsNone(broken["command"])
        self.assertEqual(broken["args"], [])


class McpListConflictTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        write_registry(self.home, REGISTRY)

    def rows(self) -> dict:
        result = run_swe(self.home, "mcp", "list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return by_name(result)

    def test_same_name_defined_differently_is_flagged(self):
        write_claude(self.home, {"clash": {"command": "node", "args": ["a.js"]}})
        write_cursor(self.home, {"clash": {"command": "node", "args": ["b.js"]}})
        clash = self.rows()["clash"]
        self.assertTrue(clash["conflict"])
        self.assertEqual(clash["harnesses"], ["claude", "cursor"])
        self.assertEqual(clash["variants"], [["claude"], ["cursor"]])

    def test_variants_group_the_harnesses_that_agree(self):
        write_registry(self.home, {**REGISTRY, "droid": {"command": "droid"}})
        write_claude(self.home, {"s": {"command": "node", "args": ["a.js"]}})
        write_cursor(self.home, {"s": {"command": "node", "args": ["b.js"]}})
        write_json(self.home / ".factory" / "mcp.json",
                   {"mcpServers": {"s": {"command": "node", "args": ["a.js"]}}})
        s = self.rows()["s"]
        self.assertEqual(sorted(map(sorted, s["variants"])), [["claude", "droid"], ["cursor"]])
        self.assertEqual(s["variants"][0][0], "claude")

    def test_identical_definitions_do_not_conflict(self):
        server = {"command": "uvx", "args": ["tool"], "env": {"K": "v"}}
        write_claude(self.home, {"same": server})
        write_cursor(self.home, {"same": dict(server)})
        same = self.rows()["same"]
        self.assertFalse(same["conflict"])
        self.assertEqual(same["variants"], [["claude", "cursor"]])

    def test_a_server_in_one_harness_cannot_conflict(self):
        write_claude(self.home, {"solo": {"command": "uvx"}})
        self.assertFalse(self.rows()["solo"]["conflict"])

    def test_different_env_value_is_a_conflict_without_showing_either(self):
        write_claude(self.home, {"s": {"command": "x", "env": {"K": "one-value"}}})
        write_cursor(self.home, {"s": {"command": "x", "env": {"K": "two-value"}}})
        result = run_swe(self.home, "mcp", "list", "--json")
        self.assertTrue(by_name(result)["s"]["conflict"])
        self.assertNotIn("one-value", result.stdout)
        self.assertNotIn("two-value", result.stdout)

    def test_sse_against_http_at_one_url_is_a_conflict(self):
        write_claude(self.home, {"t": {"type": "sse", "url": "https://h.example/mcp"}})
        write_cursor(self.home, {"t": {"type": "http", "url": "https://h.example/mcp"}})
        self.assertTrue(self.rows()["t"]["conflict"])

    def test_formatting_differences_between_harnesses_are_not_conflicts(self):
        # Cursor spells the transport out and a copilot-style config adds an
        # empty args list. Neither changes what the server is.
        write_claude(self.home, {"fmt": {"command": "uvx"}})
        write_cursor(self.home, {"fmt": {"type": "stdio", "command": "uvx", "args": []}})
        self.assertFalse(self.rows()["fmt"]["conflict"])


class _Head200(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class McpStatusJsonTest(unittest.TestCase):
    """Health comes from the checks `swe mcp status` already runs. Every
    fixture here fails or passes without leaving the machine: a missing
    binary, an unset env var, a scheme that is not http, a local server, a
    port nothing listens on."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Head200)
        cls.up_url = f"http://127.0.0.1:{cls.server.server_port}/mcp"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        write_registry(self.home, REGISTRY)
        write_claude(self.home, {
            "ok-stdio": {"command": sys.executable, "args": ["-V"],
                         "env": {"TOKEN": "present"}},
            "no-bin": {"command": "/nonexistent/bin/server"},
            "not-in-path": {"command": "zz-not-a-real-binary"},
            "no-env": {"command": sys.executable,
                       "env": {"API_KEY": "", "FINE": "x"}},
            "placeholder": {"command": sys.executable,
                            "env": {"API_KEY": "YOUR_API_KEY"}},
            "bad-scheme": {"url": "ftp://files.example.com/mcp"},
            "http-up": {"url": self.up_url},
            "http-down": {"url": "http://127.0.0.1:1/mcp"},
            "wrapped-down": {"command": "npx",
                             "args": ["mcp-remote", "http://127.0.0.1:1/mcp"]},
            "wrapped-no-url": {"command": "npx", "args": ["mcp-remote", "x"]},
            "empty": {"type": "stdio"},
        })

    def status(self, *extra) -> dict:
        result = run_swe(self.home, "mcp", "status", *extra, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = decode(result)
        self.assertEqual(doc["schema"], 1)
        return {row["name"]: row for row in doc["data"]["servers"]}

    def assert_health(self, rows, name, health, detail):
        self.assertEqual(
            (rows[name]["health"], rows[name]["detail"]), (health, detail), name)

    def test_healthy_servers_have_no_detail(self):
        rows = self.status()
        self.assert_health(rows, "ok-stdio", "ok", None)
        self.assert_health(rows, "http-up", "ok", None)

    def test_stdio_failures_name_the_problem(self):
        rows = self.status()
        self.assert_health(rows, "no-bin", "error", "binary not found")
        self.assert_health(
            rows, "not-in-path", "error", "'zz-not-a-real-binary' not in PATH")
        self.assert_health(rows, "no-env", "error", "missing env: API_KEY")
        self.assert_health(rows, "placeholder", "error", "missing env: API_KEY")

    def test_http_failures_name_the_problem(self):
        rows = self.status()
        self.assert_health(rows, "bad-scheme", "error", "invalid URL scheme")
        self.assert_health(rows, "http-down", "error", "url unreachable")

    def test_wrapped_servers_are_checked_through_their_url(self):
        rows = self.status()
        self.assert_health(rows, "wrapped-down", "error", "url unreachable")
        self.assert_health(rows, "wrapped-no-url", "error", "no url found")

    def test_a_server_with_no_command_or_url_is_unknown(self):
        rows = self.status()
        self.assert_health(rows, "empty", "unknown", "no command or url")

    def test_rows_keep_the_list_identity_fields(self):
        row = self.status()["no-env"]
        self.assertEqual(row["transport"], "stdio")
        self.assertEqual(row["command"], sys.executable)
        self.assertEqual(row["env_keys"], ["API_KEY", "FINE"])
        self.assertEqual(row["harnesses"], ["claude"])
        self.assertFalse(row["conflict"])

    def test_tool_filter_keeps_working(self):
        write_cursor(self.home, {"only-cursor": {"command": "/nonexistent/x"}})
        rows = self.status("cs")
        self.assertEqual(sorted(rows), ["only-cursor"])
        self.assertEqual(rows["only-cursor"]["health"], "error")

    def test_no_servers_is_an_empty_list(self):
        home = Path(tempfile.mkdtemp())
        write_registry(home, REGISTRY)
        result = run_swe(home, "mcp", "status", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = decode(result)["data"]
        self.assertEqual(data["servers"], [])
        self.assertIn("claude", data["harnesses"])

    def test_unknown_tool_fails_with_nothing_on_stdout(self):
        result = run_swe(self.home, "mcp", "status", "nope", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_text_status_still_prints_the_matrix(self):
        result = run_swe(self.home, "mcp", "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HEALTH", result.stdout)
        self.assertIn("binary not found", result.stdout)
        self.assertIn("url unreachable", result.stdout)


class McpListSecretSafetyTest(unittest.TestCase):
    """A fake key sits in a harness config and in .api_keys; it must not
    reach stdout from any of the commands that read those files."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        write_registry(self.home, REGISTRY)
        secrets = self.home / ".quiver" / "secrets"
        secrets.mkdir(parents=True)
        (secrets / ".api_keys").write_text(
            f"export GITHUB_TOKEN={FAKE_KEY}\n"
            "export DOCS_KEY=short-docs-key\n")
        write_json(self.home / ".quiver" / "mcp.json", {"mcpServers": {
            "github": {"command": "npx", "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}},
        }})
        write_claude(self.home, {
            "github": {
                "command": "npx",
                "args": ["server-github", "--token", FAKE_KEY,
                         "--api-key=" + LITERAL_KEY],
                "env": {"GITHUB_TOKEN": FAKE_KEY},
            },
            "docs": {
                "url": "https://docs.example.com/mcp?key=" + FAKE_KEY
                       + "&team=blue&token=" + LITERAL_KEY,
                "headers": {"Authorization": "Bearer " + FAKE_KEY,
                            "X-Docs-Key": "short-docs-key"},
            },
            "userinfo": {"url": "https://admin:" + LITERAL_KEY + "@host.example.com/mcp"},
            "hooked": {"url": "https://hooks.example.com/s/" + LITERAL_KEY + "/mcp"},
            "remote": {"command": "npx", "args": [
                "mcp-remote", "https://r.example.com/mcp",
                "--header", "Authorization: Bearer " + LITERAL_KEY]},
        })
        write_cursor(self.home, {
            "github": {"command": "npx", "env": {"GITHUB_TOKEN": FAKE_KEY}},
        })

    def assert_no_secret(self, *args):
        result = run_swe(self.home, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        decode(result)
        for secret in (FAKE_KEY, LITERAL_KEY, "short-docs-key"):
            self.assertNotIn(secret, result.stdout, args)
            self.assertNotIn(secret, result.stderr, args)
        return result

    def test_list_never_prints_a_secret_value(self):
        self.assert_no_secret("mcp", "list", "--json")

    def test_list_for_one_tool_never_prints_a_secret_value(self):
        self.assert_no_secret("mcp", "list", "cc", "--json")

    def test_list_for_the_hub_never_resolves_a_placeholder(self):
        result = self.assert_no_secret("mcp", "list", "quiver", "--json")
        github = by_name(result)["github"]
        self.assertEqual(github["env_keys"], ["GITHUB_TOKEN"])

    def test_status_never_prints_a_secret_value(self):
        self.assert_no_secret("mcp", "status", "--json")

    def test_names_survive_where_values_are_hidden(self):
        rows = by_name(self.assert_no_secret("mcp", "list", "--json"))
        self.assertEqual(rows["docs"]["header_keys"], ["Authorization", "X-Docs-Key"])
        self.assertEqual(rows["github"]["env_keys"], ["GITHUB_TOKEN"])
        # A known secret in a url or argument is shown as its reference.
        self.assertEqual(
            rows["docs"]["url"],
            "https://docs.example.com/mcp?key=${GITHUB_TOKEN}&team=***&token=***")
        self.assertIn("${GITHUB_TOKEN}", rows["github"]["args"])
        # An unknown credential in a url or flag is masked by shape.
        self.assertEqual(
            rows["userinfo"]["url"], "https://admin:***@host.example.com/mcp")
        self.assertEqual(rows["hooked"]["url"], "https://hooks.example.com/s/***/mcp")
        self.assertIn("--api-key=***", rows["github"]["args"])
        self.assertEqual(
            rows["remote"]["args"][-2:], ["--header", "Authorization: ***"])


class McpMalformedConfigJsonTest(unittest.TestCase):
    """A hand-edited config with odd value types must still produce one
    JSON document: the app has nothing to show for a traceback."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        write_registry(self.home, REGISTRY)
        write_claude(self.home, {
            "weird": {"command": "zz-not-a-real-binary", "args": [1, {"a": 1}],
                      "env": {"K": 5, "J": {"n": 1}}},
            "weird-url": {"url": 5},
        })
        write_cursor(self.home, {
            "weird": {"command": "zz-not-a-real-binary", "args": [1, {"a": 1}],
                      "env": {"K": 5, "J": {"n": 1}}},
        })

    def test_list_survives_non_string_values(self):
        result = run_swe(self.home, "mcp", "list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = by_name(result)
        self.assertEqual(rows["weird"]["args"], ["1", "{'a': 1}"])
        self.assertEqual(rows["weird"]["env_keys"], ["J", "K"])
        self.assertFalse(rows["weird"]["conflict"])

    def test_status_survives_non_string_values(self):
        result = run_swe(self.home, "mcp", "status", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = by_name(result)
        self.assertEqual(rows["weird"]["health"], "error")
        self.assertEqual(rows["weird-url"]["detail"], "invalid URL scheme")


class ScrubTest(unittest.TestCase):
    """The display scrubbing behind `url` and `args`, on its own."""

    def test_url_keeps_host_path_and_references(self):
        from quiver.mcp.secrets import scrub_url

        self.assertEqual(
            scrub_url("https://api.example.com/v1/mcp?token=${API_TOKEN}", {}),
            "https://api.example.com/v1/mcp?token=${API_TOKEN}")
        self.assertEqual(
            scrub_url("https://api.example.com/v1/mcp?flag&team=blue#top", {}),
            "https://api.example.com/v1/mcp?flag&team=***#***")

    def test_url_hides_a_username_that_is_the_credential(self):
        from quiver.mcp.secrets import scrub_url

        self.assertEqual(
            scrub_url("https://" + LITERAL_KEY + "@host.example.com/", {}),
            "https://***@host.example.com/")

    def test_url_that_cannot_be_parsed_is_hidden_whole(self):
        from quiver.mcp.secrets import scrub_url

        self.assertEqual(scrub_url("http://[bad", {}), "***")

    def test_args_hide_values_of_credential_flags_only(self):
        from quiver.mcp.secrets import scrub_args

        self.assertEqual(
            scrub_args(["--port", "8080", "--api-key", "abc", "--token=xyz",
                        "--verbose", "--password", "-q", "plain"], {}),
            ["--port", "8080", "--api-key", "***", "--token=***",
             "--verbose", "--password", "-q", "plain"])

    def test_args_keep_a_reference_and_its_header_name(self):
        from quiver.mcp.secrets import scrub_args

        self.assertEqual(
            scrub_args(["-H", "Authorization: Bearer ${GH}", "--header=X-Id: 42"], {}),
            ["-H", "Authorization: Bearer ${GH}", "--header=X-Id: ***"])

    def test_args_scrub_a_url_argument(self):
        from quiver.mcp.secrets import scrub_args

        self.assertEqual(
            scrub_args(["mcp-remote", "https://r.example.com/mcp?k=v"], {}),
            ["mcp-remote", "https://r.example.com/mcp?k=***"])

    def test_args_scrub_a_url_given_as_a_flag_value(self):
        from quiver.mcp.secrets import scrub_args

        self.assertEqual(
            scrub_args(["--url=https://h.example/mcp?token=literal-secret",
                        "--endpoint", "https://h.example/v1?key=abc"], {}),
            ["--url=https://h.example/mcp?token=***",
             "--endpoint", "https://h.example/v1?key=***"])

    def test_args_hide_an_inline_env_assignment(self):
        # `env API_KEY=... npx server` is a common wrapper in configs.
        from quiver.mcp.secrets import scrub_args

        self.assertEqual(
            scrub_args(["env", "OPENAI_API_KEY=sk-live-123", "LOG_LEVEL=debug",
                        "GH_TOKEN=${GH}", "npx", "server"], {}),
            ["env", "OPENAI_API_KEY=***", "LOG_LEVEL=debug",
             "GH_TOKEN=${GH}", "npx", "server"])


class DoctorJsonTest(unittest.TestCase):
    """The real CLI, with drift findings and off-PATH installs that do not
    depend on whether this machine has node."""

    FINDING_KEYS = {"check", "severity", "message", "fix"}

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def doctor(self, registry: dict):
        write_registry(self.home, registry)
        result = run_swe(self.home, "doctor", "--json")
        return result, decode(result)

    def finding(self, doc: dict, check: str) -> dict:
        found = [f for f in doc["data"]["findings"] if f["check"] == check]
        self.assertEqual(len(found), 1, doc["data"]["findings"])
        return found[0]

    def test_document_shape_and_ok_matches_the_exit_code(self):
        result, doc = self.doctor({})
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(set(doc["data"]), {"ok", "findings"})
        self.assertIsInstance(doc["data"]["ok"], bool)
        self.assertEqual(doc["data"]["ok"], result.returncode == 0)
        for finding in doc["data"]["findings"]:
            # "harness" appears only on a finding about one harness;
            # "action"/"via", "source" and "remedies" only on one with a
            # fix or a named file behind it.
            self.assertEqual(
                set(finding) - {"harness", "action", "via", "source", "remedies"},
                self.FINDING_KEYS)
            for remedy in finding.get("remedies") or []:
                self.assertIn(remedy["kind"], {"action", "agent", "command", "steps"})
                self.assertIsInstance(remedy["title"], str)
            self.assertIn(finding["severity"], {"error", "warning", "info"})
            self.assertTrue(finding["fix"] is None or isinstance(finding["fix"], str))

    def test_registry_drift_error_fails_the_run(self):
        result, doc = self.doctor(
            {"zzfake": {"command": "zz-fake-harness", "state": "bogus"}})
        found = self.finding(doc, "drift.registry")
        self.assertEqual(found["severity"], "error")
        self.assertIn("zzfake: state 'bogus'", found["message"])
        self.assertIsNone(found["fix"])
        self.assertFalse(doc["data"]["ok"])
        self.assertEqual(result.returncode, 1)

    def test_registry_drift_warning_is_a_warning(self):
        _, doc = self.doctor({"zzfake": {"command": "zz-fake-harness", "pin": 1}})
        found = self.finding(doc, "drift.registry")
        self.assertEqual(found["severity"], "warning")
        self.assertEqual(
            found["message"],
            "zzfake: has a pin but state is 'active', not starred")

    def test_off_path_install_is_an_error_with_a_fix(self):
        binary = self.home / ".npm-global" / "bin" / "zz-fake-harness"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        result, doc = self.doctor({"zzfake": {"command": "zz-fake-harness"}})
        found = self.finding(doc, "off-path")
        self.assertEqual(found["severity"], "error")
        self.assertEqual(found["fix"], "swe install zzfake")
        self.assertEqual(found["harness"], "zzfake")
        self.assertIn("zz-fake-harness", found["message"])
        self.assertIn(str(binary.resolve()), found["message"])
        self.assertFalse(doc["data"]["ok"])
        self.assertEqual(result.returncode, 1)

    def test_unexpected_args_fail_with_nothing_on_stdout(self):
        result = run_swe(self.home, "doctor", "--json", "bogus")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("Unexpected args", result.stderr)

    def test_text_doctor_still_prints_its_report(self):
        result = run_swe(self.home, "doctor")
        self.assertIn("swe doctor", result.stdout)
        self.assertIn("Drift", result.stdout)


HEALTHY = NodeEnv(
    node="/usr/bin/node", npm="/usr/bin/npm", node_version="22.0.0",
    npm_version="10.0.0", global_prefix="/usr/local",
    global_bin="/usr/local/bin", global_bin_on_path=True)


class DoctorTextNvmTest(unittest.TestCase):
    """The text report's nvm section with node versions installed.

    Every other doctor test points NVM_DIR at a missing directory, which
    is how `swe doctor` shipped calling an is_dir_on_path it never
    imported: on any machine with an nvm node it died with NameError.
    """

    def test_an_nvm_node_on_and_off_path_prints_its_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            nvm = Path(tmp) / ".nvm"
            bin_dir = nvm / "versions" / "node" / "v22.0.0" / "bin"
            bin_dir.mkdir(parents=True)
            for path_value in (str(bin_dir), "/usr/bin"):
                with self.subTest(PATH=path_value), patch.dict(
                    "os.environ", {"NVM_DIR": str(nvm), "PATH": path_value}
                ), patch("quiver.harness.commands.load_registry", return_value={}), patch(
                    "quiver.harness.path_health.probe_node_env", return_value=HEALTHY
                ), patch(
                    "quiver.harness.path_health.find_off_path_tools", return_value=[]
                ), patch(
                    "quiver.harness.drift.run_drift_checks", return_value=[]
                ), patch("quiver.harness.doctor.setup_findings", return_value=[]), patch(
                    "quiver.harness.doctor.issue_findings", return_value=[]
                ):
                    out = io.StringIO()
                    with redirect_stdout(out):
                        cmd_doctor([])
                    self.assertIn("1 version(s)", strip_ansi(out.getvalue()))


class DoctorJsonChecksTest(unittest.TestCase):
    """Each environment check, with the node probe stubbed so the result
    does not depend on the machine running the tests."""

    def run_doctor(self, env=HEALTHY, orphans=(), drift=(), nvm=(),
                   preferred=None, which=None):
        preferred = preferred or env.npm
        which = which or env.npm
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"NVM_DIR": str(Path(tmp) / "missing-nvm")}
        ), patch("quiver.harness.commands.load_registry", return_value={}), patch(
            "quiver.harness.path_health.probe_node_env", return_value=env
        ), patch(
            "quiver.harness.path_health.preferred_npm_bin", return_value=preferred
        ), patch(
            "quiver.harness.path_health.nvm_bin_dirs", return_value=list(nvm)
        ), patch(
            "quiver.harness.path_health.find_off_path_tools",
            return_value=list(orphans),
        ), patch("quiver.harness.commands.shutil.which", return_value=which), patch(
            "quiver.harness.drift.run_drift_checks", return_value=list(drift)
        ), patch("quiver.harness.doctor.setup_findings", return_value=[]):
            out = io.StringIO()
            with redirect_stdout(out):
                code = cmd_doctor(["--json"])
        return code, json.loads(out.getvalue())

    def test_a_healthy_machine_has_no_findings(self):
        code, doc = self.run_doctor()
        self.assertEqual(code, 0)
        self.assertEqual(doc, {"schema": 1, "data": {"ok": True, "findings": []}})

    def test_global_bin_off_path_is_an_error_that_fails_the_run(self):
        env = NodeEnv("/usr/bin/node", "/usr/bin/npm", "22.0.0", "10.0.0",
                      "/opt/npm", "/opt/npm/bin", False)
        code, doc = self.run_doctor(env)
        self.assertEqual(code, 1)
        self.assertFalse(doc["data"]["ok"])
        self.assertEqual(doc["data"]["findings"], [{
            "check": "npm.global-bin",
            "severity": "error",
            "message": "npm global bin /opt/npm/bin is not on PATH",
            "fix": 'export PATH="/opt/npm/bin:$PATH"',
        }])

    def test_missing_node_and_npm_warn_without_failing_the_run(self):
        # Today's exit code ignores a missing npm when nothing else is wrong;
        # --json keeps that, so ok stays true and these are warnings.
        env = NodeEnv(None, None, None, None, None, None, False)
        code, doc = self.run_doctor(env, preferred="", which="")
        self.assertEqual(code, 0)
        self.assertTrue(doc["data"]["ok"])
        self.assertEqual(doc["data"]["findings"], [
            {"check": "node", "severity": "warning",
             "message": "node not found on PATH", "fix": None},
            {"check": "npm", "severity": "warning",
             "message": "npm not found on PATH",
             "fix": "Install Node/npm, or put npm on PATH."},
        ])

    def test_off_path_harness_is_an_error_with_an_install_fix(self):
        hit = OffPathHit("claude", "/tmp/npm/bin/claude", "nvm")
        with patch("quiver.harness.doctor.install_method", return_value=None):
            code, doc = self.run_doctor(orphans=[("claude", "claude", hit)])
        self.assertEqual(code, 1)
        self.assertEqual(doc["data"]["findings"], [{
            "check": "off-path",
            "severity": "error",
            "message": "claude (claude) is at /tmp/npm/bin/claude [nvm], "
                       "not on PATH",
            "fix": "swe install claude",
            "harness": "claude",
            "source": "found at /tmp/npm/bin/claude [nvm]",
            "remedies": [{"kind": "steps",
                          "title": "Put it on PATH yourself",
                          "detail": "swe install claude"}],
        }])

    def test_off_path_with_an_installer_on_path_offers_install(self):
        hit = OffPathHit("claude", "/tmp/npm/bin/claude", "nvm")
        with patch("quiver.harness.doctor.install_method", return_value="npm"):
            _, doc = self.run_doctor(orphans=[("claude", "claude", hit)])
        finding = doc["data"]["findings"][0]
        self.assertEqual((finding["fix"], finding["action"], finding["via"]),
                         ("swe install claude --via=npm", "install", "npm"))

    def test_only_a_finding_about_one_harness_names_it(self):
        hit = OffPathHit("cc", "/tmp/npm/bin/cc", "nvm")
        env = NodeEnv(None, "/usr/bin/npm", None, "10.0.0", None, None, False)
        _, doc = self.run_doctor(env, orphans=[("claude-code", "cc", hit)])
        by_check = {f["check"]: f for f in doc["data"]["findings"]}
        self.assertEqual(by_check["off-path"]["harness"], "claude-code")
        self.assertNotIn("harness", by_check["node"])

    def test_nvm_bins_missing_from_path_warn(self):
        code, doc = self.run_doctor(nvm=[Path("/nonexistent/nvm/v22/bin")])
        self.assertEqual(code, 0)
        self.assertEqual(doc["data"]["findings"], [{
            "check": "nvm",
            "severity": "warning",
            "message": "nvm node bins are not on PATH, so nvm globals are "
                       "invisible to swe and non-interactive shells",
            "fix": "Avoid `npm install -g` under nvm unless nvm is always on "
                   "PATH. Prefer: swe install <name>",
        }])

    def test_a_different_preferred_npm_is_info_only(self):
        code, doc = self.run_doctor(
            preferred="/opt/homebrew/bin/npm", which="/usr/bin/npm")
        self.assertEqual(code, 0)
        self.assertTrue(doc["data"]["ok"])
        finding = doc["data"]["findings"][0]
        self.assertEqual(
            {k: finding[k] for k in ("check", "severity", "message", "source")},
            {"check": "npm.preferred",
             "severity": "info",
             "message": "which npm is /usr/bin/npm; swe prefers "
                        "/opt/homebrew/bin/npm",
             "source": "PATH order: /usr/bin precedes /opt/homebrew/bin"})
        self.assertIn("/opt/homebrew/bin", finding["fix"])
        self.assertEqual([r["kind"] for r in finding["remedies"]],
                         ["steps", "agent"])
        self.assertIn("swe doctor", finding["remedies"][1]["prompt"])

    def test_drift_findings_keep_their_area_and_severity(self):
        code, doc = self.run_doctor(drift=[
            Finding("error", "registry", "x: state 'bogus' is not valid"),
            Finding("warn", "help", "command 'y' has no help topic"),
        ])
        self.assertEqual(code, 1)
        self.assertEqual(doc["data"]["findings"], [
            {"check": "drift.registry", "severity": "error",
             "message": "x: state 'bogus' is not valid", "fix": None},
            {"check": "drift.help", "severity": "warning",
             "message": "command 'y' has no help topic", "fix": None},
        ])

    def test_a_warning_alone_does_not_fail_the_run(self):
        code, doc = self.run_doctor(
            drift=[Finding("warn", "help", "command 'y' has no help topic")])
        self.assertEqual(code, 0)
        self.assertTrue(doc["data"]["ok"])



class McpSyncJsonTest(unittest.TestCase):
    """`swe mcp sync ... --json`: what the app's sync button reads back.

    Names only: a server definition carries resolved secrets, so the report
    says which servers moved, never what they hold.
    """

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        write_registry(self.home, REGISTRY)
        write_claude(self.home, {
            "github": {"command": "npx", "args": ["gh"], "env": {"GITHUB_TOKEN": FAKE_KEY}},
            "clash": {"command": "node", "args": ["a.js"]},
        })
        write_cursor(self.home, {"clash": {"command": "node", "args": ["b.js"]}})

    def sync(self, *args):
        result = run_swe(self.home, "mcp", "sync", *args, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return decode(result)["data"]

    def cursor(self) -> dict:
        return json.loads((self.home / ".cursor" / "mcp.json").read_text())["mcpServers"]

    def test_reports_names_per_target_and_writes(self):
        data = self.sync("claude", "cursor", "--only=github")
        self.assertEqual(data["source"], "claude")
        self.assertFalse(data["dry_run"])
        self.assertEqual(data["selected"], ["github"])
        self.assertEqual(data["targets"], [{"name": "cursor", "added": ["github"],
                                            "updated": [], "skipped": [], "pruned": [],
                                            "unsupported": [], "via_gateway": []}])
        self.assertIn("github", self.cursor())

    def test_never_prints_a_secret_value(self):
        result = run_swe(self.home, "mcp", "sync", "claude", "cursor", "--json")
        self.assertNotIn(FAKE_KEY, result.stdout)
        self.assertNotIn(FAKE_KEY, result.stderr)

    def test_dry_run_writes_nothing(self):
        data = self.sync("claude", "cursor", "--only=github", "--dry-run")
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["targets"][0]["added"], ["github"])
        self.assertNotIn("github", self.cursor())

    def test_a_conflict_is_skipped_without_force_and_updated_with_it(self):
        skipped = self.sync("claude", "cursor", "--only=clash")
        self.assertEqual(skipped["targets"][0]["skipped"], ["clash"])
        self.assertEqual(self.cursor()["clash"]["args"], ["b.js"])
        forced = self.sync("claude", "cursor", "--only=clash", "--force")
        self.assertEqual(forced["targets"][0]["updated"], ["clash"])
        self.assertEqual(self.cursor()["clash"]["args"], ["a.js"])

    def test_a_no_op_run_still_names_its_source(self):
        data = self.sync("claude", "cursor", "--only=nomatch")
        self.assertEqual(data["source"], "claude")
        self.assertEqual(data["selected"], [])
        self.assertEqual(data["targets"], [])

    def test_a_refusal_fails_with_nothing_on_stdout(self):
        result = run_swe(self.home, "mcp", "sync", "claude", "nope", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("Unknown target tool", result.stderr)


if __name__ == "__main__":
    unittest.main()
