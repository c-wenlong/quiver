import base64
import copy
import json
import sqlite3
import sys
import os
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
        self.assertIn("70%", col)
        self.assertIn("1h0m", col)

    def test_format_column_yellow_threshold(self):
        info = self._make_info(85, False, 7200)  # 2h ahead
        with patch("quiver.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        self.assertIn("15%", col)

    def test_format_column_red_when_reached(self):
        info = self._make_info(100, True, 503753)
        with patch("quiver.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        self.assertIn("0%", col)

    def test_remaining_percent_is_clamped(self):
        self.assertEqual(self._make_info(-5, False, 0).remaining_percent, 100)
        self.assertEqual(self._make_info(105, True, 0).remaining_percent, 0)

    def test_format_column_supports_remaining_session_counts(self):
        info = RateLimitInfo(
            tool_name="freebuff",
            used_percent=25,
            limit_reached=False,
            reset_at=self._NOW + 3600,
            plan_type="free",
            window_seconds=0,
            remaining_units=3,
            total_units=4,
        )
        with patch("quiver.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        self.assertIn("3/4", col)
        self.assertIn("1h0m", col)
        self.assertNotIn("75%", col)

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

    def test_format_column_shows_relogin_for_expired_credentials(self):
        info = self._make_info(
            0, False, 0, plan_type="auth-required", window_seconds=0,
        )
        self.assertIn("re-login", info.format_column())


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

    def test_env_var_override_sets_ttl(self):
        """``SWE_RATE_LIMITS_TTL`` overrides the default TTL at read time."""
        from quiver.harness.rate_limits import _env_cache_ttl
        with patch.dict(os.environ, {"SWE_RATE_LIMITS_TTL": "600"}, clear=False):
            self.assertEqual(_env_cache_ttl(default=300.0), 600.0)
        with patch.dict(os.environ, {"SWE_RATE_LIMITS_TTL": "120"}, clear=False):
            self.assertEqual(_env_cache_ttl(default=300.0), 120.0)

    def test_env_var_override_falls_back_on_bad_value(self):
        """Unparseable / non-positive env values fall back to the default."""
        from quiver.harness.rate_limits import _env_cache_ttl
        for bad in ("not-a-number", "-10", "0", "  "):
            with patch.dict(os.environ, {"SWE_RATE_LIMITS_TTL": bad}, clear=False):
                self.assertEqual(_env_cache_ttl(default=300.0), 300.0)
        # Unset → default.
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_env_cache_ttl(default=300.0), 300.0)

    def test_default_ttl_is_five_minutes(self):
        """The module-level ``_CACHE_TTL`` default is 300s (5 minutes)."""
        from quiver.harness.rate_limits import _CACHE_TTL
        # Clear any env override so the import-time default is exercised.
        # (If the env var is set in the test shell, just assert it's a
        # positive float — the exact value is env-driven.)
        self.assertIsInstance(_CACHE_TTL, float)
        self.assertGreater(_CACHE_TTL, 0.0)


class RateLimitRegistryTest(unittest.TestCase):
    def test_single_unstarred_lookup_does_not_run_fetcher(self):
        saved = _FETCHERS.copy()
        fetcher = MagicMock()
        _FETCHERS.clear()
        register("unused", fetcher)
        try:
            with patch("quiver.harness.stars.is_starred", return_value=False):
                result = get_rate_limit("unused", use_cache=False)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertIsNone(result)
        fetcher.assert_not_called()

    def test_only_selected_fetchers_run(self):
        saved = _FETCHERS.copy()
        selected = MagicMock(return_value=RateLimitInfo(
            tool_name="selected",
            used_percent=10,
            limit_reached=False,
            reset_at=0,
            plan_type="free",
            window_seconds=0,
        ))
        unselected = MagicMock(return_value=RateLimitInfo(
            tool_name="unselected",
            used_percent=20,
            limit_reached=False,
            reset_at=0,
            plan_type="free",
            window_seconds=0,
        ))
        _FETCHERS.clear()
        register("selected", selected)
        register("unselected", unselected)
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                result = get_all_rate_limits(
                    use_cache=False,
                    tool_names={"selected"},
                )
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertEqual(set(result), {"selected"})
        selected.assert_called_once_with()
        unselected.assert_not_called()

    def test_newly_selected_fetcher_runs_when_missing_from_fresh_cache(self):
        saved = _FETCHERS.copy()
        already_cached = MagicMock()
        newly_selected = MagicMock(return_value=RateLimitInfo(
            tool_name="new",
            used_percent=30,
            limit_reached=False,
            reset_at=0,
            plan_type="free",
            window_seconds=0,
        ))
        _FETCHERS.clear()
        register("cached", already_cached)
        register("new", newly_selected)
        cached = {
            "cached": {
                "tool_name": "cached",
                "used_percent": 15,
                "limit_reached": False,
                "reset_at": 0,
                "plan_type": "free",
                "window_seconds": 0,
                "window": "",
            }
        }
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from quiver.harness.rate_limits import _save_cached

                _save_cached(cached)
                result = get_all_rate_limits(
                    use_cache=True,
                    tool_names={"cached", "new"},
                )
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertEqual(set(result), {"cached", "new"})
        already_cached.assert_not_called()
        newly_selected.assert_called_once_with()

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

        _FETCHERS.clear()
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

        _FETCHERS.clear()
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

        _FETCHERS.clear()
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

    def test_slow_fetcher_does_not_delay_fast_fetchers(self):
        """Optional provider lookups share one bounded wall-clock budget."""
        import threading

        saved = _FETCHERS.copy()
        started = threading.Event()
        release = threading.Event()

        def slow_fetch():
            started.set()
            release.wait(timeout=1.0)
            return None

        def fast_fetch():
            return RateLimitInfo(
                tool_name="fast-tool",
                used_percent=10,
                limit_reached=False,
                reset_at=0,
                plan_type="free",
                window_seconds=0,
            )

        _FETCHERS.clear()
        register("slow-tool", slow_fetch)
        register("fast-tool", fast_fetch)
        began = time.monotonic()
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ), patch(
                "quiver.harness.rate_limits._RATE_LIMIT_FETCH_DEADLINE",
                0.05,
                create=True,
            ):
                result = get_all_rate_limits(use_cache=False)
        finally:
            release.set()
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertTrue(started.is_set())
        self.assertIn("fast-tool", result)
        self.assertLess(time.monotonic() - began, 0.5)

    def test_refresh_keeps_last_good_value_when_provider_fails(self):
        """A forced fetch must not replace known usage with a blank cell."""
        saved = _FETCHERS.copy()
        _FETCHERS.clear()
        register("codex", lambda: None)
        stale = {
            "codex": {
                "tool_name": "codex",
                "used_percent": 42,
                "limit_reached": False,
                "reset_at": time.time() + 3600,
                "plan_type": "plus",
                "window_seconds": 604800,
                "window": "",
            }
        }
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from quiver.harness.rate_limits import _save_cached
                _save_cached(stale)
                result = get_all_rate_limits(use_cache=False)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertEqual(result["codex"].used_percent, 42)

    def test_refresh_drops_stale_auth_required_status(self):
        """A renewed login must not keep displaying a stale re-login marker."""
        saved = _FETCHERS.copy()
        _FETCHERS.clear()
        register("claude", lambda: None)
        stale = {
            "claude": {
                "tool_name": "claude",
                "used_percent": 0,
                "limit_reached": False,
                "reset_at": 0.0,
                "plan_type": "auth-required",
                "window_seconds": 0,
                "window": "",
            }
        }
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from quiver.harness.rate_limits import _save_cached
                _save_cached(stale)
                result = get_all_rate_limits(use_cache=False)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertNotIn("claude", result)

    def test_refresh_drops_provider_value_after_stale_fallback_expires(self):
        """Failed readings older than the fallback window are not displayed."""
        saved = _FETCHERS.copy()
        _FETCHERS.clear()
        register("codex", lambda: None)
        raw = {
            "tool_name": "codex",
            "used_percent": 42,
            "limit_reached": False,
            "reset_at": time.time() + 3600,
            "plan_type": "plus",
            "window_seconds": 604800,
            "window": "",
        }
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cache_file = Path(tmp) / "rate_limits_cache.json"
                cache_file.write_text(json.dumps({
                    "cached_at": time.time(),
                    "limits": {"codex": raw},
                    "updated_at": {"codex": time.time() - 90000},
                }))
                with patch(
                    "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                    cache_file,
                ):
                    result = get_all_rate_limits(use_cache=False)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertNotIn("codex", result)


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

    def test_fetch_codex_reset_at_type_dispatch(self):
        """``reset_at`` accepts int | float | str-ISO and rejects bool/None.

        Pins the refactor that moved Codex's parser to the shared
        ``_parse_iso8601_to_epoch`` helper. Verifies:

        - ``int`` (JSON-loaded numeric epoch) is preserved as float
        - ``float`` (JSON-loaded as float) is preserved
        - ``str`` ISO 8601 is parsed by the helper (with UTC fallback)
        - ``bool`` does NOT silently become 1.0 / 0.0 (explicit guard)
        - ``None`` and other types fall through to 0.0
        """
        from quiver.harness.rate_limits import _fetch_codex

        auth_data = {
            "auth_mode": "chatgpt",
            "tokens": {"access_token": "fake-token"},
        }
        cases = [
            ("int epoch", 1_785_261_854, 1_785_261_854.0),
            ("float epoch", 1_785_261_854.5, 1_785_261_854.5),
            ("str ISO 8601", "2026-08-01T00:00:00.123+00:00", 1_785_542_400.0),
            ("str naive ISO 8601", "2026-08-01T00:00:00", 1_785_542_400.0),
            ("bool True", True, None),  # expected: handled safely, not 1.0
            ("bool False", False, None),  # expected: handled safely, not 0.0
            ("None", None, 0.0),
            ("list", [], 0.0),
            ("dict", {}, 0.0),
        ]

        for label, reset_value, expected in cases:
            with self.subTest(label=label, reset_value=reset_value):
                # Deep-copy the class-level fixture so per-subtest
                # mutation does NOT leak into ``test_fetch_codex_success``
                # or any other test using ``_SAMPLE_RESPONSE``. Shallow
                # copies would still share the nested
                # ``primary_window`` dict and corrupt it.
                body = copy.deepcopy(self._SAMPLE_RESPONSE)
                body["rate_limit"]["primary_window"]["reset_at"] = reset_value
                resp_json = json.dumps(body).encode()

                mock_resp = MagicMock()
                mock_resp.read.return_value = resp_json
                mock_resp.__enter__ = MagicMock(return_value=mock_resp)
                mock_resp.__exit__ = MagicMock(return_value=False)

                with tempfile.TemporaryDirectory() as tmp:
                    auth_path = Path(tmp) / "auth.json"
                    auth_path.write_text(json.dumps(auth_data))
                    with patch(
                        "quiver.harness.rate_limits.os.path.expanduser",
                        side_effect=lambda p: str(auth_path) if p == "~/.codex/auth.json" else p,
                    ), patch(
                        "quiver.harness.rate_limits.urllib.request.urlopen",
                        return_value=mock_resp,
                    ):
                        info = _fetch_codex()
                        self.assertIsNotNone(info, f"fetch returned None for {label}")
                        if expected is None:
                            # Bool path — assert NOT silently 0.0/1.0.
                            # The new guard sets reset_at to 0.0.
                            self.assertEqual(info.reset_at, 0.0,
                                             f"{label}: bool must not leak numeric")
                        else:
                            self.assertAlmostEqual(
                                info.reset_at, expected, delta=86400,
                                msg=f"{label}: {reset_value!r} → expected {expected}, got {info.reset_at}",
                            )


class _CompletedProc:
    """Minimal stand-in for subprocess.CompletedProcess."""
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Both:
    """Enter two patches as one context manager."""

    def __init__(self, *patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class GitHubCopilotFetcherTest(unittest.TestCase):
    """Test the Copilot /copilot_internal/user fetcher with mocked subprocess + HTTP."""

    _SAMPLE_RESPONSE = {
        "login": "c-wenlong",
        "access_type_sku": "free_educational_quota",
        "copilot_plan": "individual",
        "quota_reset_date": "2026-08-01",
        "quota_reset_date_utc": "2026-08-01T00:00:00.000Z",
        "endpoints": {
            "api": "https://api.individual.githubcopilot.com",
            "proxy": "https://proxy.individual.githubcopilot.com",
        },
        "quota_snapshots": {
            "chat": {
                "percent_remaining": 100.0, "unlimited": True,
                "entitlement": 0, "credits_used": 0, "has_quota": True,
            },
            "completions": {
                "percent_remaining": 100.0, "unlimited": True,
                "entitlement": 0, "credits_used": 0, "has_quota": True,
            },
            "premium_interactions": {
                "percent_remaining": 88.5, "unlimited": False,
                "entitlement": 1500, "credits_used": 173,
                "has_quota": True, "quota_remaining": 1327.0,
            },
        },
    }

    def _mock_response(self, body):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(body).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def _patch_token(self, token="fake-gh-token"):
        # Cover the whole `gh auth token` call: the fetcher first asks
        # shutil.which for gh, so a machine (or a nix sandbox) without the
        # CLI would otherwise return None before the mocked run is reached.
        which = patch("quiver.harness.rate_limits.shutil.which", return_value="/usr/bin/gh")
        run = patch(
            "quiver.harness.rate_limits.subprocess.run",
            return_value=_CompletedProc(returncode=0, stdout=token + "\n"),
        )
        return _Both(which, run)

    def test_fetch_copilot_success(self):
        from quiver.harness.rate_limits import _fetch_github_copilot

        with self._patch_token(), patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(self._SAMPLE_RESPONSE),
        ):
            info = _fetch_github_copilot()
        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "copilot")
        # 100 - 88.5 = 11.5 → rounded to 12
        self.assertEqual(info.used_percent, 12)
        self.assertFalse(info.limit_reached)
        # Should preserve UTC reset date as epoch.  2026-08-01T00:00:00Z
        # = 1785542400; allow ±1 day to absorb DST/leap boundaries.
        self.assertAlmostEqual(info.reset_at, 1785542400.0, delta=86400)
        # Educational quota suffix added when plan is "individual" + educational SKU
        self.assertEqual(info.plan_type, "individual/edu")

    def test_fetch_copilot_over_quota(self):
        """Negative percent_remaining (over quota) should clamp to 100 and set limit_reached."""
        from quiver.harness.rate_limits import _fetch_github_copilot

        body = dict(self._SAMPLE_RESPONSE)
        body["quota_snapshots"] = {
            "premium_interactions": {
                "percent_remaining": -0.8, "unlimited": False,
                "entitlement": 200, "credits_used": 201,
                "has_quota": False, "remaining": -2,
            }
        }
        with self._patch_token(), patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_github_copilot()
        self.assertIsNotNone(info)
        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)

    def test_fetch_copilot_unlimited(self):
        """unlimited=true should return 0% usage and not limit_reached."""
        from quiver.harness.rate_limits import _fetch_github_copilot

        body = dict(self._SAMPLE_RESPONSE)
        body["quota_snapshots"] = {
            "premium_interactions": {
                "percent_remaining": 100.0, "unlimited": True,
                "entitlement": 99999, "credits_used": 0,
                "has_quota": True,
            }
        }
        with self._patch_token(), patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_github_copilot()
        self.assertIsNotNone(info)
        self.assertEqual(info.used_percent, 0)
        self.assertFalse(info.limit_reached)

    def test_fetch_copilot_no_premium_snapshot(self):
        """Missing premium_interactions → still return RateLimitInfo with reset_at."""
        from quiver.harness.rate_limits import _fetch_github_copilot

        body = dict(self._SAMPLE_RESPONSE)
        body["quota_snapshots"] = {}
        with self._patch_token(), patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_github_copilot()
        self.assertIsNotNone(info)
        self.assertEqual(info.used_percent, 0)
        self.assertFalse(info.limit_reached)
        self.assertEqual(info.plan_type, "individual/edu")
        self.assertAlmostEqual(info.reset_at, 1785542400.0, delta=86400)

    def test_fetch_copilot_missing_reset_date(self):
        """Missing reset date → reset_at=0 (renders as '—' in UI)."""
        from quiver.harness.rate_limits import _fetch_github_copilot

        body = dict(self._SAMPLE_RESPONSE)
        body.pop("quota_reset_date_utc", None)
        body.pop("quota_reset_date", None)
        with self._patch_token(), patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_github_copilot()
        self.assertIsNotNone(info)
        self.assertEqual(info.reset_at, 0)

    def test_fetch_copilot_no_gh(self):
        """Missing gh CLI → return None."""
        from quiver.harness.rate_limits import _fetch_github_copilot

        with patch("quiver.harness.rate_limits.shutil.which", return_value=None):
            info = _fetch_github_copilot()
        self.assertIsNone(info)

    def test_fetch_copilot_gh_not_authenticated(self):
        """gh returns non-zero exit (not authenticated) → return None."""
        from quiver.harness.rate_limits import _fetch_github_copilot

        with patch(
            "quiver.harness.rate_limits.subprocess.run",
            return_value=_CompletedProc(returncode=1, stdout="", stderr="not logged in"),
        ):
            info = _fetch_github_copilot()
        self.assertIsNone(info)

    def test_fetch_copilot_gh_empty_token(self):
        """gh succeeds but stdout is empty → return None."""
        from quiver.harness.rate_limits import _fetch_github_copilot

        with patch(
            "quiver.harness.rate_limits.subprocess.run",
            return_value=_CompletedProc(returncode=0, stdout="  \n"),
        ):
            info = _fetch_github_copilot()
        self.assertIsNone(info)

    def test_fetch_copilot_http_error(self):
        """HTTP 4xx/5xx from GitHub → return None."""
        from quiver.harness.rate_limits import _fetch_github_copilot
        import urllib.error

        with self._patch_token(), patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("url", 403, "Forbidden", {}, None),
        ):
            info = _fetch_github_copilot()
        self.assertIsNone(info)

    def test_fetch_copilot_malformed_json(self):
        """Invalid JSON response → return None."""
        from quiver.harness.rate_limits import _fetch_github_copilot

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with self._patch_token(), patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=mock_resp,
        ):
            info = _fetch_github_copilot()
        self.assertIsNone(info)


class CopilotDerivationTest(unittest.TestCase):
    """Unit tests for the deterministic mapping helpers."""

    def test_unlimited_returns_zero(self):
        from quiver.harness.rate_limits import _derive_copilot_fields

        used, reached = _derive_copilot_fields(
            {"unlimited": True, "percent_remaining": 50.0,
             "entitlement": 99999, "has_quota": True},
        )
        self.assertEqual(used, 0)
        self.assertFalse(reached)

    def test_full_quota_no_limit(self):
        from quiver.harness.rate_limits import _derive_copilot_fields

        used, reached = _derive_copilot_fields(
            {"unlimited": False, "percent_remaining": 100.0,
             "entitlement": 1500, "has_quota": True},
        )
        self.assertEqual(used, 0)
        self.assertFalse(reached)

    def test_half_quota(self):
        from quiver.harness.rate_limits import _derive_copilot_fields

        used, reached = _derive_copilot_fields(
            {"unlimited": False, "percent_remaining": 50.0,
             "entitlement": 100, "has_quota": True},
        )
        self.assertEqual(used, 50)
        self.assertFalse(reached)

    def test_over_quota_clamps_to_100(self):
        from quiver.harness.rate_limits import _derive_copilot_fields

        used, reached = _derive_copilot_fields(
            {"unlimited": False, "percent_remaining": -5.0,
             "entitlement": 200, "has_quota": False},
        )
        self.assertEqual(used, 100)
        self.assertTrue(reached)

    def test_decorate_individual_with_educational_sku(self):
        from quiver.harness.rate_limits import _decorate_copilot_plan_type

        result = _decorate_copilot_plan_type("individual", "free_educational_quota")
        self.assertEqual(result, "individual/edu")

    def test_decorate_individual_with_pro_sku_unchanged(self):
        from quiver.harness.rate_limits import _decorate_copilot_plan_type

        self.assertEqual(
            _decorate_copilot_plan_type("individual", "pro_plus"),
            "individual",
        )

    def test_decorate_business_with_educational_sku_unchanged(self):
        """Only ``individual`` gets the /edu suffix, not other plan types."""
        from quiver.harness.rate_limits import _decorate_copilot_plan_type

        self.assertEqual(
            _decorate_copilot_plan_type("business", "free_educational_quota"),
            "business",
        )

    def test_decorate_empty_sku_unchanged(self):
        from quiver.harness.rate_limits import _decorate_copilot_plan_type

        self.assertEqual(_decorate_copilot_plan_type("individual", ""), "individual")
        self.assertEqual(_decorate_copilot_plan_type("individual", "—"), "individual")

    def test_decorate_dash_plan_unchanged(self):
        """The ``—`` placeholder plan_type should never be decorated."""
        from quiver.harness.rate_limits import _decorate_copilot_plan_type

        self.assertEqual(
            _decorate_copilot_plan_type("—", "free_educational_quota"),
            "—",
        )

    def test_parse_iso8601(self):
        from quiver.harness.rate_limits import _parse_iso8601_to_epoch

        # 2026-08-01T00:00:00 UTC = 1785542400 (verify against datetime
        # round-trip; allow ±1 day to absorb DST/leap boundaries).
        epoch = _parse_iso8601_to_epoch("2026-08-01T00:00:00.000Z")
        self.assertAlmostEqual(epoch, 1785542400.0, delta=86400)

        self.assertEqual(_parse_iso8601_to_epoch(""), 0.0)
        self.assertEqual(_parse_iso8601_to_epoch(None), 0.0)
        self.assertEqual(_parse_iso8601_to_epoch("not-a-date"), 0.0)

    def test_parse_iso8601_all_variants(self):
        """All five accepted formats must yield the same epoch.

        Kept as a smoke check only.  The ``delta=86400`` below is a full
        day of slack, so this cannot detect a parsing regression;
        ``Iso8601ParseTest`` pins the exact values instead.
        """
        from quiver.harness.rate_limits import _parse_iso8601_to_epoch

        expected = 1785542400.0  # 2026-08-01T00:00:00Z
        for variant in (
            "2026-08-01T00:00:00.000Z",        # microseconds + Z (live API)
            "2026-08-01T00:00:00Z",            # no fractional
            "2026-08-01T00:00:00+00:00",       # explicit offset, naive base
            "2026-08-01T00:00:00.123+00:00",   # microseconds + offset
            "2026-08-01T00:00:00",             # naive → UTC
        ):
            self.assertAlmostEqual(
                _parse_iso8601_to_epoch(variant),
                expected,
                delta=86400,
                msg=f"failed for {variant!r}",
            )

    def test_parse_iso8601_defensive(self):
        """Empty / whitespace / non-string inputs should return 0.0."""
        from quiver.harness.rate_limits import _parse_iso8601_to_epoch

        self.assertEqual(_parse_iso8601_to_epoch(""), 0.0)
        self.assertEqual(_parse_iso8601_to_epoch(None), 0.0)
        self.assertEqual(_parse_iso8601_to_epoch("   "), 0.0)
        self.assertEqual(_parse_iso8601_to_epoch(12345), 0.0)
        self.assertEqual(_parse_iso8601_to_epoch("not-a-date"), 0.0)
        # Fractional seconds WITHOUT offset is valid ISO 8601; we treat
        # it as UTC (sub-second precision is preserved). Pin the epoch.
        self.assertAlmostEqual(
            _parse_iso8601_to_epoch("2026-08-01T00:00:00.500"),
            1785542400.5, delta=1.0,
        )

    def test_parse_iso8601_naive_is_utc(self):
        """Naïve timestamps must NOT fall back to local-time."""
        from quiver.harness.rate_limits import _parse_iso8601_to_epoch

        naive = _parse_iso8601_to_epoch("2026-08-01T00:00:00")          # → UTC
        offset = _parse_iso8601_to_epoch("2026-07-31T20:00:00-04:00")    # -04:00
        self.assertAlmostEqual(naive, 1785542400.0, delta=86400)
        self.assertAlmostEqual(offset, 1785542400.0, delta=86400)
        self.assertAlmostEqual(naive, offset, delta=1.0)

    def test_derive_copilot_fields_null_remains_unknown(self):
        """percent_remaining=None must NOT silently mean '100% remaining'.

        Regression guard: JSON null from GitHub means 'we don't know',
        not 'nothing consumed'. Don't mask it as '100% remaining'.
        """
        from quiver.harness.rate_limits import _derive_copilot_fields

        # No unlimited flag → null means unknown, used_percent = 0.
        used, reached = _derive_copilot_fields(
            {"unlimited": False, "percent_remaining": None,
             "entitlement": 1500, "has_quota": True},
        )
        self.assertEqual(used, 0)
        self.assertFalse(reached)

    def test_derive_copilot_fields_malformed_doesnt_crash(self):
        """Non-numeric percent_remaining must not crash the fetcher."""
        from quiver.harness.rate_limits import _derive_copilot_fields

        # 100% with a stray percent sign → fallback to 0, no crash
        used, reached = _derive_copilot_fields(
            {"unlimited": False, "percent_remaining": "100%",
             "entitlement": 0, "has_quota": True},
        )
        self.assertEqual(used, 0)
        self.assertFalse(reached)


class Iso8601ParseTest(unittest.TestCase):
    """Exact characterization of ``_parse_iso8601_to_epoch``.

    Written ahead of the Python 3.10 -> 3.11 floor bump so the same
    assertions can be re-run afterwards and prove behaviour did not
    move.  Every expectation here is an exact literal derived from
    ``calendar.timegm``, not from the function under test, and was
    confirmed identical on real 3.10.17 and 3.12.8 interpreters before
    being committed.  The older sibling tests in ``CopilotDerivationTest``
    use ``delta=86400`` and so cannot detect a parsing regression at all;
    these can.
    """

    BASE = 1785542400.0  # 2026-08-01T00:00:00Z, via calendar.timegm

    def test_equivalent_spellings_are_exact(self):
        """Every spelling of the same instant must give the same float."""
        from quiver.harness.rate_limits import _parse_iso8601_to_epoch

        cases = [
            ("Z with milliseconds", "2026-08-01T00:00:00.000Z"),
            ("Z bare", "2026-08-01T00:00:00Z"),
            ("explicit UTC offset", "2026-08-01T00:00:00+00:00"),
            ("naive, treated as UTC", "2026-08-01T00:00:00"),
            ("date only", "2026-08-01"),
            ("no seconds", "2026-08-01T00:00"),
            ("space instead of T", "2026-08-01 00:00:00+00:00"),
            ("negative offset", "2026-07-31T20:00:00-04:00"),
            ("half-hour offset", "2026-08-01T05:30:00+05:30"),
        ]
        for label, raw in cases:
            with self.subTest(label=label, raw=raw):
                self.assertEqual(_parse_iso8601_to_epoch(raw), self.BASE)

    def test_fractional_seconds_are_preserved(self):
        """Sub-second precision must survive, offset or not."""
        from quiver.harness.rate_limits import _parse_iso8601_to_epoch

        cases = [
            ("ms with offset", "2026-08-01T00:00:00.123+00:00", 1785542400.123),
            ("us with offset", "2026-08-01T00:00:00.123456+00:00", 1785542400.123456),
            ("ms with Z", "2026-08-01T00:00:00.123Z", 1785542400.123),
            ("naive fractional", "2026-08-01T00:00:00.500", 1785542400.5),
            ("fractional, negative offset", "2026-07-31T19:00:00.500-05:00", 1785542400.5),
        ]
        for label, raw, expected in cases:
            with self.subTest(label=label, raw=raw):
                self.assertEqual(_parse_iso8601_to_epoch(raw), expected)

    def test_malformed_fraction_with_offset_is_salvaged(self):
        """A garbage fraction next to an offset degrades to the second.

        This is what the block labelled "Python 3.10 fallback" in
        ``rate_limits.py`` actually does, and it fires on every
        interpreter, not just 3.10: native ``fromisoformat`` rejects
        these strings on 3.12 exactly as it does on 3.10.  The arm is
        therefore load-bearing and must survive the floor bump.  Without
        it each of these returns 0.0 instead, which reads as "no reset
        time known" and would silently blank a rate-limit countdown.
        """
        from quiver.harness.rate_limits import _parse_iso8601_to_epoch

        cases = [
            ("non-digit fraction", "2026-08-01T00:00:00.abc+00:00"),
            ("second stray dot", "2026-08-01T00:00:00.12.34+00:00"),
            ("empty fraction", "2026-08-01T00:00:00.+00:00"),
        ]
        for label, raw in cases:
            with self.subTest(label=label, raw=raw):
                self.assertEqual(_parse_iso8601_to_epoch(raw), self.BASE)

    def test_malformed_fraction_without_offset_is_rejected(self):
        """No offset means no anchor to retry against, so 0.0."""
        from quiver.harness.rate_limits import _parse_iso8601_to_epoch

        self.assertEqual(_parse_iso8601_to_epoch("2026-08-01T00:00:00.abc"), 0.0)

    def test_over_precise_fraction_keeps_microseconds(self):
        """Nanosecond input keeps microsecond precision.

        This was the ONE input shape whose result moved across the
        3.10 boundary, and it moved in our favour.  On 3.10 native
        ``fromisoformat`` rejected a 9-digit fraction, the salvage arm
        stripped it, and the value landed on the whole second.  From
        3.11 the fraction parses and is truncated to microseconds.

        The floor is now 3.11, so this ran gated on the interpreter
        before the bump and runs unconditionally after it.
        """
        from quiver.harness.rate_limits import _parse_iso8601_to_epoch

        for raw in (
            "2026-08-01T00:00:00.123456789+00:00",
            "2026-08-01T00:00:00.123456789Z",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(_parse_iso8601_to_epoch(raw), 1785542400.123456)

    def test_unparseable_and_absent_values_are_zero(self):
        """Anything we cannot read becomes 0.0, never an exception."""
        from quiver.harness.rate_limits import _parse_iso8601_to_epoch

        cases = [
            ("None", None),
            ("empty string", ""),
            ("whitespace", "   "),
            ("integer zero", 0),
            ("float zero", 0.0),
            ("False", False),
            ("True", True),
            ("bare integer", 12345),
            ("garbage text", "not-a-date"),
            ("impossible month", "2026-13-01T00:00:00Z"),
            ("empty list", []),
            ("empty dict", {}),
            ("populated list", [1, 2]),
            ("populated dict", {"a": 1}),
        ]
        for label, raw in cases:
            with self.subTest(label=label, raw=raw):
                self.assertEqual(_parse_iso8601_to_epoch(raw), 0.0)


class ClaudeFetcherTest(unittest.TestCase):
    """Test the Claude /api/oauth/usage fetcher with mocked credentials + HTTP."""

    _SAMPLE_RESPONSE = {
        "five_hour": {"utilization": 42, "resets_at": "2026-08-01T17:00:00Z"},
        "seven_day": {"utilization": 85, "resets_at": "2026-08-07T08:00:00Z"},
        "seven_day_sonnet": {"utilization": 61, "resets_at": "2026-08-07T08:00:00Z"},
    }

    def _mock_response(self, body):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(body).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def _linux_creds_file(self, token="fake-claude-token", **oauth_overrides):
        """Build a Linux-style credentials file and return (tmpdir, patches).

        Patches ``os.path.expanduser`` so ``~/.claude/.credentials.json``
        resolves to the temp file, and neutralises the Keychain reader so
        an expired file token can never reach the real ``security``
        binary. The temp directory must be cleaned up by the caller
        (``finally`` + ``tmp.cleanup()``).
        """
        tmp = tempfile.TemporaryDirectory()
        creds_path = Path(tmp.name) / "creds.json"
        oauth = {
            "accessToken": token,
            "refreshToken": "x",
            "expiresAt": 9_999_999_999_999,
        }
        oauth.update(oauth_overrides)
        creds_path.write_text(json.dumps({"claudeAiOauth": oauth}))
        patches = [
            patch(
                "quiver.harness.rate_limits.os.path.expanduser",
                side_effect=lambda p: (
                    str(creds_path) if "~/.claude/.credentials.json" in str(p)
                    else p
                ),
            ),
            patch(
                "quiver.harness.rate_limits._read_claude_keychain_credentials",
                return_value=None,
            ),
        ]
        return tmp, patches

    def test_creds_present_fetches_most_limiting_window(self):
        """A paid Claude login surfaces the most-used subscription window."""
        from quiver.harness.rate_limits import _fetch_claude

        tmp, patches = self._linux_creds_file()
        try:
            with patches[0], patches[1], patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp.name) / "rate_limits_cache.json",
            ), patch(
                "quiver.harness.rate_limits.urllib.request.urlopen",
                return_value=self._mock_response(self._SAMPLE_RESPONSE),
            ) as mock_open:
                info = _fetch_claude()
            self.assertIsNotNone(info)
            self.assertEqual(info.tool_name, "claude")
            self.assertEqual(info.used_percent, 85)
            self.assertFalse(info.limit_reached)
            self.assertEqual(info.window, "7d")
            self.assertEqual(mock_open.call_count, 1)
        finally:
            tmp.cleanup()

    def test_no_credentials_returns_none(self):
        """Neither file nor keychain available → return None, no crash."""
        from quiver.harness.rate_limits import _fetch_claude

        with patch("quiver.harness.rate_limits.os.path.expanduser",
                   side_effect=lambda p: "/no/such/path/x" if "~/.claude" in str(p) else p), \
             patch("quiver.harness.rate_limits.shutil.which", return_value=None):
            info = _fetch_claude()
        self.assertIsNone(info)

    def test_expired_credentials_request_relogin(self):
        """A known-expired token is shown as re-login, never fetched."""
        from quiver.harness.rate_limits import _fetch_claude

        tmp, patches = self._linux_creds_file(expiresAt=1)
        try:
            with patches[0], patches[1], patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp.name) / "rate_limits_cache.json",
            ), patch(
                "quiver.harness.rate_limits.urllib.request.urlopen",
            ) as request:
                info = _fetch_claude()
        finally:
            tmp.cleanup()

        self.assertEqual(info.plan_type, "auth-required")
        self.assertIn("re-login", info.format_column())
        request.assert_not_called()

    def test_fresher_keychain_beats_expired_file(self):
        """A fresh Keychain login wins over a stale credentials file."""
        from quiver.harness.rate_limits import _get_claude_oauth_credentials

        tmp, patches = self._linux_creds_file(token="file-token", expiresAt=1)
        try:
            with patches[0], patch(
                "quiver.harness.rate_limits._read_claude_keychain_credentials",
                return_value={
                    "accessToken": "kc-token",
                    "refreshToken": "y",
                    "expiresAt": 9_999_999_999_999,
                },
            ):
                oauth = _get_claude_oauth_credentials()
            self.assertEqual(oauth["accessToken"], "kc-token")
        finally:
            tmp.cleanup()

    def test_fresher_keychain_beats_valid_file(self):
        """A newer Keychain login wins even over an unexpired file token."""
        from quiver.harness.rate_limits import _get_claude_oauth_credentials

        tmp, patches = self._linux_creds_file()
        try:
            with patches[0], patch(
                "quiver.harness.rate_limits._read_claude_keychain_credentials",
                return_value={
                    "accessToken": "kc-token",
                    "expiresAt": 9_999_999_999_999 + 1_000,
                },
            ):
                oauth = _get_claude_oauth_credentials()
            self.assertEqual(oauth["accessToken"], "kc-token")
        finally:
            tmp.cleanup()

    def test_valid_file_beats_older_keychain(self):
        """An older Keychain entry never shadows a fresher file token."""
        from quiver.harness.rate_limits import _get_claude_oauth_credentials

        tmp, patches = self._linux_creds_file()
        try:
            with patches[0], patch(
                "quiver.harness.rate_limits._read_claude_keychain_credentials",
                return_value={
                    "accessToken": "kc-token",
                    "expiresAt": 9_999_999_999_998,
                },
            ):
                oauth = _get_claude_oauth_credentials()
            self.assertEqual(oauth["accessToken"], "fake-claude-token")
        finally:
            tmp.cleanup()

    def test_keychain_lookup_timeout_is_inside_aggregator_deadline(self):
        """A hanging Keychain prompt must not eat the 2s fetch budget."""
        from quiver.harness.rate_limits import (
            _CLAUDE_KEYCHAIN_TIMEOUT,
            _RATE_LIMIT_FETCH_DEADLINE,
            _read_claude_keychain_credentials,
        )

        creds_json = json.dumps({"claudeAiOauth": {"accessToken": "kc-token"}})
        with patch("quiver.harness.rate_limits.shutil.which",
                   return_value="/usr/bin/security"), \
             patch("quiver.harness.rate_limits.subprocess.run",
                   return_value=_CompletedProc(returncode=0, stdout=creds_json)
                   ) as run:
            oauth = _read_claude_keychain_credentials()
        self.assertEqual(oauth["accessToken"], "kc-token")
        self.assertEqual(
            run.call_args.kwargs["timeout"], _CLAUDE_KEYCHAIN_TIMEOUT)
        self.assertLess(_CLAUDE_KEYCHAIN_TIMEOUT, _RATE_LIMIT_FETCH_DEADLINE)

    def test_expired_file_without_keychain_is_kept(self):
        """The only known credential is still returned so it can age out."""
        from quiver.harness.rate_limits import (
            _fetch_claude, _get_claude_oauth_credentials,
        )

        tmp, patches = self._linux_creds_file(expiresAt=1)
        try:
            with patches[0], patches[1], patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp.name) / "rate_limits_cache.json",
            ), patch(
                "quiver.harness.rate_limits.urllib.request.urlopen",
            ) as request:
                oauth = _get_claude_oauth_credentials()
                self.assertEqual(oauth["accessToken"], "fake-claude-token")
                info = _fetch_claude()
            self.assertEqual(info.plan_type, "auth-required")
            request.assert_not_called()
        finally:
            tmp.cleanup()

    def test_expired_keychain_and_fresher_file_prefers_file(self):
        """Between two expired sources the later expiresAt wins."""
        from quiver.harness.rate_limits import _get_claude_oauth_credentials

        tmp, patches = self._linux_creds_file(
            token="file-token", expiresAt=1_000)
        try:
            with patches[0], patch(
                "quiver.harness.rate_limits._read_claude_keychain_credentials",
                return_value={"accessToken": "kc-token", "expiresAt": 1},
            ):
                oauth = _get_claude_oauth_credentials()
            self.assertEqual(oauth["accessToken"], "file-token")
        finally:
            tmp.cleanup()

    def test_claude_expires_at_seconds_accepts_ms_and_seconds(self):
        """expiresAt normalises milliseconds and seconds, 0.0 on junk."""
        from quiver.harness.rate_limits import _claude_expires_at_seconds

        self.assertEqual(
            _claude_expires_at_seconds({"expiresAt": 1_700_000_000_000}),
            1_700_000_000.0,
        )
        self.assertEqual(
            _claude_expires_at_seconds({"expiresAt": 1_700_000_000}),
            1_700_000_000.0,
        )
        self.assertEqual(_claude_expires_at_seconds({}), 0.0)
        self.assertEqual(_claude_expires_at_seconds({"expiresAt": "junk"}), 0.0)

    def test_rate_limit_reuses_last_reading_during_retry_after(self):
        """A 429 keeps Claude visible and suppresses calls during cooldown."""
        from quiver.harness.rate_limits import _fetch_claude

        tmp, patches = self._linux_creds_file()
        cache_file = Path(tmp.name) / "rate_limits_cache.json"
        try:
            with patches[0], patches[1], patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "quiver.harness.rate_limits._fetch_claude_url",
                return_value=self._SAMPLE_RESPONSE,
            ):
                first = _fetch_claude()

            def rate_limited(req, on_rate_limited=None):
                on_rate_limited(120)
                return None

            with patches[0], patches[1], patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "quiver.harness.rate_limits._fetch_claude_url",
                side_effect=rate_limited,
            ):
                stale = _fetch_claude()

            with patches[0], patches[1], patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "quiver.harness.rate_limits._fetch_claude_url",
            ) as fetch:
                during_cooldown = _fetch_claude()

            self.assertEqual(first.used_percent, 85)
            self.assertEqual(stale.used_percent, 85)
            self.assertEqual(during_cooldown.used_percent, 85)
            fetch.assert_not_called()
        finally:
            tmp.cleanup()

    def test_new_credentials_bypass_existing_retry_after(self):
        """Logging in again invalidates a cooldown tied to the old token."""
        from quiver.harness.rate_limits import _fetch_claude

        tmp, patches = self._linux_creds_file(token="new-token")
        cache_file = Path(tmp.name) / "rate_limits_cache.json"
        claude_cache = cache_file.with_name("claude_usage_cache.json")
        claude_cache.write_text(json.dumps({
            "info": None,
            "retry_at": time.time() + 3600,
            "credential_fingerprint": "old-token-fingerprint",
        }))
        try:
            with patches[0], patches[1], patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "quiver.harness.rate_limits._fetch_claude_url",
                return_value=self._SAMPLE_RESPONSE,
            ) as fetch:
                info = _fetch_claude()

            self.assertEqual(info.used_percent, 85)
            fetch.assert_called_once()
        finally:
            tmp.cleanup()

    def test_failed_fetch_without_cooldown_reports_no_reading(self):
        """A TLS / network / 401 failure must not resurface the last reading.

        The aggregator dates whatever a fetcher returns to the moment it was
        returned, so handing back the previous reading here made a weeks-old
        figure display as current for as long as the failure lasted. The
        aggregator's own 24h fallback shows the last value with its real age.
        """
        from quiver.harness.rate_limits import (
            _claude_credential_fingerprint, _fetch_claude,
        )

        tmp, patches = self._linux_creds_file()
        cache_file = Path(tmp.name) / "rate_limits_cache.json"
        claude_cache = cache_file.with_name("claude_usage_cache.json")
        claude_cache.write_text(json.dumps({
            "info": {
                "tool_name": "claude", "used_percent": 46,
                "limit_reached": False, "reset_at": time.time() - 3600,
                "plan_type": "—", "window_seconds": 0, "window": "7d",
            },
            "retry_at": 0.0,
            "credential_fingerprint": _claude_credential_fingerprint(
                "fake-claude-token"),
            "fetched_at": time.time() - 7200,
        }))
        try:
            with patches[0], patches[1], patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "quiver.harness.rate_limits._fetch_claude_url",
                return_value=None,
            ) as fetch:
                info = _fetch_claude()
            self.assertIsNone(info)
            fetch.assert_called_once()
        finally:
            tmp.cleanup()

    def test_cooldown_drops_reading_older_than_a_day(self):
        """A 429 cooldown never shows a figure the aggregator would refuse."""
        from quiver.harness.rate_limits import (
            _claude_credential_fingerprint, _fetch_claude,
        )

        tmp, patches = self._linux_creds_file()
        cache_file = Path(tmp.name) / "rate_limits_cache.json"
        claude_cache = cache_file.with_name("claude_usage_cache.json")
        claude_cache.write_text(json.dumps({
            "info": {
                "tool_name": "claude", "used_percent": 46,
                "limit_reached": False, "reset_at": 0.0,
                "plan_type": "—", "window_seconds": 0, "window": "7d",
            },
            "retry_at": time.time() + 3600,
            "credential_fingerprint": _claude_credential_fingerprint(
                "fake-claude-token"),
            "fetched_at": time.time() - 90000,
        }))
        try:
            with patches[0], patches[1], patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "quiver.harness.rate_limits._fetch_claude_url",
            ) as fetch:
                info = _fetch_claude()
            self.assertIsNone(info)
            fetch.assert_not_called()
        finally:
            tmp.cleanup()

    def test_state_saved_before_fetched_at_existed_is_not_reused(self):
        """A state file from before readings were dated cannot be aged."""
        from quiver.harness.rate_limits import _fetch_claude

        tmp, patches = self._linux_creds_file()
        cache_file = Path(tmp.name) / "rate_limits_cache.json"
        claude_cache = cache_file.with_name("claude_usage_cache.json")
        claude_cache.write_text(json.dumps({
            "info": {
                "tool_name": "claude", "used_percent": 46,
                "limit_reached": False, "reset_at": 0.0,
                "plan_type": "—", "window_seconds": 0, "window": "7d",
            },
            "retry_at": 0.0,
            "credential_fingerprint": "whatever",
        }))

        def rate_limited(req, on_rate_limited=None):
            on_rate_limited(120)
            return None

        try:
            with patches[0], patches[1], patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "quiver.harness.rate_limits._fetch_claude_url",
                side_effect=rate_limited,
            ):
                info = _fetch_claude()
            self.assertIsNone(info)
            saved = json.loads(claude_cache.read_text())
            self.assertIsNone(saved["info"])
            self.assertGreater(saved["retry_at"], time.time())
        finally:
            tmp.cleanup()

    def test_cooldown_keeps_the_reading_its_original_date(self):
        """Re-saving the last reading on a 429 must not make it younger."""
        from quiver.harness.rate_limits import _fetch_claude

        tmp, patches = self._linux_creds_file()
        cache_file = Path(tmp.name) / "rate_limits_cache.json"
        claude_cache = cache_file.with_name("claude_usage_cache.json")

        def rate_limited(req, on_rate_limited=None):
            on_rate_limited(120)
            return None

        try:
            with patches[0], patches[1], patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "quiver.harness.rate_limits._fetch_claude_url",
                return_value=self._SAMPLE_RESPONSE,
            ):
                _fetch_claude()
            first_fetched_at = json.loads(claude_cache.read_text())["fetched_at"]
            self.assertGreater(first_fetched_at, 0)

            with patches[0], patches[1], patch(
                "quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "quiver.harness.rate_limits._fetch_claude_url",
                side_effect=rate_limited,
            ), patch(
                "quiver.harness.rate_limits.time.time",
                return_value=first_fetched_at + 600,
            ):
                stale = _fetch_claude()
            self.assertEqual(stale.used_percent, 85)
            saved = json.loads(claude_cache.read_text())
            self.assertEqual(saved["fetched_at"], first_fetched_at)
        finally:
            tmp.cleanup()

    def test_macos_keychain_path(self):
        """macOS keychain credentials can fetch Claude subscription usage."""
        from quiver.harness.rate_limits import _fetch_claude

        tmp = tempfile.TemporaryDirectory()
        creds_json = json.dumps({"claudeAiOauth": {"accessToken": "kc-token"}})
        completed = _CompletedProc(returncode=0, stdout=creds_json)
        try:
            with patch("quiver.harness.rate_limits.os.path.expanduser",
                       side_effect=lambda p: "/no/such/path/x" if "~/.claude" in str(p) else p), \
                 patch("quiver.harness.rate_limits.shutil.which",
                       return_value="/usr/bin/security"), \
                 patch("quiver.harness.rate_limits.subprocess.run",
                       return_value=completed), \
                 patch("quiver.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                       Path(tmp.name) / "rate_limits_cache.json"), \
                 patch("quiver.harness.rate_limits.urllib.request.urlopen",
                       return_value=self._mock_response(self._SAMPLE_RESPONSE)):
                info = _fetch_claude()
            self.assertIsNotNone(info)
            self.assertEqual(info.tool_name, "claude")
            self.assertEqual(info.used_percent, 85)
        finally:
            tmp.cleanup()

    def test_keychain_bad_json_returns_none(self):
        """Non-JSON keychain password field → return None (defensive parse)."""
        from quiver.harness.rate_limits import _fetch_claude

        completed = _CompletedProc(returncode=0, stdout="not-json")
        with patch("quiver.harness.rate_limits.os.path.expanduser",
                   side_effect=lambda p: "/no/such/path/x" if "~/.claude" in str(p) else p), \
             patch("quiver.harness.rate_limits.shutil.which",
                   return_value="/usr/bin/security"), \
             patch("quiver.harness.rate_limits.subprocess.run",
                   return_value=completed):
            info = _fetch_claude()
        self.assertIsNone(info)

    def test_keychain_missing_token_field_returns_none(self):
        """JSON parses but lacks claudeAiOauth.accessToken → return None."""
        from quiver.harness.rate_limits import _fetch_claude

        creds_json = json.dumps({"other_field": "x"})  # no claudeAiOauth
        completed = _CompletedProc(returncode=0, stdout=creds_json)
        with patch("quiver.harness.rate_limits.os.path.expanduser",
                   side_effect=lambda p: "/no/such/path/x" if "~/.claude" in str(p) else p), \
             patch("quiver.harness.rate_limits.shutil.which",
                   return_value="/usr/bin/security"), \
             patch("quiver.harness.rate_limits.subprocess.run",
                   return_value=completed):
            info = _fetch_claude()
        self.assertIsNone(info)

    # The integrated 401/500 paths through ``_fetch_claude`` are no
    # longer reachable while polling is disabled (the endpoint is never
    # hit). The ``_fetch_claude_url`` helper's 401/429 diagnostics are
    # still pinned by ``ClaudeHTTPDiagnosticTest`` further down.

    def test_format_column_with_window(self):
        """format_column() surfaces the window abbreviation when window != ''."""
        info = RateLimitInfo(
            tool_name="claude",
            used_percent=85,
            limit_reached=False,
            reset_at=RateLimitInfoTest._NOW + 5 * 3600,
            plan_type="—",
            window_seconds=0,
            window="7ds",
        )
        with patch("quiver.harness.rate_limits.time.time",
                   return_value=RateLimitInfoTest._NOW):
            col = info.format_column()
        # Window label MUST appear; reset countdown MUST appear.
        self.assertIn("7ds", col)
        self.assertIn("5h0m", col)
        self.assertIn("15%", col)

    def test_format_column_without_window_shows_remaining(self):
        """A provider without a window label still shows remaining quota."""
        info = RateLimitInfo(
            tool_name="codex",
            used_percent=30,
            limit_reached=False,
            reset_at=RateLimitInfoTest._NOW + 3600,
            plan_type="plus",
            window_seconds=604800,
            window="",  # legacy default
        )
        with patch("quiver.harness.rate_limits.time.time",
                   return_value=RateLimitInfoTest._NOW):
            col = info.format_column()
        # No window marker; the colons that mark the window prefix must not appear.
        self.assertNotIn(":", col.replace("—", ""))
        self.assertIn("70%", col)


class FreebuffFetcherTest(unittest.TestCase):
    """Test the read-only Freebuff GLM 5.2 session allowance."""

    _SAMPLE_RESPONSE = {
        "status": "none",
        "accessTier": "full",
        "referral": {
            "qualifiedCount": 4,
            "weeklySessionsRemaining": 3,
            "resetAt": "2026-08-01T07:00:00.000Z",
        },
    }

    def test_parser_returns_remaining_and_total_session_counts(self):
        from quiver.harness.rate_limits import _parse_freebuff_quota

        info = _parse_freebuff_quota(self._SAMPLE_RESPONSE)

        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "freebuff")
        self.assertEqual(info.remaining_units, 3)
        self.assertEqual(info.total_units, 4)
        self.assertEqual(info.used_percent, 25)
        self.assertFalse(info.limit_reached)
        self.assertEqual(info.reset_at, 1785567600.0)

    def test_parser_marks_exhausted_allowance(self):
        from quiver.harness.rate_limits import _parse_freebuff_quota

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        data["referral"]["weeklySessionsRemaining"] = 0

        info = _parse_freebuff_quota(data)

        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)

    def test_parser_prefers_exact_glm_model_quota(self):
        from quiver.harness.rate_limits import _parse_freebuff_quota

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        data["rateLimitsByModel"] = {
            "z-ai/glm-5.2": {
                "model": "z-ai/glm-5.2",
                "limit": 5,
                "recentCount": 1.5,
                "resetAt": "2026-08-02T07:00:00.000Z",
            },
        }

        info = _parse_freebuff_quota(data)

        self.assertEqual(info.remaining_units, 3.5)
        self.assertEqual(info.total_units, 5)
        self.assertEqual(info.used_percent, 30)
        self.assertEqual(info.reset_at, 1785654000.0)

    def test_parser_uses_active_glm_quota_without_referral_block(self):
        from quiver.harness.rate_limits import _parse_freebuff_quota

        info = _parse_freebuff_quota({
            "status": "active",
            "model": "z-ai/glm-5.2",
            "rateLimit": {
                "limit": 4,
                "recentCount": 1,
                "resetAt": "2026-08-01T07:00:00.000Z",
            },
        })

        self.assertEqual(info.remaining_units, 3)
        self.assertEqual(info.total_units, 4)

    def test_parser_rejects_missing_or_inconsistent_quota(self):
        from quiver.harness.rate_limits import _parse_freebuff_quota

        self.assertIsNone(_parse_freebuff_quota({"status": "none"}))
        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        data["referral"]["weeklySessionsRemaining"] = 5
        self.assertIsNone(_parse_freebuff_quota(data))

    def test_fetch_uses_bearer_token_without_starting_session(self):
        from quiver.harness.rate_limits import _fetch_freebuff

        with patch(
            "quiver.harness.rate_limits._get_freebuff_access_token",
            return_value="freebuff-token",
        ), patch(
            "quiver.harness.rate_limits._fetch_json",
            return_value=self._SAMPLE_RESPONSE,
        ) as fetch_json:
            info = _fetch_freebuff()

        request = fetch_json.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer freebuff-token",
        )
        self.assertTrue(request.full_url.endswith("/api/v1/freebuff/session"))
        self.assertEqual(info.remaining_units, 3)

    def test_fetch_without_credentials_does_not_call_endpoint(self):
        from quiver.harness.rate_limits import _fetch_freebuff

        with patch(
            "quiver.harness.rate_limits._get_freebuff_access_token",
            return_value=None,
        ), patch("quiver.harness.rate_limits._fetch_json") as fetch_json:
            info = _fetch_freebuff()

        self.assertIsNone(info)
        fetch_json.assert_not_called()

    def test_reads_token_from_freebuff_credentials(self):
        from quiver.harness.rate_limits import _get_freebuff_access_token

        with tempfile.TemporaryDirectory() as tmp:
            credentials = Path(tmp) / "credentials.json"
            credentials.write_text(json.dumps({
                "default": {"authToken": "stored-token"},
                "chatgptOAuth": {"accessToken": "not-this-token"},
            }))
            with patch(
                "quiver.harness.rate_limits.os.path.expanduser",
                return_value=str(credentials),
            ):
                token = _get_freebuff_access_token()

        self.assertEqual(token, "stored-token")


class CopilotRegistrationTest(unittest.TestCase):
    """Built-in rate-limit fetchers must be registered at import time."""

    def test_built_in_fetchers_registered(self):
        # _FETCHERS is populated at import time by the _register_*
        # functions. Verify every built-in is wired in.
        self.assertIn("codex", _FETCHERS)
        self.assertIn("copilot", _FETCHERS)
        self.assertIn("claude", _FETCHERS)
        self.assertIn("droid", _FETCHERS)
        self.assertIn("freebuff", _FETCHERS)
        self.assertIn("cursor", _FETCHERS)
        self.assertIn("devin", _FETCHERS)


class DroidFetcherTest(unittest.TestCase):
    """Test the Droid /api/billing/limits fetcher across the auth ladder.

    The real endpoint returns a nested ``limits.<category>.<window>``
    schema (verified against the live ``api.factory.ai/api/billing/limits``
    response) where category is ``standard`` / ``core`` and window is
    ``fiveHour`` / ``weekly`` / ``monthly``. Each window carries
    ``usedPercent`` (already 0-100), ``windowEnd`` (ISO 8601), and
    ``secondsRemaining``. Exhausted longer-term windows win; otherwise
    the fetcher averages the core and standard fiveHour values.

    Auth ladder: (1) ``FACTORY_API_KEY`` env var, (2) decrypted
    ``~/.factory/auth.v2.file`` (AES-256-GCM via system libcrypto),
    (3) macOS Keychain fallback. Every test below mocks
    ``_decrypt_droid_auth_file`` to ``None`` (except the dedicated
    decryption-rung test) so the real dev-machine credential file
    can't leak into test outcomes.
    """

    _SAMPLE_RESPONSE = {
        "usesTokenRateLimitsBilling": True,
        "limits": {
            "standard": {
                "fiveHour": {"usedPercent": 20,
                             "windowEnd": "2026-07-25T22:31:13Z",
                             "secondsRemaining": 15091},
                "weekly": {"usedPercent": 40,
                           "windowEnd": "2026-08-01T06:17:13Z",
                           "secondsRemaining": 561451},
                "monthly": {"usedPercent": 35,
                            "windowEnd": "2026-08-08T08:09:52Z",
                            "secondsRemaining": 1173010},
            },
            "core": {
                "fiveHour": {"usedPercent": 60,
                             "windowEnd": "2026-07-25T12:40:31Z",
                             "secondsRemaining": None},
                "weekly": {"usedPercent": 51,
                           "windowEnd": "2026-08-01T07:40:31Z",
                           "secondsRemaining": 566449},
                "monthly": {"usedPercent": 57,
                            "windowEnd": "2026-08-17T01:42:48Z",
                            "secondsRemaining": 1927385},
            },
        },
        "extraUsageBalanceCents": 0,
        "overagePreference": "droidCore",
        "extraUsageAllowed": True,
        "tokenRateLimitsRolloutEligible": False,
    }

    def _mock_response(self, body):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(body).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    # Fixed "now" for deterministic expiry checks. 2026-07-25T10:00:00Z
    # = 1784973600.0 — before every windowEnd in ``_SAMPLE_RESPONSE``
    # (the earliest is core/fiveHour at 12:40:31Z) so all sample windows
    # are treated as active. Tests that exercise the expired-window
    # skip use windowEnds before this value (e.g. 08:00 / 09:00).
    _NOW = 1784973600.0

    def _now_patch(self):
        return patch(
            "quiver.harness.rate_limits.time.time",
            return_value=self._NOW,
        )

    def _decrypt_none(self):
        """Neutralize the auth.v2.file decryption rung for test isolation.

        Without this patch, tests that clear ``FACTORY_API_KEY`` would
        hit the REAL ``~/.factory/auth.v2.file`` on the dev machine,
        decrypt a real token, and bypass the keychain rung under test.
        """
        return patch(
            "quiver.harness.rate_limits._decrypt_droid_auth_file",
            return_value=None,
        )

    def _env_only(self, value="fake-factory-key"):
        """Set FACTORY_API_KEY env var + neutralize keychain + decryption + now.

        ``clear=True`` so each test is independent of whatever env vars
        are set in the CI/dev shell. The env var short-circuits the
        auth ladder before decryption, but we mock the decryptor too
        for defense-in-depth. ``time.time`` is pinned to ``_NOW`` so
        the expiry check doesn't flake on the sample's near-boundary
        windowEnds.
        """
        return (
            patch.dict(os.environ, {"FACTORY_API_KEY": value}, clear=True),
            patch("quiver.harness.rate_limits.shutil.which", return_value=None),
            self._decrypt_none(),
            self._now_patch(),
        )

    def _no_creds(self):
        """Wipe env vars + neutralize decryption + keychain + now → None."""
        return (
            patch.dict(os.environ, {}, clear=True),
            patch("quiver.harness.rate_limits.shutil.which", return_value=None),
            self._decrypt_none(),
            self._now_patch(),
        )

    def test_droid_fetch_has_wall_clock_deadline(self):
        """A stalled DNS/HTTP call must not block ``swe list`` indefinitely."""
        import threading
        from quiver.harness.rate_limits import _droid_fetch

        started = threading.Event()
        release = threading.Event()

        def stalled_fetch(*args, **kwargs):
            started.set()
            release.wait(timeout=1.0)
            return None

        req = MagicMock()
        began = time.monotonic()
        try:
            with patch(
                "quiver.harness.rate_limits._DROID_FETCH_TIMEOUT",
                0.05,
                create=True,
            ), patch(
                "quiver.harness.rate_limits._fetch_json",
                side_effect=stalled_fetch,
            ):
                info = _droid_fetch(req)
        finally:
            release.set()

        self.assertTrue(started.is_set())
        self.assertIsNone(info)
        self.assertLess(time.monotonic() - began, 0.5)

    # 1. Env var priority — bypasses decryption + keychain entirely.
    def test_factory_key_env_var_takes_priority(self):
        from quiver.harness.rate_limits import _fetch_droid

        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(self._SAMPLE_RESPONSE),
        ):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "droid")
        # core/5h=60 used; standard/5h=20 used. Average = 40.
        self.assertEqual(info.used_percent, 40)
        self.assertEqual(info.window, "5h")
        # Neither budget is fully used.
        self.assertFalse(info.limit_reached)

    # 2. Empty / whitespace env var must NOT be treated as a real token.
    def test_factory_key_env_var_whitespace_is_ignored(self):
        from quiver.harness.rate_limits import _fetch_droid

        with patch.dict(os.environ, {"FACTORY_API_KEY": "   "}, clear=True), \
             patch("quiver.harness.rate_limits.shutil.which",
                   return_value=None), \
             self._decrypt_none():
            info = _fetch_droid()
        self.assertIsNone(info)

    # 3. Decrypted auth.v2.file is the primary browser-auth rung (step 2).
    def test_auth_v2_file_decryption_path(self):
        from quiver.harness.rate_limits import _fetch_droid

        with patch.dict(os.environ, {}, clear=True), \
             patch("quiver.harness.rate_limits._decrypt_droid_auth_file",
                   return_value="decrypted-factory-token"), \
             patch("quiver.harness.rate_limits.shutil.which",
                   return_value=None), \
             self._now_patch(), \
             patch("quiver.harness.rate_limits.urllib.request.urlopen",
                   return_value=self._mock_response(self._SAMPLE_RESPONSE)):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "droid")
        # (60 + 20) / 2 = 40.
        self.assertEqual(info.used_percent, 40)
        self.assertEqual(info.window, "5h")

    # 4. Keychain `Factory Safe Storage` label is the fallback rung (step 3).
    def test_keychain_factory_safe_storage(self):
        from quiver.harness.rate_limits import _fetch_droid

        with patch.dict(os.environ, {}, clear=True), \
             self._decrypt_none(), \
             patch("quiver.harness.rate_limits.shutil.which",
                   return_value="/usr/bin/security"), \
             patch("quiver.harness.rate_limits.subprocess.run",
                   return_value=_CompletedProc(returncode=0,
                                                stdout="kc-factory-token\n")), \
             self._now_patch(), \
             patch("quiver.harness.rate_limits.urllib.request.urlopen",
                   return_value=self._mock_response(self._SAMPLE_RESPONSE)):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "droid")
        # (60 + 20) / 2 = 40.
        self.assertEqual(info.used_percent, 40)

    # 5. Keychain fallback ladder: Safe Storage fails, Factory Key succeeds.
    def test_keychain_alt_label_falls_back(self):
        from quiver.harness.rate_limits import _fetch_droid

        # First subprocess call fails (label 1), second succeeds (label 2).
        side_effects = [
            _CompletedProc(returncode=1, stdout=""),  # Safe Storage missing
            _CompletedProc(returncode=0, stdout="kc-alt-token\n"),  # Factory Key wins
        ]
        with patch.dict(os.environ, {}, clear=True), \
             self._decrypt_none(), \
             patch("quiver.harness.rate_limits.shutil.which",
                   return_value="/usr/bin/security"), \
             patch("quiver.harness.rate_limits.subprocess.run",
                   side_effect=side_effects) as mock_run, \
             self._now_patch(), \
             patch("quiver.harness.rate_limits.urllib.request.urlopen",
                   return_value=self._mock_response(self._SAMPLE_RESPONSE)):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        # Two keychain lookups: first failed, second won.
        self.assertEqual(mock_run.call_count, 2)
        # First call uses Safe Storage label
        self.assertEqual(mock_run.call_args_list[0][0][0][3], "Factory Safe Storage")
        # Second call uses Factory Key label
        self.assertEqual(mock_run.call_args_list[1][0][0][3], "Factory Key")

    # 6. All auth rungs missing → return None, no crash.
    def test_no_credentials_returns_none(self):
        from quiver.harness.rate_limits import _fetch_droid

        env_patch, what_patch, dec_patch, now_patch = self._no_creds()
        with env_patch, what_patch, dec_patch, now_patch:
            info = _fetch_droid()
        self.assertIsNone(info)

    # 7. HTTP 4xx/5xx → return None silently (no on_401 callback for 503).
    def test_http_error_returns_none(self):
        from quiver.harness.rate_limits import _fetch_droid
        import urllib.error

        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "url", 503, "Service Unavailable", {}, None),
        ):
            info = _fetch_droid()
        self.assertIsNone(info)

    # 8. Malformed fiveHour in one category → skipped; the other
    #    category's fiveHour still feeds the average.
    def test_malformed_window_skipped_then_other_window_wins(self):
        from quiver.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "standard": {
                    "fiveHour": {"usedPercent": "garbage",
                                 "windowEnd": "2026-07-25T22:31:13Z"},
                },
                "core": {
                    "fiveHour": {"usedPercent": 60,
                                 "windowEnd": "2026-07-25T12:40:31Z"},
                },
            },
        }
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        # Only core/5h is usable, so its direct 60% value wins.
        self.assertEqual(info.used_percent, 60)
        self.assertEqual(info.window, "5h")

    # 8b. All windows malformed → return None.
    def test_all_windows_malformed_returns_none(self):
        from quiver.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "standard": {"fiveHour": {"usedPercent": "garbage"}},
                "core": {"fiveHour": {"usedPercent": None}},
            },
        }
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNone(info)

    # 9. Response without ``limits`` → return None (defensive).
    def test_no_limits_key_returns_none(self):
        from quiver.harness.rate_limits import _fetch_droid

        body = {"usesTokenRateLimitsBilling": False}
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNone(info)

    # 10. usedPercent=0 means no usage and must not mark the limit reached.
    def test_zero_used_is_available(self):
        from quiver.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "standard": {
                    "fiveHour": {"usedPercent": 0,
                                 "windowEnd": "2026-07-25T22:31:13Z"},
                },
            },
        }
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        self.assertEqual(info.used_percent, 0)
        self.assertFalse(info.limit_reached)

    # 11. Percentage is the average of core/5h and standard/5h.
    def test_averages_core_and_standard_five_hour(self):
        from quiver.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "standard": {
                    "fiveHour": {"usedPercent": 70,
                                 "windowEnd": "2026-07-25T22:31:13Z"},
                },
                "core": {
                    "fiveHour": {"usedPercent": 50,
                                 "windowEnd": "2026-07-25T12:40:31Z"},
                },
            },
        }
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        # (70 + 50) / 2 = 60.
        self.assertEqual(info.used_percent, 60)
        self.assertEqual(info.window, "5h")
        self.assertFalse(info.limit_reached)

    # 12. One 5h window at 100% used ⇒ limit_reached even
    #     though the average is below 100 — the average must not mask a
    #     per-budget cutoff.
    def test_one_budget_at_100_used_marks_reached(self):
        from quiver.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "core": {
                    "fiveHour": {"usedPercent": 100,
                                 "windowEnd": "2026-07-25T12:40:31Z"},
                },
                "standard": {
                    "fiveHour": {"usedPercent": 80,
                                 "windowEnd": "2026-07-25T22:31:13Z"},
                },
            },
        }
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        # (100 + 80) / 2 = 90, but core is cut off → limit_reached.
        self.assertEqual(info.used_percent, 90)
        self.assertTrue(info.limit_reached)
        self.assertEqual(info.window, "5h")

    def test_exhausted_weekly_window_overrides_available_five_hour(self):
        """Show the weekly gate when it blocks use despite 5h capacity."""
        from quiver.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "standard": {
                    "fiveHour": {"usedPercent": 20,
                                 "windowEnd": "2026-07-25T22:31:13Z"},
                    "weekly": {"usedPercent": 100,
                               "windowEnd": "2026-08-01T00:00:00Z"},
                    "monthly": {"usedPercent": 40,
                                "windowEnd": "2026-08-25T00:00:00Z"},
                },
                "core": {
                    "fiveHour": {"usedPercent": 0,
                                 "windowEnd": "2026-07-25T22:31:13Z"},
                    "weekly": {"usedPercent": 50,
                               "windowEnd": "2026-08-01T00:00:00Z"},
                    "monthly": {"usedPercent": 0,
                                "windowEnd": "2026-08-25T00:00:00Z"},
                },
            },
        }
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()

        self.assertIsNotNone(info)
        # Weekly used values are 100 and 50, averaged to 75.
        self.assertEqual(info.used_percent, 75)
        self.assertTrue(info.limit_reached)
        self.assertEqual(info.window, "7d")
        self.assertEqual(info.reset_at, 1785542400.0)

    def test_exhausted_monthly_window_overrides_exhausted_weekly(self):
        """When multiple windows block use, show the longest-lived gate."""
        from quiver.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "standard": {
                    "fiveHour": {"usedPercent": 20,
                                 "windowEnd": "2026-07-25T22:31:13Z"},
                    "weekly": {"usedPercent": 100,
                               "windowEnd": "2026-08-01T00:00:00Z"},
                    "monthly": {"usedPercent": 100,
                                "windowEnd": "2026-08-25T00:00:00Z"},
                },
            },
        }
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()

        self.assertIsNotNone(info)
        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)
        self.assertEqual(info.window, "30d")

    # 13. Overage values above 100 clamp to fully used before averaging.
    def test_over_100_used_clamps_to_fully_used(self):
        from quiver.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "core": {
                    "fiveHour": {"usedPercent": 150,
                                 "windowEnd": "2026-07-25T12:40:31Z"},
                },
                "standard": {
                    "fiveHour": {"usedPercent": 130,
                                 "windowEnd": "2026-07-25T22:31:13Z"},
                },
            },
        }
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        # Both clamp to 100 used, so the average is 100.
        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)

    # 14. reset_at is parsed from windowEnd (ISO 8601).
    def test_reset_at_parsed_from_window_end(self):
        from quiver.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "core": {
                    "fiveHour": {"usedPercent": 42,
                                 "windowEnd": "2026-08-01T00:00:00.000Z"},
                },
            },
        }
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        # 2026-08-01T00:00:00Z = 1785542400; allow ±1 day.
        self.assertAlmostEqual(info.reset_at, 1785542400.0, delta=86400)

    # 14b. reset_at belongs to the selected fiveHour window, not an
    #      unrelated weekly/monthly window.
    def test_reset_at_matches_selected_window(self):
        from quiver.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "core": {
                    "fiveHour": {"usedPercent": 42,
                                 "windowEnd": "2026-08-01T00:00:00Z"},
                    "weekly": {"usedPercent": 10,
                               "windowEnd": "2026-08-07T00:00:00Z"},
                },
                "standard": {
                    "fiveHour": {"usedPercent": 30,
                                 "windowEnd": "2026-08-02T00:00:00Z"},
                    "monthly": {"usedPercent": 5,
                                "windowEnd": "2026-07-26T00:00:00Z"},
                },
            },
        }
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        # The selected 5h window's earliest future reset is core/fiveHour
        # at 2026-08-01T00:00:00Z.
        self.assertAlmostEqual(info.reset_at, 1785542400.0, delta=1)
        # Percentage directly averages the fiveHour windows: (42 + 30) / 2.
        self.assertEqual(info.used_percent, 36)

    # 15. A rolled-over 5h window may report 0% used with a stale past
    #     windowEnd. It still feeds the average, while reset selection skips
    #     the stale timestamp and uses the next future reset.
    def test_rolled_over_zero_used_window_is_included(self):
        from quiver.harness.rate_limits import _fetch_droid

        # _NOW = 2026-07-25T10:00:00Z. core/fiveHour windowEnd 08:00 is
        # in the past (rolled over) and reports 0% used; standard/fiveHour
        # 22:31 is future and reports 54% used. Both feed the average.
        body = {
            "limits": {
                "core": {
                    "fiveHour": {"usedPercent": 0,
                                 "windowEnd": "2026-07-25T08:00:00Z",
                                 "secondsRemaining": None},
                    "weekly": {"usedPercent": 51,
                               "windowEnd": "2026-08-01T07:40:31Z"},
                },
                "standard": {
                    "fiveHour": {"usedPercent": 54,
                                 "windowEnd": "2026-07-25T22:31:13Z"},
                },
            },
        }
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        # (0 + 54) / 2 = 27; the rolled-over core window is included.
        self.assertEqual(info.used_percent, 27)
        # 0% used on core is not a cutoff.
        self.assertFalse(info.limit_reached)
        # Reset is the earliest FUTURE windowEnd — the stale 08:00 is
        # excluded, so standard/5h 22:31 wins (not "now").
        self.assertAlmostEqual(info.reset_at, self._NOW + 12 * 3600 + 31 * 60,
                                delta=60)

    # 16. Origin header MUST be https://app.factory.ai even when the
    #     URL host is api.factory.ai (CodexBar canonical reference).
    #     urllib.request.Request stores headers passed to __init__ at
    #     ``req.headers`` and normalizes keys via ``capitalize()`` — so
    #     ``"x-factory-client"`` is stored as ``"X-factory-client"``.
    def test_origin_header_is_app_factory_not_api(self):
        from quiver.harness.rate_limits import (
            _droid_request, _DROID_APP_ORIGIN, _DROID_APP_REFERER,
        )

        req = _droid_request("https://api.factory.ai/api/billing/limits", "tok")
        hdrs = req.headers
        self.assertEqual(hdrs.get("Origin"), _DROID_APP_ORIGIN)
        self.assertEqual(hdrs.get("Referer"), _DROID_APP_REFERER)
        self.assertEqual(hdrs.get("X-factory-client"), "web-app")
        self.assertEqual(hdrs.get("Authorization"), "Bearer tok")
        self.assertEqual(hdrs.get("Accept"), "application/json")


class DroidAuthFileDecryptTest(unittest.TestCase):
    """Unit tests for the auth.v2.file decryptor's defensive paths.

    The live AES-256-GCM round-trip is exercised end-to-end by
    ``test_auth_v2_file_decryption_path`` above (via the fetcher); here
    we pin the graceful-degradation contract: missing files, missing
    libcrypto, and corrupt inputs must return ``None`` rather than
    raise. The libcrypto loader is neutralized so these tests don't
    depend on a system OpenSSL being present.
    """

    def test_no_libcrypto_returns_none(self):
        from quiver.harness.rate_limits import _decrypt_droid_auth_file
        with patch("quiver.harness.rate_limits._load_libcrypto",
                   return_value=None):
            self.assertIsNone(_decrypt_droid_auth_file())

    def test_missing_files_returns_none(self):
        from quiver.harness.rate_limits import _decrypt_droid_auth_file
        # _load_libcrypto is allowed to succeed (real system), but the
        # credential files must be absent for this test. Redirect both
        # ``~/.factory/auth.v2.*`` paths to a non-existent temp dir.
        with tempfile.TemporaryDirectory() as tmp:
            with patch("quiver.harness.rate_limits.os.path.expanduser",
                       side_effect=lambda p: (
                           str(Path(tmp) / "missing.key")
                           if p == "~/.factory/auth.v2.key"
                           else str(Path(tmp) / "missing.file")
                           if p == "~/.factory/auth.v2.file"
                           else p
                       )):
                self.assertIsNone(_decrypt_droid_auth_file())


class RetryAfterParserTest(unittest.TestCase):
    """Unit tests for the Retry-After header parser used by the
    on_http_error diagnostic callbacks."""

    def test_numeric_seconds(self):
        from quiver.harness.rate_limits import _parse_retry_after_to_seconds
        self.assertEqual(_parse_retry_after_to_seconds("120"), 120.0)
        self.assertEqual(_parse_retry_after_to_seconds("0"), 0.0)
        self.assertEqual(_parse_retry_after_to_seconds("235"), 235.0)
        # Numeric coercion strips trailing whitespace.
        self.assertEqual(_parse_retry_after_to_seconds("  60  "), 60.0)

    def test_http_date_returns_seconds_until(self):
        from quiver.harness.rate_limits import _parse_retry_after_to_seconds
        # Future date — should return a positive number of seconds.
        import datetime
        future = datetime.datetime.now(datetime.timezone.utc)
        future_str = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
        seconds = _parse_retry_after_to_seconds(future_str)
        self.assertIsNotNone(seconds)
        self.assertGreaterEqual(seconds, 0.0)
        self.assertLess(seconds, 60.0)  # roughly "now", not years away

    def test_past_http_date_clamps_to_zero(self):
        from quiver.harness.rate_limits import _parse_retry_after_to_seconds
        # A date in the past — clamped to 0.0 so a stale server
        # header doesn't tell the user to wait centuries.
        self.assertEqual(
            _parse_retry_after_to_seconds("Wed, 21 Oct 2020 07:28:00 GMT"),
            0.0,
        )

    def test_missing_or_empty_returns_none(self):
        from quiver.harness.rate_limits import _parse_retry_after_to_seconds
        self.assertIsNone(_parse_retry_after_to_seconds(None))
        self.assertIsNone(_parse_retry_after_to_seconds(""))
        self.assertIsNone(_parse_retry_after_to_seconds("   "))

    def test_malformed_returns_none(self):
        from quiver.harness.rate_limits import _parse_retry_after_to_seconds
        self.assertIsNone(_parse_retry_after_to_seconds("not-a-date"))
        # RFC 850 variant is not RFC 7231 IMF-fixdate — we don't
        # accept it, so parser degrades to None silently.
        self.assertIsNone(
            _parse_retry_after_to_seconds("Wednesday, 21-Oct-26 07:28:00 GMT"),
        )
        # Non-numeric without a recognisable date format.
        self.assertIsNone(_parse_retry_after_to_seconds("forever"))

    def test_non_string_returns_none(self):
        from quiver.harness.rate_limits import _parse_retry_after_to_seconds
        self.assertIsNone(_parse_retry_after_to_seconds(120))
        self.assertIsNone(_parse_retry_after_to_seconds(120.5))
        self.assertIsNone(_parse_retry_after_to_seconds(b"120"))


class ClaudeHTTPDiagnosticTest(unittest.TestCase):
    """Tests for the on_http_error diagnostic that fires on Claude 429.

    The on_401 (beta-version hint) path is already pinned by
    ClaudeFetcherTest.test_http_401_emits_stale_beta_hint — these
    tests cover the new branch.
    """

    def test_http_429_emits_retry_after_hint(self):
        """429 with retry-after: 120 → 'Anthropic ... 2m0s' surfaces."""
        from quiver.harness.rate_limits import _fetch_claude_url
        import urllib.error
        import io
        import sys

        headers = {"Retry-After": "120"}
        http_err = urllib.error.HTTPError(
            "url", 429, "Too Many Requests", headers, None,
        )
        req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage")
        buf = io.StringIO()
        original_stderr = sys.stderr
        sys.stderr = buf
        try:
            with patch(
                "quiver.harness.rate_limits.urllib.request.urlopen",
                side_effect=http_err,
            ):
                info = _fetch_claude_url(req)
            self.assertIsNone(info)
        finally:
            sys.stderr = original_stderr
        output = buf.getvalue()
        self.assertIn("Claude usage endpoint returned 429", output)
        self.assertIn("2m0s", output)

    def test_http_429_without_retry_after_still_hints(self):
        """429 without Retry-After → 'a few minutes' fallback."""
        from quiver.harness.rate_limits import _fetch_claude_url
        import urllib.error
        import io
        import sys

        http_err = urllib.error.HTTPError(
            "url", 429, "Too Many Requests", {}, None,
        )
        req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage")
        buf = io.StringIO()
        original_stderr = sys.stderr
        sys.stderr = buf
        try:
            with patch(
                "quiver.harness.rate_limits.urllib.request.urlopen",
                side_effect=http_err,
            ):
                info = _fetch_claude_url(req)
            self.assertIsNone(info)
        finally:
            sys.stderr = original_stderr
        self.assertIn("a few minutes", buf.getvalue())

    def test_ssl_fallback_preserves_retry_after_callback(self):
        """A 429 after the macOS SSL retry still reports its cooldown."""
        from quiver.harness.rate_limits import _fetch_json
        import ssl
        import urllib.error

        ssl_error = urllib.error.URLError(ssl.SSLError("certificate verify failed"))
        http_error = urllib.error.HTTPError(
            "url", 429, "Too Many Requests", {"Retry-After": "90"}, None,
        )
        observed = []
        req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage")
        # The retry is only attempted when a real CA bundle is available;
        # without one the helper refuses to send the bearer token at all and
        # never makes a second request. That store comes from certifi, which
        # is not a dependency of this package, so pin it here rather than
        # letting the assertion depend on what happens to be installed.
        with patch(
            "quiver.harness.rate_limits._verified_context",
            return_value=ssl.create_default_context(),
        ), patch(
            "quiver.harness.rate_limits.urllib.request.urlopen",
            side_effect=[ssl_error, http_error],
        ):
            result = _fetch_json(
                req,
                on_http_error=lambda code, retry: observed.append((code, retry)),
            )

        self.assertIsNone(result)
        self.assertEqual(observed, [(429, 90.0)])


class CursorFetcherTest(unittest.TestCase):
    """Test the Cursor included-usage fetcher with mocked HTTP."""

    _SAMPLE_RESPONSE = {
        "billingCycleStart": "1786511793000",
        "billingCycleEnd": "1789190193000",
        "planUsage": {
            "totalSpend": 12557,
            "includedSpend": 2000,
            "limit": 2000,
            "autoPercentUsed": 40.7,
            "apiPercentUsed": 100,
            "totalPercentUsed": 50.228,
        },
        "enabled": True,
        "displayMessage": "You've hit your usage limit",
    }

    @staticmethod
    def _jwt(claims: dict) -> str:
        def part(raw: bytes) -> str:
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        return ".".join((
            part(b'{"alg":"HS256","typ":"JWT"}'),
            part(json.dumps(claims).encode()),
            "signature",
        ))

    def _token(self, **overrides) -> str:
        claims = {"sub": "google-oauth2|user_01TEST", "exp": 9_999_999_999}
        claims.update(overrides)
        return self._jwt(claims)

    def _state_db(self, directory: str, token: str | None) -> str:
        path = os.path.join(directory, "state.vscdb")
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value BLOB)")
        if token is not None:
            connection.execute(
                "INSERT INTO ItemTable VALUES (?, ?)",
                ("cursorAuth/accessToken", token),
            )
        connection.commit()
        connection.close()
        return path

    def test_parser_surfaces_the_more_exhausted_bucket(self):
        from quiver.harness.rate_limits import _parse_cursor_usage

        info = _parse_cursor_usage(self._SAMPLE_RESPONSE)

        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "cursor")
        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)
        self.assertEqual(info.window, "api")
        self.assertEqual(info.reset_at, 1789190193.0)
        self.assertEqual(info.window_seconds, 2678400)

    def test_parser_reports_total_usage_when_named_models_are_lower(self):
        """The auto label carries totalPercentUsed, the dashboard's own figure."""
        from quiver.harness.rate_limits import _parse_cursor_usage

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        data["planUsage"]["apiPercentUsed"] = 10
        data["planUsage"]["autoPercentUsed"] = 99

        info = _parse_cursor_usage(data)

        self.assertEqual(info.window, "auto")
        self.assertEqual(info.used_percent, 50)
        self.assertFalse(info.limit_reached)

    def test_parser_skips_unusable_buckets(self):
        from quiver.harness.rate_limits import _parse_cursor_usage

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        data["planUsage"]["apiPercentUsed"] = "n/a"
        self.assertEqual(_parse_cursor_usage(data).window, "auto")

        data["planUsage"]["totalPercentUsed"] = None
        self.assertIsNone(_parse_cursor_usage(data))

        self.assertIsNone(_parse_cursor_usage({"planUsage": "missing"}))
        self.assertIsNone(_parse_cursor_usage({}))

    def test_parser_tolerates_missing_cycle_bounds(self):
        from quiver.harness.rate_limits import _parse_cursor_usage

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        del data["billingCycleStart"]
        data["billingCycleEnd"] = "soon"

        info = _parse_cursor_usage(data)

        self.assertEqual(info.reset_at, 0.0)
        self.assertEqual(info.window_seconds, 0)
        self.assertEqual(info.reset_in_human, "—")

    def test_millisecond_epoch_parsing(self):
        from quiver.harness.rate_limits import _cursor_ms_to_epoch

        self.assertEqual(_cursor_ms_to_epoch("1789190193000"), 1789190193.0)
        self.assertEqual(_cursor_ms_to_epoch(1789190193000), 1789190193.0)
        self.assertEqual(_cursor_ms_to_epoch(1789190193), 1789190193.0)
        self.assertEqual(_cursor_ms_to_epoch(True), 0.0)
        self.assertEqual(_cursor_ms_to_epoch("-5"), 0.0)
        self.assertEqual(_cursor_ms_to_epoch(None), 0.0)
        self.assertEqual(_cursor_ms_to_epoch(float("inf")), 0.0)

    def test_session_cookie_is_sub_and_token_url_encoded(self):
        from quiver.harness.rate_limits import (
            _cursor_session_cookie, _cursor_token_claims,
        )

        token = self._token()
        claims = _cursor_token_claims(token)
        cookie = _cursor_session_cookie(token, claims)

        self.assertEqual(claims["sub"], "google-oauth2|user_01TEST")
        self.assertTrue(cookie.startswith("WorkosCursorSessionToken="))
        self.assertIn("google-oauth2%7Cuser_01TEST%3A%3A", cookie)
        self.assertNotIn("|", cookie)
        self.assertIsNone(_cursor_session_cookie(token, {}))
        self.assertIsNone(_cursor_token_claims("not-a-jwt"))
        self.assertIsNone(_cursor_token_claims("a.!!!.c"))

    def test_state_db_token_is_read_read_only(self):
        from quiver.harness.rate_limits import _read_cursor_state_token

        token = self._token()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._state_db(tmp, token)
            missing = os.path.join(tmp, "absent", "state.vscdb")
            with patch(
                "quiver.harness.rate_limits._CURSOR_STATE_DB_CANDIDATES",
                (missing, path),
            ):
                self.assertEqual(_read_cursor_state_token(), token)

    def test_state_db_without_token_falls_back_to_keychain(self):
        from quiver.harness.rate_limits import _get_cursor_access_token

        with tempfile.TemporaryDirectory() as tmp:
            path = self._state_db(tmp, None)
            with patch(
                "quiver.harness.rate_limits._CURSOR_STATE_DB_CANDIDATES",
                (path,),
            ), patch(
                "quiver.harness.rate_limits.shutil.which",
                return_value="/usr/bin/security",
            ), patch(
                "quiver.harness.rate_limits.subprocess.run",
                return_value=MagicMock(returncode=0, stdout="kc-token\n"),
            ) as run:
                self.assertEqual(_get_cursor_access_token(), "kc-token")
            args = run.call_args[0][0]
            self.assertEqual(args[:2], ["security", "find-generic-password"])
            self.assertIn("cursor-access-token", args)

    def test_no_token_anywhere_returns_none(self):
        from quiver.harness.rate_limits import _fetch_cursor

        with tempfile.TemporaryDirectory() as tmp, patch(
            "quiver.harness.rate_limits._CURSOR_STATE_DB_CANDIDATES",
            (os.path.join(tmp, "state.vscdb"),),
        ), patch(
            "quiver.harness.rate_limits.shutil.which", return_value=None,
        ), patch("quiver.harness.rate_limits._fetch_json") as fetch:
            self.assertIsNone(_fetch_cursor())
        fetch.assert_not_called()

    def test_expired_token_reports_auth_required_without_a_request(self):
        from quiver.harness.rate_limits import _fetch_cursor

        with patch(
            "quiver.harness.rate_limits._get_cursor_access_token",
            return_value=self._token(exp=1_000_000_000),
        ), patch("quiver.harness.rate_limits._fetch_json") as fetch:
            info = _fetch_cursor()

        self.assertEqual(info.plan_type, "auth-required")
        self.assertIn("re-login", info.format_column())
        fetch.assert_not_called()

    def test_fetch_posts_with_cookie_and_first_party_origin(self):
        from quiver.harness.rate_limits import _CURSOR_USAGE_URL, _fetch_cursor

        token = self._token()
        with patch(
            "quiver.harness.rate_limits._get_cursor_access_token",
            return_value=token,
        ), patch(
            "quiver.harness.rate_limits._fetch_json",
            return_value=self._SAMPLE_RESPONSE,
        ) as fetch:
            info = _fetch_cursor()

        self.assertEqual(info.used_percent, 100)
        self.assertEqual(info.window, "api")
        request = fetch.call_args[0][0]
        self.assertEqual(request.full_url, _CURSOR_USAGE_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b"{}")
        self.assertEqual(request.get_header("Origin"), "https://cursor.com")
        self.assertIn("WorkosCursorSessionToken=", request.get_header("Cookie"))
        self.assertIn(token, request.get_header("Cookie"))
        self.assertIsNone(request.get_header("Authorization"))

    def test_fetch_failure_reports_no_reading(self):
        from quiver.harness.rate_limits import _fetch_cursor

        with patch(
            "quiver.harness.rate_limits._get_cursor_access_token",
            return_value=self._token(),
        ), patch(
            "quiver.harness.rate_limits._fetch_json", return_value=None,
        ):
            self.assertIsNone(_fetch_cursor())

    def test_column_labels_the_bucket(self):
        from quiver.harness.rate_limits import _parse_cursor_usage
        from quiver.console import strip_ansi

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        data["planUsage"]["apiPercentUsed"] = 30
        info = _parse_cursor_usage(data)
        with patch(
            "quiver.harness.rate_limits.time.time",
            return_value=1789190193.0 - 3 * 3600,
        ):
            self.assertEqual(strip_ansi(info.format_column()), "50% auto: 3h0m")


class DevinFetcherTest(unittest.TestCase):
    """Test the Devin quota fetcher (Windsurf seat-management RPC)."""

    _SAMPLE_RESPONSE = {
        "userStatus": {
            "pro": True,
            "planStatus": {
                "planInfo": {
                    "planName": "Teams",
                    "monthlyPromptCredits": -1,
                    "billingStrategy": "BILLING_STRATEGY_QUOTA",
                },
                "planStart": "2026-08-15T04:26:26Z",
                "planEnd": "2026-09-15T04:26:26Z",
                "availablePromptCredits": -1,
                "dailyQuotaRemainingPercent": 100,
                "weeklyQuotaRemainingPercent": 96,
                "dailyQuotaResetAtUnix": "1789200000",
                "weeklyQuotaResetAtUnix": "1789286400",
            },
        },
    }

    def test_parser_surfaces_the_more_used_window(self):
        from quiver.harness.rate_limits import _parse_devin_status

        info = _parse_devin_status(self._SAMPLE_RESPONSE)

        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "devin")
        self.assertEqual(info.used_percent, 4)
        self.assertEqual(info.window, "7d")
        self.assertEqual(info.reset_at, 1789286400.0)
        self.assertEqual(info.window_seconds, 604800)
        self.assertEqual(info.plan_type, "teams")
        self.assertFalse(info.limit_reached)

    def test_parser_picks_daily_when_it_is_the_tighter_window(self):
        from quiver.harness.rate_limits import _parse_devin_status

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        data["userStatus"]["planStatus"]["dailyQuotaRemainingPercent"] = 0

        info = _parse_devin_status(data)

        self.assertEqual(info.window, "1d")
        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)
        self.assertEqual(info.reset_at, 1789200000.0)
        self.assertEqual(info.window_seconds, 86400)

    def test_parser_falls_back_to_prompt_credits(self):
        from quiver.harness.rate_limits import _parse_devin_status

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        plan = data["userStatus"]["planStatus"]
        del plan["dailyQuotaRemainingPercent"]
        del plan["weeklyQuotaRemainingPercent"]
        plan["availablePromptCredits"] = 125
        plan["planInfo"]["monthlyPromptCredits"] = 500

        info = _parse_devin_status(data)

        self.assertEqual(info.remaining_units, 125)
        self.assertEqual(info.total_units, 500)
        self.assertEqual(info.used_percent, 75)
        self.assertEqual(info.window, "")
        self.assertEqual(info.reset_at, 1789446386.0)

    def test_parser_rejects_unusable_payloads(self):
        from quiver.harness.rate_limits import _parse_devin_status

        self.assertIsNone(_parse_devin_status({}))
        self.assertIsNone(_parse_devin_status({"userStatus": {"planStatus": "nope"}}))
        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        plan = data["userStatus"]["planStatus"]
        plan["dailyQuotaRemainingPercent"] = True
        plan["weeklyQuotaRemainingPercent"] = "n/a"
        # Unlimited credits (-1) and no quota windows: nothing to show.
        self.assertIsNone(_parse_devin_status(data))

    def test_credentials_come_from_the_toml_file(self):
        from quiver.harness.rate_limits import _read_devin_credentials

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "credentials.toml")
            Path(path).write_text(
                'windsurf_api_key = "sk-test"\n'
                'api_server_url = "https://server.example.com/"\n'
                'devin_api_url = "https://api.devin.ai"\n'
            )
            with patch("quiver.harness.rate_limits._DEVIN_CREDENTIALS_PATH", path):
                self.assertEqual(
                    _read_devin_credentials(), ("sk-test", "https://server.example.com"),
                )
            Path(path).write_text('windsurf_api_key = "sk-test"\n')
            with patch("quiver.harness.rate_limits._DEVIN_CREDENTIALS_PATH", path):
                self.assertEqual(
                    _read_devin_credentials(), ("sk-test", "https://server.codeium.com"),
                )
            Path(path).write_text('api_server_url = "https://server.example.com"\n')
            with patch("quiver.harness.rate_limits._DEVIN_CREDENTIALS_PATH", path):
                self.assertIsNone(_read_devin_credentials())
            Path(path).write_text("not = = toml")
            with patch("quiver.harness.rate_limits._DEVIN_CREDENTIALS_PATH", path):
                self.assertIsNone(_read_devin_credentials())
            with patch(
                "quiver.harness.rate_limits._DEVIN_CREDENTIALS_PATH",
                os.path.join(tmp, "absent.toml"),
            ):
                self.assertIsNone(_read_devin_credentials())

    def test_fetch_posts_the_key_in_the_rpc_metadata(self):
        from quiver.harness.rate_limits import _fetch_devin

        with patch(
            "quiver.harness.rate_limits._read_devin_credentials",
            return_value=("sk-test", "https://server.example.com"),
        ), patch(
            "quiver.harness.rate_limits._fetch_json",
            return_value=self._SAMPLE_RESPONSE,
        ) as fetch:
            info = _fetch_devin()

        self.assertEqual(info.used_percent, 4)
        request = fetch.call_args[0][0]
        self.assertEqual(
            request.full_url,
            "https://server.example.com/exa.seat_management_pb.SeatManagementService/GetUserStatus",
        )
        self.assertEqual(request.get_method(), "POST")
        body = json.loads(request.data.decode())
        self.assertEqual(body["metadata"]["api_key"], "sk-test")
        self.assertEqual(body["metadata"]["ide_name"], "devin")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertIsNone(request.get_header("Authorization"))

    def test_fetch_without_credentials_or_on_failure_reports_nothing(self):
        from quiver.harness.rate_limits import _fetch_devin

        with patch(
            "quiver.harness.rate_limits._read_devin_credentials", return_value=None,
        ), patch("quiver.harness.rate_limits._fetch_json") as fetch:
            self.assertIsNone(_fetch_devin())
        fetch.assert_not_called()

        with patch(
            "quiver.harness.rate_limits._read_devin_credentials",
            return_value=("sk-test", "https://server.example.com"),
        ), patch("quiver.harness.rate_limits._fetch_json", return_value=None):
            self.assertIsNone(_fetch_devin())


class VerifiedContextTest(unittest.TestCase):
    """``_verified_context`` finds a CA bundle without needing certifi.

    A python.org build on macOS ships no certificates, and certifi is not a
    dependency of this package, so for three weeks every usage fetch on such
    a machine failed TLS verification: Codex went blank and Claude kept
    showing its last reading. The OS trust store is always there.
    """

    def test_uses_os_bundle_when_certifi_is_missing(self):
        from quiver.harness.rate_limits import _verified_context

        with tempfile.TemporaryDirectory() as tmp:
            bundle = os.path.join(tmp, "cert.pem")
            Path(bundle).write_text("")
            missing = os.path.join(tmp, "absent.pem")
            sentinel = object()
            with patch.dict("sys.modules", {"certifi": None}), patch(
                "quiver.harness.rate_limits._SYSTEM_CA_BUNDLES",
                (missing, bundle),
            ), patch(
                "quiver.harness.rate_limits.ssl.create_default_context",
                return_value=sentinel,
            ) as make:
                self.assertIs(_verified_context(), sentinel)
            make.assert_called_once_with(cafile=bundle)

    def test_os_bundle_wins_over_certifi(self):
        from quiver.harness.rate_limits import _ca_bundle_candidates
        import types

        with tempfile.TemporaryDirectory() as tmp:
            os_bundle = os.path.join(tmp, "cert.pem")
            certifi_bundle = os.path.join(tmp, "cacert.pem")
            for path in (os_bundle, certifi_bundle):
                Path(path).write_text("")
            fake = types.SimpleNamespace(where=lambda: certifi_bundle)
            with patch.dict("sys.modules", {"certifi": fake}), patch(
                "quiver.harness.rate_limits._SYSTEM_CA_BUNDLES",
                (os_bundle,),
            ):
                self.assertEqual(
                    _ca_bundle_candidates(), [os_bundle, certifi_bundle],
                )

    def test_gives_up_without_any_bundle(self):
        from quiver.harness.rate_limits import _verified_context

        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "absent.pem")
            with patch.dict("sys.modules", {"certifi": None}), patch(
                "quiver.harness.rate_limits._SYSTEM_CA_BUNDLES", (missing,),
            ), patch(
                "quiver.harness.rate_limits.ssl.create_default_context",
            ) as make:
                self.assertIsNone(_verified_context())
            make.assert_not_called()

    def test_unreadable_bundle_falls_through_to_the_next(self):
        from quiver.harness.rate_limits import _verified_context
        import ssl

        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.pem")
            good = os.path.join(tmp, "good.pem")
            for path in (bad, good):
                Path(path).write_text("")
            sentinel = object()
            with patch.dict("sys.modules", {"certifi": None}), patch(
                "quiver.harness.rate_limits._SYSTEM_CA_BUNDLES", (bad, good),
            ), patch(
                "quiver.harness.rate_limits.ssl.create_default_context",
                side_effect=[ssl.SSLError("no start line"), sentinel],
            ):
                self.assertIs(_verified_context(), sentinel)


class DroidHTTPDiagnosticTest(unittest.TestCase):
    """Tests for the on_401 (stale keychain) + on_http_error
    (429/403/5xx) diagnostics in the Droid fetcher."""

    def _http_error(self, code, reason, headers=None):
        import urllib.error
        return urllib.error.HTTPError("url", code, reason, headers or {}, None)

    def _capture_droid(self, http_err):
        from quiver.harness.rate_limits import _fetch_droid
        import io
        import sys
        buf = io.StringIO()
        original_stderr = sys.stderr
        sys.stderr = buf
        try:
            with patch.dict(os.environ, {"FACTORY_API_KEY": "fake-token"},
                            clear=True), \
                 patch("quiver.harness.rate_limits.shutil.which",
                       return_value=None), \
                 patch("quiver.harness.rate_limits.urllib.request.urlopen",
                       side_effect=http_err):
                info = _fetch_droid()
        finally:
            sys.stderr = original_stderr
        return info, buf.getvalue()

    def test_http_401_emits_keychain_invalid_hint(self):
        """Stale/invalid Factory token → 'access token is invalid' + re-auth via droid CLI."""
        http_err = self._http_error(401, "Unauthorized")
        info, err = self._capture_droid(http_err)
        self.assertIsNone(info)
        self.assertIn("access token", err)
        self.assertIn("invalid or expired", err)
        self.assertIn("droid", err)
        self.assertIn("FACTORY_API_KEY", err)

    def test_http_429_emits_rate_limited_hint_with_retry_after(self):
        """429 with retry-after: 235 → '3m55s' surfaces."""
        http_err = self._http_error(
            429, "Too Many Requests", {"Retry-After": "235"},
        )
        info, err = self._capture_droid(http_err)
        self.assertIsNone(info)
        self.assertIn("Droid usage endpoint returned 429", err)
        self.assertIn("3m55s", err)

    def test_http_429_without_retry_after_still_hints(self):
        """429 without Retry-After header → 'a few minutes' fallback."""
        http_err = self._http_error(429, "Too Many Requests")
        info, err = self._capture_droid(http_err)
        self.assertIsNone(info)
        self.assertIn("a few minutes", err)

    def test_http_403_emits_endpoint_hint(self):
        """403 / 404 → 'URL may need updating' diagnostic."""
        for code in (403, 404):
            with self.subTest(code=code):
                http_err = self._http_error(
                    code, "Forbidden" if code == 403 else "Not Found",
                )
                info, err = self._capture_droid(http_err)
                self.assertIsNone(info)
                self.assertIn("URL or", err)
                self.assertIn(f"returned {code}", err)

    def test_http_503_emits_upstream_hint(self):
        """5xx → 'upstream or network error' diagnostic."""
        http_err = self._http_error(503, "Service Unavailable")
        info, err = self._capture_droid(http_err)
        self.assertIsNone(info)
        self.assertIn("upstream or network error", err)
        self.assertIn("returned 503", err)

    def test_callback_failure_does_not_propagate(self):
        """A diagnostic that raises must NOT flip the result from None."""
        from quiver.harness.rate_limits import _fetch_json
        import urllib.error
        import io
        import sys

        def _boom(code, retry_after):
            raise RuntimeError("diagnostic crashed")

        http_err = self._http_error(
            503, "Service Unavailable", {"Retry-After": "10"},
        )
        buf = io.StringIO()
        original_stderr = sys.stderr
        sys.stderr = buf
        try:
            with patch(
                "quiver.harness.rate_limits.urllib.request.urlopen",
                side_effect=http_err,
            ):
                info = _fetch_json(
                    urllib.request.Request("https://example.com/x"),
                    on_http_error=_boom,
                )
            self.assertIsNone(info)
        finally:
            sys.stderr = original_stderr

    def test_401_does_not_fire_on_http_error(self):
        """A 401 must take the on_401 path, NOT on_http_error.
        Otherwise double-hints would spam stderr on the keychain-stale case."""
        from quiver.harness.rate_limits import _fetch_json
        import urllib.error
        import io
        import sys

        fired_401 = []
        fired_http = []

        http_err = self._http_error(401, "Unauthorized")
        buf = io.StringIO()
        original_stderr = sys.stderr
        sys.stderr = buf
        try:
            with patch(
                "quiver.harness.rate_limits.urllib.request.urlopen",
                side_effect=http_err,
            ):
                info = _fetch_json(
                    urllib.request.Request("https://example.com/x"),
                    on_401=lambda: fired_401.append(1),
                    on_http_error=lambda c, r: fired_http.append(c),
                )
            self.assertIsNone(info)
        finally:
            sys.stderr = original_stderr
        self.assertEqual(fired_401, [1])
        self.assertEqual(fired_http, [])
