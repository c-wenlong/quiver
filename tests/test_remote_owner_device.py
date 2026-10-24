"""The hub owner's own tailnet devices use the hub without pairing.

A request with no bearer is let in when its tailnet login is the hub
owner's, it carries ``X-Quiver-Pair: 1`` from a Host the hub answers to,
and its Origin (if any) is the hub's own: the gate the approval page
already uses, so a page on another site can't borrow the identity.
"""

import json
import threading
import unittest
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
        status, _ = self.srv.request("GET", "/v1/sessions", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, [])

    def test_whois_asked_once_per_ip(self):
        for _ in range(3):
            self.assertEqual(self.get("/v1/sessions")[0], 200)
        self.assertEqual(self.calls, [_OWNER_IP])


class TestWhoisCache(unittest.TestCase):
    def test_hits_and_misses_expire(self):
        state = hub_mod.HubState(None)
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
        state = hub_mod.HubState(None)
        state.whois = lambda ip: None
        for i in range(300):
            state.whois_cached(f"100.64.1.{i}")
        self.assertLessEqual(len(state._whois_cache), 257)
        self.assertLessEqual(len(state._whois_ip_locks), 257)

    def test_concurrent_callers_share_one_lookup(self):
        state = hub_mod.HubState(None)
        started = threading.Event()
        release = threading.Event()
        calls = []

        def slow_whois(ip):
            calls.append(ip)
            started.set()
            release.wait(5)
            return _PEERS.get(ip)

        state.whois = slow_whois
        results = []
        threads = [threading.Thread(
            target=lambda: results.append(state.whois_cached(_OWNER_IP)))
            for _ in range(4)]
        threads[0].start()
        self.assertTrue(started.wait(5))
        for t in threads[1:]:
            t.start()
        release.set()
        for t in threads:
            t.join(5)
        self.assertEqual(calls, [_OWNER_IP])
        self.assertEqual([r["node_id"] for r in results], ["n-laptop"] * 4)


if __name__ == "__main__":
    unittest.main()
