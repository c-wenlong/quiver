"""The hub owner's own tailnet devices use the hub without pairing.

A request with no bearer is let in when its tailnet login is the hub
owner's, it carries ``X-Quiver-Pair: 1`` from a Host the hub answers to,
and its Origin (if any) is the hub's own: the gate the approval page
already uses, so a page on another site can't borrow the identity.
"""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quiver.remote import dismissals
from quiver.remote import hub as hub_mod

from tests.test_remote_hub import _Server, _session

_OWNER_IP = "100.64.0.9"
_PEERS = {
    _OWNER_IP: {"node_id": "n-laptop", "login": "dev@github",
                "node_name": "laptop.ts.net", "node_short": "laptop",
                "tagged": False},
    "100.64.0.20": {"node_id": "n-friend", "login": "friend@github",
                    "tagged": False},
    "100.64.0.30": {"node_id": "n-server", "login": "dev@github",
                    "tagged": True},
}


class OwnerBase(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[_session()])
        self.addCleanup(self.srv.stop)
        self.srv.state.trust_proxy = True
        # The test client stands in for tailscale serve's connection.
        self.srv.state.proxy_peer = lambda server, peer: True
        self.srv.state.owner_login = "dev@github"
        self.calls = []

        def whois(ip):
            self.calls.append(ip)
            return _PEERS.get(ip)

        self.srv.state.whois = whois

    def get(self, path, ip=_OWNER_IP, **extra):
        headers = {"X-Forwarded-For": ip, "X-Forwarded-Proto": "https",
                   "X-Quiver-Pair": "1", **extra}
        return self.srv.request("GET", path, headers=headers)


class TestOwnerDevice(OwnerBase):
    def test_owner_reads_without_pairing(self):
        status, data = self.get("/v1/sessions")
        self.assertEqual(status, 200, data)
        self.assertEqual(len(json.loads(data)["sessions"]), 1)

    def test_needs_the_page_header(self):
        status, _ = self.srv.request("GET", "/v1/sessions", headers={
            "X-Forwarded-For": _OWNER_IP, "X-Forwarded-Proto": "https",
        })
        self.assertEqual(status, 401)

    def test_other_logins_and_tagged_nodes_still_pair(self):
        for ip in ("100.64.0.20", "100.64.0.30", "100.64.0.99"):
            self.assertEqual(self.get("/v1/sessions", ip=ip)[0], 401, ip)

    def test_cross_site_origin_refused(self):
        status, _ = self.get("/v1/sessions", Origin="https://evil.example")
        self.assertEqual(status, 401)

    def test_unknown_host_refused(self):
        # DNS rebinding: a name the hub doesn't answer to.
        self.srv.state.allowed_hosts = {"box.ts.net:8477"}
        self.assertEqual(self.get("/v1/sessions")[0], 401)
        self.assertEqual(
            self.get("/v1/sessions", Host="box.ts.net:8477")[0], 200)

    def test_no_owner_known_means_no_shortcut(self):
        self.srv.state.owner_login = ""
        self.assertEqual(self.get("/v1/sessions")[0], 401)

    def test_satellite_routes_stay_bearer_only(self):
        status, _ = self.get("/v1/agent/commands?machine=m1")
        self.assertEqual(status, 401)

    def test_owner_writes_are_its_own_device(self):
        headers = {"X-Forwarded-For": _OWNER_IP, "X-Forwarded-Proto": "https",
                   "X-Quiver-Pair": "1", "Content-Type": "application/json"}
        status, data = self.srv.request(
            "POST", "/v1/inbox/dismiss",
            {"key": "claude:s1", "last_active_at": 100.0}, headers=headers,
        )
        self.assertEqual(status, 200, data)
        self.assertIn("claude:s1",
                      dismissals.for_device(self.srv.home, "tailnet:n-laptop"))

    def test_bearer_still_wins(self):
        auth = {"Authorization": f"Bearer {self.srv.pair()['credential']}"}
        # The pair exchange itself may resolve the tailnet node for the
        # device row; an already-paired bearer's requests never do.
        self.calls.clear()
        status, _ = self.srv.request("GET", "/v1/sessions", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, [])

    def test_whois_asked_once_per_ip(self):
        for _ in range(3):
            self.assertEqual(self.get("/v1/sessions")[0], 200)
        self.assertEqual(self.calls, [_OWNER_IP])


    def test_forged_forwarded_for_ignored_without_trust_proxy(self):
        # No serve in front: X-Forwarded-For is just a client header,
        # so naming the owner's tailnet IP in it must not let a local
        # caller borrow the owner's identity.
        self.srv.state.trust_proxy = False
        status, _ = self.get("/v1/sessions")
        self.assertEqual(status, 401)
        self.assertNotIn(_OWNER_IP, self.calls)


class TestClientAddress(unittest.TestCase):
    """_proxied/_client_ip without a socket: which peer the hub trusts
    to speak for another address."""

    def _handler(self, peer, forwarded=None, trust_proxy=True,
                 is_proxy=True):
        handler = hub_mod._Handler.__new__(hub_mod._Handler)
        handler.server = MagicMock()
        handler.server.state.trust_proxy = trust_proxy
        handler.server.state.proxy_peer = MagicMock(return_value=is_proxy)
        handler.connection = MagicMock()
        handler.connection.getsockname.return_value = ("127.0.0.1", 8477)
        handler.client_address = (peer, 50000)
        handler.headers = (
            {"X-Forwarded-For": forwarded} if forwarded is not None else {}
        )
        return handler

    def test_non_loopback_peer_is_never_proxied(self):
        h = self._handler("100.64.0.20", forwarded=_OWNER_IP)
        self.assertFalse(h._proxied())
        self.assertEqual(h._client_ip(), "100.64.0.20")

    def test_ipv6_loopback_peer_is_proxied(self):
        h = self._handler("::1", forwarded=_OWNER_IP)
        self.assertTrue(h._proxied())
        self.assertEqual(h._client_ip(), _OWNER_IP)

    def test_last_hop_wins(self):
        # Serve appends the hop it saw; anything before it is the
        # client's own claim.
        h = self._handler("127.0.0.1", forwarded=f"1.2.3.4, {_OWNER_IP}")
        self.assertEqual(h._client_ip(), _OWNER_IP)

    def test_loopback_without_a_hop_is_the_peer(self):
        for forwarded in (None, "", " , "):
            h = self._handler("127.0.0.1", forwarded=forwarded)
            self.assertEqual(h._client_ip(), "127.0.0.1", forwarded)

    def test_trust_proxy_off_ignores_the_header(self):
        for peer in ("127.0.0.1", "::1"):
            h = self._handler(peer, forwarded=_OWNER_IP, trust_proxy=False)
            self.assertFalse(h._proxied())
            self.assertEqual(h._client_ip(), peer)

class TestWhoisCache(unittest.TestCase):
    def setUp(self):
        # HubState(None) would write machine.json under the real HOME.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def test_hits_and_misses_expire(self):
        state = hub_mod.HubState(self.home)
        state.whois = MagicMock(side_effect=lambda ip: _PEERS.get(ip))
        clock = [1000.0]
        with patch.object(hub_mod.time, "time", lambda: clock[0]):
            state.whois_cached(_OWNER_IP)
            state.whois_cached("100.64.0.99")
            clock[0] += hub_mod._WHOIS_MISS_TTL_S + 1
            state.whois_cached(_OWNER_IP)       # still cached
            state.whois_cached("100.64.0.99")   # miss asked again
            clock[0] += hub_mod._WHOIS_TTL_S
            state.whois_cached(_OWNER_IP)       # hit expired
        self.assertEqual(
            [c.args[0] for c in state.whois.call_args_list],
            [_OWNER_IP, "100.64.0.99", "100.64.0.99", _OWNER_IP],
        )


    def test_cache_stays_bounded(self):
        state = hub_mod.HubState(self.home)
        state.whois = lambda ip: None
        for i in range(300):
            state.whois_cached(f"100.64.1.{i}")
        self.assertLessEqual(len(state._whois_cache), 257)
        self.assertLessEqual(len(state._whois_ip_locks), 257)

    def test_concurrent_callers_share_one_lookup(self):
        state = hub_mod.HubState(self.home)
        started = threading.Event()
        release = threading.Event()
        calls = []

        def slow_whois(ip):
            calls.append(ip)
            started.set()
            release.wait(5)
            return _PEERS.get(ip)

        state.whois = slow_whois
        # The lookup is released only once all four callers have missed
        # the cache (the first twice: before and under its lock), so
        # the other three really are queued behind it.
        misses = []
        all_missed = threading.Event()
        real_hit = state._whois_hit

        def whois_hit(ip):
            found = real_hit(ip)
            if not found[0]:
                misses.append(ip)
                if len(misses) >= 5:
                    all_missed.set()
            return found

        state._whois_hit = whois_hit
        results = []
        threads = [threading.Thread(
            target=lambda: results.append(state.whois_cached(_OWNER_IP)))
            for _ in range(4)]
        threads[0].start()
        self.assertTrue(started.wait(5))
        for t in threads[1:]:
            t.start()
        self.assertTrue(all_missed.wait(5))
        release.set()
        for t in threads:
            t.join(5)
        self.assertEqual(calls, [_OWNER_IP])
        self.assertEqual([r["node_id"] for r in results], ["n-laptop"] * 4)


if __name__ == "__main__":
    unittest.main()
