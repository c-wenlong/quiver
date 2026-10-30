"""The MCP gateway: one copy of a shared server serving every session.

``GatewayServerTest`` runs the real HTTP server in-process against a fake
stdio server (``fixtures/fake_stdio_mcp.py``). The CLI tests run
``python -m quiver.mcp`` against a throwaway HOME, the way
``test_mcp_sync_integration`` does.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from quiver.mcp import gateway
from quiver.mcp import gateway_server
from quiver.mcp.gateway_server import Gateway, make_server
from quiver.remote import launchd

FAKE = pathlib.Path(__file__).resolve().parent / "fixtures" / "fake_stdio_mcp.py"
PROJECT_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "test", "version": "0"}}}


class GatewayHelpersTest(unittest.TestCase):
    def test_settings_default_and_bad_port(self):
        self.assertEqual(gateway.settings_of(None), {"enabled": False, "port": gateway.DEFAULT_PORT})
        self.assertEqual(gateway.settings_of({"gateway": {"enabled": True, "port": 9100}}),
                         {"enabled": True, "port": 9100})
        for bad in (0, 70000, "9100", True):
            self.assertEqual(gateway.settings_of({"gateway": {"port": bad}})["port"],
                             gateway.DEFAULT_PORT)

    def test_runs_on_gateway_needs_shared_stdio_and_enabled(self):
        on = {"enabled": True, "port": 9100}
        stdio = {"command": "node", "args": ["s.js"], "shared": True}
        self.assertTrue(gateway.runs_on_gateway(stdio, on))
        self.assertFalse(gateway.runs_on_gateway(stdio, {**on, "enabled": False}))
        self.assertFalse(gateway.runs_on_gateway({"command": "node"}, on))
        self.assertFalse(gateway.runs_on_gateway({"url": "https://x", "shared": True}, on))

    def test_matches_hub_accepts_the_command_or_the_gateway_url(self):
        settings = {"enabled": False, "port": 9100}
        hub = {"command": "node", "args": ["s.js"], "shared": True}
        self.assertTrue(gateway.matches_hub("gh", hub, {"command": "node", "args": ["s.js"]}, settings))
        # Still ours after uninstall, so discover never pulls it into the hub.
        self.assertTrue(gateway.matches_hub("gh", hub, {"url": "http://127.0.0.1:9100/mcp/gh"}, settings))
        self.assertFalse(gateway.matches_hub("gh", hub, {"url": "http://127.0.0.1:9100/mcp/other"}, settings))
        self.assertFalse(gateway.matches_hub("gh", {"command": "node", "args": ["s.js"]},
                                             {"url": "http://127.0.0.1:9100/mcp/gh"}, settings))

    def test_looks_stateful(self):
        self.assertTrue(gateway.looks_stateful("dv__playwright", {"command": "node"}))
        self.assertTrue(gateway.looks_stateful("web", {"command": "npx", "args": ["@playwright/mcp"]}))
        self.assertFalse(gateway.looks_stateful("dv__linear", {"command": "node", "args": ["l.js"]}))


class GatewayServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.specs = {"fake": {"command": sys.executable, "args": [str(FAKE)]}}
        self.gateway = Gateway(pathlib.Path(self.tmp.name), load=lambda: (dict(self.specs), {}),
                               watched=lambda: [])
        self.server = make_server(self.gateway, 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.gateway.stop()
        self.tmp.cleanup()

    def request(self, body=None, *, method="POST", path="/mcp/fake", headers=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                return resp.status, dict(resp.headers), json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            with exc:
                raw = exc.read()
                return exc.code, dict(exc.headers), json.loads(raw) if raw else None

    def call(self, tool, args=None, msg_id=7):
        status, _, body = self.request({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
                                        "params": {"name": tool, "arguments": args or {}}})
        self.assertEqual(status, 200)
        return body

    @staticmethod
    def payload(body):
        return json.loads(body["result"]["content"][0]["text"])

    def test_initialize_comes_from_one_handshake(self):
        status, headers, body = self.request(INIT)
        self.assertEqual(status, 200)
        self.assertTrue(headers.get("Mcp-Session-Id"))
        self.assertEqual(body["id"], 1)
        # The gateway drops notifications, so it doesn't promise them.
        self.assertEqual(body["result"]["capabilities"], {"tools": {}, "resources": {}})
        _, again, _ = self.request(INIT)
        self.assertNotEqual(again["Mcp-Session-Id"], headers["Mcp-Session-Id"])
        self.assertEqual(self.gateway.backends["fake"].starts, 1)

    def test_concurrent_sessions_share_one_process_and_keep_their_ids(self):
        results = {}

        def run(i):
            results[i] = self.call("echo", {"i": i}, msg_id=1)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        pids = {self.payload(r)["pid"] for r in results.values()}
        self.assertEqual(len(pids), 1)
        for i, body in results.items():
            self.assertEqual(body["id"], 1)
            self.assertEqual(self.payload(body)["args"], {"i": i})

    def test_unknown_methods_are_answered_here_and_the_server_survives(self):
        pid = self.payload(self.call("echo"))["pid"]
        _, _, body = self.request({"jsonrpc": "2.0", "id": 2, "method": "server/discover"})
        self.assertEqual(body["error"]["code"], gateway_server.METHOD_NOT_FOUND)
        _, _, body = self.request({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(body["result"], {})
        self.assertEqual(self.payload(self.call("echo"))["pid"], pid)

    def test_notifications_and_batches(self):
        status, _, body = self.request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((status, body), (202, None))
        status, _, body = self.request([
            {"jsonrpc": "2.0", "id": "a", "method": "tools/list"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": "b", "method": "ping"},
        ])
        self.assertEqual(status, 200)
        self.assertEqual([r["id"] for r in body], ["a", "b"])

    def test_server_to_client_requests_are_refused(self):
        answer = self.payload(self.call("ask"))["args"]["answer"]
        self.assertEqual(answer["id"], "srv-1")
        self.assertEqual(answer["error"]["code"], gateway_server.METHOD_NOT_FOUND)

    def test_a_crashed_server_restarts_on_the_next_request(self):
        first = self.payload(self.call("echo"))["pid"]
        with patch.object(gateway_server, "BACKOFF_MIN_S", 0.0):
            body = self.call("crash")
            self.assertIn("exited", body["error"]["message"])
            deadline = time.time() + 10
            while self.gateway.backends["fake"].status()["state"] == "running" and time.time() < deadline:
                time.sleep(0.05)
            second = self.payload(self.call("echo"))["pid"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.gateway.backends["fake"].starts, 2)

    def test_a_server_that_keeps_exiting_backs_off(self):
        self.specs["fake"] = {"command": sys.executable, "args": ["-c", "import sys; sys.exit(2)"]}
        self.gateway.reload(force=True)
        body = self.call("echo")
        self.assertIn("initialize failed", body["error"]["message"])
        body = self.call("echo")
        self.assertIn("next start in", body["error"]["message"])
        self.assertEqual(self.gateway.backends["fake"].starts, 1)

    def test_browser_shaped_requests_are_refused(self):
        status, _, _ = self.request(INIT, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.request(INIT, headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        self.assertNotIn("fake", [b for b, be in self.gateway.backends.items() if be.starts])

    def test_get_head_delete_and_unknown_servers(self):
        self.assertEqual(self.request(method="GET")[0], 405)
        self.assertEqual(self.request(method="HEAD")[0], 200)
        self.assertEqual(self.request(method="HEAD", path="/mcp/nope")[0], 404)
        self.assertEqual(self.request(INIT, path="/mcp/nope")[0], 404)
        self.assertEqual(self.request(method="DELETE")[0], 200)
        status, _, body = self.request(method="GET", path="/health")
        self.assertEqual((status, body["ok"]), (200, True))
        # HEAD starts nothing: `swe mcp status` probes every URL server.
        self.assertEqual(self.gateway.backends["fake"].starts, 0)

    def test_a_changed_definition_stops_the_old_process(self):
        old = self.payload(self.call("echo"))["pid"]
        self.specs["fake"] = {"command": sys.executable, "args": [str(FAKE)], "env": {"X": "1"}}
        self.gateway.reload(force=True)
        self.assertNotEqual(self.payload(self.call("echo"))["pid"], old)
        del self.specs["fake"]
        self.gateway.reload(force=True)
        self.assertEqual(self.request(INIT)[0], 404)

    def test_status_lists_problems(self):
        self.gateway.problems = {"broken": "no value for TOKEN"}
        rows = {r["name"]: r for r in self.gateway.status()["servers"]}
        self.assertEqual(rows["broken"]["error"], "no value for TOKEN")
        self.assertEqual(rows["fake"]["state"], "idle")


def _env(home: pathlib.Path) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(PROJECT_SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


class GatewayCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        (self.home / ".quiver" / "config").mkdir(parents=True)
        (self.home / ".quiver" / "config" / "tools.json").write_text(
            json.dumps({"claude": {"aliases": ["cc"]}}) + "\n")
        self.hub = self.home / ".quiver" / "mcp.json"
        self.hub.write_text(json.dumps({"mcpServers": {
            "api": {"command": "node", "args": ["/srv/api.js"], "env": {"TOKEN": "${API_TOKEN}"}},
            "dv__playwright": {"command": "node", "args": ["/srv/pw.js"]},
            "remote": {"url": "https://mcp.example.com/mcp"},
        }}) + "\n")
        secrets = self.home / ".quiver" / "secrets" / "mcp"
        secrets.mkdir(parents=True)
        (secrets / ".api_keys").write_text("export API_TOKEN='tok'\n")
        self.claude = self.home / ".claude.json"
        self.claude.write_text(json.dumps({"mcpServers": {}}) + "\n")
        self.desktop = self.home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        self.desktop.parent.mkdir(parents=True)
        self.desktop.write_text(json.dumps({"mcpServers": {}}) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_mcp(self, *args):
        return subprocess.run([sys.executable, "-m", "quiver.mcp", *args], capture_output=True,
                              text=True, env=_env(self.home), timeout=60)

    def hub_data(self):
        return json.loads(self.hub.read_text())

    def enable(self, port=9100):
        data = self.hub_data()
        data["gateway"] = {"enabled": True, "port": port}
        self.hub.write_text(json.dumps(data) + "\n")

    def test_share_and_unshare(self):
        result = self.run_mcp("gateway", "share", "api")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIs(self.hub_data()["mcpServers"]["api"]["shared"], True)
        self.assertIn("not installed yet", result.stdout)
        result = self.run_mcp("gateway", "unshare", "api")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("shared", self.hub_data()["mcpServers"]["api"])

    def test_share_refusals(self):
        result = self.run_mcp("gateway", "share", "nope")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not in the hub: nope", result.stderr)
        result = self.run_mcp("gateway", "share", "remote")
        self.assertEqual(result.returncode, 1)
        self.assertIn("only stdio servers", result.stderr)
        result = self.run_mcp("gateway", "share", "dv__playwright")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--force", result.stderr)
        self.assertNotIn("shared", self.hub_data()["mcpServers"]["dv__playwright"])
        result = self.run_mcp("gateway", "share", "dv__playwright", "--force")
        self.assertEqual(result.returncode, 0)

    def test_sync_writes_the_gateway_url_only_once_it_is_enabled(self):
        self.run_mcp("gateway", "share", "api")
        result = self.run_mcp("sync", "quiver", "claude", "--no-interactive")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        api = json.loads(self.claude.read_text())["mcpServers"]["api"]
        self.assertEqual(api["command"], "node")
        self.enable()
        result = self.run_mcp("sync", "quiver", "claude", "claude-desktop", "--no-interactive",
                              "--force", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = {t["name"]: t for t in json.loads(result.stdout)["data"]["targets"]}
        self.assertEqual(report["claude"]["via_gateway"], ["api"])
        claude = json.loads(self.claude.read_text())["mcpServers"]
        self.assertEqual(claude["api"], {"type": "http", "url": "http://127.0.0.1:9100/mcp/api"})
        self.assertEqual(claude["dv__playwright"]["command"], "node")
        # Claude Desktop takes no URL server, so it keeps the command.
        desktop = json.loads(self.desktop.read_text())["mcpServers"]
        self.assertEqual(desktop["api"]["env"], {"TOKEN": "tok"})

    def test_discover_and_list_read_the_gateway_url_as_the_hub_server(self):
        self.run_mcp("gateway", "share", "api")
        self.enable()
        self.run_mcp("sync", "quiver", "claude", "claude-desktop", "--no-interactive", "--force")
        result = self.run_mcp("discover", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        findings = json.loads(result.stdout)["data"]["findings"]
        self.assertNotIn("api", [f["name"] for f in findings])
        result = self.run_mcp("list", "--json")
        servers = json.loads(result.stdout)["data"]["servers"]
        api = next(s for s in servers if s["name"] == "api")
        self.assertEqual(sorted(api["harnesses"]), ["claude", "claude-desktop"])
        self.assertFalse(api["conflict"])

    def test_discover_apply_keeps_the_flag_on_a_real_edit(self):
        self.run_mcp("gateway", "share", "api")
        self.claude.write_text(json.dumps({"mcpServers": {
            "api": {"command": "node", "args": ["/srv/api-v2.js"], "env": {"TOKEN": "tok"}}}}) + "\n")
        result = self.run_mcp("discover", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        api = self.hub_data()["mcpServers"]["api"]
        self.assertEqual(api["args"], ["/srv/api-v2.js"])
        self.assertIs(api["shared"], True)

    def test_status_json_lists_shared_servers(self):
        self.run_mcp("gateway", "share", "api")
        self.enable(port=1)  # nothing listens there
        result = self.run_mcp("gateway", "status", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)["data"]
        self.assertEqual(data["port"], 1)
        self.assertFalse(data["reachable"])
        self.assertEqual([(r["name"], r["state"], r["url"]) for r in data["servers"]],
                         [("api", "down", "http://127.0.0.1:1/mcp/api")])

    def test_unknown_subcommand_and_bad_port(self):
        self.assertEqual(self.run_mcp("gateway", "nope").returncode, 1)
        result = self.run_mcp("gateway", "serve", "--port=abc")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--port must be", result.stderr)


class GatewayLaunchdTest(unittest.TestCase):
    def test_the_agent_gets_its_own_label(self):
        from quiver.mcp.gateway_commands import LABEL

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            calls = []

            def run(argv, **kw):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, "", "")

            path = launchd.install(["swe", "mcp", "gateway", "serve"], home / "g.log", "/usr/bin",
                                   home=home, run=run, label=LABEL)
            self.assertEqual(path.name, f"{LABEL}.plist")
            import plistlib
            self.assertEqual(plistlib.loads(path.read_bytes())["Label"], LABEL)
            self.assertTrue(all(LABEL in " ".join(c) or c[1] == "bootstrap" for c in calls))
            self.assertFalse((home / "Library" / "LaunchAgents" / f"{launchd.LABEL}.plist").exists())


if __name__ == "__main__":
    unittest.main()
