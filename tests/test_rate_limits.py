import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from io import BytesIO

from quiver.harness.rate_limits import (
    RateLimitInfo,
    get_all_rate_limits,
    get_rate_limit,
    invalidate_cache,
    register,
    _FETCHERS,
)


class RateLimitInfoTest(unittest.TestCase):
    _NOW = 1784758000.0  # fixed "current time" for deterministic tests

    def _make_info(self, used_percent, limit_reached, reset_offset,
                   plan_type="plus", window_seconds=604800):
        """Build a RateLimitInfo with reset_at relative to _NOW."""
        with patch("quiver.harness.rate_limits.time.time", return_value=self._NOW):
            return RateLimitInfo(
                tool_name="codex",
                used_percent=used_percent,
                limit_reached=limit_reached,
                reset_at=self._NOW + reset_offset,
                plan_type=plan_type,
                window_seconds=window_seconds,
            )

    def test_format_column_green(self):
        info = self._make_info(30, False, 3600)  # 1h ahead
        with patch("quiver.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        self.assertIn("30%", col)
        self.assertIn("1h0m", col)

    def test_format_column_yellow_threshold(self):
        info = self._make_info(85, False, 7200)  # 2h ahead
        with patch("quiver.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        self.assertIn("85%", col)

    def test_format_column_red_when_reached(self):
        info = self._make_info(100, True, 503753)
        with patch("quiver.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        self.assertIn("100%", col)

    def test_reset_in_human_days(self):
        info = self._make_info(50, False, 5 * 86400 + 3600)  # 5d1h ahead
        with patch("quiver.harness.rate_limits.time.time", return_value=self._NOW):
            self.assertEqual(info.reset_in_human, "5d1h")

    def test_reset_in_human_now(self):
        info = self._make_info(100, True, -10)  # already past
        with patch("quiver.harness.rate_limits.time.time", return_value=self._NOW):
            self.assertEqual(info.reset_in_human, "now")

    def test_reset_in_human_unknown(self):
        info = self._make_info(50, False, 0, plan_type="—", window_seconds=0)
        info.reset_at = 0  # override to truly unknown
        with patch("quiver.harness.rate_limits.time.time", return_value=self._NOW):
            self.assertEqual(info.reset_in_human, "—")


class RateLimitCacheTest(unittest.TestCase):
    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "rate_limits_cache.json"
            with patch("quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE", cache_file):
                # Save some data
                from quiver.harness.rate_limits import _save_cached, _load_cached

                raw = {
                    "codex": {
                        "tool_name": "codex",
                        "used_percent": 42,
                        "limit_reached": False,
                        "reset_at": time.time() + 3600,
                        "plan_type": "plus",
                        "window_seconds": 604800,
                    }
                }
                _save_cached(raw)
                loaded = _load_cached()
                self.assertIsNotNone(loaded)
                self.assertIn("codex", loaded)
                self.assertEqual(loaded["codex"]["used_percent"], 42)

    def test_cache_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "rate_limits_cache.json"
            with patch("quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE", cache_file):
                from quiver.harness.rate_limits import _save_cached, _load_cached, _CACHE_TTL

                raw = {
                    "codex": {
                        "tool_name": "codex",
                        "used_percent": 42,
                        "limit_reached": False,
                        "reset_at": time.time() + 3600,
                        "plan_type": "plus",
                        "window_seconds": 604800,
                    }
                }
                # Write with an old timestamp
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(
                    json.dumps({"cached_at": time.time() - _CACHE_TTL - 10, "limits": raw})
                )
                loaded = _load_cached()
                self.assertIsNone(loaded)

    def test_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "rate_limits_cache.json"
            with patch("quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE", cache_file):
                from quiver.harness.rate_limits import _save_cached

                _save_cached({"codex": {"tool_name": "codex", "used_percent": 50,
                                         "limit_reached": False, "reset_at": 0,
                                         "plan_type": "plus", "window_seconds": 0}})
                self.assertTrue(cache_file.exists())
                invalidate_cache()
                self.assertFalse(cache_file.exists())


class RateLimitRegistryTest(unittest.TestCase):
    def test_register_and_fetch(self):
        """A custom fetcher can be registered and queried."""
        saved = _FETCHERS.copy()

        def fake_fetch():
            return RateLimitInfo(
                tool_name="test-tool",
                used_percent=10,
                limit_reached=False,
                reset_at=time.time() + 100,
                plan_type="free",
                window_seconds=3600,
            )

        register("test-tool", fake_fetch)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cache_file = Path(tmp) / "rate_limits_cache.json"
                with patch("quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE", cache_file):
                    result = get_all_rate_limits(use_cache=False)
                    self.assertIn("test-tool", result)
                    self.assertEqual(result["test-tool"].used_percent, 10)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

    def test_fetcher_returns_none_is_omitted(self):
        """Tools whose fetcher returns None should not appear in results."""
        saved = _FETCHERS.copy()

        def none_fetch():
            return None

        register("no-limits-tool", none_fetch)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cache_file = Path(tmp) / "rate_limits_cache.json"
                with patch("quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE", cache_file):
                    result = get_all_rate_limits(use_cache=False)
                    self.assertNotIn("no-limits-tool", result)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

    def test_fetcher_exception_is_swallowed(self):
        """A fetcher that raises should not crash get_all_rate_limits."""
        saved = _FETCHERS.copy()

        def boom_fetch():
            raise RuntimeError("network down")

        register("boom-tool", boom_fetch)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cache_file = Path(tmp) / "rate_limits_cache.json"
                with patch("quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE", cache_file):
                    result = get_all_rate_limits(use_cache=False)
                    self.assertNotIn("boom-tool", result)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)


class CodexFetcherTest(unittest.TestCase):
    """Test the Codex wham/usage fetcher with mocked HTTP."""

    _SAMPLE_RESPONSE = {
        "user_id": "user-test",
        "plan_type": "plus",
        "rate_limit": {
            "allowed": False,
            "limit_reached": True,
            "primary_window": {
                "used_percent": 100,
                "limit_window_seconds": 604800,
                "reset_after_seconds": 503753,
                "reset_at": 1785261854,
            },
            "secondary_window": None,
        },
        "additional_rate_limits": [],
        "credits": {"has_credits": False, "balance": "0"},
    }

    def test_fetch_codex_success(self):
        from quiver.harness.rate_limits import _fetch_codex

        auth_data = {
            "auth_mode": "chatgpt",
            "tokens": {"access_token": "fake-token", "id_token": "x",
                       "refresh_token": "y", "account_id": "z"},
        }
        resp_json = json.dumps(self._SAMPLE_RESPONSE).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = resp_json
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text(json.dumps(auth_data))
            with patch("quiver.harness.rate_limits.os.path.expanduser",
                       side_effect=lambda p: str(auth_path) if p == "~/.codex/auth.json" else p), \
                 patch("quiver.harness.rate_limits.urllib.request.urlopen",
                       return_value=mock_resp):
                info = _fetch_codex()
                self.assertIsNotNone(info)
                self.assertEqual(info.tool_name, "codex")
                self.assertEqual(info.used_percent, 100)
                self.assertTrue(info.limit_reached)
                self.assertEqual(info.reset_at, 1785261854.0)
                self.assertEqual(info.plan_type, "plus")
                self.assertEqual(info.window_seconds, 604800)

    def test_fetch_codex_no_auth_file(self):
        from quiver.harness.rate_limits import _fetch_codex

        with patch("quiver.harness.rate_limits.os.path.expanduser",
                   side_effect=lambda p: "/nonexistent/path" if p == "~/.codex/auth.json" else p):
            info = _fetch_codex()
            self.assertIsNone(info)

    def test_fetch_codex_no_access_token(self):
        from quiver.harness.rate_limits import _fetch_codex

        auth_data = {"auth_mode": "chatgpt", "tokens": {}}
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text(json.dumps(auth_data))
            with patch("quiver.harness.rate_limits.os.path.expanduser",
                       side_effect=lambda p: str(auth_path) if p == "~/.codex/auth.json" else p):
                info = _fetch_codex()
                self.assertIsNone(info)

    def test_fetch_codex_http_error(self):
        from quiver.harness.rate_limits import _fetch_codex
        import urllib.error

        auth_data = {
            "auth_mode": "chatgpt",
            "tokens": {"access_token": "fake-token"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text(json.dumps(auth_data))
            with patch("quiver.harness.rate_limits.os.path.expanduser",
                       side_effect=lambda p: str(auth_path) if p == "~/.codex/auth.json" else p), \
                 patch("quiver.harness.rate_limits.urllib.request.urlopen",
                       side_effect=urllib.error.HTTPError(
                           "url", 401, "Unauthorized", {}, None)):
                info = _fetch_codex()
                self.assertIsNone(info)


if __name__ == "__main__":
    unittest.main()
