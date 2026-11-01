"""usage.py and GET /v1/usage: swe list --usage rows for the hub machine."""

import http.client
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from swe.harness.models import RateLimitInfo
from swe.remote import agent as agent_mod
from swe.remote import pairing, usage

from tests.test_remote_hub import HubBase


def _limit(tool="codex", used=40):
    return RateLimitInfo(tool_name=tool, used_percent=used,
                         limit_reached=False, reset_at=2_000_000_000.0,
                         plan_type="plus", window_seconds=18000, window="5h")


class _Patched:
    """Patches every source ``snapshot`` reads; ``fetch`` records calls."""

    def __init__(self, registry, stars=(), archived=None, limits=None,
                 counts=None, no_reading=None):
        self.calls = []

        def fetch(**kw):
            self.calls.append(kw)
            if kw.get("no_reading") is not None:
                kw["no_reading"].update(no_reading or {})
            return dict(limits or {})

        self._patches = [
            patch("swe.harness.registry.load_registry_readonly",
                  return_value=registry),
            patch("swe.harness.stars.load_stars", return_value=list(stars)),
            patch("swe.harness.archive.load_archive",
                  return_value=dict(archived or {})),
            patch("swe.harness.rate_limits.get_all_rate_limits",
                  side_effect=fetch),
            patch("swe.sessions.usage.session_counts",
                  return_value=dict(counts or {})),
            patch("swe.harness.tools.is_installed", return_value=True),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()


class TestSnapshot(unittest.TestCase):
    def test_rows_counts_and_starred_limits(self):
        reg = {"codex": {"command": "codex"}, "aider": {"command": "aider"}}
        with _Patched(reg, stars=["codex"], limits={"codex": _limit()},
                      counts={"codex": 12}) as p:
            body = usage.snapshot()
        rows = {r["name"]: r for r in body["harnesses"]}
        self.assertEqual(body["window_days"], 100)
        self.assertEqual(rows["codex"]["sessions_100d"], 12)
        self.assertEqual(rows["codex"]["rate_limit"]["used_percent"], 40)
        self.assertEqual(rows["codex"]["rate_limit"]["window"], "5h")
        self.assertIsNone(rows["aider"]["rate_limit"])
        # No count means swe has no session parser for it, as in swe list.
        self.assertIsNone(rows["aider"]["sessions_100d"])
        # Only starred harnesses are polled, as swe list does.
        self.assertEqual(p.calls[0]["tool_names"], {"codex"})
        self.assertFalse(p.calls[0]["refresh_failed"])
        self.assertEqual(p.calls[0]["cache_ttl"], usage.CACHE_TTL_S)

    def test_rows_say_why_a_starred_limit_is_missing(self):
        reg = {"codex": {"command": "codex"}, "cursor": {"command": "cursor-agent"},
               "devin": {"command": "devin"}}
        with _Patched(reg, stars=["codex", "cursor", "devin"],
                      no_reading={"codex": "signed_out", "cursor": "error"}):
            body = usage.snapshot()
        rows = {r["name"]: r for r in body["harnesses"]}
        self.assertEqual(rows["codex"]["no_reading"], "signed_out")
        self.assertEqual(rows["cursor"]["no_reading"], "error")
        self.assertIsNone(rows["devin"]["no_reading"])
        self.assertIsNone(rows["codex"]["rate_limit"])

    def test_archived_harness_left_out(self):
        reg = {"codex": {"command": "codex"}, "old": {"command": "old"}}
        with _Patched(reg, stars=["codex", "old"],
                      archived={"old": {"reason": "x"}}) as p:
            body = usage.snapshot()
        self.assertEqual([r["name"] for r in body["harnesses"]], ["codex"])
        self.assertEqual(p.calls[0]["tool_names"], {"codex"})

    def test_star_without_registry_entry_not_polled(self):
        with _Patched({"codex": {"command": "codex"}},
                      stars=["codex", "ghost"]) as p:
            usage.snapshot()
        self.assertEqual(p.calls[0]["tool_names"], {"codex"})

    def test_ttl_override_from_shell_ignored(self):
        # SWE_RATE_LIMITS_TTL=inf in the shell that started the hub must
        # not freeze the page on one reading.
        with patch.dict("os.environ", {"SWE_RATE_LIMITS_TTL": "inf"}), \
                _Patched({"codex": {"command": "codex"}},
                         stars=["codex"]) as p:
            usage.snapshot()
        self.assertEqual(p.calls[0]["cache_ttl"], 300.0)

    def test_refresh_retries_failed_and_stale_only(self):
        with _Patched({"codex": {"command": "codex"}}, stars=["codex"]) as p:
            usage.snapshot(refresh=True)
        self.assertTrue(p.calls[0]["refresh_failed"])
        self.assertEqual(p.calls[0]["cache_ttl"], usage.REFRESH_TTL_S)


class TestUsageRoute(HubBase):
    def test_requires_auth(self):
        status, _ = self.srv.request("GET", "/v1/usage")
        self.assertEqual(status, 401)

    def test_body_carries_machine(self):
        with patch.object(usage, "snapshot",
                          return_value={"harnesses": [], "window_days": 100}
                          ) as snap:
            status, data = self.srv.request(
                "GET", "/v1/usage?refresh=1", headers=self.auth()
            )
        self.assertEqual(status, 200)
        body = json.loads(data)
        self.assertEqual(body["machine"]["id"], self.srv.state.machine_id)
        self.assertEqual(body["harnesses"], [])
        snap.assert_called_once_with(refresh=True)

    def test_plain_get_does_not_refresh(self):
        with patch.object(usage, "snapshot",
                          return_value={"harnesses": []}) as snap:
            self.srv.request("GET", "/v1/usage", headers=self.auth())
        snap.assert_called_once_with(refresh=False)

    def test_unreadable_registry_is_503(self):
        with patch.object(usage, "snapshot",
                          side_effect=ValueError("harness.json is malformed")):
            status, data = self.srv.request(
                "GET", "/v1/usage", headers=self.auth()
            )
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(data)["error"]["code"],
                         "usage_unavailable")


class TestSatelliteStore(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_round_trip_newest_first(self):
        with patch("swe.remote.usage.time.time", return_value=100.0):
            usage.save_satellite(self.home, "old", {
                "harnesses": [{"name": "codex"}], "server_time": 90.0,
            })
        with patch("swe.remote.usage.time.time", return_value=200.0):
            usage.save_satellite(self.home, "mbp", {
                "harnesses": [{"name": "claude"}, "junk"],
                "window_days": 100, "server_time": 195.5,
            })
        records = usage.load_satellites(self.home)
        self.assertEqual([r["machine_id"] for r in records], ["mbp", "old"])
        self.assertEqual(records[0]["harnesses"], [{"name": "claude"}])
        self.assertEqual(records[0]["reported_at"], 195.5)
        self.assertEqual(records[0]["received_at"], 200.0)

    def test_daily_usage_kept_when_well_formed(self):
        usage.save_satellite(self.home, "a", {"harnesses": [], "server_time": 50.0, "daily_usage": {
            "time_zone": "UTC", "days": [
                {"date": "2026-10-01", "harness": "claude", "tokens": 5, "cost_usd": float("inf")},
                {"date": "2026-10-02", "tokens": -3, "cost_usd": 10 ** 400},
                "junk", {"no": "date"}]}})
        usage.save_satellite(self.home, "b", {"harnesses": [], "daily_usage": "bad"})
        records = {r["machine_id"]: r for r in usage.load_satellites(self.home)}
        daily = records["a"]["daily_usage"]
        self.assertEqual(daily["reported_at"], 50.0)
        self.assertEqual([(r["date"], r["harness"], r["tokens"], r["cost_usd"]) for r in daily["days"]],
                         [("2026-10-01", "claude", 5, 0.0), ("2026-10-02", "unknown", 0, 0.0)])
        self.assertIsNone(records["b"]["daily_usage"])

    def test_a_report_without_daily_rows_keeps_the_last_ones(self):
        usage.save_satellite(self.home, "a", {"harnesses": [], "server_time": 50.0, "daily_usage": {
            "time_zone": "UTC", "days": [{"date": "2026-10-01", "tokens": 5}]}})
        usage.save_satellite(self.home, "a", {"harnesses": [{"name": "x"}], "server_time": 80.0})
        record = usage.load_satellites(self.home)[0]
        self.assertEqual(record["reported_at"], 80.0)
        self.assertEqual(record["daily_usage"]["reported_at"], 50.0)
        self.assertEqual(record["daily_usage"]["days"][0]["tokens"], 5)

    def test_bad_harnesses_rejected(self):
        with self.assertRaises(ValueError):
            usage.save_satellite(self.home, "mbp", {"harnesses": "x"})
        self.assertEqual(usage.load_satellites(self.home), [])

    def test_bool_server_time_not_a_clock(self):
        record = usage.save_satellite(
            self.home, "mbp", {"harnesses": [], "server_time": True}
        )
        self.assertIsNone(record["reported_at"])

    def test_machine_id_cannot_escape_the_store(self):
        usage.save_satellite(self.home, "../../evil", {"harnesses": []})
        store = usage._satellite_file(self.home, "../../evil")
        self.assertEqual(store.parent.name, "satellite-usage")
        self.assertEqual(len(usage.load_satellites(self.home)), 1)

    def test_no_store_yet_is_empty(self):
        self.assertEqual(usage.load_satellites(self.home), [])

    def test_unreadable_store_is_empty(self):
        with patch.object(Path, "glob", side_effect=OSError("denied")):
            self.assertEqual(usage.load_satellites(self.home), [])

    def test_corrupt_file_skipped(self):
        usage.save_satellite(self.home, "mbp", {"harnesses": []})
        usage._satellite_file(self.home, "bad").write_text("{nope")
        self.assertEqual(
            [r["machine_id"] for r in usage.load_satellites(self.home)],
            ["mbp"],
        )


class TestAgentUsageRoute(HubBase):
    def _usage(self, _satellite_auth=None):
        """``/v1/usage`` as a phone reads it; a satellite's credential is
        for the agent routes only."""
        with patch.object(usage, "snapshot", return_value={
            "harnesses": [{"name": "hubtool"}], "window_days": 100,
            "server_time": 1000.0,
        }):
            status, data = self.srv.request(
                "GET", "/v1/usage", headers=self.auth()
            )
        self.assertEqual(status, 200)
        return json.loads(data)

    def test_requires_auth(self):
        status, _ = self.srv.request(
            "POST", "/v1/agent/usage",
            {"machine_id": "mbp", "harnesses": []},
        )
        self.assertEqual(status, 401)

    def test_rejects_missing_machine_and_bad_rows(self):
        auth = self.srv.satellite("mbp")
        for body in ({"harnesses": []},
                     {"machine_id": "mbp", "harnesses": {"a": 1}}):
            status, _ = self.srv.request(
                "POST", "/v1/agent/usage", body, headers=auth
            )
            self.assertEqual(status, 422, body)

    def test_satellite_report_listed_after_hub(self):
        auth = self.srv.satellite("mbp")
        status, _ = self.srv.request(
            "POST", "/v1/agent/register",
            {"machine": {"id": "mbp", "name": "MacBook"}}, headers=auth,
        )
        self.assertEqual(status, 200)
        status, _ = self.srv.request(
            "POST", "/v1/agent/usage",
            {"machine_id": "mbp", "harnesses": [{"name": "claude"}],
             "window_days": 100, "server_time": 990.0},
            headers=auth,
        )
        self.assertEqual(status, 200)
        body = self._usage(auth)
        # The hub's own rows stay at the top level for older clients.
        self.assertEqual(body["harnesses"], [{"name": "hubtool"}])
        hub, sat = body["machines"]
        self.assertTrue(hub["hub"])
        self.assertEqual(hub["machine"]["id"], self.srv.state.machine_id)
        self.assertEqual(hub["reported_at"], 1000.0)
        self.assertFalse(sat["hub"])
        self.assertEqual(sat["machine"]["name"], "MacBook")
        self.assertEqual(sat["harnesses"], [{"name": "claude"}])
        self.assertEqual(sat["reported_at"], 990.0)
        self.assertIsInstance(sat["received_at"], float)

    def test_overview_filters_by_machine(self):
        auth = self.srv.satellite("mbp")
        self.srv.request("POST", "/v1/agent/register",
                         {"machine": {"id": "mbp", "name": "MacBook"}}, headers=auth)
        today = time.strftime("%Y-%m-%d", time.gmtime())
        row = {"date": today, "harness": "codex", "input": 500, "output": 0,
               "cache_read": 0, "cache_write": 0, "reasoning": 0, "tokens": 500,
               "cost_usd": 1.25, "unpriced_tokens": 0}
        status, _ = self.srv.request(
            "POST", "/v1/agent/usage",
            {"machine_id": "mbp", "harnesses": [], "server_time": 990.0,
             "daily_usage": {"time_zone": "UTC", "days": [row, "junk"]}},
            headers=auth)
        self.assertEqual(status, 200)

        def overview(query=""):
            status, data = self.srv.request(
                "GET", f"/v1/app/overview?period=all&tz=UTC{query}", headers=self.auth())
            self.assertEqual(status, 200, data)
            return json.loads(data)

        everything = overview()
        hub_id = self.srv.state.machine_id
        self.assertEqual(everything["machine_filter"], "all")
        by_id = {m["id"]: m for m in everything["machines"]}
        self.assertEqual(by_id["mbp"]["name"], "MacBook")
        self.assertEqual(by_id["mbp"]["tokens"], 500)
        self.assertTrue(by_id[hub_id]["hub"])
        self.assertEqual(everything["totals"]["tokens"],
                         by_id[hub_id]["tokens"] + 500)
        laptop = overview("&machine=mbp")
        self.assertEqual(laptop["machine_filter"], "mbp")
        self.assertEqual(laptop["totals"]["tokens"], 500)
        self.assertEqual([h["harness"] for h in laptop["harnesses"]], ["codex"])
        here = overview(f"&machine={hub_id}")
        self.assertEqual(here["totals"]["tokens"], by_id[hub_id]["tokens"])
        self.assertEqual(overview("&machine=nope")["machine_filter"], "all")

    def test_unreadable_body_is_400(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                          timeout=5)
        try:
            conn.request("POST", "/v1/agent/usage", body="{nope",
                         headers={**self.srv.satellite("mbp"),
                                  "Content-Type": "application/json"})
            self.assertEqual(conn.getresponse().status, 400)
        finally:
            conn.close()

    def test_store_write_failure_is_503(self):
        with patch.object(usage, "save_satellite",
                          side_effect=OSError("disk full")):
            status, data = self.srv.request(
                "POST", "/v1/agent/usage",
                {"machine_id": "mbp", "harnesses": []},
                headers=self.srv.satellite("mbp"),
            )
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(data)["error"]["code"],
                         "usage_unavailable")

    def test_unregistered_satellite_named_by_id(self):
        # Bound to "ghost" without a register call, so no machine row
        # exists: the usage listing falls back to id-as-name.
        cred = self.srv.pair(role="satellite")
        pairing.bind_machine(self.srv.home, cred["device_id"], "ghost")
        auth = {"Authorization": f"Bearer {cred['credential']}"}
        self.srv.request("POST", "/v1/agent/usage",
                         {"machine_id": "ghost", "harnesses": []},
                         headers=auth)
        sat = self._usage(auth)["machines"][1]
        self.assertEqual(sat["machine"], {"id": "ghost", "name": "ghost"})

    def test_report_under_hub_id_ignored(self):
        # A satellite cannot claim the hub's own machine_id: register
        # refuses it outright, so a usage report never binds to it.
        cred = self.srv.pair(role="satellite")
        sat = {"Authorization": f"Bearer {cred['credential']}"}
        status, _ = self.srv.request(
            "POST", "/v1/agent/register",
            {"machine": {"id": self.srv.state.machine_id}}, headers=sat,
        )
        self.assertEqual(status, 403)
        auth = self.auth()
        status, _ = self.srv.request(
            "POST", "/v1/agent/usage",
            {"machine_id": self.srv.state.machine_id, "harnesses": []},
            headers=sat,
        )
        self.assertEqual(status, 403)
        self.assertEqual(len(self._usage(auth)["machines"]), 1)


class TestAgentPushesUsage(unittest.TestCase):
    def test_push_sends_machine_and_snapshot(self):
        agent = agent_mod.RemoteAgent(
            "http://hub", "cred", home=Path(tempfile.mkdtemp())
        )
        snap = {"harnesses": [{"name": "claude"}], "window_days": 100,
                "server_time": 5.0}
        with patch.object(usage, "snapshot", return_value=snap), \
                patch.object(agent_mod, "_post",
                             return_value=(200, {"ok": True})) as post:
            self.assertTrue(agent._push_usage())
        hub, path, cred, payload = post.call_args.args
        self.assertEqual((hub, path, cred),
                         ("http://hub", "/v1/agent/usage", "cred"))
        self.assertEqual(payload, {"machine_id": agent.machine_id, **snap})

    def test_push_carries_daily_usage_when_the_ledger_reads(self):
        agent = agent_mod.RemoteAgent(
            "http://hub", "cred", home=Path(tempfile.mkdtemp())
        )
        daily = {"time_zone": "UTC", "days": [{"date": "2026-10-01", "tokens": 5}]}
        for value, expected in ((daily, daily), (None, "absent")):
            with patch.object(usage, "snapshot", return_value={"harnesses": []}), \
                    patch.object(usage, "daily_usage", return_value=value), \
                    patch.object(agent_mod, "_post", return_value=(200, {})) as post:
                agent._push_usage()
            payload = post.call_args.args[3]
            self.assertEqual(payload.get("daily_usage", "absent"), expected)

    def test_daily_usage_reads_this_machines_ledger(self):
        home = Path(tempfile.mkdtemp())
        out = usage.daily_usage(home)
        self.assertEqual(out["days"], [])
        self.assertIn("time_zone", out)

    def test_failed_push_reports_false(self):
        agent = agent_mod.RemoteAgent(
            "http://hub", "cred", home=Path(tempfile.mkdtemp())
        )
        with patch.object(usage, "snapshot", return_value={"harnesses": []}), \
                patch.object(agent_mod, "_post", return_value=(0, {})):
            self.assertFalse(agent._push_usage())


class TestUsageLoop(unittest.TestCase):
    def _waits(self, push):
        agent = agent_mod.RemoteAgent(
            "http://hub", "cred", home=Path(tempfile.mkdtemp())
        )
        agent._stop = MagicMock()
        agent._stop.is_set.side_effect = [False, True]
        with patch.object(agent, "_push_usage", side_effect=push):
            agent._usage_loop()
        return [c.args[0] for c in agent._stop.wait.call_args_list]

    def test_success_waits_the_push_interval(self):
        self.assertEqual(self._waits([True]), [usage.SATELLITE_PUSH_S])

    def test_failure_retries_sooner(self):
        retry = agent_mod.RECONNECT_S * 12
        self.assertEqual(self._waits([False]), [retry])
        self.assertEqual(self._waits(RuntimeError("hub down")), [retry])
        self.assertLess(retry, usage.SATELLITE_PUSH_S)


if __name__ == "__main__":
    unittest.main()
