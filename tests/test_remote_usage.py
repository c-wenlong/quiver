"""usage.py and GET /v1/usage: swe list --usage rows for the hub machine."""

import json
import unittest
from unittest.mock import patch

from quiver.harness.models import RateLimitInfo
from quiver.remote import usage

from tests.test_remote_hub import HubBase


def _limit(tool="codex", used=40):
    return RateLimitInfo(tool_name=tool, used_percent=used,
                         limit_reached=False, reset_at=2_000_000_000.0,
                         plan_type="plus", window_seconds=18000, window="5h")


class _Patched:
    """Patches every source ``snapshot`` reads; ``fetch`` records calls."""

    def __init__(self, registry, stars=(), archived=None, limits=None,
                 counts=None):
        self.calls = []

        def fetch(**kw):
            self.calls.append(kw)
            return dict(limits or {})

        self._patches = [
            patch("quiver.harness.registry.load_registry_readonly",
                  return_value=registry),
            patch("quiver.harness.stars.load_stars", return_value=list(stars)),
            patch("quiver.harness.archive.load_archive",
                  return_value=dict(archived or {})),
            patch("quiver.harness.rate_limits.get_all_rate_limits",
                  side_effect=fetch),
            patch("quiver.sessions.usage.session_counts",
                  return_value=dict(counts or {})),
            patch("quiver.harness.tools.is_installed", return_value=True),
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
        self.assertIsNone(p.calls[0]["cache_ttl"])

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


if __name__ == "__main__":
    unittest.main()
