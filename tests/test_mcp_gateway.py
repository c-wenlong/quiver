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
        # A URL left from before an unshare or a port change is still ours:
        # importing it would swap the hub's command for a dead URL.
        self.assertTrue(gateway.matches_hub("gh", {"command": "node", "args": ["s.js"]},
                                            {"url": "http://127.0.0.1:9100/mcp/gh"}, settings))
        self.assertTrue(gateway.matches_hub("gh", hub, {"url": "http://127.0.0.1:7000/mcp/gh"}, settings))
        # Only over a hub command, and only a loopback /mcp/<name> URL.
        self.assertFalse(gateway.matches_hub("gh", {"url": "https://api.example/mcp"},
                                             {"url": "http://127.0.0.1:9100/mcp/gh"}, settings))
        for url in ("http://127.0.0.1:9100/mcp/gh/x", "http://localhost:9100/mcp/gh",
                    "http://127.0.0.1:port/mcp/gh", "http://127.0.0.1:9100/gh"):
            self.assertFalse(gateway.matches_hub("gh", hub, {"url": url}, settings), url)

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

    def test_a_burst_of_connections_is_queued_not_reset(self):
        # One session opens every shared server at once; 5 queued
        # connections (Python 3.13's default) reset the rest.
        self.assertGreaterEqual(self.server.request_queue_size, 64)
        results = []

        def ping(i):
            results.append(self.request({"jsonrpc": "2.0", "id": i, "method": "ping"})[0])

        threads = [threading.Thread(target=ping, args=(i,)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results, [200] * 40)

    def test_status_lists_problems(self):
        self.gateway.problems = {"broken": "no value for TOKEN"}
        rows = {r["name"]: r for r in self.gateway.status()["servers"]}
        self.assertEqual(rows["broken"]["error"], "no value for TOKEN")
        self.assertEqual(rows["fake"]["state"], "idle")


class GatewayHardeningTest(GatewayServerTest):
    """The review's findings: smuggled requests, bad lengths, stray
    processes, pipe errors, unreadable hubs and unbounded logs."""

    def raw(self, data: bytes) -> bytes:
        import socket

        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(data)
            chunks = []
            while True:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)

    def test_a_refused_request_cannot_smuggle_a_second_one(self):
        inner = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                            "params": {"name": "echo", "arguments": {}}}).encode()
        smuggled = (f"POST /mcp/fake HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                    f"Content-Type: application/json\r\nContent-Length: {len(inner)}\r\n\r\n").encode() + inner
        outer = (f"POST /mcp/fake HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                 f"Origin: https://evil.example\r\nContent-Type: text/plain\r\n"
                 f"Content-Length: {len(smuggled)}\r\n\r\n").encode() + smuggled
        reply = self.raw(outer)
        self.assertEqual(reply.count(b"HTTP/1.1 "), 1, reply)
        self.assertIn(b"403", reply.split(b"\r\n", 1)[0])
        self.assertIn(b"Connection: close", reply)
        self.assertEqual(self.gateway.backends["fake"].starts, 0)

    def test_only_json_bodies_with_a_real_length(self):
        status, _, _ = self.request(INIT, headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        reply = self.raw((f"POST /mcp/fake HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                          "Content-Type: application/json\r\nContent-Length: -1\r\n\r\n{}").encode())
        self.assertIn(b"411", reply.split(b"\r\n", 1)[0])
        self.assertEqual(self.gateway.backends["fake"].starts, 0)

    def test_an_unreadable_hub_keeps_the_running_servers(self):
        pid = self.payload(self.call("echo"))["pid"]

        def broken():
            raise ValueError("Expecting value: line 1 column 1")

        self.gateway._load = broken
        self.gateway.reload(force=True)
        self.assertEqual(self.payload(self.call("echo"))["pid"], pid)
        self.assertIn("keeping the last good servers", self.gateway.status()["error"])
        self.gateway._load = lambda: (dict(self.specs), {})
        self.gateway.reload(force=True)
        self.assertIsNone(self.gateway.status()["error"])

    def test_a_pipe_error_in_the_handshake_is_a_json_rpc_error(self):
        import io

        read_end, write_end = os.pipe()
        os.close(read_end)  # a server that is gone: writes get EPIPE

        class Pipe:
            closed = False

            def fileno(self):
                return write_end

            def close(self):
                if not self.closed:
                    os.close(write_end)
                self.closed = True

        class Proc:
            pid = 2 ** 22 + 12345  # no such process group
            stdin = Pipe()
            stdout = io.BytesIO()
            stderr = io.BytesIO()

            def poll(self):
                return 1

            def wait(self, timeout=None):
                return 1

            def terminate(self):
                pass

            kill = terminate

        backend = gateway_server.Backend("dead", {"command": "x"}, pathlib.Path(self.tmp.name),
                                         popen=lambda *a, **k: Proc())
        body = backend.call({"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, "2025-06-18")
        self.assertEqual(body["id"], 4)
        self.assertIn("exited before initialize", body["error"]["message"])
        Proc.stdin.close()

    def test_a_server_that_stops_reading_times_the_write_out(self):
        # Initialized, then it never reads stdin again.
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        backend = gateway_server.Backend("stuck", {"command": "x"}, pathlib.Path(self.tmp.name))
        child = gateway_server._Child(proc)
        backend._child, backend._init_result = child, {"capabilities": {}}
        big = {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
               "params": {"name": "echo", "arguments": {"blob": "x" * 1_000_000}}}
        with patch.object(gateway_server, "WRITE_TIMEOUT_S", 0.5), \
                patch.object(gateway_server, "KILL_GRACE_S", 0.5):
            started = time.monotonic()
            body = backend.call(big, "2025-06-18")
            self.assertLess(time.monotonic() - started, 5)
            self.assertIn("stopped reading", body["error"]["message"])
            # The lock is free again and the stuck server is being stopped.
            self.assertTrue(child.write_lock.acquire(timeout=1))
            child.write_lock.release()
            proc.wait(timeout=5)
        child.close()

    def test_stopping_reaches_the_whole_process_group(self):
        # The leader (a wrapper) exits at once; one member ignores SIGTERM.
        proc = subprocess.Popen(
            ["sh", "-c", "sleep 60 & (trap '' TERM; exec sleep 61) & echo $!; wait"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True)
        stubborn = int(proc.stdout.readline())
        child = gateway_server._Child(proc)
        backend = gateway_server.Backend("g", {"command": "sh"}, pathlib.Path(self.tmp.name))
        with patch.object(gateway_server, "KILL_GRACE_S", 0.5):
            backend._kill(child)
        self.assertFalse(gateway_server._group_alive(proc.pid))
        with self.assertRaises(ProcessLookupError):
            os.kill(stubborn, 0)
        proc.wait()
        child.close()

    def test_the_log_starts_over_at_its_limit(self):
        import io

        backend = gateway_server.Backend("noisy", {"command": "x"}, pathlib.Path(self.tmp.name))
        stream = io.BytesIO(b"".join(b"line %04d of noise\n" % i for i in range(500)))
        with patch.object(gateway_server, "MAX_LOG", 1000):
            backend._log(stream)
        text = (pathlib.Path(self.tmp.name) / "noisy.log").read_bytes()
        self.assertLessEqual(len(text), 1000)
        self.assertIn(b"log started over", text)
        self.assertIn(b"line 0499", text)


class GatewayCommandsInProcessTest(unittest.TestCase):
    """The command handlers, run in-process against a throwaway HOME."""

    def setUp(self):
        from quiver.mcp import cli as mcp_cli

        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        self.hub = self.home / ".quiver" / "mcp.json"
        self.hub.parent.mkdir(parents=True)
        self.hub.write_text(json.dumps({"mcpServers": {
            "api": {"command": sys.executable, "args": [str(FAKE)], "env": {"T": "${API_TOKEN}"}},
            "nokey": {"command": "node", "args": ["n.js"], "env": {"T": "${MISSING}"}, "shared": True},
            "web": {"url": "https://mcp.example.com/mcp", "shared": True},
            "dv__playwright": {"command": "node"},
        }}) + "\n")
        secrets = self.home / ".quiver" / "secrets" / "mcp"
        secrets.mkdir(parents=True)
        (secrets / ".api_keys").write_text("export API_TOKEN='tok'\n")
        for p in (patch.dict(os.environ, {"HOME": str(self.home)}),
                  patch.object(mcp_cli, "MCP_SOURCE_FILE", self.hub)):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, *args):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from quiver.mcp.gateway_commands import cmd_gateway

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cmd_gateway(list(args))
        return code, out.getvalue(), err.getvalue()

    def hub_data(self):
        return json.loads(self.hub.read_text())

    def test_share_unshare_and_refusals(self):
        self.assertEqual(self.run_cmd("share", "api")[0], 0)
        self.assertIs(self.hub_data()["mcpServers"]["api"]["shared"], True)
        code, out, _ = self.run_cmd("share", "api")
        self.assertIn("already shared", out)
        self.assertEqual(self.run_cmd("share")[0], 1)
        self.assertEqual(self.run_cmd("share", "ghost")[0], 1)
        self.assertEqual(self.run_cmd("share", "dv__playwright")[0], 1)
        self.assertEqual(self.run_cmd("unshare", "api")[0], 0)
        self.assertNotIn("shared", self.hub_data()["mcpServers"]["api"])
        self.assertEqual(self.run_cmd("bogus")[0], 1)
        self.assertIn("swe mcp gateway share", self.run_cmd("help")[1])

    def test_load_hub_specs(self):
        self.run_cmd("share", "api")
        specs, problems = gateway_server.load_hub_specs()
        self.assertEqual(specs["api"]["env"], {"T": "tok"})
        self.assertEqual(problems, {"nokey": "no value for MISSING", "web": "not a stdio server"})
        self.hub.write_text("{ not json")
        with self.assertRaises(Exception):
            gateway_server.load_hub_specs()

    def test_status_text_and_json(self):
        from quiver.mcp import gateway_commands as gc

        self.run_cmd("share", "api")
        live = {"error": "cannot read the hub, keeping the last good servers: bad",
                "servers": [{"name": "api", "state": "running", "pid": 42, "uptime_s": 9,
                             "starts": 1, "error": None},
                            {"name": "nokey", "state": "error", "pid": None, "uptime_s": None,
                             "starts": 0, "error": "no value for MISSING"}]}
        with patch.object(gc, "_fetch", return_value=live), \
                patch.object(gc.launchd, "loaded_pid", return_value=(True, 7)):
            code, out, _ = self.run_cmd("status")
            self.assertEqual(code, 0)
            self.assertIn("running on http://127.0.0.1:8478", out)
            self.assertIn("pid 42, up 9s", out)
            self.assertIn("no value for MISSING", out)
            self.assertIn("keeping the last good servers", out)
            code, out, _ = self.run_cmd("status", "--json")
            rows = {r["name"]: r for r in json.loads(out)["data"]["servers"]}
            self.assertEqual(rows["api"]["pid"], 42)
        with patch.object(gc, "_fetch", return_value=None):
            code, out, _ = self.run_cmd()
            self.assertIn("not installed", out)
            self.assertIn("down", out)

    def test_install_and_uninstall(self):
        from quiver.mcp import gateway_commands as gc

        installed = {}

        def fake_install(program, log_path, path_env, label, process_type):
            installed.update(program=program, label=label, process_type=process_type)
            return self.home / "agent.plist"

        with patch.object(gc.sys, "platform", "darwin"), \
                patch.object(gc, "gateway_program", return_value=(["swe", "mcp", "gateway", "serve", "--port=9300"], None)), \
                patch.object(gc.launchd, "install", side_effect=fake_install), \
                patch.object(gc, "_fetch", return_value={"ok": True}):
            code, out, _ = self.run_cmd("install", "--port=9300")
        self.assertEqual(code, 0)
        self.assertEqual(installed["label"], gc.LABEL)
        # Not throttled like the hub: sessions wait on its answers.
        self.assertEqual(installed["process_type"], "Standard")
        self.assertIn("answering on http://127.0.0.1:9300", out)
        self.assertEqual(self.hub_data()["gateway"], {"enabled": True, "port": 9300})
        with patch.object(gc.sys, "platform", "darwin"), \
                patch.object(gc.launchd, "uninstall", return_value=True):
            code, out, _ = self.run_cmd("uninstall")
        self.assertEqual(code, 0)
        self.assertIn("swe mcp sync quiver --all", out)
        self.assertIs(self.hub_data()["gateway"]["enabled"], False)
        with patch.object(gc.sys, "platform", "linux"):
            self.assertEqual(self.run_cmd("install")[0], 1)
        with patch.object(gc.sys, "platform", "darwin"), \
                patch.object(gc, "gateway_program", return_value=([], "no swe here")):
            code, _, err = self.run_cmd("install")
        self.assertEqual((code, "no swe here" in err), (1, True))

    def test_gateway_program_always_names_the_port(self):
        from quiver.mcp import gateway_commands as gc

        with patch.object(gc.launchd, "bundled_swe", return_value=pathlib.Path("/Apps/swe")), \
                patch.object(gc, "_supports_gateway", return_value=True):
            program, err = gc.gateway_program(gateway.DEFAULT_PORT)
        self.assertIsNone(err)
        self.assertEqual(program, ["/Apps/swe", "mcp", "gateway", "serve", f"--port={gateway.DEFAULT_PORT}"])
        swe = self.home / "Desktop" / "swe"
        swe.parent.mkdir()
        swe.write_text("#!/bin/sh\n")
        swe.chmod(0o755)
        with patch.object(gc.launchd, "bundled_swe", return_value=None), \
                patch.object(gc, "_supports_gateway", return_value=True), \
                patch.object(gc.sys, "argv", [str(swe)]):
            program, err = gc.gateway_program(9300)
        self.assertEqual(program, [])
        self.assertIn("~/Desktop", err)

    def test_serve_runs_until_interrupted_and_stops_servers(self):
        import io

        from quiver.mcp import gateway_commands as gc

        class Stub:
            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        self.run_cmd("share", "api")
        out = io.StringIO()
        previous = gateway_server.signal.getsignal(gateway_server.signal.SIGTERM)
        try:
            with patch.object(gateway_server, "make_server", return_value=Stub()):
                self.assertEqual(gateway_server.serve(9300, self.home / "logs", out=out), 0)
        finally:
            gateway_server.signal.signal(gateway_server.signal.SIGTERM, previous)
        self.assertIn("shared: api", out.getvalue())
        self.assertIn("MCP gateway stopped", out.getvalue())
        with patch.object(gateway_server, "serve", return_value=0) as serve:
            self.assertEqual(self.run_cmd("serve", "--port=9301")[0], 0)
        self.assertEqual(serve.call_args[0][0], 9301)
        with patch.object(gateway_server, "serve", side_effect=OSError("in use")):
            code, _, err = self.run_cmd("serve")
        self.assertEqual(code, 1)
        self.assertIn("cannot bind", err)


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
                                   home=home, run=run, label=LABEL, process_type="Standard")
            self.assertEqual(path.name, f"{LABEL}.plist")
            import plistlib
            written = plistlib.loads(path.read_bytes())
            self.assertEqual(written["Label"], LABEL)
            self.assertEqual(written["ProcessType"], "Standard")
            # The hub keeps its default.
            self.assertEqual(launchd.build_plist(["swe"], home / "h.log", "/usr/bin")["ProcessType"],
                             "Background")
            self.assertTrue(all(LABEL in " ".join(c) or c[1] == "bootstrap" for c in calls))
            self.assertFalse((home / "Library" / "LaunchAgents" / f"{launchd.LABEL}.plist").exists())


if __name__ == "__main__":
    unittest.main()
