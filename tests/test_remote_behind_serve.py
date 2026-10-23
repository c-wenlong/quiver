"""The hub behind ``tailscale serve``: forwarded client, https, public URL.

Serve terminates TLS and dials the hub on loopback, replacing any
X-Forwarded-For / X-Forwarded-Proto the client sent with the real
tailnet client (checked against a live serve, 2026-10-23).
"""

import json
import ssl
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from quiver.remote import agent as agent_mod
from quiver.remote import commands, pairing
from quiver.remote import hub as hub_mod

from tests.test_remote_commands import _run
from tests.test_remote_hub import _Server

_PHONE_IP = "100.64.0.9"
_PHONE = {"node_id": "n-phone", "login": "kai@github", "node_name":
          "iphone.tailnet.ts.net", "node_short": "iphone", "tagged": False}


class _Proxied(unittest.TestCase):
    trust = True

    def setUp(self):
        self.srv = _Server(sessions=[])
        self.addCleanup(self.srv.stop)
        self.srv.state.trust_proxy = self.trust
        self.seen: list[str] = []

        def whois(ip):
            self.seen.append(ip)
            return dict(_PHONE) if ip == _PHONE_IP else None

        self.srv.state.whois = whois

    def ask(self, ip=_PHONE_IP):
        return self.srv.request(
            "POST", "/v1/pair/request", {"name": "phone"},
            headers={"X-Forwarded-For": ip, "X-Forwarded-Proto": "https"},
        )


class TestForwardedClient(_Proxied):
    def test_pair_request_identifies_forwarded_client(self):
        status, data = self.ask()
        self.assertEqual(status, 201, data)
        self.assertEqual(self.seen, [_PHONE_IP])

    def test_last_hop_wins(self):
        status, _ = self.srv.request(
            "POST", "/v1/pair/request", {},
            headers={"X-Forwarded-For": f"6.6.6.6, {_PHONE_IP}"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(self.seen, [_PHONE_IP])

    def test_no_header_falls_back_to_peer(self):
        status, _ = self.srv.request("POST", "/v1/pair/request", {})
        self.assertEqual(status, 403)
        self.assertEqual(self.seen, ["127.0.0.1"])

    def test_rate_limit_is_per_forwarded_client(self):
        for _ in range(5):
            self.ask("100.64.0.50")
        status, _ = self.ask("100.64.0.50")
        self.assertEqual(status, 429)
        # Everyone arrives from loopback; one client's flood must not
        # lock the rest of the tailnet out.
        status, _ = self.ask()
        self.assertEqual(status, 201)


class TestHeadersIgnoredWithoutTrust(_Proxied):
    trust = False

    def test_forwarded_for_ignored(self):
        status, _ = self.ask()
        self.assertEqual(status, 403)
        self.assertEqual(self.seen, ["127.0.0.1"])


class TestOrigin(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[])
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.auth = {"Authorization": f"Bearer {cred['credential']}"}
        self.host = f"127.0.0.1:{self.srv.port}"

    def _list(self, origin, proto=None):
        headers = {**self.auth, "Origin": origin}
        if proto:
            headers["X-Forwarded-Proto"] = proto
        status, _ = self.srv.request("GET", "/v1/pair/requests",
                                     headers=headers)
        return status

    def test_https_origin_accepted_behind_serve(self):
        self.srv.state.trust_proxy = True
        self.assertEqual(self._list(f"https://{self.host}", "https"), 200)
        self.assertEqual(self._list(f"http://{self.host}", "https"), 403)

    def test_https_origin_refused_without_trust(self):
        self.assertEqual(self._list(f"https://{self.host}", "https"), 403)
        self.assertEqual(self._list(f"http://{self.host}"), 200)

    def test_odd_proto_reads_as_http(self):
        self.srv.state.trust_proxy = True
        self.assertEqual(self._list(f"http://{self.host}", "gopher"), 200)


class TestConfigure(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.state = hub_mod.HubState(self.home)
        self.state.whois = MagicMock(return_value={
            "login": "kai@github", "node_name": "box.tailnet.ts.net",
            "node_short": "box", "tagged": False,
        })

    def test_behind_serve(self):
        url = "https://box.tailnet.ts.net:8477"
        with patch.object(hub_mod.catalog_mod, "tailscale_ip",
                          return_value="100.64.0.1"):
            hub_mod.configure(self.state, "127.0.0.1", 8477, url)
        self.assertTrue(self.state.trust_proxy)
        self.assertEqual(self.state.base_url, url)
        self.assertEqual(self.state.allowed_hosts,
                         {"box.tailnet.ts.net:8477"})
        # Owner identity comes from this node's own tailnet IP.
        self.state.whois.assert_called_once_with("100.64.0.1")
        self.assertEqual(self.state.owner_login, "kai@github")
        self.assertEqual(hub_mod.load_hub_config(self.home), {
            "host": "127.0.0.1", "port": 8477, "public_url": url,
        })

    def test_public_url_on_tailnet_bind_does_not_trust_headers(self):
        hub_mod.configure(self.state, "100.64.0.1", 8477,
                          "https://box.tailnet.ts.net")
        self.assertFalse(self.state.trust_proxy)
        self.assertEqual(self.state.allowed_hosts, {"box.tailnet.ts.net"})

    def test_plain_tailnet_bind_unchanged(self):
        hub_mod.configure(self.state, "100.64.0.1", 8477)
        self.assertFalse(self.state.trust_proxy)
        self.assertEqual(self.state.base_url, "http://100.64.0.1:8477")
        self.assertEqual(self.state.allowed_hosts, {
            "100.64.0.1:8477", "box.tailnet.ts.net:8477", "box:8477",
        })
        self.assertEqual(hub_mod.load_hub_config(self.home)["public_url"], "")

    def test_loopback_without_public_url_has_no_identity(self):
        hub_mod.configure(self.state, "127.0.0.1", 8477)
        self.assertFalse(self.state.trust_proxy)
        self.state.whois.assert_not_called()
        self.assertEqual(self.state.owner_login, "")

    def test_no_tailscale_behind_serve(self):
        with patch.object(hub_mod.catalog_mod, "tailscale_ip",
                          return_value=""):
            hub_mod.configure(self.state, "127.0.0.1", 8477,
                              "https://box.tailnet.ts.net:8477")
        self.assertTrue(self.state.trust_proxy)
        self.state.whois.assert_not_called()

    def test_check_public_url(self):
        self.assertEqual(
            hub_mod.check_public_url(" https://box.ts.net:8477/x?y#z "),
            "https://box.ts.net:8477",
        )
        for bad in ("", "box.ts.net:8477", "ftp://box", "https://"):
            with self.assertRaises(ValueError):
                hub_mod.check_public_url(bad)

    def test_hub_config_unreadable(self):
        self.assertEqual(hub_mod.load_hub_config(self.home), {})
        hub_mod._hub_file(self.home).parent.mkdir(parents=True, exist_ok=True)
        hub_mod._hub_file(self.home).write_text("[1]")
        self.assertEqual(hub_mod.load_hub_config(self.home), {})


class TestPairLinks(unittest.TestCase):
    def test_https_scheme(self):
        self.assertEqual(
            pairing.pair_url("box.ts.net", 8477, "t", "ph", "https"),
            "https://box.ts.net:8477/#pair=t&name=ph",
        )
        self.assertEqual(
            pairing.pair_payload("box.ts.net", 8477, "t", "", "https"),
            "quiver://pair?host=box.ts.net&port=8477&token=t&scheme=https",
        )

    def test_http_links_unchanged(self):
        self.assertEqual(pairing.pair_payload("h", 1, "t"),
                         "quiver://pair?host=h&port=1&token=t")
        self.assertEqual(pairing.pair_url("h", 1, "t"),
                         "http://h:1/#pair=t")


class TestCommands(unittest.TestCase):
    def _saved(self, cfg):
        return patch.object(commands, "load_hub_config", return_value=cfg)

    def test_pair_uses_public_url(self):
        cfg = {"host": "127.0.0.1", "port": 8477,
               "public_url": "https://box.ts.net:8477"}
        with self._saved(cfg), \
                patch.object(pairing, "mint_pair_token", return_value="tok"):
            rc, out, _ = _run(["pair", "--json"])
        self.assertEqual(rc, 0)
        body = json.loads(out)["data"]
        self.assertEqual(body["url"], "https://box.ts.net:8477/#pair=tok")
        self.assertIn("scheme=https", body["deep_link"])

    def test_pair_default_https_port(self):
        with self._saved({"public_url": "https://box.ts.net"}), \
                patch.object(pairing, "mint_pair_token", return_value="tok"):
            _, out, _ = _run(["pair", "--json"])
        self.assertEqual(json.loads(out)["data"]["port"], 443)

    def test_pair_host_flag_beats_saved_url(self):
        with self._saved({"public_url": "https://box.ts.net:8477"}) as load, \
                patch.object(pairing, "mint_pair_token", return_value="tok"):
            _, out, _ = _run(["pair", "--host=100.1.2.3", "--json"])
        self.assertEqual(json.loads(out)["data"]["url"],
                         "http://100.1.2.3:8477/#pair=tok")
        load.assert_not_called()

    def test_status_probes_saved_bind(self):
        cfg = {"host": "127.0.0.1", "port": 9001,
               "public_url": "https://box.ts.net:9001"}
        with self._saved(cfg), \
                patch.object(commands, "_probe",
                             return_value={"ok": True}) as probe:
            rc, out, _ = _run(["status", "--json"])
        self.assertEqual(rc, 0)
        probe.assert_called_once_with("127.0.0.1", 9001)
        self.assertEqual(json.loads(out)["data"]["public_url"],
                         "https://box.ts.net:9001")

    def test_status_wildcard_bind_probes_loopback(self):
        with self._saved({"host": "0.0.0.0", "port": 9001}), \
                patch.object(commands, "_probe", return_value=None) as probe:
            _run(["status", "--json"])
        probe.assert_called_once_with("127.0.0.1", 9001)

    def test_serve_public_url_binds_loopback(self):
        seen = {}

        def fake_serve(state, host, port, public_url=""):
            seen.update(host=host, port=port, public_url=public_url)
            return 0

        state = MagicMock()
        state.machine = {"name": "srv"}
        with patch.object(commands, "HubState", return_value=state), \
                patch.object(commands, "serve", fake_serve):
            rc, out, _ = _run(["serve", "--public-url=https://box.ts.net:8477/"])
        self.assertEqual(rc, 0)
        self.assertEqual(seen, {"host": "127.0.0.1", "port": 8477,
                                "public_url": "https://box.ts.net:8477"})
        self.assertIn("https://box.ts.net:8477", out)

    def test_install_keeps_public_url_and_loopback(self):
        seen = {}

        def fake_install(program, log_path, path_env):
            seen["program"] = program
            return Path("/h/Library/LaunchAgents/x.plist")

        with patch.object(commands.sys, "platform", "darwin"), \
                patch.object(commands.sys, "argv", ["/opt/swe/bin/swe"]), \
                patch("quiver.remote.commands.os.access", return_value=True), \
                patch.object(commands.launchd, "loaded_pid",
                             return_value=(False, None)), \
                patch.object(commands.launchd, "install", fake_install), \
                patch.object(commands, "_probe", return_value=None) as probe:
            rc, _, _ = _run(["serve", "--install",
                             "--public-url=https://box.ts.net:8477"])
        self.assertEqual(rc, 0)
        # Behind serve the agent binds loopback, never waits on --tailnet.
        self.assertEqual(seen["program"], [
            "/opt/swe/bin/swe", "remote", "serve", "--host=127.0.0.1",
            "--public-url=https://box.ts.net:8477",
        ])
        probe.assert_called_once_with("127.0.0.1", 8477, timeout=1.0)

    def test_install_bad_public_url(self):
        with patch.object(commands.sys, "platform", "darwin"):
            rc, _, err = _run(["serve", "--install", "--public-url=nope"])
        self.assertEqual(rc, 1)
        self.assertIn("--public-url", err)

    def test_serve_bad_public_url(self):
        rc, _, err = _run(["serve", "--public-url=box.ts.net"])
        self.assertEqual(rc, 1)
        self.assertIn("--public-url", err)


class TestAgentCaFallback(unittest.TestCase):
    def setUp(self):
        agent_mod._fallback_ctx = None
        self.addCleanup(setattr, agent_mod, "_fallback_ctx", None)

    def _cert_error(self):
        return urllib.error.URLError(ssl.SSLCertVerificationError("no CA"))

    def test_retries_with_os_bundle_then_keeps_it(self):
        ctx = object()
        calls = []

        def urlopen(req, timeout=None, context=None):
            calls.append(context)
            if context is None:
                raise self._cert_error()
            return "resp"

        with patch("urllib.request.urlopen", urlopen), \
                patch("quiver.harness.rate_limits._verified_context",
                      return_value=ctx):
            self.assertEqual(agent_mod._urlopen("req", 5), "resp")
            self.assertEqual(agent_mod._urlopen("req", 5), "resp")
        # One failed default try, then the bundle on every call.
        self.assertEqual(calls, [None, ctx, ctx])

    def test_no_bundle_reraises(self):
        with patch("urllib.request.urlopen",
                   side_effect=self._cert_error()), \
                patch("quiver.harness.rate_limits._verified_context",
                      return_value=None):
            with self.assertRaises(urllib.error.URLError):
                agent_mod._urlopen("req", 5)
        self.assertIsNone(agent_mod._fallback_ctx)

    def test_other_errors_not_retried(self):
        err = urllib.error.URLError(ConnectionRefusedError())
        with patch("urllib.request.urlopen", side_effect=err) as urlopen:
            with self.assertRaises(urllib.error.URLError):
                agent_mod._urlopen("req", 5)
        self.assertEqual(urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
