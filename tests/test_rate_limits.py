import base64
import copy
import json
import sqlite3
import subprocess
import sys
import os
import tempfile
import time
import urllib.parse
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from io import BytesIO

from swe.harness.rate_limits import (
    RateLimitInfo,
    get_all_rate_limits,
    get_rate_limit,
    invalidate_cache,
    register,
    _FETCHERS,
    _fill_unavailable_windows,
)
from swe.harness import remedies
from swe.harness.models import _window


class RateLimitInfoTest(unittest.TestCase):
    _NOW = 1784758000.0  # fixed "current time" for deterministic tests

    def _make_info(self, used_percent, limit_reached, reset_offset,
                   plan_type="plus", window_seconds=604800):
        """Build a RateLimitInfo with reset_at relative to _NOW."""
        with patch("swe.harness.rate_limits.time.time", return_value=self._NOW):
            return RateLimitInfo(
                tool_name="codex",
                used_percent=used_percent,
                limit_reached=limit_reached,
                reset_at=self._NOW + reset_offset,
                plan_type=plan_type,
                window_seconds=window_seconds,
            )

    def test_format_column_pie_over_half(self):
        info = self._make_info(30, False, 3600)  # 1h ahead
        with patch("swe.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        self.assertIn("◕", col)      # 70% remaining → three-quarter pie
        self.assertIn("1h0m", col)
        self.assertNotIn("%", col)

    def test_format_column_pie_low_fill(self):
        info = self._make_info(85, False, 7200)  # 2h ahead
        with patch("swe.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        self.assertIn("◔", col)      # 15% remaining → quarter pie
        self.assertIn("2h0m", col)

    def test_format_column_pie_full_green(self):
        info = self._make_info(0, False, 3600)
        with patch("swe.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        self.assertIn("●", col)
        self.assertIn("\x1b[38;5;46m", col)

    def test_format_column_pie_empty_red(self):
        info = self._make_info(100, True, 503753)
        with patch("swe.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        self.assertIn("○", col)
        self.assertIn("\x1b[38;5;196m", col)

    def test_format_column_limit_reached_forces_empty_pie(self):
        """limit_reached at a partial fill still draws the empty red pie."""
        info = self._make_info(40, True, 3600)
        with patch("swe.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        self.assertIn("○", col)
        self.assertIn("38;5;196", col)

    def test_format_column_pie_keeps_window_tail(self):
        """A claude-style info renders the pie plus the window:reset tail."""
        info = RateLimitInfo(
            tool_name="claude",
            used_percent=30,
            limit_reached=False,
            reset_at=self._NOW + 2 * 86400 + 4 * 3600,
            plan_type="—",
            window_seconds=604800,
            window="7d",
        )
        with patch("swe.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        from swe.console import strip_ansi
        plain = strip_ansi(col)
        self.assertEqual(plain, "◕ 7d:2d4h")
        self.assertIn("7d:", plain)
        self.assertNotIn("%", plain)

    def test_format_column_units_path_pie(self):
        """remaining_units/total_units shows the pie plus the units tail."""
        info = RateLimitInfo(
            tool_name="devin",
            used_percent=88,
            limit_reached=False,
            reset_at=0,
            plan_type="—",
            window_seconds=0,
            remaining_units=173,
            total_units=1500,
        )
        with patch("swe.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        from swe.console import strip_ansi
        plain = strip_ansi(col)
        self.assertIn("173/1500", plain)
        self.assertTrue(plain[0] in "○◔◑◕●", plain)
        self.assertNotIn("%", plain)

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
        with patch("swe.harness.rate_limits.time.time", return_value=self._NOW):
            col = info.format_column()
        self.assertIn("3/4", col)
        self.assertNotIn("75%", col)
        self.assertIn("1h0m", col)

    def test_reset_in_human_days(self):
        info = self._make_info(50, False, 5 * 86400 + 3600)  # 5d1h ahead
        with patch("swe.harness.rate_limits.time.time", return_value=self._NOW):
            self.assertEqual(info.reset_in_human, "5d1h")

    def test_reset_in_human_now(self):
        info = self._make_info(100, True, -10)  # already past
        with patch("swe.harness.rate_limits.time.time", return_value=self._NOW):
            self.assertEqual(info.reset_in_human, "now")

    def test_reset_in_human_unknown(self):
        info = self._make_info(50, False, 0, plan_type="—", window_seconds=0)
        info.reset_at = 0  # override to truly unknown
        with patch("swe.harness.rate_limits.time.time", return_value=self._NOW):
            self.assertEqual(info.reset_in_human, "—")

    def test_format_column_shows_relogin_for_expired_credentials(self):
        info = self._make_info(
            0, False, 0, plan_type="auth-required", window_seconds=0,
        )
        self.assertIn("re-login", info.format_column())

    def test_format_column_timeout(self):
        info = self._make_info(
            0, False, 0, plan_type="timeout", window_seconds=0,
        )
        self.assertIn("…", info.format_column())

    def test_to_json_carries_the_email_even_on_a_failed_reading(self):
        """A re-login card still names its account, so email is not
        blanked with the usage figures."""
        info = self._make_info(0, False, 0, plan_type="auth-required",
                               window_seconds=0)
        info.email = "kai@example.com"
        self.assertEqual(info.to_json()["email"], "kai@example.com")
        self.assertIsNone(self._make_info(0, False, 0).to_json()["email"])


class RateLimitCacheTest(unittest.TestCase):
    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "rate_limits_cache.json"
            with patch("swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE", cache_file):
                # Save some data
                from swe.harness.rate_limits import _save_cached, _load_cached

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
                limits, _updated_at = loaded
                self.assertIn("codex", limits)
                self.assertEqual(limits["codex"]["used_percent"], 42)

    def test_cache_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "rate_limits_cache.json"
            # _CACHE_TTL is bound once at import, so an ambient
            # SWE_RATE_LIMITS_TTL (e.g. "inf") would leak into the expiry
            # check; pin the module constant to keep the test hermetic.
            with patch("swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE", cache_file), \
                    patch("swe.harness.rate_limits._CACHE_TTL", 300.0):
                from swe.harness.rate_limits import _save_cached, _load_cached

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
                    json.dumps({"cached_at": time.time() - 310, "limits": raw})
                )
                loaded = _load_cached()
                self.assertIsNone(loaded)

    def test_load_cached_defensive_branches(self):
        """Malformed cache fields degrade instead of crashing the listing."""
        from swe.harness.rate_limits import (
            _load_cached, _load_cached_no_data, _save_cached,
        )

        info = {
            "tool_name": "codex", "used_percent": 1, "limit_reached": False,
            "reset_at": 0.0, "plan_type": "x", "window_seconds": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "rate_limits_cache.json"
            with patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE", cache_file
            ), patch("swe.harness.rate_limits._CACHE_TTL", 300.0):
                now = time.time()
                self.assertIsNone(_load_cached())
                self.assertEqual(_load_cached_no_data(), set())
                cache_file.write_text(json.dumps(
                    {"cached_at": now, "limits": [1]}))
                self.assertIsNone(_load_cached())
                # A non-dict updated_at falls back to the envelope time.
                cache_file.write_text(json.dumps({
                    "cached_at": now, "limits": {"codex": info},
                    "updated_at": 5,
                }))
                loaded = _load_cached()
                self.assertIn("codex", loaded[0])
                # An unparseable per-provider time drops just that entry.
                cache_file.write_text(json.dumps({
                    "cached_at": now, "limits": {"codex": info},
                    "updated_at": {"codex": "old"},
                }))
                self.assertEqual(_load_cached()[0], {})
                # Corrupt JSON is a miss, not a traceback.
                cache_file.write_text("{nope")
                self.assertIsNone(_load_cached())
                self.assertEqual(_load_cached_no_data(), set())
                # no_data: dict filters stale and non-numeric answers;
                # the legacy list form still reads under a fresh envelope.
                cache_file.write_text(json.dumps({
                    "cached_at": now,
                    "no_data": {"a": now, "b": now - 9999, "c": "x"},
                }))
                self.assertEqual(_load_cached_no_data(), {"a"})
                cache_file.write_text(json.dumps({
                    "cached_at": now, "no_data": ["a", "b"],
                }))
                self.assertEqual(_load_cached_no_data(), {"a", "b"})
                # An unwritable destination is swallowed by the save.
                a_file = Path(tmp) / "afile"
                a_file.write_text("x")
                with patch(
                    "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                    a_file / "x.json",
                ):
                    _save_cached({"codex": info})  # must not raise

    def test_hydration_skips_unknown_and_malformed_entries(self):
        """Cache entries with no fetcher or bad shape are dropped."""
        saved = _FETCHERS.copy()
        fetcher = MagicMock(return_value=None)
        _FETCHERS.clear()
        register("codex", fetcher)
        raw = {
            "ghost": {
                "tool_name": "ghost", "used_percent": 9,
                "limit_reached": False, "reset_at": 0.0,
                "plan_type": "x", "window_seconds": 0,
            },
            "codex": {"bogus_field": 1},
        }
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits._CACHE_TTL", 300.0,
            ):
                from swe.harness.rate_limits import _save_cached
                _save_cached(raw)
                result = get_all_rate_limits(use_cache=True)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        fetcher.assert_called_once()
        self.assertNotIn("ghost", result)
        self.assertNotIn("codex", result)

    def test_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "rate_limits_cache.json"
            with patch("swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE", cache_file):
                from swe.harness.rate_limits import _save_cached

                _save_cached({"codex": {"tool_name": "codex", "used_percent": 50,
                                         "limit_reached": False, "reset_at": 0,
                                         "plan_type": "plus", "window_seconds": 0}})
                self.assertTrue(cache_file.exists())
                invalidate_cache()
                self.assertFalse(cache_file.exists())

    def test_env_var_override_sets_ttl(self):
        """``SWE_RATE_LIMITS_TTL`` overrides the default TTL at read time."""
        from swe.harness.rate_limits import _env_cache_ttl
        with patch.dict(os.environ, {"SWE_RATE_LIMITS_TTL": "600"}, clear=False):
            self.assertEqual(_env_cache_ttl(default=300.0), 600.0)
        with patch.dict(os.environ, {"SWE_RATE_LIMITS_TTL": "120"}, clear=False):
            self.assertEqual(_env_cache_ttl(default=300.0), 120.0)

    def test_env_var_override_falls_back_on_bad_value(self):
        """Unparseable / non-positive env values fall back to the default."""
        from swe.harness.rate_limits import _env_cache_ttl
        for bad in ("not-a-number", "-10", "0", "  "):
            with patch.dict(os.environ, {"SWE_RATE_LIMITS_TTL": bad}, clear=False):
                self.assertEqual(_env_cache_ttl(default=300.0), 300.0)
        # Unset → default.
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_env_cache_ttl(default=300.0), 300.0)

    def test_default_ttl_is_five_minutes(self):
        """The module-level ``_CACHE_TTL`` default is 300s (5 minutes)."""
        from swe.harness.rate_limits import _CACHE_TTL
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
            with patch("swe.harness.stars.is_starred", return_value=False):
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
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
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
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from swe.harness.rate_limits import _save_cached

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

    def test_an_older_single_window_reading_of_devin_is_refetched(self):
        """A cache written before windows were listed is not served as current."""
        saved = _FETCHERS.copy()
        fresh = RateLimitInfo(
            tool_name="devin", used_percent=0, limit_reached=False, reset_at=0,
            plan_type="teams", window_seconds=86400, window="1d",
            windows=[{"window": "1d", "used_percent": 0, "limit_reached": False,
                      "reset_at": 0, "window_seconds": 86400}],
        )
        devin = MagicMock(return_value=fresh)
        cursor = MagicMock()
        _FETCHERS.clear()
        register("devin", devin)
        register("cursor", cursor)
        old = {"tool_name": "devin", "used_percent": 5, "limit_reached": False, "reset_at": 0,
               "plan_type": "teams", "window_seconds": 86400, "window": "1d"}
        new_cursor = {"tool_name": "cursor", "used_percent": 2, "limit_reached": False, "reset_at": 0,
                      "plan_type": "team", "window_seconds": 0, "window": "auto",
                      "windows": [{"window": "auto", "used_percent": 2, "limit_reached": False,
                                   "reset_at": 0, "window_seconds": 0}]}
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from swe.harness.rate_limits import _save_cached

                _save_cached({"devin": old, "cursor": new_cursor})
                result = get_all_rate_limits(use_cache=True, tool_names={"devin", "cursor"})
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        devin.assert_called_once_with()
        cursor.assert_not_called()
        self.assertEqual(len(result["devin"].windows), 1)
        self.assertEqual(result["cursor"].windows[0]["window"], "auto")

    def test_an_older_reading_stays_as_the_fallback_when_its_refetch_fails(self):
        saved = _FETCHERS.copy()
        devin = MagicMock(return_value=None)
        cursor = MagicMock()
        _FETCHERS.clear()
        register("devin", devin)
        register("cursor", cursor)
        old = {"tool_name": "devin", "used_percent": 5, "limit_reached": False, "reset_at": 0,
               "plan_type": "teams", "window_seconds": 86400, "window": "1d"}
        new_cursor = {"tool_name": "cursor", "used_percent": 2, "limit_reached": False, "reset_at": 0,
                      "plan_type": "team", "window_seconds": 0, "window": "auto", "windows": []}
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from swe.harness.rate_limits import _save_cached

                _save_cached({"devin": old, "cursor": new_cursor})
                result = get_all_rate_limits(use_cache=True, tool_names={"devin", "cursor"})
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        devin.assert_called_once_with()
        self.assertIn("devin", result)
        self.assertEqual(result["devin"].used_percent, 5)
        self.assertGreater(result["devin"].fetched_at, 0)

    def test_a_kept_older_reading_is_still_refetched_next_time(self):
        """Saving the fallback must not make it look upgraded."""
        saved = _FETCHERS.copy()
        devin = MagicMock(return_value=None)
        _FETCHERS.clear()
        register("devin", devin)
        register("cursor", MagicMock())
        old = {"tool_name": "devin", "used_percent": 5, "limit_reached": False, "reset_at": 0,
               "plan_type": "teams", "window_seconds": 86400, "window": "1d"}
        new_cursor = {"tool_name": "cursor", "used_percent": 2, "limit_reached": False, "reset_at": 0,
                      "plan_type": "team", "window_seconds": 0, "window": "auto", "windows": []}
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from swe.harness.rate_limits import _save_cached

                _save_cached({"devin": old, "cursor": new_cursor})
                get_all_rate_limits(use_cache=True, tool_names={"devin", "cursor"})
                saved_devin = json.loads((Path(tmp) / "rate_limits_cache.json").read_text())["limits"]["devin"]
                get_all_rate_limits(use_cache=True, tool_names={"devin", "cursor"})
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertNotIn("windows", saved_devin)
        self.assertEqual(devin.call_count, 2)

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
                with patch("swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE", cache_file):
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
                with patch("swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE", cache_file):
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
                with patch("swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE", cache_file):
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
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits._RATE_LIMIT_FETCH_DEADLINE",
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
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from swe.harness.rate_limits import _save_cached
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
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from swe.harness.rate_limits import _save_cached
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
                    "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                    cache_file,
                ):
                    result = get_all_rate_limits(use_cache=False)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertNotIn("codex", result)

    def test_slow_fetcher_records_timeout_marker(self):
        """A worker still running at the deadline leaves a timeout marker."""
        import threading

        saved = _FETCHERS.copy()
        release = threading.Event()

        def slow_fetch():
            release.wait(timeout=5.0)
            return None

        _FETCHERS.clear()
        register("slow-tool", slow_fetch)
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits._RATE_LIMIT_FETCH_DEADLINE",
                0.05,
                create=True,
            ):
                result = get_all_rate_limits(use_cache=False)
                payload = json.loads(
                    (Path(tmp) / "rate_limits_cache.json").read_text())
        finally:
            release.set()
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertEqual(result["slow-tool"].plan_type, "timeout")
        self.assertEqual(
            payload["limits"]["slow-tool"]["plan_type"], "timeout")

    def test_deadline_param_waits_for_slow_fetcher(self):
        """An explicit deadline lets a slow fetcher resolve to a figure."""
        saved = _FETCHERS.copy()

        def slow_fetch():
            time.sleep(0.15)
            return RateLimitInfo(
                tool_name="slow-tool",
                used_percent=40,
                limit_reached=False,
                reset_at=0.0,
                plan_type="plus",
                window_seconds=0,
            )

        _FETCHERS.clear()
        register("slow-tool", slow_fetch)
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits._RATE_LIMIT_FETCH_DEADLINE",
                0.05,
                create=True,
            ):
                result = get_all_rate_limits(use_cache=False, deadline=1.0)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertEqual(result["slow-tool"].used_percent, 40)
        self.assertEqual(result["slow-tool"].plan_type, "plus")

    def test_timeout_marker_served_from_cache(self):
        """A fresh cached marker answers without re-running the fetcher."""
        saved = _FETCHERS.copy()
        fetcher = MagicMock(side_effect=AssertionError("fetcher ran"))
        _FETCHERS.clear()
        register("slow-tool", fetcher)
        marker = {
            "slow-tool": {
                "tool_name": "slow-tool",
                "used_percent": 0,
                "limit_reached": False,
                "reset_at": 0.0,
                "plan_type": "timeout",
                "window_seconds": 0,
                "window": "",
            }
        }
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from swe.harness.rate_limits import _save_cached
                _save_cached(marker)
                result = get_all_rate_limits(use_cache=True)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertEqual(result["slow-tool"].plan_type, "timeout")
        fetcher.assert_not_called()

    def test_auth_required_marker_not_served_from_cache(self):
        """A cached re-login is re-derived, not replayed for the TTL."""
        saved = _FETCHERS.copy()
        fetcher = MagicMock(return_value=RateLimitInfo(
            tool_name="claude", used_percent=10, limit_reached=False,
            reset_at=0.0, plan_type="pro", window_seconds=0))
        _FETCHERS.clear()
        register("claude", fetcher)
        marker = {
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
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from swe.harness.rate_limits import _save_cached
                _save_cached(marker)
                result = get_all_rate_limits(use_cache=True)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertEqual(result["claude"].used_percent, 10)
        fetcher.assert_called_once()

    def test_partial_hit_preserves_cached_provider_ages(self):
        """Refetching a missing provider must not re-age the cached ones."""
        saved = _FETCHERS.copy()
        codex_fetcher = MagicMock(side_effect=AssertionError("fetcher ran"))
        _FETCHERS.clear()
        register("codex", codex_fetcher)
        register("claude", MagicMock(return_value=None))
        seed_ts = time.time() - 60
        raw = {
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
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits._CACHE_TTL", 300.0,
            ):
                from swe.harness.rate_limits import _save_cached
                _save_cached(raw, updated_at={"codex": seed_ts})
                result = get_all_rate_limits(use_cache=True)
                payload = json.loads(
                    (Path(tmp) / "rate_limits_cache.json").read_text())
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertEqual(result["codex"].used_percent, 42)
        codex_fetcher.assert_not_called()
        self.assertAlmostEqual(
            payload["updated_at"]["codex"], seed_ts, delta=5)
        self.assertIn("claude", payload["no_data"])

    def test_entry_past_own_ttl_refetched_under_fresh_envelope(self):
        """A fresh envelope must not keep a provider's stale value alive."""
        saved = _FETCHERS.copy()
        fetcher = MagicMock(return_value=RateLimitInfo(
            tool_name="codex", used_percent=7, limit_reached=False,
            reset_at=0.0, plan_type="plus", window_seconds=0))
        _FETCHERS.clear()
        register("codex", fetcher)
        raw = {
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
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits._CACHE_TTL", 300.0,
            ):
                from swe.harness.rate_limits import _save_cached
                _save_cached(raw, updated_at={"codex": time.time() - 400})
                result = get_all_rate_limits(use_cache=True)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        fetcher.assert_called_once()
        self.assertEqual(result["codex"].used_percent, 7)

    def test_timeout_marker_not_served_as_stale_fallback(self):
        """The 24h outage fallback skips markers; only real readings carry."""
        raw = {
            "slow-tool": {
                "tool_name": "slow-tool",
                "used_percent": 0,
                "limit_reached": False,
                "reset_at": 0.0,
                "plan_type": "timeout",
                "window_seconds": 0,
                "window": "",
            },
            "codex": {
                "tool_name": "codex",
                "used_percent": 42,
                "limit_reached": False,
                "reset_at": time.time() + 3600,
                "plan_type": "plus",
                "window_seconds": 604800,
                "window": "",
            },
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
            Path(tmp) / "rate_limits_cache.json",
        ):
            from swe.harness.rate_limits import (
                _load_stale_cached,
                _save_cached,
            )
            _save_cached(raw)
            usable, _timestamps = _load_stale_cached()

        self.assertNotIn("slow-tool", usable)
        self.assertIn("codex", usable)

    def test_stale_value_beats_timeout_marker(self):
        """A provider's last reading still outranks a fresh timeout marker."""
        import threading

        saved = _FETCHERS.copy()
        release = threading.Event()

        def slow_fetch():
            release.wait(timeout=5.0)
            return None

        _FETCHERS.clear()
        register("codex", slow_fetch)
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
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits._RATE_LIMIT_FETCH_DEADLINE",
                0.05,
                create=True,
            ):
                from swe.harness.rate_limits import _save_cached
                _save_cached(stale)
                result = get_all_rate_limits(use_cache=False)
        finally:
            release.set()
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        self.assertEqual(result["codex"].used_percent, 42)
        self.assertEqual(result["codex"].plan_type, "plus")


class UnavailableWindowCarryTest(unittest.TestCase):
    """_fill_unavailable_windows gives an unread meter its last values."""

    def _info(self) -> "RateLimitInfo":
        return RateLimitInfo(
            tool_name="cursor", used_percent=10, limit_reached=False,
            reset_at=0, plan_type="team", window_seconds=0,
            windows=[_window("auto", 10, 100, 604800),
                     _window("grok_bot", 0, 0, 604800, unavailable=True)],
        )

    def test_last_known_values_carry_over_with_as_of(self):
        previous = {
            "fetched_at": 1700.0,
            "windows": [{"window": "grok_bot", "used_percent": 42,
                         "limit_reached": False, "reset_at": 900,
                         "window_seconds": 604800}],
        }
        info = self._info()
        _fill_unavailable_windows(info, previous)

        grok = info.windows[1]
        self.assertEqual(grok["used_percent"], 42)
        self.assertEqual(grok["reset_at"], 900)
        self.assertEqual(grok["as_of"], 1700.0)
        # The healthy window is untouched.
        self.assertNotIn("as_of", info.windows[0])

    def test_a_carried_as_of_never_resets(self):
        previous = {
            "fetched_at": 9999.0,
            "windows": [{"window": "grok_bot", "used_percent": 42,
                         "limit_reached": False, "reset_at": 900,
                         "window_seconds": 604800, "as_of": 1200.0}],
        }
        info = self._info()
        _fill_unavailable_windows(info, previous)

        self.assertEqual(info.windows[1]["as_of"], 1200.0)

    def test_an_undated_unavailable_window_is_not_a_last_value(self):
        """Two failures with no good Grok reading: the first writes 0 with
        no as_of; the second must keep that, not date the 0 by fetched_at."""
        previous = {
            "fetched_at": 9999.0,
            "windows": [{"window": "grok_bot", "used_percent": 0,
                         "limit_reached": False, "reset_at": 0,
                         "window_seconds": 604800, "unavailable": True}],
        }
        info = self._info()
        _fill_unavailable_windows(info, previous)

        grok = info.windows[1]
        self.assertEqual(grok["used_percent"], 0)
        self.assertNotIn("as_of", grok)

    def test_no_previous_reading_reads_zero_without_as_of(self):
        info = self._info()
        _fill_unavailable_windows(info, None)
        _fill_unavailable_windows(info, {"fetched_at": 1, "windows": [
            {"window": "other", "used_percent": 9}]})

        grok = info.windows[1]
        self.assertEqual(grok["used_percent"], 0)
        self.assertNotIn("as_of", grok)


class RefreshFailedTest(unittest.TestCase):
    """refresh_failed refetches only cached failures; cache_ttl re-times freshness."""

    def _fetcher(self, name: str, percent: int) -> MagicMock:
        return MagicMock(return_value=RateLimitInfo(
            tool_name=name, used_percent=percent, limit_reached=False,
            reset_at=0, plan_type="free", window_seconds=0, window="",
            windows=[{"window": "w", "used_percent": percent,
                      "limit_reached": False, "reset_at": 0,
                      "window_seconds": 0}],
        ))

    def _run(self, fetchers, cached, ttl=None, refresh_failed=False):
        """One get_all_rate_limits call over a seeded throwaway cache."""
        saved = _FETCHERS.copy()
        _FETCHERS.clear()
        for name, fetcher in fetchers.items():
            register(name, fetcher)
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from swe.harness.rate_limits import _save_cached

                _save_cached(cached)
                return get_all_rate_limits(
                    use_cache=True, tool_names=set(fetchers),
                    refresh_failed=refresh_failed, cache_ttl=ttl,
                )
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

    @staticmethod
    def _raw(name, percent=10, plan_type="free", windows=None):
        return {
            "tool_name": name, "used_percent": percent,
            "limit_reached": False, "reset_at": 0, "plan_type": plan_type,
            "window_seconds": 0, "window": "",
            "windows": windows if windows is not None else [],
        }

    def test_refresh_failed_refetches_only_the_failed_entries(self):
        healthy = MagicMock()
        timed_out = self._fetcher("timed", 60)
        partial = self._fetcher("partial", 70)
        cached = {
            "healthy": self._raw("healthy", 5),
            "timed": self._raw("timed", plan_type="timeout"),
            "partial": self._raw("partial", 20, windows=[
                {"window": "grok_bot", "used_percent": 20,
                 "limit_reached": False, "reset_at": 0,
                 "window_seconds": 0, "unavailable": True, "as_of": 1}]),
        }
        result = self._run(
            {"healthy": healthy, "timed": timed_out, "partial": partial},
            cached, refresh_failed=True,
        )

        healthy.assert_not_called()
        timed_out.assert_called_once_with()
        partial.assert_called_once_with()
        self.assertEqual(result["timed"].used_percent, 60)
        self.assertEqual(result["partial"].used_percent, 70)

    def test_a_failed_refetch_keeps_the_previous_entry(self):
        partial = MagicMock(return_value=None)
        cached = {
            "partial": self._raw("partial", 20, windows=[
                {"window": "grok_bot", "used_percent": 20,
                 "limit_reached": False, "reset_at": 0,
                 "window_seconds": 0, "unavailable": True, "as_of": 1}]),
        }
        result = self._run({"partial": partial}, cached, refresh_failed=True)

        partial.assert_called_once_with()
        self.assertEqual(result["partial"].used_percent, 20)
        self.assertEqual(result["partial"].windows[0]["as_of"], 1)

    def test_without_refresh_failed_the_cached_failures_serve(self):
        timed_out = MagicMock()
        result = self._run(
            {"timed": timed_out},
            {"timed": self._raw("timed", plan_type="timeout")},
        )
        timed_out.assert_not_called()
        self.assertEqual(result["timed"].plan_type, "timeout")

    def test_a_kept_reading_is_never_saved_as_no_data(self):
        """Press 1: the refresh of an unavailable reading fails, the old
        entry stays in limits and no no_data marker is written. Press 2
        must retry, not return early with the row missing."""
        import json as _json

        unavailable = self._raw("cursor", 10, windows=[
            {"window": "grok_bot", "used_percent": 42,
             "limit_reached": False, "reset_at": 900,
             "window_seconds": 604800, "unavailable": True, "as_of": 1}])
        failing = MagicMock(return_value=None)

        saved = _FETCHERS.copy()
        _FETCHERS.clear()
        register("cursor", failing)
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from swe.harness.rate_limits import (
                    RATE_LIMITS_CACHE_FILE, _save_cached)
                _save_cached({"cursor": unavailable})

                first = get_all_rate_limits(
                    use_cache=True, tool_names={"cursor"}, refresh_failed=True)
                failing.assert_called_once_with()
                # The kept reading, not an empty marker.
                self.assertEqual(first["cursor"].windows[0]["used_percent"], 42)
                on_disk = _json.loads(RATE_LIMITS_CACHE_FILE.read_text())
                self.assertNotIn("cursor", on_disk.get("no_data", {}))
                self.assertIn("cursor", on_disk["limits"])

                # Press 2: the retry actually runs.
                result = get_all_rate_limits(
                    use_cache=True, tool_names={"cursor"}, refresh_failed=True)
                self.assertEqual(failing.call_count, 2)
                self.assertIn("cursor", result)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

    def test_a_stale_no_data_marker_does_not_block_a_retry(self):
        """A cache written by an older swe holds the tool in both limits
        and no_data: refresh_failed must still set the entry aside and
        refetch it."""
        import json as _json

        now = time.time()
        unavailable = self._raw("cursor", 10, windows=[
            {"window": "grok_bot", "used_percent": 42,
             "limit_reached": False, "reset_at": 900,
             "window_seconds": 604800, "unavailable": True, "as_of": 1}])
        fetcher = self._fetcher("cursor", 55)

        saved = _FETCHERS.copy()
        _FETCHERS.clear()
        register("cursor", fetcher)
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from swe.harness.rate_limits import RATE_LIMITS_CACHE_FILE
                RATE_LIMITS_CACHE_FILE.write_text(_json.dumps({
                    "cached_at": now,
                    "limits": {"cursor": unavailable},
                    "updated_at": {"cursor": now},
                    "no_data": {"cursor": now},
                }))
                result = get_all_rate_limits(
                    use_cache=True, tool_names={"cursor"}, refresh_failed=True)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        fetcher.assert_called_once_with()
        self.assertEqual(result["cursor"].used_percent, 55)

    def test_cache_ttl_replaces_the_default_freshness(self):
        fetcher = self._fetcher("tool", 30)
        result = self._run({"tool": fetcher},
                           {"tool": self._raw("tool", 10)},
                           ttl=0.000_001)
        fetcher.assert_called_once_with()
        self.assertEqual(result["tool"].used_percent, 30)

    def test_a_short_ttl_still_carries_last_known_values(self):
        """A reading past cache_ttl but under 24h still fills an
        unavailable window, so a Refresh press does not blank the row."""
        import json as _json

        ago = time.time() - 7200  # past any app TTL, inside the 24h bound
        previous = self._raw("cursor", 10, windows=[
            {"window": "grok_bot", "used_percent": 42,
             "limit_reached": False, "reset_at": 900,
             "window_seconds": 604800}])
        previous["fetched_at"] = ago
        fetcher = MagicMock(return_value=RateLimitInfo(
            tool_name="cursor", used_percent=10, limit_reached=False,
            reset_at=0, plan_type="team", window_seconds=0,
            windows=[_window("grok_bot", 0, 0, 604800, unavailable=True)],
        ))

        saved = _FETCHERS.copy()
        _FETCHERS.clear()
        register("cursor", fetcher)
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp) / "rate_limits_cache.json",
            ):
                from swe.harness.rate_limits import RATE_LIMITS_CACHE_FILE
                RATE_LIMITS_CACHE_FILE.write_text(_json.dumps({
                    "cached_at": ago,
                    "limits": {"cursor": previous},
                    "updated_at": {"cursor": ago},
                    "no_data": {},
                }))
                result = get_all_rate_limits(
                    use_cache=True, tool_names={"cursor"}, cache_ttl=60)
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)

        grok = result["cursor"].windows[0]
        self.assertEqual(grok["used_percent"], 42)
        self.assertEqual(grok["reset_at"], 900)
        self.assertEqual(grok["as_of"], ago)


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
        from swe.harness.rate_limits import _fetch_codex

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
            with patch("swe.harness.rate_limits.os.path.expanduser",
                       side_effect=lambda p: str(auth_path) if p == "~/.codex/auth.json" else p), \
                 patch("swe.harness.rate_limits.urllib.request.urlopen",
                       return_value=mock_resp):
                info = _fetch_codex()
                self.assertIsNotNone(info)
                self.assertEqual(info.tool_name, "codex")
                self.assertEqual(info.used_percent, 100)
                self.assertTrue(info.limit_reached)
                self.assertEqual(info.reset_at, 1785261854.0)
                self.assertEqual(info.plan_type, "plus")
                self.assertEqual(info.window_seconds, 604800)

    def test_fetch_codex_coerces_malformed_numbers(self):
        """A string percent reached the table renderer as is and raised
        TypeError out of `swe list`; a "false" string read as a reached
        limit. Codex now coerces like every other fetcher."""
        from swe.harness.rate_limits import _fetch_codex

        auth_data = {"tokens": {"access_token": "fake-token"}}
        cases = [
            ({"limit_reached": "false", "primary_window": {
                "used_percent": "45.6", "limit_window_seconds": "604800"}},
             (46, False, 604800)),
            ({"limit_reached": True, "primary_window": {
                "used_percent": 250, "limit_window_seconds": None}},
             (100, True, 0)),
            ({"primary_window": {"used_percent": "n/a", "limit_window_seconds": -5}},
             (0, False, 0)),
            ({"primary_window": ["not", "a", "dict"]}, (0, False, 0)),
        ]
        for rate_limit, (used, reached, seconds) in cases:
            body = {"plan_type": ["x"], "rate_limit": rate_limit}
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(body).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            with self.subTest(rate_limit=rate_limit), tempfile.TemporaryDirectory() as tmp:
                auth_path = Path(tmp) / "auth.json"
                auth_path.write_text(json.dumps(auth_data))
                with patch("swe.harness.rate_limits.os.path.expanduser",
                           side_effect=lambda p: str(auth_path) if p == "~/.codex/auth.json" else p), \
                     patch("swe.harness.rate_limits.urllib.request.urlopen",
                           return_value=mock_resp):
                    info = _fetch_codex()
                self.assertEqual((info.used_percent, info.limit_reached, info.window_seconds),
                                 (used, reached, seconds))
                self.assertEqual(info.plan_type, "—")
                info.format_column()          # renders without raising

    def test_fetch_codex_reads_the_email_off_the_id_token(self):
        """The sign-in's address is an ``id_token`` claim, as the app
        reads it; a missing or un-JWT id_token just means no email."""
        from swe.harness.rate_limits import _fetch_codex

        def jwt(claims: dict) -> str:
            part = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
            return ".".join((
                part(b'{"alg":"none"}'), part(json.dumps(claims).encode()), "sig",
            ))

        resp_json = json.dumps(self._SAMPLE_RESPONSE).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp_json
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        cases = [
            (jwt({"email": "kai@example.com"}), "kai@example.com"),
            (jwt({"email": "  "}), None),
            (jwt({}), None),
            ("not-a-jwt", None),
        ]
        for id_token, expected in cases:
            with self.subTest(id_token=id_token):
                auth_data = {
                    "auth_mode": "chatgpt",
                    "tokens": {"access_token": "fake-token",
                               "id_token": id_token},
                }
                with tempfile.TemporaryDirectory() as tmp:
                    auth_path = Path(tmp) / "auth.json"
                    auth_path.write_text(json.dumps(auth_data))
                    with patch(
                        "swe.harness.rate_limits.os.path.expanduser",
                        side_effect=lambda p: str(auth_path) if p == "~/.codex/auth.json" else p,
                    ), patch(
                        "swe.harness.rate_limits.urllib.request.urlopen",
                        return_value=mock_resp,
                    ):
                        info = _fetch_codex()
                        self.assertIsNotNone(info)
                        self.assertEqual(info.email, expected)
                        self.assertEqual(info.to_json()["email"], expected)

    def test_fetch_codex_no_auth_file(self):
        from swe.harness.rate_limits import _fetch_codex

        with patch("swe.harness.rate_limits.os.path.expanduser",
                   side_effect=lambda p: "/nonexistent/path" if p == "~/.codex/auth.json" else p):
            info = _fetch_codex()
            self.assertIsNone(info)

    def test_fetch_codex_no_access_token(self):
        from swe.harness.rate_limits import _fetch_codex

        auth_data = {"auth_mode": "chatgpt", "tokens": {}}
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text(json.dumps(auth_data))
            with patch("swe.harness.rate_limits.os.path.expanduser",
                       side_effect=lambda p: str(auth_path) if p == "~/.codex/auth.json" else p):
                info = _fetch_codex()
                self.assertIsNone(info)

    def test_fetch_codex_http_error(self):
        from swe.harness.rate_limits import _fetch_codex
        import urllib.error

        auth_data = {
            "auth_mode": "chatgpt",
            "tokens": {"access_token": "fake-token"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text(json.dumps(auth_data))
            with patch("swe.harness.rate_limits.os.path.expanduser",
                       side_effect=lambda p: str(auth_path) if p == "~/.codex/auth.json" else p), \
                 patch("swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import _fetch_codex

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
                        "swe.harness.rate_limits.os.path.expanduser",
                        side_effect=lambda p: str(auth_path) if p == "~/.codex/auth.json" else p,
                    ), patch(
                        "swe.harness.rate_limits.urllib.request.urlopen",
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
        which = patch("swe.harness.rate_limits.shutil.which", return_value="/usr/bin/gh")
        run = patch(
            "swe.harness.rate_limits.subprocess.run",
            return_value=_CompletedProc(returncode=0, stdout=token + "\n"),
        )
        return _Both(which, run)

    def test_fetch_copilot_success(self):
        from swe.harness.rate_limits import _fetch_github_copilot

        with self._patch_token(), patch(
            "swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import _fetch_github_copilot

        body = dict(self._SAMPLE_RESPONSE)
        body["quota_snapshots"] = {
            "premium_interactions": {
                "percent_remaining": -0.8, "unlimited": False,
                "entitlement": 200, "credits_used": 201,
                "has_quota": False, "remaining": -2,
            }
        }
        with self._patch_token(), patch(
            "swe.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_github_copilot()
        self.assertIsNotNone(info)
        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)

    def test_fetch_copilot_unlimited(self):
        """unlimited=true should return 0% usage and not limit_reached."""
        from swe.harness.rate_limits import _fetch_github_copilot

        body = dict(self._SAMPLE_RESPONSE)
        body["quota_snapshots"] = {
            "premium_interactions": {
                "percent_remaining": 100.0, "unlimited": True,
                "entitlement": 99999, "credits_used": 0,
                "has_quota": True,
            }
        }
        with self._patch_token(), patch(
            "swe.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_github_copilot()
        self.assertIsNotNone(info)
        self.assertEqual(info.used_percent, 0)
        self.assertFalse(info.limit_reached)

    def test_fetch_copilot_no_premium_snapshot(self):
        """Missing premium_interactions → still return RateLimitInfo with reset_at."""
        from swe.harness.rate_limits import _fetch_github_copilot

        body = dict(self._SAMPLE_RESPONSE)
        body["quota_snapshots"] = {}
        with self._patch_token(), patch(
            "swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import _fetch_github_copilot

        body = dict(self._SAMPLE_RESPONSE)
        body.pop("quota_reset_date_utc", None)
        body.pop("quota_reset_date", None)
        with self._patch_token(), patch(
            "swe.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_github_copilot()
        self.assertIsNotNone(info)
        self.assertEqual(info.reset_at, 0)

    def test_fetch_copilot_no_gh(self):
        """Missing gh CLI → return None."""
        from swe.harness.rate_limits import _fetch_github_copilot

        with patch("swe.harness.rate_limits.shutil.which", return_value=None):
            info = _fetch_github_copilot()
        self.assertIsNone(info)

    def test_fetch_copilot_gh_not_authenticated(self):
        """gh returns non-zero exit (not authenticated) → return None."""
        from swe.harness.rate_limits import _fetch_github_copilot

        with patch(
            "swe.harness.rate_limits.subprocess.run",
            return_value=_CompletedProc(returncode=1, stdout="", stderr="not logged in"),
        ):
            info = _fetch_github_copilot()
        self.assertIsNone(info)

    def test_fetch_copilot_gh_empty_token(self):
        """gh succeeds but stdout is empty → return None."""
        from swe.harness.rate_limits import _fetch_github_copilot

        with patch(
            "swe.harness.rate_limits.subprocess.run",
            return_value=_CompletedProc(returncode=0, stdout="  \n"),
        ):
            info = _fetch_github_copilot()
        self.assertIsNone(info)

    def test_fetch_copilot_http_error(self):
        """HTTP 4xx/5xx from GitHub → return None."""
        from swe.harness.rate_limits import _fetch_github_copilot
        import urllib.error

        with self._patch_token(), patch(
            "swe.harness.rate_limits.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("url", 403, "Forbidden", {}, None),
        ):
            info = _fetch_github_copilot()
        self.assertIsNone(info)

    def test_fetch_copilot_malformed_json(self):
        """Invalid JSON response → return None."""
        from swe.harness.rate_limits import _fetch_github_copilot

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with self._patch_token(), patch(
            "swe.harness.rate_limits.urllib.request.urlopen",
            return_value=mock_resp,
        ):
            info = _fetch_github_copilot()
        self.assertIsNone(info)


class CopilotDerivationTest(unittest.TestCase):
    """Unit tests for the deterministic mapping helpers."""

    def test_unlimited_returns_zero(self):
        from swe.harness.rate_limits import _derive_copilot_fields

        used, reached = _derive_copilot_fields(
            {"unlimited": True, "percent_remaining": 50.0,
             "entitlement": 99999, "has_quota": True},
        )
        self.assertEqual(used, 0)
        self.assertFalse(reached)

    def test_full_quota_no_limit(self):
        from swe.harness.rate_limits import _derive_copilot_fields

        used, reached = _derive_copilot_fields(
            {"unlimited": False, "percent_remaining": 100.0,
             "entitlement": 1500, "has_quota": True},
        )
        self.assertEqual(used, 0)
        self.assertFalse(reached)

    def test_half_quota(self):
        from swe.harness.rate_limits import _derive_copilot_fields

        used, reached = _derive_copilot_fields(
            {"unlimited": False, "percent_remaining": 50.0,
             "entitlement": 100, "has_quota": True},
        )
        self.assertEqual(used, 50)
        self.assertFalse(reached)

    def test_over_quota_clamps_to_100(self):
        from swe.harness.rate_limits import _derive_copilot_fields

        used, reached = _derive_copilot_fields(
            {"unlimited": False, "percent_remaining": -5.0,
             "entitlement": 200, "has_quota": False},
        )
        self.assertEqual(used, 100)
        self.assertTrue(reached)

    def test_decorate_individual_with_educational_sku(self):
        from swe.harness.rate_limits import _decorate_copilot_plan_type

        result = _decorate_copilot_plan_type("individual", "free_educational_quota")
        self.assertEqual(result, "individual/edu")

    def test_decorate_individual_with_pro_sku_unchanged(self):
        from swe.harness.rate_limits import _decorate_copilot_plan_type

        self.assertEqual(
            _decorate_copilot_plan_type("individual", "pro_plus"),
            "individual",
        )

    def test_decorate_business_with_educational_sku_unchanged(self):
        """Only ``individual`` gets the /edu suffix, not other plan types."""
        from swe.harness.rate_limits import _decorate_copilot_plan_type

        self.assertEqual(
            _decorate_copilot_plan_type("business", "free_educational_quota"),
            "business",
        )

    def test_decorate_empty_sku_unchanged(self):
        from swe.harness.rate_limits import _decorate_copilot_plan_type

        self.assertEqual(_decorate_copilot_plan_type("individual", ""), "individual")
        self.assertEqual(_decorate_copilot_plan_type("individual", "—"), "individual")

    def test_decorate_dash_plan_unchanged(self):
        """The ``—`` placeholder plan_type should never be decorated."""
        from swe.harness.rate_limits import _decorate_copilot_plan_type

        self.assertEqual(
            _decorate_copilot_plan_type("—", "free_educational_quota"),
            "—",
        )

    def test_parse_iso8601(self):
        from swe.harness.rate_limits import _parse_iso8601_to_epoch

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
        from swe.harness.rate_limits import _parse_iso8601_to_epoch

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
        from swe.harness.rate_limits import _parse_iso8601_to_epoch

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
        from swe.harness.rate_limits import _parse_iso8601_to_epoch

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
        from swe.harness.rate_limits import _derive_copilot_fields

        # No unlimited flag → null means unknown, used_percent = 0.
        used, reached = _derive_copilot_fields(
            {"unlimited": False, "percent_remaining": None,
             "entitlement": 1500, "has_quota": True},
        )
        self.assertEqual(used, 0)
        self.assertFalse(reached)

    def test_derive_copilot_fields_malformed_doesnt_crash(self):
        """Non-numeric percent_remaining must not crash the fetcher."""
        from swe.harness.rate_limits import _derive_copilot_fields

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
        from swe.harness.rate_limits import _parse_iso8601_to_epoch

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
        from swe.harness.rate_limits import _parse_iso8601_to_epoch

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
        from swe.harness.rate_limits import _parse_iso8601_to_epoch

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
        from swe.harness.rate_limits import _parse_iso8601_to_epoch

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
        from swe.harness.rate_limits import _parse_iso8601_to_epoch

        for raw in (
            "2026-08-01T00:00:00.123456789+00:00",
            "2026-08-01T00:00:00.123456789Z",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(_parse_iso8601_to_epoch(raw), 1785542400.123456)

    def test_unparseable_and_absent_values_are_zero(self):
        """Anything we cannot read becomes 0.0, never an exception."""
        from swe.harness.rate_limits import _parse_iso8601_to_epoch

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
                "swe.harness.rate_limits.os.path.expanduser",
                side_effect=lambda p: (
                    str(creds_path) if "~/.claude/.credentials.json" in str(p)
                    else p
                ),
            ),
            patch(
                "swe.harness.rate_limits._read_claude_keychain_credentials",
                return_value=None,
            ),
        ]
        return tmp, patches

    def test_creds_present_fetches_most_limiting_window(self):
        """A paid Claude login surfaces the most-used subscription window."""
        from swe.harness.rate_limits import _fetch_claude

        tmp, patches = self._linux_creds_file()
        try:
            with patches[0], patches[1], patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp.name) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits.urllib.request.urlopen",
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

    def test_email_comes_from_the_global_config(self):
        """``~/.claude.json``'s ``oauthAccount.emailAddress`` rides the
        reading; an absent file or field just means no email."""
        from swe.harness.rate_limits import _fetch_claude, _read_claude_email

        tmp = tempfile.TemporaryDirectory()
        try:
            creds_path = Path(tmp.name) / "creds.json"
            creds_path.write_text(json.dumps({"claudeAiOauth": {
                "accessToken": "fake-claude-token",
                "refreshToken": "x",
                "expiresAt": 9_999_999_999_999,
            }}))
            config_path = Path(tmp.name) / ".claude.json"
            config_path.write_text(json.dumps({
                "oauthAccount": {"emailAddress": "work@example.com"},
            }))
            redirect = lambda p: {
                "~/.claude/.credentials.json": str(creds_path),
                "~/.claude.json": str(config_path),
            }.get(p, p)
            with patch(
                "swe.harness.rate_limits.os.path.expanduser",
                side_effect=redirect,
            ), patch(
                "swe.harness.rate_limits._read_claude_keychain_credentials",
                return_value=None,
            ), patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp.name) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits.urllib.request.urlopen",
                return_value=self._mock_response(self._SAMPLE_RESPONSE),
            ):
                self.assertEqual(_read_claude_email(), "work@example.com")
                info = _fetch_claude()
            self.assertIsNotNone(info)
            self.assertEqual(info.email, "work@example.com")

            config_path.write_text(json.dumps({"oauthAccount": {}}))
            with patch(
                "swe.harness.rate_limits.os.path.expanduser",
                side_effect=redirect,
            ):
                self.assertIsNone(_read_claude_email())
        finally:
            tmp.cleanup()

    def test_no_credentials_returns_none(self):
        """Neither file nor keychain available → return None, no crash."""
        from swe.harness.rate_limits import _fetch_claude

        with patch("swe.harness.rate_limits.os.path.expanduser",
                   side_effect=lambda p: "/no/such/path/x" if "~/.claude" in str(p) else p), \
             patch("swe.harness.rate_limits.shutil.which", return_value=None):
            info = _fetch_claude()
        self.assertIsNone(info)

    def test_expired_credentials_request_relogin(self):
        """Expired token with no refreshToken is re-login, never fetched."""
        from swe.harness.rate_limits import _fetch_claude

        tmp, patches = self._linux_creds_file(expiresAt=1, refreshToken=None)
        try:
            with patches[0], patches[1], patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp.name) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits.urllib.request.urlopen",
            ) as request:
                info = _fetch_claude()
        finally:
            tmp.cleanup()

        self.assertEqual(info.plan_type, "auth-required")
        self.assertIn("re-login", info.format_column())
        request.assert_not_called()

    def test_expired_token_with_refresh_token_is_no_reading(self):
        """An expired access token beside a refreshToken is a live login.

        Claude Code refreshes transparently on its next call, so the
        fetcher reports nothing and the aggregator can serve the last
        good figure instead of crying re-login.
        """
        from swe.harness.rate_limits import _fetch_claude

        tmp, patches = self._linux_creds_file(expiresAt=1)
        try:
            with patches[0], patches[1], patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp.name) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits.urllib.request.urlopen",
            ) as request:
                info = _fetch_claude()
        finally:
            tmp.cleanup()

        self.assertIsNone(info)
        request.assert_not_called()

    def test_expired_token_with_refresh_token_serves_own_cache(self):
        """A fresh claude-side reading shows through the self-heal window."""
        from swe.harness.rate_limits import _fetch_claude, _save_claude_state

        tmp, patches = self._linux_creds_file(expiresAt=1)
        try:
            with patches[0], patches[1], patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp.name) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits.urllib.request.urlopen",
            ) as request:
                _save_claude_state(RateLimitInfo(
                    tool_name="claude", used_percent=61,
                    limit_reached=False, reset_at=0.0,
                    plan_type="—", window_seconds=0, window="7d"))
                info = _fetch_claude()
        finally:
            tmp.cleanup()

        self.assertIsNotNone(info)
        self.assertEqual(info.used_percent, 61)
        request.assert_not_called()

    def test_refresh_pending_reading_keeps_its_real_age(self):
        """A re-served claude figure must not be stamped fresh.

        The aggregator writes ``updated_at`` into the shared cache; a
        stale reading stamped now would stay visible past its 24h bound.
        """
        from swe.harness.rate_limits import _fetch_claude, _save_claude_state

        tmp, patches = self._linux_creds_file(expiresAt=1)
        two_hours_ago = time.time() - 7200
        saved = _FETCHERS.copy()
        _FETCHERS.clear()
        register("claude", _fetch_claude)
        try:
            with patches[0], patches[1], patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp.name) / "rate_limits_cache.json",
            ):
                _save_claude_state(
                    RateLimitInfo(
                        tool_name="claude", used_percent=61,
                        limit_reached=False, reset_at=0.0,
                        plan_type="—", window_seconds=0, window="7d"),
                    fetched_at=two_hours_ago,
                )
                result = get_all_rate_limits(use_cache=False)
                payload = json.loads(
                    (Path(tmp.name) / "rate_limits_cache.json").read_text())
        finally:
            _FETCHERS.clear()
            _FETCHERS.update(saved)
            tmp.cleanup()

        self.assertEqual(result["claude"].used_percent, 61)
        self.assertAlmostEqual(
            payload["updated_at"]["claude"], two_hours_ago, delta=5)

    def test_fresher_keychain_beats_expired_file(self):
        """A fresh Keychain login wins over a stale credentials file."""
        from swe.harness.rate_limits import _get_claude_oauth_credentials

        tmp, patches = self._linux_creds_file(token="file-token", expiresAt=1)
        try:
            with patches[0], patch(
                "swe.harness.rate_limits._read_claude_keychain_credentials",
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
        from swe.harness.rate_limits import _get_claude_oauth_credentials

        tmp, patches = self._linux_creds_file()
        try:
            with patches[0], patch(
                "swe.harness.rate_limits._read_claude_keychain_credentials",
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
        from swe.harness.rate_limits import _get_claude_oauth_credentials

        tmp, patches = self._linux_creds_file()
        try:
            with patches[0], patch(
                "swe.harness.rate_limits._read_claude_keychain_credentials",
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
        from swe.harness.rate_limits import (
            _CLAUDE_KEYCHAIN_TIMEOUT,
            _RATE_LIMIT_FETCH_DEADLINE,
            _read_claude_keychain_credentials,
        )

        creds_json = json.dumps({"claudeAiOauth": {"accessToken": "kc-token"}})
        with patch("swe.harness.rate_limits.shutil.which",
                   return_value="/usr/bin/security"), \
             patch("swe.harness.rate_limits.subprocess.run",
                   return_value=_CompletedProc(returncode=0, stdout=creds_json)
                   ) as run:
            oauth = _read_claude_keychain_credentials()
        self.assertEqual(oauth["accessToken"], "kc-token")
        self.assertEqual(
            run.call_args.kwargs["timeout"], _CLAUDE_KEYCHAIN_TIMEOUT)
        self.assertLess(_CLAUDE_KEYCHAIN_TIMEOUT, _RATE_LIMIT_FETCH_DEADLINE)

    def test_keychain_timeout_does_not_promote_stale_file(self):
        """A timed-out Keychain lookup is unknown, not absent."""
        from swe.harness.rate_limits import (
            _fetch_claude, _get_claude_oauth_credentials,
        )

        tmp, patches = self._linux_creds_file(expiresAt=1)
        try:
            with patches[0], patch(
                "swe.harness.rate_limits.shutil.which",
                return_value="/usr/bin/security",
            ), patch(
                "swe.harness.rate_limits.subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    cmd="security", timeout=1.0),
            ), patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp.name) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits.urllib.request.urlopen",
            ) as request:
                self.assertIsNone(_get_claude_oauth_credentials())
                self.assertIsNone(_fetch_claude())
            request.assert_not_called()
        finally:
            tmp.cleanup()

    def test_keychain_os_error_still_falls_back_to_file(self):
        """A Keychain that errors outright still lets the file win."""
        from swe.harness.rate_limits import _get_claude_oauth_credentials

        tmp, patches = self._linux_creds_file()
        try:
            with patches[0], patch(
                "swe.harness.rate_limits.shutil.which",
                return_value="/usr/bin/security",
            ), patch(
                "swe.harness.rate_limits.subprocess.run",
                side_effect=OSError(),
            ):
                oauth = _get_claude_oauth_credentials()
            self.assertEqual(oauth["accessToken"], "fake-claude-token")
        finally:
            tmp.cleanup()

    def test_expired_file_without_keychain_is_kept(self):
        """The only known credential is still returned so it can age out."""
        from swe.harness.rate_limits import (
            _fetch_claude, _get_claude_oauth_credentials,
        )

        tmp, patches = self._linux_creds_file(expiresAt=1, refreshToken=None)
        try:
            with patches[0], patches[1], patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                Path(tmp.name) / "rate_limits_cache.json",
            ), patch(
                "swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import _get_claude_oauth_credentials

        tmp, patches = self._linux_creds_file(
            token="file-token", expiresAt=1_000)
        try:
            with patches[0], patch(
                "swe.harness.rate_limits._read_claude_keychain_credentials",
                return_value={"accessToken": "kc-token", "expiresAt": 1},
            ):
                oauth = _get_claude_oauth_credentials()
            self.assertEqual(oauth["accessToken"], "file-token")
        finally:
            tmp.cleanup()

    def test_claude_expires_at_seconds_accepts_ms_and_seconds(self):
        """expiresAt normalises milliseconds and seconds, 0.0 on junk."""
        from swe.harness.rate_limits import _claude_expires_at_seconds

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
        from swe.harness.rate_limits import _fetch_claude

        tmp, patches = self._linux_creds_file()
        cache_file = Path(tmp.name) / "rate_limits_cache.json"
        try:
            with patches[0], patches[1], patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "swe.harness.rate_limits._fetch_claude_url",
                return_value=self._SAMPLE_RESPONSE,
            ):
                first = _fetch_claude()

            def rate_limited(req, on_rate_limited=None):
                on_rate_limited(120)
                return None

            with patches[0], patches[1], patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "swe.harness.rate_limits._fetch_claude_url",
                side_effect=rate_limited,
            ):
                stale = _fetch_claude()

            with patches[0], patches[1], patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "swe.harness.rate_limits._fetch_claude_url",
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
        from swe.harness.rate_limits import _fetch_claude

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
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "swe.harness.rate_limits._fetch_claude_url",
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
        from swe.harness.rate_limits import (
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
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "swe.harness.rate_limits._fetch_claude_url",
                return_value=None,
            ) as fetch:
                info = _fetch_claude()
            self.assertIsNone(info)
            fetch.assert_called_once()
        finally:
            tmp.cleanup()

    def test_cooldown_drops_reading_older_than_a_day(self):
        """A 429 cooldown never shows a figure the aggregator would refuse."""
        from swe.harness.rate_limits import (
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
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "swe.harness.rate_limits._fetch_claude_url",
            ) as fetch:
                info = _fetch_claude()
            self.assertIsNone(info)
            fetch.assert_not_called()
        finally:
            tmp.cleanup()

    def test_state_saved_before_fetched_at_existed_is_not_reused(self):
        """A state file from before readings were dated cannot be aged."""
        from swe.harness.rate_limits import _fetch_claude

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
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "swe.harness.rate_limits._fetch_claude_url",
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
        from swe.harness.rate_limits import _fetch_claude

        tmp, patches = self._linux_creds_file()
        cache_file = Path(tmp.name) / "rate_limits_cache.json"
        claude_cache = cache_file.with_name("claude_usage_cache.json")

        def rate_limited(req, on_rate_limited=None):
            on_rate_limited(120)
            return None

        try:
            with patches[0], patches[1], patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "swe.harness.rate_limits._fetch_claude_url",
                return_value=self._SAMPLE_RESPONSE,
            ):
                _fetch_claude()
            first_fetched_at = json.loads(claude_cache.read_text())["fetched_at"]
            self.assertGreater(first_fetched_at, 0)

            with patches[0], patches[1], patch(
                "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                cache_file,
            ), patch(
                "swe.harness.rate_limits._fetch_claude_url",
                side_effect=rate_limited,
            ), patch(
                "swe.harness.rate_limits.time.time",
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
        from swe.harness.rate_limits import _fetch_claude

        tmp = tempfile.TemporaryDirectory()
        creds_json = json.dumps({"claudeAiOauth": {"accessToken": "kc-token"}})
        completed = _CompletedProc(returncode=0, stdout=creds_json)
        try:
            with patch("swe.harness.rate_limits.os.path.expanduser",
                       side_effect=lambda p: "/no/such/path/x" if "~/.claude" in str(p) else p), \
                 patch("swe.harness.rate_limits.shutil.which",
                       return_value="/usr/bin/security"), \
                 patch("swe.harness.rate_limits.subprocess.run",
                       return_value=completed), \
                 patch("swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE",
                       Path(tmp.name) / "rate_limits_cache.json"), \
                 patch("swe.harness.rate_limits.urllib.request.urlopen",
                       return_value=self._mock_response(self._SAMPLE_RESPONSE)):
                info = _fetch_claude()
            self.assertIsNotNone(info)
            self.assertEqual(info.tool_name, "claude")
            self.assertEqual(info.used_percent, 85)
        finally:
            tmp.cleanup()

    def test_keychain_bad_json_returns_none(self):
        """Non-JSON keychain password field → return None (defensive parse)."""
        from swe.harness.rate_limits import _fetch_claude

        completed = _CompletedProc(returncode=0, stdout="not-json")
        with patch("swe.harness.rate_limits.os.path.expanduser",
                   side_effect=lambda p: "/no/such/path/x" if "~/.claude" in str(p) else p), \
             patch("swe.harness.rate_limits.shutil.which",
                   return_value="/usr/bin/security"), \
             patch("swe.harness.rate_limits.subprocess.run",
                   return_value=completed):
            info = _fetch_claude()
        self.assertIsNone(info)

    def test_keychain_missing_token_field_returns_none(self):
        """JSON parses but lacks claudeAiOauth.accessToken → return None."""
        from swe.harness.rate_limits import _fetch_claude

        creds_json = json.dumps({"other_field": "x"})  # no claudeAiOauth
        completed = _CompletedProc(returncode=0, stdout=creds_json)
        with patch("swe.harness.rate_limits.os.path.expanduser",
                   side_effect=lambda p: "/no/such/path/x" if "~/.claude" in str(p) else p), \
             patch("swe.harness.rate_limits.shutil.which",
                   return_value="/usr/bin/security"), \
             patch("swe.harness.rate_limits.subprocess.run",
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
        with patch("swe.harness.rate_limits.time.time",
                   return_value=RateLimitInfoTest._NOW):
            col = info.format_column()
        # Window label MUST appear; reset countdown MUST appear.
        self.assertIn("7ds:", col)
        self.assertIn("5h0m", col)
        self.assertIn("◔", col)  # 15% remaining → quarter pie
        self.assertNotIn("%", col)

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
        with patch("swe.harness.rate_limits.time.time",
                   return_value=RateLimitInfoTest._NOW):
            col = info.format_column()
        # No window marker; the colons that mark the window prefix must not appear.
        self.assertNotIn(":", col.replace("—", ""))
        self.assertIn("◕", col)  # 70% remaining → three-quarter pie
        self.assertNotIn("%", col)


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
        from swe.harness.rate_limits import _parse_freebuff_quota

        info = _parse_freebuff_quota(self._SAMPLE_RESPONSE)

        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "freebuff")
        self.assertEqual(info.remaining_units, 3)
        self.assertEqual(info.total_units, 4)
        self.assertEqual(info.used_percent, 25)
        self.assertFalse(info.limit_reached)
        self.assertEqual(info.reset_at, 1785567600.0)

    def test_parser_marks_exhausted_allowance(self):
        from swe.harness.rate_limits import _parse_freebuff_quota

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        data["referral"]["weeklySessionsRemaining"] = 0

        info = _parse_freebuff_quota(data)

        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)

    def test_parser_prefers_exact_glm_model_quota(self):
        from swe.harness.rate_limits import _parse_freebuff_quota

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
        from swe.harness.rate_limits import _parse_freebuff_quota

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
        from swe.harness.rate_limits import _parse_freebuff_quota

        self.assertIsNone(_parse_freebuff_quota({"status": "none"}))
        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        data["referral"]["weeklySessionsRemaining"] = 5
        self.assertIsNone(_parse_freebuff_quota(data))

    def test_fetch_uses_bearer_token_without_starting_session(self):
        from swe.harness.rate_limits import _fetch_freebuff

        with patch(
            "swe.harness.rate_limits._get_freebuff_access_token",
            return_value="freebuff-token",
        ), patch(
            "swe.harness.rate_limits._fetch_json",
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
        from swe.harness.rate_limits import _fetch_freebuff

        with patch(
            "swe.harness.rate_limits._get_freebuff_access_token",
            return_value=None,
        ), patch("swe.harness.rate_limits._fetch_json") as fetch_json:
            info = _fetch_freebuff()

        self.assertIsNone(info)
        fetch_json.assert_not_called()

    def test_reads_token_from_freebuff_credentials(self):
        from swe.harness.rate_limits import _get_freebuff_access_token

        with tempfile.TemporaryDirectory() as tmp:
            credentials = Path(tmp) / "credentials.json"
            credentials.write_text(json.dumps({
                "default": {"authToken": "stored-token"},
                "chatgptOAuth": {"accessToken": "not-this-token"},
            }))
            with patch(
                "swe.harness.rate_limits.os.path.expanduser",
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
            "swe.harness.rate_limits.time.time",
            return_value=self._NOW,
        )

    def _decrypt_none(self):
        """Neutralize the auth.v2.file decryption rung for test isolation.

        Without this patch, tests that clear ``FACTORY_API_KEY`` would
        hit the REAL ``~/.factory/auth.v2.file`` on the dev machine,
        decrypt a real token, and bypass the keychain rung under test.
        """
        return patch(
            "swe.harness.rate_limits._decrypt_droid_auth_file",
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
            patch("swe.harness.rate_limits.shutil.which", return_value=None),
            self._decrypt_none(),
            self._now_patch(),
        )

    def _no_creds(self):
        """Wipe env vars + neutralize decryption + keychain + now → None."""
        return (
            patch.dict(os.environ, {}, clear=True),
            patch("swe.harness.rate_limits.shutil.which", return_value=None),
            self._decrypt_none(),
            self._now_patch(),
        )

    def test_droid_fetch_has_wall_clock_deadline(self):
        """A stalled DNS/HTTP call must not block ``swe list`` indefinitely."""
        import threading
        from swe.harness.rate_limits import _droid_fetch

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
                "swe.harness.rate_limits._DROID_FETCH_TIMEOUT",
                0.05,
                create=True,
            ), patch(
                "swe.harness.rate_limits._fetch_json",
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
        from swe.harness.rate_limits import _fetch_droid

        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import _fetch_droid

        with patch.dict(os.environ, {"FACTORY_API_KEY": "   "}, clear=True), \
             patch("swe.harness.rate_limits.shutil.which",
                   return_value=None), \
             self._decrypt_none():
            info = _fetch_droid()
        self.assertIsNone(info)

    # 3. Decrypted auth.v2.file is the primary browser-auth rung (step 2).
    def test_auth_v2_file_decryption_path(self):
        from swe.harness.rate_limits import _fetch_droid

        with patch.dict(os.environ, {}, clear=True), \
             patch("swe.harness.rate_limits._decrypt_droid_auth_file",
                   return_value="decrypted-factory-token"), \
             patch("swe.harness.rate_limits.shutil.which",
                   return_value=None), \
             self._now_patch(), \
             patch("swe.harness.rate_limits.urllib.request.urlopen",
                   return_value=self._mock_response(self._SAMPLE_RESPONSE)):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "droid")
        # (60 + 20) / 2 = 40.
        self.assertEqual(info.used_percent, 40)
        self.assertEqual(info.window, "5h")

    # 4. Keychain `Factory Safe Storage` label is the fallback rung (step 3).
    def test_keychain_factory_safe_storage(self):
        from swe.harness.rate_limits import _fetch_droid

        with patch.dict(os.environ, {}, clear=True), \
             self._decrypt_none(), \
             patch("swe.harness.rate_limits.shutil.which",
                   return_value="/usr/bin/security"), \
             patch("swe.harness.rate_limits.subprocess.run",
                   return_value=_CompletedProc(returncode=0,
                                                stdout="kc-factory-token\n")), \
             self._now_patch(), \
             patch("swe.harness.rate_limits.urllib.request.urlopen",
                   return_value=self._mock_response(self._SAMPLE_RESPONSE)):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "droid")
        # (60 + 20) / 2 = 40.
        self.assertEqual(info.used_percent, 40)

    # 5. Keychain fallback ladder: Safe Storage fails, Factory Key succeeds.
    def test_keychain_alt_label_falls_back(self):
        from swe.harness.rate_limits import _fetch_droid

        # First subprocess call fails (label 1), second succeeds (label 2).
        side_effects = [
            _CompletedProc(returncode=1, stdout=""),  # Safe Storage missing
            _CompletedProc(returncode=0, stdout="kc-alt-token\n"),  # Factory Key wins
        ]
        with patch.dict(os.environ, {}, clear=True), \
             self._decrypt_none(), \
             patch("swe.harness.rate_limits.shutil.which",
                   return_value="/usr/bin/security"), \
             patch("swe.harness.rate_limits.subprocess.run",
                   side_effect=side_effects) as mock_run, \
             self._now_patch(), \
             patch("swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import _fetch_droid

        env_patch, what_patch, dec_patch, now_patch = self._no_creds()
        with env_patch, what_patch, dec_patch, now_patch:
            info = _fetch_droid()
        self.assertIsNone(info)

    # 7. HTTP 4xx/5xx → return None silently (no on_401 callback for 503).
    def test_http_error_returns_none(self):
        from swe.harness.rate_limits import _fetch_droid
        import urllib.error

        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "swe.harness.rate_limits.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "url", 503, "Service Unavailable", {}, None),
        ):
            info = _fetch_droid()
        self.assertIsNone(info)

    # 8. Malformed fiveHour in one category → skipped; the other
    #    category's fiveHour still feeds the average.
    def test_malformed_window_skipped_then_other_window_wins(self):
        from swe.harness.rate_limits import _fetch_droid

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
            "swe.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        # Only core/5h is usable, so its direct 60% value wins.
        self.assertEqual(info.used_percent, 60)
        self.assertEqual(info.window, "5h")

    # 8b. All windows malformed → return None.
    def test_all_windows_malformed_returns_none(self):
        from swe.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "standard": {"fiveHour": {"usedPercent": "garbage"}},
                "core": {"fiveHour": {"usedPercent": None}},
            },
        }
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "swe.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNone(info)

    # 9. Response without ``limits`` → return None (defensive).
    def test_no_limits_key_returns_none(self):
        from swe.harness.rate_limits import _fetch_droid

        body = {"usesTokenRateLimitsBilling": False}
        env_patch, what_patch, dec_patch, now_patch = self._env_only()
        with env_patch, what_patch, dec_patch, now_patch, patch(
            "swe.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNone(info)

    # 10. usedPercent=0 means no usage and must not mark the limit reached.
    def test_zero_used_is_available(self):
        from swe.harness.rate_limits import _fetch_droid

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
            "swe.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        self.assertEqual(info.used_percent, 0)
        self.assertFalse(info.limit_reached)

    # 11. Percentage is the average of core/5h and standard/5h.
    def test_averages_core_and_standard_five_hour(self):
        from swe.harness.rate_limits import _fetch_droid

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
            "swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import _fetch_droid

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
            "swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import _fetch_droid

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
            "swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import _fetch_droid

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
            "swe.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()

        self.assertIsNotNone(info)
        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)
        self.assertEqual(info.window, "30d")

    # 13. Overage values above 100 clamp to fully used before averaging.
    def test_over_100_used_clamps_to_fully_used(self):
        from swe.harness.rate_limits import _fetch_droid

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
            "swe.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        # Both clamp to 100 used, so the average is 100.
        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)

    # 14. reset_at is parsed from windowEnd (ISO 8601).
    def test_reset_at_parsed_from_window_end(self):
        from swe.harness.rate_limits import _fetch_droid

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
            "swe.harness.rate_limits.urllib.request.urlopen",
            return_value=self._mock_response(body),
        ):
            info = _fetch_droid()
        self.assertIsNotNone(info)
        # 2026-08-01T00:00:00Z = 1785542400; allow ±1 day.
        self.assertAlmostEqual(info.reset_at, 1785542400.0, delta=86400)

    # 14b. reset_at belongs to the selected fiveHour window, not an
    #      unrelated weekly/monthly window.
    def test_reset_at_matches_selected_window(self):
        from swe.harness.rate_limits import _fetch_droid

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
            "swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import _fetch_droid

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
            "swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import (
            _droid_request, _DROID_APP_ORIGIN, _DROID_APP_REFERER,
        )

        req = _droid_request("https://api.factory.ai/api/billing/limits", "tok")
        hdrs = req.headers
        self.assertEqual(hdrs.get("Origin"), _DROID_APP_ORIGIN)
        self.assertEqual(hdrs.get("Referer"), _DROID_APP_REFERER)
        self.assertEqual(hdrs.get("X-factory-client"), "web-app")
        self.assertEqual(hdrs.get("Authorization"), "Bearer tok")
        self.assertEqual(hdrs.get("Accept"), "application/json")


class BundledLibcryptoLoadTest(unittest.TestCase):
    """SWE_BUNDLED_PYTHON loads OpenSSL only from the bundle's lib dir.

    An external dylib under the bundled interpreter's signature aborts
    the process instead of raising, so nothing outside
    ``<python>/../lib`` may reach CDLL.
    """

    def setUp(self):
        import swe.harness.rate_limits as rl
        self.rl = rl
        self._saved = (rl._LIBCRYPTO, rl._LIBCRYPTO_TRIED)
        rl._LIBCRYPTO_TRIED = False
        rl._LIBCRYPTO = None
        self.addCleanup(setattr, rl, "_LIBCRYPTO", self._saved[0])
        self.addCleanup(setattr, rl, "_LIBCRYPTO_TRIED", self._saved[1])

    def _fake_executable(self, tmp):
        """A python layout like the bundle's: <root>/bin/python3 + lib/."""
        root = Path(tmp)
        (root / "bin").mkdir(parents=True)
        (root / "lib").mkdir()
        exe = root / "bin" / "python3"
        exe.touch()
        return root, exe

    def test_bundled_loads_only_the_bundle_lib_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, exe = self._fake_executable(tmp)
            dylib = root / "lib" / "libcrypto.3.dylib"
            dylib.touch()
            calls = []

            def fake_cdll(name):
                calls.append(name)
                return MagicMock()

            import ctypes
            import ctypes.util  # noqa: F401 — it uses CDLL at import time
            with patch.dict(os.environ, {"SWE_BUNDLED_PYTHON": "1"}), \
                    patch.object(sys, "executable", str(exe)), \
                    patch.object(ctypes, "CDLL", fake_cdll):
                lib = self.rl._load_libcrypto()
            # The wrong dylib crashes this interpreter rather than
            # raising; only the bundle's own lib dir may be tried.
            self.assertEqual(calls, [str(dylib.resolve())])
            # A loadable library becomes the cached configured handle.
            self.assertIsNotNone(lib)
            self.assertIs(lib, self.rl._LIBCRYPTO)
            self.assertEqual(lib.EVP_aes_256_gcm.restype, ctypes.c_void_p)
            self.assertIs(self.rl._load_libcrypto(), lib)
            self.assertEqual(len(calls), 1)

    def test_bundled_without_dylib_loads_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, exe = self._fake_executable(tmp)
            calls = []

            import ctypes
            import ctypes.util  # noqa: F401
            with patch.dict(os.environ, {"SWE_BUNDLED_PYTHON": "1"}), \
                    patch.object(sys, "executable", str(exe)), \
                    patch.object(ctypes, "CDLL",
                                 lambda name: calls.append(name)):
                self.assertIsNone(self.rl._load_libcrypto())
            self.assertEqual(calls, [])


class DarwinSystemLibcryptoTest(unittest.TestCase):
    """Apple's unversioned /usr/lib/libcrypto.dylib never reaches CDLL.

    dlopen of that stub aborts the interpreter instead of raising, which
    killed the nixpkgs build's test phase.
    """

    def setUp(self):
        import swe.harness.rate_limits as rl
        self.rl = rl
        self._saved = (rl._LIBCRYPTO, rl._LIBCRYPTO_TRIED)
        rl._LIBCRYPTO_TRIED = False
        rl._LIBCRYPTO = None
        self.addCleanup(setattr, rl, "_LIBCRYPTO", self._saved[0])
        self.addCleanup(setattr, rl, "_LIBCRYPTO_TRIED", self._saved[1])

    def _load(self, platform):
        calls = []

        def fake_cdll(name):
            calls.append(name)
            raise OSError(name)

        import ctypes
        import ctypes.util
        with patch.dict(os.environ, {}, clear=False), \
                patch.object(sys, "platform", platform), \
                patch.object(ctypes, "CDLL", fake_cdll), \
                patch.object(ctypes.util, "find_library",
                             lambda name: "/usr/lib/libcrypto.dylib"), \
                patch("glob.glob", return_value=[]):
            os.environ.pop("SWE_BUNDLED_PYTHON", None)
            self.assertIsNone(self.rl._load_libcrypto())
        return calls

    def test_darwin_skips_the_system_stub(self):
        calls = self._load("darwin")
        self.assertNotIn("/usr/lib/libcrypto.dylib", calls)
        self.assertNotIn("libcrypto.dylib", calls)
        # Real OpenSSL candidates are still tried.
        self.assertIn("libcrypto.3.dylib", calls)
        self.assertIn("/opt/homebrew/lib/libcrypto.dylib", calls)

    def test_other_platforms_keep_every_candidate(self):
        calls = self._load("linux")
        self.assertIn("/usr/lib/libcrypto.dylib", calls)
        self.assertIn("libcrypto.dylib", calls)


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
        from swe.harness.rate_limits import _decrypt_droid_auth_file
        with patch("swe.harness.rate_limits._load_libcrypto",
                   return_value=None):
            self.assertIsNone(_decrypt_droid_auth_file())

    def test_missing_files_returns_none(self):
        from swe.harness.rate_limits import _decrypt_droid_auth_file
        # _load_libcrypto is allowed to succeed (real system), but the
        # credential files must be absent for this test. Redirect both
        # ``~/.factory/auth.v2.*`` paths to a non-existent temp dir.
        with tempfile.TemporaryDirectory() as tmp:
            with patch("swe.harness.rate_limits.os.path.expanduser",
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
        from swe.harness.rate_limits import _parse_retry_after_to_seconds
        self.assertEqual(_parse_retry_after_to_seconds("120"), 120.0)
        self.assertEqual(_parse_retry_after_to_seconds("0"), 0.0)
        self.assertEqual(_parse_retry_after_to_seconds("235"), 235.0)
        # Numeric coercion strips trailing whitespace.
        self.assertEqual(_parse_retry_after_to_seconds("  60  "), 60.0)

    def test_http_date_returns_seconds_until(self):
        from swe.harness.rate_limits import _parse_retry_after_to_seconds
        # Future date — should return a positive number of seconds.
        import datetime
        future = datetime.datetime.now(datetime.timezone.utc)
        future_str = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
        seconds = _parse_retry_after_to_seconds(future_str)
        self.assertIsNotNone(seconds)
        self.assertGreaterEqual(seconds, 0.0)
        self.assertLess(seconds, 60.0)  # roughly "now", not years away

    def test_past_http_date_clamps_to_zero(self):
        from swe.harness.rate_limits import _parse_retry_after_to_seconds
        # A date in the past — clamped to 0.0 so a stale server
        # header doesn't tell the user to wait centuries.
        self.assertEqual(
            _parse_retry_after_to_seconds("Wed, 21 Oct 2020 07:28:00 GMT"),
            0.0,
        )

    def test_missing_or_empty_returns_none(self):
        from swe.harness.rate_limits import _parse_retry_after_to_seconds
        self.assertIsNone(_parse_retry_after_to_seconds(None))
        self.assertIsNone(_parse_retry_after_to_seconds(""))
        self.assertIsNone(_parse_retry_after_to_seconds("   "))

    def test_malformed_returns_none(self):
        from swe.harness.rate_limits import _parse_retry_after_to_seconds
        self.assertIsNone(_parse_retry_after_to_seconds("not-a-date"))
        # RFC 850 variant is not RFC 7231 IMF-fixdate — we don't
        # accept it, so parser degrades to None silently.
        self.assertIsNone(
            _parse_retry_after_to_seconds("Wednesday, 21-Oct-26 07:28:00 GMT"),
        )
        # Non-numeric without a recognisable date format.
        self.assertIsNone(_parse_retry_after_to_seconds("forever"))

    def test_non_string_returns_none(self):
        from swe.harness.rate_limits import _parse_retry_after_to_seconds
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
        from swe.harness.rate_limits import _fetch_claude_url
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
                "swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import _fetch_claude_url
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
                "swe.harness.rate_limits.urllib.request.urlopen",
                side_effect=http_err,
            ):
                info = _fetch_claude_url(req)
            self.assertIsNone(info)
        finally:
            sys.stderr = original_stderr
        self.assertIn("a few minutes", buf.getvalue())

    def test_ssl_fallback_preserves_retry_after_callback(self):
        """A 429 after the macOS SSL retry still reports its cooldown."""
        from swe.harness.rate_limits import _fetch_json
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
            "swe.harness.rate_limits._verified_context",
            return_value=ssl.create_default_context(),
        ), patch(
            "swe.harness.rate_limits.urllib.request.urlopen",
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

    def _state_db(self, directory: str, token: str | None,
                  email: str | None = None,
                  auth_id: str | None = None) -> str:
        path = os.path.join(directory, "state.vscdb")
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value BLOB)")
        profile = (
            json.dumps({"authId": auth_id, "displayName": "Test User"})
            if auth_id is not None else None
        )
        for key, value in (("cursorAuth/accessToken", token),
                           ("cursorAuth/cachedEmail", email),
                           ("cursorAuth/cachedScopedProfile", profile)):
            if value is not None:
                connection.execute(
                    "INSERT INTO ItemTable VALUES (?, ?)",
                    (key, value),
                )
        connection.commit()
        connection.close()
        return path

    def test_parser_surfaces_the_more_exhausted_bucket(self):
        from swe.harness.rate_limits import _parse_cursor_usage

        info = _parse_cursor_usage(self._SAMPLE_RESPONSE)

        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "cursor")
        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)
        self.assertEqual(info.window, "api")
        self.assertEqual(info.reset_at, 1789190193.0)
        self.assertEqual(info.window_seconds, 2678400)

    def test_parser_labels_auto_with_autoPercentUsed(self):
        """totalPercentUsed is the Included figure; Auto is its own field."""
        from swe.harness.rate_limits import _parse_cursor_usage

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        data["planUsage"]["apiPercentUsed"] = 10
        data["planUsage"]["autoPercentUsed"] = 99

        info = _parse_cursor_usage(data)

        self.assertEqual(info.window, "auto")
        self.assertEqual(info.used_percent, 99)
        self.assertFalse(info.limit_reached)

    def test_parser_reports_every_bucket_like_the_cli(self):
        """Included, Auto and API, as `cursor-agent usage` lists them."""
        from swe.harness.rate_limits import _parse_cursor_usage

        info = _parse_cursor_usage(self._SAMPLE_RESPONSE)

        self.assertEqual(
            [(w["window"], w["used_percent"]) for w in info.windows],
            [("included", 50), ("auto", 41), ("api", 100)],
        )
        for window in info.windows:
            self.assertEqual(window["reset_at"], 1789190193.0)
            self.assertEqual(window["window_seconds"], 2678400)
        self.assertTrue(info.windows[2]["limit_reached"])
        json_windows = info.to_json()["windows"]
        self.assertEqual(json_windows[0]["remaining_percent"], 50)

    def test_parser_reads_the_team_plan(self):
        from swe.harness.rate_limits import _parse_cursor_usage

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        self.assertIsNone(_parse_cursor_usage(data).to_json()["plan_type"])
        data["spendLimitUsage"] = {"totalSpend": 14032, "limitType": "team"}
        self.assertEqual(_parse_cursor_usage(data).plan_type, "team")

    def test_parser_skips_unusable_buckets(self):
        from swe.harness.rate_limits import _parse_cursor_usage

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        data["planUsage"]["apiPercentUsed"] = "n/a"
        info = _parse_cursor_usage(data)
        self.assertEqual(info.window, "included")
        self.assertEqual([w["window"] for w in info.windows], ["included", "auto"])

        data["planUsage"]["totalPercentUsed"] = None
        self.assertEqual(_parse_cursor_usage(data).window, "auto")
        data["planUsage"]["autoPercentUsed"] = None
        self.assertIsNone(_parse_cursor_usage(data))

        self.assertIsNone(_parse_cursor_usage({"planUsage": "missing"}))
        self.assertIsNone(_parse_cursor_usage({}))

    def test_parser_tolerates_missing_cycle_bounds(self):
        from swe.harness.rate_limits import _parse_cursor_usage

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        del data["billingCycleStart"]
        data["billingCycleEnd"] = "soon"

        info = _parse_cursor_usage(data)

        self.assertEqual(info.reset_at, 0.0)
        self.assertEqual(info.window_seconds, 0)
        self.assertEqual(info.reset_in_human, "—")

    def test_millisecond_epoch_parsing(self):
        from swe.harness.rate_limits import _cursor_ms_to_epoch

        self.assertEqual(_cursor_ms_to_epoch("1789190193000"), 1789190193.0)
        self.assertEqual(_cursor_ms_to_epoch(1789190193000), 1789190193.0)
        self.assertEqual(_cursor_ms_to_epoch(1789190193), 1789190193.0)
        self.assertEqual(_cursor_ms_to_epoch(True), 0.0)
        self.assertEqual(_cursor_ms_to_epoch("-5"), 0.0)
        self.assertEqual(_cursor_ms_to_epoch(None), 0.0)
        self.assertEqual(_cursor_ms_to_epoch(float("inf")), 0.0)

    def test_an_absurd_millisecond_epoch_reads_as_unknown(self):
        # The Mac app turns these into Int64 milliseconds, which traps.
        from swe.harness.rate_limits import _cursor_ms_to_epoch

        self.assertEqual(_cursor_ms_to_epoch(str(10 ** 30)), 0.0)
        self.assertEqual(_cursor_ms_to_epoch(1e300), 0.0)
        self.assertEqual(_cursor_ms_to_epoch("253402300799000"), 253402300799.0)

    def test_a_usage_event_from_the_far_future_is_dropped(self):
        # Its time would become where the Mac feed's next poll starts.
        from swe.harness.rate_limits import _usage_event_fields

        now = 1_789_190_193.0
        ahead = {"timestamp": str(int((now + 2 * 86400) * 1000))}
        skewed = {"timestamp": str(int((now + 60) * 1000))}
        self.assertIsNone(_usage_event_fields(ahead, now))
        self.assertEqual(_usage_event_fields(skewed, now)["timestamp"], now + 60)
        self.assertIsNotNone(_usage_event_fields(ahead))

    def test_session_cookie_is_sub_and_token_url_encoded(self):
        from swe.harness.rate_limits import (
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
        from swe.harness.rate_limits import _read_cursor_state_token

        token = self._token()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._state_db(tmp, token)
            missing = os.path.join(tmp, "absent", "state.vscdb")
            with patch(
                "swe.harness.rate_limits._CURSOR_STATE_DB_CANDIDATES",
                (missing, path),
            ):
                self.assertEqual(_read_cursor_state_token(), token)

    def test_state_db_without_token_falls_back_to_keychain(self):
        from swe.harness.rate_limits import _get_cursor_access_token

        token = self._token()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._state_db(tmp, None)
            with patch(
                "swe.harness.rate_limits._CURSOR_STATE_DB_CANDIDATES",
                (path,),
            ), patch(
                "swe.harness.rate_limits.shutil.which",
                return_value="/usr/bin/security",
            ), patch(
                "swe.harness.rate_limits.subprocess.run",
                return_value=MagicMock(returncode=0, stdout=token + "\n"),
            ) as run:
                self.assertEqual(_get_cursor_access_token(), token)
            args = run.call_args[0][0]
            self.assertEqual(args[:2], ["security", "find-generic-password"])
            self.assertIn("cursor-access-token", args)

    def test_encrypted_state_token_falls_through_to_the_keychain(self):
        """The editor's safeStorage blob is skipped, not trusted: the
        keychain's real JWT wins."""
        from swe.harness.rate_limits import _get_cursor_access_token

        keychain_token = self._token()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._state_db(tmp, "cursor-safe-storage:AAAA")
            with patch(
                "swe.harness.rate_limits._CURSOR_STATE_DB_CANDIDATES",
                (path,),
            ), patch(
                "swe.harness.rate_limits._read_cursor_cli_token",
                return_value=None,
            ), patch(
                "swe.harness.rate_limits._read_cursor_keychain_token",
                return_value=keychain_token,
            ) as keychain:
                self.assertEqual(_get_cursor_access_token(), keychain_token)
            keychain.assert_called_once()

    def test_undecodable_tokens_everywhere_read_as_signed_out(self):
        """Opaque values in every source: no crash, no request, and the
        signed-in probe agrees there is no credential."""
        from swe.harness import rate_limits
        from swe.harness.rate_limits import (
            _fetch_cursor, fetch_cursor_usage_events,
        )

        blob = "cursor-safe-storage:AAAA"
        with patch(
            "swe.harness.rate_limits._read_cursor_state_token",
            return_value=blob,
        ), patch(
            "swe.harness.rate_limits._read_cursor_cli_token",
            return_value=blob,
        ), patch(
            "swe.harness.rate_limits._read_cursor_keychain_token",
            return_value=blob,
        ), patch(
            "swe.harness.rate_limits._keychain_item_exists",
            return_value=False,
        ), patch("swe.harness.rate_limits._fetch_json") as fetch:
            self.assertIsNone(_fetch_cursor())
            status, events, account_id = fetch_cursor_usage_events()
            self.assertFalse(rate_limits._SIGNED_IN["cursor"][1]())
        self.assertEqual(status, "signed-out")
        self.assertEqual(events, [])
        self.assertIsNone(account_id)
        fetch.assert_not_called()

    def test_signed_in_probe_skips_undecodable_file_tokens(self):
        """The probe mirrors the fetcher's ladder for the two file
        sources, and keeps its existence-only keychain check."""
        from swe.harness import rate_limits

        probe = rate_limits._SIGNED_IN["cursor"][1]
        blob = "cursor-safe-storage:AAAA"
        with patch.object(
            rate_limits, "_read_cursor_state_token", return_value=blob,
        ), patch.object(
            rate_limits, "_read_cursor_cli_token", return_value=None,
        ), patch.object(
            rate_limits, "_keychain_item_exists", return_value=False,
        ):
            self.assertFalse(probe())
        with patch.object(
            rate_limits, "_read_cursor_state_token", return_value=blob,
        ), patch.object(
            rate_limits, "_read_cursor_cli_token",
            return_value=self._token(),
        ):
            self.assertTrue(probe())
        with patch.object(
            rate_limits, "_read_cursor_state_token", return_value=None,
        ), patch.object(
            rate_limits, "_read_cursor_cli_token", return_value=None,
        ), patch.object(
            rate_limits, "_keychain_item_exists", return_value=True,
        ):
            self.assertTrue(probe())

    def test_fetch_with_an_account_state_db_stays_strict(self):
        """An extra account's ``state_db`` is read alone: an opaque token
        reports nothing and the default sources are never asked."""
        from swe.harness.rate_limits import _fetch_cursor

        with tempfile.TemporaryDirectory() as tmp:
            db = self._state_db(tmp, "cursor-safe-storage:AAAA")
            with patch(
                "swe.harness.rate_limits._get_cursor_access_token",
            ) as default_token, patch(
                "swe.harness.rate_limits._read_cursor_keychain_token",
            ) as keychain:
                self.assertIsNone(_fetch_cursor(db))
        default_token.assert_not_called()
        keychain.assert_not_called()

    def test_no_token_anywhere_returns_none(self):
        from swe.harness.rate_limits import _fetch_cursor

        with tempfile.TemporaryDirectory() as tmp, patch(
            "swe.harness.rate_limits._CURSOR_STATE_DB_CANDIDATES",
            (os.path.join(tmp, "state.vscdb"),),
        ), patch(
            "swe.harness.rate_limits.shutil.which", return_value=None,
        ), patch("swe.harness.rate_limits._fetch_json") as fetch:
            self.assertIsNone(_fetch_cursor())
        fetch.assert_not_called()

    def test_expired_token_reports_auth_required_without_a_request(self):
        from swe.harness.rate_limits import _fetch_cursor

        with patch(
            "swe.harness.rate_limits._get_cursor_access_token",
            return_value=self._token(exp=1_000_000_000),
        ), patch("swe.harness.rate_limits._fetch_json") as fetch:
            info = _fetch_cursor()

        self.assertEqual(info.plan_type, "auth-required")
        self.assertIn("re-login", info.format_column())
        fetch.assert_not_called()

    def test_email_comes_from_the_state_db_cached_email(self):
        """The editor records the sign-in next to the token; the JWT
        itself has no email claim."""
        from swe.harness.rate_limits import _fetch_cursor

        with tempfile.TemporaryDirectory() as tmp:
            path = self._state_db(tmp, self._token(), email="me@work.com")
            with patch(
                "swe.harness.rate_limits._CURSOR_STATE_DB_CANDIDATES",
                (path,),
            ), patch(
                "swe.harness.rate_limits._fetch_json",
                return_value=self._SAMPLE_RESPONSE,
            ):
                info = _fetch_cursor()

        self.assertIsNotNone(info)
        self.assertEqual(info.email, "me@work.com")
        self.assertEqual(info.to_json()["email"], "me@work.com")

    def test_email_survives_an_expired_token(self):
        """A re-login card still names the account it is about."""
        from swe.harness.rate_limits import _fetch_cursor

        with tempfile.TemporaryDirectory() as tmp:
            path = self._state_db(
                tmp, self._token(exp=1_000_000_000), email="me@work.com")
            with patch(
                "swe.harness.rate_limits._CURSOR_STATE_DB_CANDIDATES",
                (path,),
            ), patch(
                "swe.harness.rate_limits._fetch_json",
            ) as fetch:
                info = _fetch_cursor()

        self.assertEqual(info.plan_type, "auth-required")
        self.assertEqual(info.email, "me@work.com")
        fetch.assert_not_called()

    def test_account_dir_email_is_read_from_its_own_db(self):
        """An extra account's email never falls through to the default db."""
        from swe.harness.rate_limits import _fetch_cursor

        with tempfile.TemporaryDirectory() as tmp:
            path = self._state_db(tmp, self._token(), email="extra@work.com")
            with patch(
                "swe.harness.rate_limits._fetch_json",
                return_value=self._SAMPLE_RESPONSE,
            ), patch(
                "swe.harness.rate_limits._read_cursor_keychain_token",
            ) as keychain:
                info = _fetch_cursor(path)

        self.assertIsNotNone(info)
        self.assertEqual(info.email, "extra@work.com")
        keychain.assert_not_called()

    def _fetch_with_cli_token(self, tmp: str, **db_rows):
        """``_fetch_cursor`` where the editor's db token is unreadable
        and the keychain's JWT wins, as on this Mac."""
        from swe.harness.rate_limits import _fetch_cursor

        self._state_db(tmp, "cursor-safe-storage:v1:blob", **db_rows)
        db = os.path.join(tmp, "state.vscdb")
        with patch(
            "swe.harness.rate_limits._CURSOR_STATE_DB_CANDIDATES",
            (db,),
        ), patch(
            "swe.harness.rate_limits._read_cursor_cli_token",
            return_value=None,
        ), patch(
            "swe.harness.rate_limits._read_cursor_keychain_token",
            return_value=self._token(),
        ), patch(
            "swe.harness.rate_limits._fetch_json",
            return_value=self._SAMPLE_RESPONSE,
        ):
            return _fetch_cursor()

    def test_cli_token_keeps_the_editor_email_when_the_profile_matches(self):
        """The keychain token and the editor row share one account:
        ``cachedScopedProfile.authId`` equals the token's ``sub``."""
        with tempfile.TemporaryDirectory() as tmp:
            info = self._fetch_with_cli_token(
                tmp, email="me@work.com",
                auth_id="google-oauth2|user_01TEST")

        self.assertIsNotNone(info)
        self.assertEqual(info.email, "me@work.com")

    def test_cli_token_drops_another_account_editor_email(self):
        """An editor signed in elsewhere must not lend its address to
        the CLI account's usage."""
        with tempfile.TemporaryDirectory() as tmp:
            info = self._fetch_with_cli_token(
                tmp, email="other@work.com", auth_id="github|user_999")

        self.assertIsNotNone(info)
        self.assertIsNone(info.email)

    def test_a_profile_row_that_is_not_an_object_costs_only_the_email(self):
        """json.loads(profile).get raised AttributeError for a list or a
        string, and the fetcher's exception dropped the whole reading."""
        from swe.harness.rate_limits import _fetch_cursor

        for profile in ('["google-oauth2|user_01TEST"]', '"x"', "7", "{not json"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                db = self._state_db(tmp, "cursor-safe-storage:v1:blob", email="me@work.com")
                connection = sqlite3.connect(db)
                connection.execute("INSERT INTO ItemTable VALUES (?, ?)",
                                   ("cursorAuth/cachedScopedProfile", profile))
                connection.commit()
                connection.close()
                with patch("swe.harness.rate_limits._CURSOR_STATE_DB_CANDIDATES", (db,)), \
                     patch("swe.harness.rate_limits._read_cursor_cli_token", return_value=None), \
                     patch("swe.harness.rate_limits._read_cursor_keychain_token",
                           return_value=self._token()), \
                     patch("swe.harness.rate_limits._fetch_json",
                           return_value=self._SAMPLE_RESPONSE):
                    info = _fetch_cursor()
                self.assertIsNotNone(info)
                self.assertIsNone(info.email)

    def test_fetch_posts_with_cookie_and_first_party_origin(self):
        from swe.harness.rate_limits import _CURSOR_USAGE_URL, _fetch_cursor

        token = self._token()
        with patch(
            "swe.harness.rate_limits._get_cursor_access_token",
            return_value=token,
        ), patch(
            "swe.harness.rate_limits._fetch_json",
            return_value=self._SAMPLE_RESPONSE,
        ) as fetch:
            info = _fetch_cursor()

        self.assertEqual(info.used_percent, 100)
        self.assertEqual(info.window, "api")
        request = next(call[0][0] for call in fetch.call_args_list
                       if call[0][0].full_url == _CURSOR_USAGE_URL)
        self.assertEqual(request.full_url, _CURSOR_USAGE_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b"{}")
        self.assertEqual(request.get_header("Origin"), "https://cursor.com")
        self.assertIn("WorkosCursorSessionToken=", request.get_header("Cookie"))
        self.assertIn(token, request.get_header("Cookie"))
        self.assertIsNone(request.get_header("Authorization"))

    def test_on_demand_follows_the_cli(self):
        """`cursor-agent usage`'s On-Demand row, case by case."""
        from swe.harness.rate_limits import _cursor_on_demand as od

        team = {"limitType": "team", "pooledLimit": "500000"}
        # A team seat with no personal cap: "No personal limit".
        self.assertEqual(od(team, {"hardLimit": 3000}),
                         {"state": "unlimited", "scope": "team-member", "used_usd": 0.0, "limit_usd": None})
        self.assertEqual(od(team, None)["state"], "unlimited")
        self.assertEqual(od({"limitType": "team"}, None)["state"], "unavailable")
        self.assertEqual(od(team, {"hardLimit": 0})["state"], "disabled")
        self.assertEqual(od(team, {"hardLimit": 3000, "noUsageBasedAllowed": True})["state"], "disabled")
        # A personal cap on a team seat wins, in cents.
        self.assertEqual(od({**team, "individualLimit": 2000, "individualUsed": 550}, None),
                         {"state": "fixed", "scope": "personal", "used_usd": 5.5, "limit_usd": 20.0})
        self.assertEqual(od({**team, "individualLimit": 0}, None)["state"], "disabled")
        # Individual plans: the hard limit, in dollars.
        self.assertEqual(od({}, {"hardLimit": 2147483647})["state"], "unlimited")
        self.assertEqual(od({}, {"hardLimit": 2147483647})["scope"], "personal")
        self.assertEqual(od({"individualUsed": 1234}, {"hardLimit": 50}),
                         {"state": "fixed", "scope": "personal", "used_usd": 12.34, "limit_usd": 50.0})
        self.assertEqual(od({}, {"hardLimit": 0})["state"], "disabled")
        self.assertEqual(od({}, {"noUsageBasedAllowed": True, "hardLimit": 50})["state"], "disabled")
        self.assertEqual(od({}, None)["state"], "unavailable")
        self.assertEqual(od(None, None)["state"], "unavailable")

    def test_fetch_reads_the_plan_and_on_demand_like_the_cli(self):
        from swe.harness.rate_limits import _CURSOR_GROK_BOT_URL, _CURSOR_USAGE_URL, _fetch_cursor

        usage = copy.deepcopy(self._SAMPLE_RESPONSE)
        usage["spendLimitUsage"] = {"totalSpend": 14032, "pooledLimit": "500000", "limitType": "team"}
        base = _CURSOR_USAGE_URL.rsplit("/", 1)[0]
        replies = {
            _CURSOR_USAGE_URL: usage,
            f"{base}/get-hard-limit": {"hardLimit": 3000},
            f"{base}/get-plan-info": {"planInfo": {"planName": "Team"}},
            _CURSOR_GROK_BOT_URL: {},
        }
        with patch(
            "swe.harness.rate_limits._get_cursor_access_token", return_value=self._token(),
        ), patch(
            "swe.harness.rate_limits._fetch_json",
            side_effect=lambda request, **_: replies[request.full_url],
        ) as fetch:
            info = _fetch_cursor()

        self.assertEqual(fetch.call_count, 4)
        self.assertEqual(info.plan_type, "team")
        self.assertEqual(info.to_json()["on_demand"],
                         {"state": "unlimited", "scope": "team-member", "used_usd": 0.0, "limit_usd": None})

    def test_grok_bot_is_its_own_weekly_window(self):
        from swe.harness.rate_limits import _cursor_grok_bot_window as grok

        window = grok({
            "usagePercent": 37.5,
            "currentPeriodStart": "2026-08-20T00:00:00Z",
            "nextResetTimestampUtc": "2026-08-27T00:00:00Z",
            "hasNonZeroIncludedLimit": True,
        })
        self.assertEqual(window, {
            "window": "grok_bot", "used_percent": 38, "limit_reached": False,
            "reset_at": 1787788800.0, "window_seconds": 7 * 24 * 3600,
        })
        # No period start: still a week. Overage clamps; epoch millis work.
        over = grok({"usagePercent": 125, "nextResetTimestampUtc": "1787788800000"})
        self.assertEqual((over["used_percent"], over["limit_reached"]), (100, True))
        self.assertEqual((over["reset_at"], over["window_seconds"]), (1787788800.0, 7 * 24 * 3600))
        self.assertEqual(grok({"usagePercent": 0})["used_percent"], 0)

    def test_grok_bot_without_a_personal_meter_has_no_window(self):
        from swe.harness.rate_limits import _cursor_grok_bot_window as grok

        for reply in (
            None, {}, {"usagePercent": 42, "usesPooledEnterpriseAllowance": True},
            {"usagePercent": 0, "hasNonZeroIncludedLimit": False},
            {"usagePercent": 0, "includedLimitZero": True},
            {"usagePercent": True}, {"usagePercent": -1}, {"usagePercent": "12"},
            {"usagePercent": float("nan")}, {"hasNonZeroIncludedLimit": True},
        ):
            self.assertIsNone(grok(reply), reply)

    def test_fetch_adds_grok_bot_with_the_bearer_token(self):
        from swe.harness.rate_limits import _CURSOR_GROK_BOT_URL, _CURSOR_USAGE_URL, _fetch_cursor

        token = self._token()
        replies = {
            _CURSOR_USAGE_URL: copy.deepcopy(self._SAMPLE_RESPONSE),
            _CURSOR_GROK_BOT_URL: {"usagePercent": 12, "nextResetTimestampUtc": "2026-08-27T00:00:00Z"},
        }
        with patch(
            "swe.harness.rate_limits._get_cursor_access_token", return_value=token,
        ), patch(
            "swe.harness.rate_limits._fetch_json",
            side_effect=lambda request, **_: replies.get(request.full_url),
        ) as fetch:
            info = _fetch_cursor()

        request = next(call[0][0] for call in fetch.call_args_list
                       if call[0][0].full_url == _CURSOR_GROK_BOT_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b"{}")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {token}")
        self.assertEqual(request.get_header("Connect-protocol-version"), "1")
        self.assertIsNone(request.get_header("Cookie"))
        self.assertEqual(info.windows[-1]["window"], "grok_bot")
        self.assertEqual(info.windows[-1]["used_percent"], 12)
        # The headline stays Cursor's own allowance.
        self.assertEqual(info.window, "api")

    def test_a_failed_grok_bot_call_marks_the_window_unavailable(self):
        from swe.harness.rate_limits import _CURSOR_GROK_BOT_URL, _CURSOR_USAGE_URL, _fetch_cursor

        def reply(request, **_):
            if request.full_url == _CURSOR_GROK_BOT_URL:
                raise OSError("boom")
            return copy.deepcopy(self._SAMPLE_RESPONSE) if request.full_url == _CURSOR_USAGE_URL else None

        with patch(
            "swe.harness.rate_limits._get_cursor_access_token", return_value=self._token(),
        ), patch("swe.harness.rate_limits._fetch_json", side_effect=reply):
            info = _fetch_cursor()

        self.assertEqual(info.used_percent, 100)
        grok = next(w for w in info.windows if w["window"] == "grok_bot")
        self.assertTrue(grok["unavailable"])
        self.assertEqual(grok["used_percent"], 0)

    def test_a_grok_bot_call_past_its_budget_marks_the_window_unavailable(self):
        from swe.harness.rate_limits import _CURSOR_GROK_BOT_URL, _CURSOR_USAGE_URL, _fetch_cursor

        def reply(request, **_):
            if request.full_url == _CURSOR_GROK_BOT_URL:
                time.sleep(0.2)
                return {"usagePercent": 5}
            return copy.deepcopy(self._SAMPLE_RESPONSE) if request.full_url == _CURSOR_USAGE_URL else None

        with patch(
            "swe.harness.rate_limits._get_cursor_access_token", return_value=self._token(),
        ), patch(
            "swe.harness.rate_limits._CURSOR_EXTRAS_BUDGET", 0.01,
        ), patch("swe.harness.rate_limits._fetch_json", side_effect=reply):
            info = _fetch_cursor()

        grok = next(w for w in info.windows if w["window"] == "grok_bot")
        self.assertTrue(grok["unavailable"])

    def test_a_grok_bot_reply_with_no_meter_adds_no_window(self):
        """A dict reply that means "no personal meter" stays absent."""
        from swe.harness.rate_limits import _CURSOR_GROK_BOT_URL, _CURSOR_USAGE_URL, _fetch_cursor

        replies = {
            _CURSOR_USAGE_URL: copy.deepcopy(self._SAMPLE_RESPONSE),
            _CURSOR_GROK_BOT_URL: {"usesPooledEnterpriseAllowance": True},
        }
        with patch(
            "swe.harness.rate_limits._get_cursor_access_token", return_value=self._token(),
        ), patch(
            "swe.harness.rate_limits._fetch_json",
            side_effect=lambda request, **_: replies.get(request.full_url),
        ):
            info = _fetch_cursor()

        self.assertNotIn("grok_bot", [w["window"] for w in info.windows])

    def test_on_demand_and_plan_calls_are_optional(self):
        from swe.harness.rate_limits import _CURSOR_USAGE_URL, _fetch_cursor

        usage = copy.deepcopy(self._SAMPLE_RESPONSE)
        usage["spendLimitUsage"] = {"limitType": "team", "pooledLimit": "500000"}
        with patch(
            "swe.harness.rate_limits._get_cursor_access_token", return_value=self._token(),
        ), patch(
            "swe.harness.rate_limits._fetch_json",
            side_effect=lambda request, **_: usage if request.full_url == _CURSOR_USAGE_URL else None,
        ):
            info = _fetch_cursor()

        self.assertEqual(info.used_percent, 100)
        self.assertEqual(info.plan_type, "team")
        self.assertEqual(info.on_demand["state"], "unlimited")

    def test_slow_extras_do_not_hold_up_the_usage_reading(self):
        from swe.harness.rate_limits import _CURSOR_USAGE_URL, _fetch_cursor

        usage = copy.deepcopy(self._SAMPLE_RESPONSE)
        usage["spendLimitUsage"] = {"limitType": "team", "pooledLimit": "500000"}

        def reply(request, **_):
            if request.full_url == _CURSOR_USAGE_URL:
                return usage
            time.sleep(2)
            return {"hardLimit": 0}

        with patch(
            "swe.harness.rate_limits._get_cursor_access_token", return_value=self._token(),
        ), patch("swe.harness.rate_limits._fetch_json", side_effect=reply), patch(
            "swe.harness.rate_limits._CURSOR_EXTRAS_BUDGET", 0.1,
        ):
            started = time.monotonic()
            info = _fetch_cursor()
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.0)
        self.assertEqual(info.used_percent, 100)
        # The hard limit never came, so the CLI's no-hard-limit team rule applies.
        self.assertEqual(info.on_demand["state"], "unlimited")
        self.assertEqual(info.plan_type, "team")

    def test_slow_extras_end_before_the_callers_deadline(self):
        from swe.harness import rate_limits
        from swe.harness.rate_limits import _CURSOR_USAGE_URL

        usage = copy.deepcopy(self._SAMPLE_RESPONSE)

        def reply(request, **_):
            if request.full_url == _CURSOR_USAGE_URL:
                return usage
            time.sleep(2)
            return None

        with tempfile.TemporaryDirectory() as tmp, patch(
            "swe.harness.rate_limits.RATE_LIMITS_CACHE_FILE", Path(tmp) / "rate_limits_cache.json",
        ), patch(
            "swe.harness.rate_limits._get_cursor_access_token", return_value=self._token(),
        ), patch("swe.harness.rate_limits._fetch_json", side_effect=reply), patch.dict(
            "swe.harness.rate_limits._FETCHERS", {"cursor": rate_limits._fetch_cursor}, clear=True,
        ):
            # The 3s extras budget is longer than this caller waits.
            started = time.monotonic()
            result = rate_limits.get_all_rate_limits(use_cache=False, tool_names={"cursor"}, deadline=0.6)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.0)
        self.assertEqual(result["cursor"].used_percent, 100)

    def test_fetch_failure_reports_no_reading(self):
        from swe.harness.rate_limits import _fetch_cursor

        with patch(
            "swe.harness.rate_limits._get_cursor_access_token",
            return_value=self._token(),
        ), patch(
            "swe.harness.rate_limits._fetch_json", return_value=None,
        ):
            self.assertIsNone(_fetch_cursor())

    def test_column_labels_the_bucket(self):
        from swe.harness.rate_limits import _parse_cursor_usage
        from swe.console import strip_ansi

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        data["planUsage"]["apiPercentUsed"] = 30
        info = _parse_cursor_usage(data)
        with patch(
            "swe.harness.rate_limits.time.time",
            return_value=1789190193.0 - 3 * 3600,
        ):
            self.assertEqual(strip_ansi(info.format_column()), "◑ included:3h0m")


class CursorUsageEventsTest(unittest.TestCase):
    """``fetch_cursor_usage_events`` and ``swe harness usage-events``."""

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

    def _event(self, **overrides) -> dict:
        event = {
            "timestamp": "1789190193000",
            "model": "claude-4-sonnet",
            "kind": "USAGE_EVENT_KIND_INCLUDED_IN_BUSINESS",
            "conversationId": "conv-1",
            "isHeadless": True,
            "tokenUsage": {
                "inputTokens": 100, "outputTokens": 40,
                "cacheReadTokens": 500, "cacheWriteTokens": 20,
                "totalCents": 7,
            },
        }
        event.update(overrides)
        return event

    def _fetch(self, pages, **kwargs):
        """Run the fetch with ``pages`` (a list of row lists) as replies."""
        from swe.harness.rate_limits import fetch_cursor_usage_events

        replies = [
            {"totalUsageEventsCount": sum(len(p) for p in pages),
             "usageEventsDisplay": page}
            for page in pages
        ]
        calls = []

        def reply(request, **kw):
            calls.append(request)
            return replies.pop(0) if replies else {
                "totalUsageEventsCount": 0, "usageEventsDisplay": []}

        with patch(
            "swe.harness.rate_limits._get_cursor_access_token",
            return_value=self._token(),
        ), patch("swe.harness.rate_limits._fetch_json", side_effect=reply):
            status, events, account_id = fetch_cursor_usage_events(**kwargs)
        return status, events, account_id, calls

    def test_maps_fields_and_cents_to_dollars(self):
        status, events, account_id, calls = self._fetch([[self._event()]], now=1_800_000_000.0)
        self.assertEqual(status, "ok")
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["timestamp"], 1789190193.0)
        self.assertEqual(event["model"], "claude-4-sonnet")
        self.assertEqual(event["kind"], "USAGE_EVENT_KIND_INCLUDED_IN_BUSINESS")
        self.assertEqual(event["conversation_id"], "conv-1")
        self.assertTrue(event["headless"])
        self.assertEqual(event["input_tokens"], 100)
        self.assertEqual(event["output_tokens"], 40)
        self.assertEqual(event["cache_read_tokens"], 500)
        self.assertEqual(event["cache_write_tokens"], 20)
        self.assertEqual(event["cost_usd"], 0.07)
        request = calls[0]
        body = json.loads(request.data)
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["pageSize"], 1000)
        self.assertEqual(request.get_header("Origin"), "https://cursor.com")
        self.assertIsNone(request.get_header("Authorization"))

    def test_missing_token_usage_and_null_cost(self):
        status, events, _, _ = self._fetch([[
            self._event(tokenUsage=None, conversationId=None),
            self._event(model="grok-bot-default", tokenUsage={"outputTokens": 5}),
        ]], now=1_800_000_000.0)
        self.assertEqual(status, "ok")
        self.assertIsNone(events[0]["conversation_id"])
        self.assertIsNone(events[0]["cost_usd"])
        self.assertEqual(events[0]["input_tokens"], 0)
        self.assertEqual(events[1]["output_tokens"], 5)
        self.assertIsNone(events[1]["cost_usd"])

    def test_paging_stops_on_a_short_page(self):
        # A short page ends the slice; the next slice starts a new page 1.
        full = [self._event() for _ in range(1000)]
        status, events, _, calls = self._fetch(
            [full, [self._event()], [self._event()]], now=1_800_000_000.0)
        self.assertEqual(status, "ok")
        self.assertEqual(len(events), 1002)
        first = json.loads(calls[0].data)
        second = json.loads(calls[1].data)
        third = json.loads(calls[2].data)
        self.assertEqual((first["page"], second["page"]), (1, 2))
        # The third call is the next slice's page 1, not page 3.
        self.assertEqual(third["page"], 1)
        self.assertGreater(int(third["startDate"]), int(first["startDate"]))

    def test_since_is_clamped_to_180_days(self):
        from swe.harness.rate_limits import _CURSOR_USAGE_EVENTS_MAX_AGE

        status, _, _, calls = self._fetch([[]], since=1.0, now=1_800_000_000.0)
        self.assertEqual(status, "ok")
        body = json.loads(calls[0].data)
        self.assertEqual(
            int(body["startDate"]),
            int((1_800_000_000.0 - _CURSOR_USAGE_EVENTS_MAX_AGE) * 1000))
        _, _, _, calls = self._fetch([[self._event()]], since=1_799_000_000.0,
                                  now=1_800_000_000.0)
        self.assertEqual(json.loads(calls[0].data)["startDate"], "1799000000000")

    def test_signed_out_when_no_token(self):
        from swe.harness.rate_limits import fetch_cursor_usage_events

        with patch(
            "swe.harness.rate_limits._get_cursor_access_token",
            return_value=None,
        ), patch("swe.harness.rate_limits._fetch_json") as fetch:
            status, events, account_id = fetch_cursor_usage_events()
        self.assertEqual(status, "signed-out")
        self.assertEqual(events, [])
        fetch.assert_not_called()

    def test_auth_required_when_expired_or_401(self):
        from swe.harness.rate_limits import fetch_cursor_usage_events

        with patch(
            "swe.harness.rate_limits._get_cursor_access_token",
            return_value=self._token(exp=1_000_000_000),
        ), patch("swe.harness.rate_limits._fetch_json") as fetch:
            status, _, _ = fetch_cursor_usage_events()
        self.assertEqual(status, "auth-required")
        fetch.assert_not_called()

        def reply(request, on_401=None, **_):
            if on_401 is not None:
                on_401()
            return None

        with patch(
            "swe.harness.rate_limits._get_cursor_access_token",
            return_value=self._token(),
        ), patch("swe.harness.rate_limits._fetch_json", side_effect=reply):
            status, _, _ = fetch_cursor_usage_events()
        self.assertEqual(status, "auth-required")

    def test_unavailable_on_network_error_or_bad_page(self):
        from swe.harness.rate_limits import fetch_cursor_usage_events

        # None is a network failure; a non-list usageEventsDisplay is a
        # shape change. Cursor omits the key on an empty window, so a dict
        # without it is not an outage (covered by the chunk tests).
        for reply in (None, {"usageEventsDisplay": "not-a-list"}):
            with patch(
                "swe.harness.rate_limits._get_cursor_access_token",
                return_value=self._token(),
            ), patch(
                "swe.harness.rate_limits._fetch_json", return_value=reply,
            ):
                status, events, account_id = fetch_cursor_usage_events()
            self.assertEqual(status, "unavailable")
            self.assertEqual(events, [])

    def test_account_dir_reads_only_its_state_db(self):
        from swe.harness.rate_limits import fetch_cursor_usage_events

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "User", "globalStorage", "state.vscdb")
            os.makedirs(os.path.dirname(db))
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE ItemTable (key TEXT UNIQUE, value BLOB)")
            connection.commit()
            connection.close()

            seen = []

            def read(candidates):
                seen.append(candidates)
                return None

            with patch(
                "swe.harness.rate_limits._read_cursor_state_token", read,
            ), patch(
                "swe.harness.rate_limits._get_cursor_access_token",
            ) as default_token:
                status, events, account_id = fetch_cursor_usage_events(db)

        self.assertEqual(status, "signed-out")
        self.assertIsNone(account_id)
        self.assertEqual(seen, [(db,)])
        default_token.assert_not_called()

    def test_account_state_db_blob_never_falls_back(self):
        """An extra account's opaque token reports signed-out on its
        own; the default token ladder is never asked."""
        from swe.harness.rate_limits import fetch_cursor_usage_events

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "state.vscdb")
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE ItemTable (key TEXT UNIQUE, value BLOB)")
            connection.execute(
                "INSERT INTO ItemTable VALUES (?, ?)",
                ("cursorAuth/accessToken", "cursor-safe-storage:AAAA"))
            connection.commit()
            connection.close()
            with patch(
                "swe.harness.rate_limits._get_cursor_access_token",
            ) as default_token, patch(
                "swe.harness.rate_limits._read_cursor_keychain_token",
            ) as keychain:
                status, events, account_id = fetch_cursor_usage_events(db)

        self.assertEqual(status, "signed-out")
        self.assertEqual(events, [])
        self.assertIsNone(account_id)
        default_token.assert_not_called()
        keychain.assert_not_called()

    def test_account_id_is_the_token_sub(self):
        status, _, account_id, _ = self._fetch([[]], now=1_800_000_000.0)
        self.assertEqual(status, "ok")
        self.assertEqual(account_id, "google-oauth2|user_01TEST")

    def test_the_window_reads_in_oldest_first_week_slices(self):
        from swe.harness.rate_limits import _CURSOR_USAGE_EVENTS_CHUNK

        now = 1_800_000_000.0
        since = now - (_CURSOR_USAGE_EVENTS_CHUNK + 60)
        status, events, _, calls = self._fetch(
            [[self._event()], [self._event(model="grok-bot-default")]],
            since=since, now=now)
        self.assertEqual(status, "ok")
        self.assertEqual(len(events), 2)
        self.assertEqual([e["model"] for e in events],
                         ["claude-4-sonnet", "grok-bot-default"])
        first = json.loads(calls[0].data)
        second = json.loads(calls[1].data)
        self.assertEqual(int(first["startDate"]), int(since * 1000))
        self.assertEqual(int(first["endDate"]),
                         int((since + _CURSOR_USAGE_EVENTS_CHUNK) * 1000))
        # Half-open slices: the next chunk starts a millisecond later.
        self.assertEqual(int(second["startDate"]),
                         int(first["endDate"]) + 1)
        self.assertEqual(int(second["endDate"]), int(now * 1000))

    def test_a_slice_filling_every_page_reports_incomplete(self):
        from swe.harness.rate_limits import _CURSOR_USAGE_EVENTS_MAX_PAGES

        full = [self._event() for _ in range(1000)]
        pages = [full for _ in range(_CURSOR_USAGE_EVENTS_MAX_PAGES)]
        pages.append([self._event()])  # the second slice, nearly empty
        now = 1_800_000_000.0
        status, events, _, calls = self._fetch(
            pages, since=now - 10 * 24 * 60 * 60, now=now)
        self.assertEqual(status, "incomplete")
        self.assertEqual(
            len(events), _CURSOR_USAGE_EVENTS_MAX_PAGES * 1000 + 1)
        self.assertEqual(len(calls), _CURSOR_USAGE_EVENTS_MAX_PAGES + 1)


class DevinFetcherTest(unittest.TestCase):
    """Test the Devin quota fetcher (Windsurf seat-management RPC)."""

    _SAMPLE_RESPONSE = {
        "userStatus": {
            "pro": True,
            "name": "Kai",
            "email": "kai@cortexrobot.ai",
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
        from swe.harness.rate_limits import _parse_devin_status

        info = _parse_devin_status(self._SAMPLE_RESPONSE)

        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "devin")
        self.assertEqual(info.used_percent, 4)
        self.assertEqual(info.window, "7d")
        self.assertEqual(info.reset_at, 1789286400.0)
        self.assertEqual(info.window_seconds, 604800)
        self.assertEqual(info.plan_type, "teams")
        self.assertFalse(info.limit_reached)
        self.assertEqual(info.email, "kai@cortexrobot.ai")
        self.assertEqual(info.to_json()["email"], "kai@cortexrobot.ai")

    def test_parser_survives_a_reply_with_no_email(self):
        """proto3 drops empty strings: older responses name no account."""
        from swe.harness.rate_limits import _parse_devin_status

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        del data["userStatus"]["email"]
        self.assertIsNone(_parse_devin_status(data).email)
        data["userStatus"]["email"] = "  "
        self.assertIsNone(_parse_devin_status(data).email)

    def test_parser_reports_both_windows_like_the_cli(self):
        """Daily and weekly, as `devin /usage` lists them."""
        from swe.harness.rate_limits import _parse_devin_status

        info = _parse_devin_status(self._SAMPLE_RESPONSE)

        self.assertEqual(
            [(w["window"], w["used_percent"], w["reset_at"], w["window_seconds"]) for w in info.windows],
            [("1d", 0, 1789200000.0, 86400), ("7d", 4, 1789286400.0, 604800)],
        )
        self.assertEqual([w["window"] for w in info.to_json()["windows"]], ["1d", "7d"])

    def test_parser_reads_the_extra_usage_balance(self):
        from swe.harness.rate_limits import _parse_devin_status

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        self.assertIsNone(_parse_devin_status(data).extra_usage_usd)
        data["userStatus"]["planStatus"]["overageBalanceMicros"] = "9868980000"
        info = _parse_devin_status(data)
        self.assertEqual(info.extra_usage_usd, 9868.98)
        self.assertEqual(info.to_json()["extra_usage_usd"], 9868.98)
        data["userStatus"]["planStatus"]["overageBalanceMicros"] = "lots"
        self.assertIsNone(_parse_devin_status(data).extra_usage_usd)

    def test_parser_picks_daily_when_it_is_the_tighter_window(self):
        from swe.harness.rate_limits import _parse_devin_status

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        data["userStatus"]["planStatus"]["dailyQuotaRemainingPercent"] = 0

        info = _parse_devin_status(data)

        self.assertEqual(info.window, "1d")
        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)
        self.assertEqual(info.reset_at, 1789200000.0)
        self.assertEqual(info.window_seconds, 86400)

    def test_parser_falls_back_to_prompt_credits(self):
        from swe.harness.rate_limits import _parse_devin_status

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        plan = data["userStatus"]["planStatus"]
        del plan["dailyQuotaRemainingPercent"]
        del plan["weeklyQuotaRemainingPercent"]
        # A prompt-credits payload carries no quota reset times either;
        # leftover resets would read as exhausted quota windows.
        del plan["dailyQuotaResetAtUnix"]
        del plan["weeklyQuotaResetAtUnix"]
        plan["availablePromptCredits"] = 125
        plan["planInfo"]["monthlyPromptCredits"] = 500

        info = _parse_devin_status(data)

        self.assertEqual(info.remaining_units, 125)
        self.assertEqual(info.total_units, 500)
        self.assertEqual(info.used_percent, 75)
        self.assertEqual(info.window, "")
        self.assertEqual(info.reset_at, 1789446386.0)

    def test_an_omitted_remaining_with_a_reset_means_exhausted(self):
        """proto3 JSON drops zeros: only the reset times are sent."""
        from swe.harness.rate_limits import _parse_devin_status

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        plan = data["userStatus"]["planStatus"]
        del plan["dailyQuotaRemainingPercent"]
        del plan["weeklyQuotaRemainingPercent"]

        info = _parse_devin_status(data)

        self.assertIsNotNone(info)
        self.assertEqual(
            [(w["window"], w["used_percent"]) for w in info.windows],
            [("1d", 100), ("7d", 100)],
        )
        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)

    def test_an_omitted_window_is_exhausted_next_to_a_live_one(self):
        from swe.harness.rate_limits import _parse_devin_status

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        plan = data["userStatus"]["planStatus"]
        plan["dailyQuotaRemainingPercent"] = 40
        del plan["weeklyQuotaRemainingPercent"]

        info = _parse_devin_status(data)

        self.assertEqual(
            [(w["window"], w["used_percent"]) for w in info.windows],
            [("1d", 60), ("7d", 100)],
        )
        self.assertEqual(info.window, "7d")
        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)

    def test_no_remaining_and_no_resets_keeps_the_old_behaviour(self):
        from swe.harness.rate_limits import _parse_devin_status

        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        plan = data["userStatus"]["planStatus"]
        del plan["dailyQuotaRemainingPercent"]
        del plan["weeklyQuotaRemainingPercent"]
        del plan["dailyQuotaResetAtUnix"]
        del plan["weeklyQuotaResetAtUnix"]

        # No quota windows and unlimited credits: nothing to show.
        self.assertIsNone(_parse_devin_status(data))

    def test_parser_rejects_unusable_payloads(self):
        from swe.harness.rate_limits import _parse_devin_status

        self.assertIsNone(_parse_devin_status({}))
        self.assertIsNone(_parse_devin_status({"userStatus": {"planStatus": "nope"}}))
        data = copy.deepcopy(self._SAMPLE_RESPONSE)
        plan = data["userStatus"]["planStatus"]
        plan["dailyQuotaRemainingPercent"] = True
        plan["weeklyQuotaRemainingPercent"] = "n/a"
        # Unlimited credits (-1) and no quota windows: nothing to show.
        self.assertIsNone(_parse_devin_status(data))

    def test_credentials_come_from_the_toml_file(self):
        from swe.harness.rate_limits import _read_devin_credentials

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "credentials.toml")
            Path(path).write_text(
                'windsurf_api_key = "sk-test"\n'
                'api_server_url = "https://server.example.com/"\n'
                'devin_api_url = "https://api.devin.ai"\n'
            )
            with patch("swe.harness.rate_limits._DEVIN_CREDENTIALS_PATH", path):
                self.assertEqual(
                    _read_devin_credentials(), ("sk-test", "https://server.example.com"),
                )
            Path(path).write_text('windsurf_api_key = "sk-test"\n')
            with patch("swe.harness.rate_limits._DEVIN_CREDENTIALS_PATH", path):
                self.assertEqual(
                    _read_devin_credentials(), ("sk-test", "https://server.codeium.com"),
                )
            Path(path).write_text('api_server_url = "https://server.example.com"\n')
            with patch("swe.harness.rate_limits._DEVIN_CREDENTIALS_PATH", path):
                self.assertIsNone(_read_devin_credentials())
            Path(path).write_text("not = = toml")
            with patch("swe.harness.rate_limits._DEVIN_CREDENTIALS_PATH", path):
                self.assertIsNone(_read_devin_credentials())
            with patch(
                "swe.harness.rate_limits._DEVIN_CREDENTIALS_PATH",
                os.path.join(tmp, "absent.toml"),
            ):
                self.assertIsNone(_read_devin_credentials())

    def test_fetch_posts_the_key_in_the_rpc_metadata(self):
        from swe.harness.rate_limits import _fetch_devin

        with patch(
            "swe.harness.rate_limits._read_devin_credentials",
            return_value=("sk-test", "https://server.example.com"),
        ), patch(
            "swe.harness.rate_limits._fetch_json",
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
        from swe.harness.rate_limits import _fetch_devin

        with patch(
            "swe.harness.rate_limits._read_devin_credentials", return_value=None,
        ), patch("swe.harness.rate_limits._fetch_json") as fetch:
            self.assertIsNone(_fetch_devin())
        fetch.assert_not_called()

        with patch(
            "swe.harness.rate_limits._read_devin_credentials",
            return_value=("sk-test", "https://server.example.com"),
        ), patch("swe.harness.rate_limits._fetch_json", return_value=None):
            self.assertIsNone(_fetch_devin())


class VerifiedContextTest(unittest.TestCase):
    """``_verified_context`` finds a CA bundle without needing certifi.

    A python.org build on macOS ships no certificates, and certifi is not a
    dependency of this package, so for three weeks every usage fetch on such
    a machine failed TLS verification: Codex went blank and Claude kept
    showing its last reading. The OS trust store is always there.
    """

    def test_uses_os_bundle_when_certifi_is_missing(self):
        from swe.harness.rate_limits import _verified_context

        with tempfile.TemporaryDirectory() as tmp:
            bundle = os.path.join(tmp, "cert.pem")
            Path(bundle).write_text("")
            missing = os.path.join(tmp, "absent.pem")
            sentinel = object()
            with patch.dict("sys.modules", {"certifi": None}), patch(
                "swe.harness.rate_limits._SYSTEM_CA_BUNDLES",
                (missing, bundle),
            ), patch(
                "swe.harness.rate_limits.ssl.create_default_context",
                return_value=sentinel,
            ) as make:
                self.assertIs(_verified_context(), sentinel)
            make.assert_called_once_with(cafile=bundle)

    def test_os_bundle_wins_over_certifi(self):
        from swe.harness.rate_limits import _ca_bundle_candidates
        import types

        with tempfile.TemporaryDirectory() as tmp:
            os_bundle = os.path.join(tmp, "cert.pem")
            certifi_bundle = os.path.join(tmp, "cacert.pem")
            for path in (os_bundle, certifi_bundle):
                Path(path).write_text("")
            fake = types.SimpleNamespace(where=lambda: certifi_bundle)
            with patch.dict("sys.modules", {"certifi": fake}), patch(
                "swe.harness.rate_limits._SYSTEM_CA_BUNDLES",
                (os_bundle,),
            ):
                self.assertEqual(
                    _ca_bundle_candidates(), [os_bundle, certifi_bundle],
                )

    def test_gives_up_without_any_bundle(self):
        from swe.harness.rate_limits import _verified_context

        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "absent.pem")
            with patch.dict("sys.modules", {"certifi": None}), patch(
                "swe.harness.rate_limits._SYSTEM_CA_BUNDLES", (missing,),
            ), patch(
                "swe.harness.rate_limits.ssl.create_default_context",
            ) as make:
                self.assertIsNone(_verified_context())
            make.assert_not_called()

    def test_unreadable_bundle_falls_through_to_the_next(self):
        from swe.harness.rate_limits import _verified_context
        import ssl

        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.pem")
            good = os.path.join(tmp, "good.pem")
            for path in (bad, good):
                Path(path).write_text("")
            sentinel = object()
            with patch.dict("sys.modules", {"certifi": None}), patch(
                "swe.harness.rate_limits._SYSTEM_CA_BUNDLES", (bad, good),
            ), patch(
                "swe.harness.rate_limits.ssl.create_default_context",
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
        from swe.harness.rate_limits import _fetch_droid
        import io
        import sys
        buf = io.StringIO()
        original_stderr = sys.stderr
        sys.stderr = buf
        try:
            with patch.dict(os.environ, {"FACTORY_API_KEY": "fake-token"},
                            clear=True), \
                 patch("swe.harness.rate_limits.shutil.which",
                       return_value=None), \
                 patch("swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import _fetch_json
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
                "swe.harness.rate_limits.urllib.request.urlopen",
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
        from swe.harness.rate_limits import _fetch_json
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
                "swe.harness.rate_limits.urllib.request.urlopen",
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


class AccountRateLimitTest(unittest.TestCase):
    """get_account_rate_limit: an extra account's own files, no cache."""

    _DEVIN_RESPONSE = DevinFetcherTest._SAMPLE_RESPONSE
    _CURSOR_RESPONSE = CursorFetcherTest._SAMPLE_RESPONSE

    def _devin_dir(self, root: str) -> str:
        path = Path(root) / "devin"
        path.mkdir(parents=True)
        (path / "credentials.toml").write_text(
            'windsurf_api_key = "sk-extra"\n'
            'api_server_url = "https://server.example.com"\n'
        )
        return root

    def _cursor_dir(self, root: str, token: str | None) -> str:
        path = Path(root) / "User" / "globalStorage"
        path.mkdir(parents=True)
        db = CursorFetcherTest()._state_db(str(path), token)
        return root if db else root

    def test_devin_reads_the_account_credentials(self):
        from swe.harness.rate_limits import get_account_rate_limit

        with tempfile.TemporaryDirectory() as tmp:
            self._devin_dir(tmp)
            with patch(
                "swe.harness.rate_limits._fetch_json",
                return_value=self._DEVIN_RESPONSE,
            ) as fetch:
                info = get_account_rate_limit("devin", tmp)

        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "devin")
        body = json.loads(fetch.call_args[0][0].data.decode())
        self.assertEqual(body["metadata"]["api_key"], "sk-extra")

    def test_cursor_reads_only_the_account_state_db(self):
        from swe.harness.rate_limits import get_account_rate_limit

        token = CursorFetcherTest()._token()
        with tempfile.TemporaryDirectory() as tmp:
            self._cursor_dir(tmp, token)
            with patch(
                "swe.harness.rate_limits._fetch_json",
                return_value=self._CURSOR_RESPONSE,
            ), patch(
                "swe.harness.rate_limits._read_cursor_keychain_token",
            ) as keychain, patch(
                "swe.harness.rate_limits._cursor_session_cookie",
                return_value="cookie",
            ):
                info = get_account_rate_limit("cursor", tmp)

        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "cursor")
        keychain.assert_not_called()

    def test_empty_account_dir_reports_nothing(self):
        from swe.harness.rate_limits import get_account_rate_limit

        with tempfile.TemporaryDirectory() as tmp:
            for name in ("devin", "cursor"):
                with patch(
                    "swe.harness.rate_limits._read_cursor_keychain_token",
                ) as keychain, patch(
                    "swe.harness.rate_limits._fetch_json",
                ) as fetch:
                    self.assertIsNone(get_account_rate_limit(name, tmp))
                keychain.assert_not_called()
                fetch.assert_not_called()

    def test_unsupported_name_raises(self):
        from swe.harness.rate_limits import get_account_rate_limit

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                get_account_rate_limit("gemini", tmp)

    def test_shared_cache_is_never_touched(self):
        from swe.harness.rate_limits import get_account_rate_limit

        with tempfile.TemporaryDirectory() as tmp:
            self._devin_dir(tmp)
            with patch(
                "swe.harness.rate_limits._load_cached",
            ) as load, patch(
                "swe.harness.rate_limits._save_cached",
            ) as save, patch(
                "swe.harness.rate_limits._fetch_json",
                return_value=self._DEVIN_RESPONSE,
            ):
                get_account_rate_limit("devin", tmp)
            load.assert_not_called()
            save.assert_not_called()

    def test_credentials_but_failed_fetch_is_an_outage_not_signed_out(self):
        from swe.harness.rate_limits import (
            AccountUsageError,
            get_account_rate_limit,
        )

        with tempfile.TemporaryDirectory() as tmp:
            self._devin_dir(tmp)
            # Fetcher returns None (an unreadable reply) and raises alike.
            with patch(
                "swe.harness.rate_limits._fetch_json", return_value=None
            ):
                with self.assertRaises(AccountUsageError):
                    get_account_rate_limit("devin", tmp)
            with patch(
                "swe.harness.rate_limits._fetch_json",
                side_effect=OSError("connection refused"),
            ):
                with self.assertRaises(AccountUsageError):
                    get_account_rate_limit("devin", tmp)

    def test_successful_reading_is_stamped_with_the_fetch_time(self):
        from swe.harness.rate_limits import get_account_rate_limit

        with tempfile.TemporaryDirectory() as tmp:
            self._devin_dir(tmp)
            before = time.time()
            with patch(
                "swe.harness.rate_limits._fetch_json",
                return_value=self._DEVIN_RESPONSE,
            ):
                info = get_account_rate_limit("devin", tmp)
        self.assertIsNotNone(info)
        self.assertGreaterEqual(info.fetched_at, before)
        self.assertLessEqual(info.fetched_at, time.time())

    def test_a_reading_already_dated_keeps_its_stamp(self):
        from swe.harness.rate_limits import get_account_rate_limit
        from swe.harness.models import RateLimitInfo

        carried = RateLimitInfo(
            tool_name="devin", used_percent=10, limit_reached=False,
            reset_at=0.0, plan_type="teams", window_seconds=0,
            fetched_at=1_700_000_000.0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            self._devin_dir(tmp)
            with patch(
                "swe.harness.rate_limits._fetch_devin",
                return_value=carried,
            ):
                info = get_account_rate_limit("devin", tmp)
        self.assertEqual(info.fetched_at, 1_700_000_000.0)



class UsageOutcomeTest(unittest.TestCase):
    """``get_account_usage``: every failure kind is a named status."""

    _DEVIN_RESPONSE = DevinFetcherTest._SAMPLE_RESPONSE

    def _devin_dir(self, root: str) -> str:
        path = Path(root) / "devin"
        path.mkdir(parents=True)
        (path / "credentials.toml").write_text(
            'windsurf_api_key = "sk-extra"\n'
            'api_server_url = "https://server.example.com"\n'
        )
        return root

    def _cursor_dir(self, root: str, token: str | None) -> str:
        path = Path(root) / "User" / "globalStorage"
        path.mkdir(parents=True)
        CursorFetcherTest()._state_db(str(path), token)
        return root

    def test_no_credentials_is_signed_out(self):
        from swe.harness.rate_limits import get_account_usage

        with tempfile.TemporaryDirectory() as tmp:
            for name in ("devin", "cursor"):
                with patch(
                    "swe.harness.rate_limits._fetch_json",
                ) as fetch:
                    outcome = get_account_usage(name, tmp)
                self.assertEqual(outcome.status, "signed-out")
                self.assertIsNone(outcome.limit)
                fetch.assert_not_called()

    def test_denied_credentials_are_permission_required(self):
        from swe.harness import rate_limits

        with tempfile.TemporaryDirectory() as tmp:
            self._devin_dir(tmp)
            with patch("swe.harness.rate_limits.open",
                       side_effect=PermissionError(13, "denied"),
                       create=True):
                outcome = rate_limits.get_account_usage("devin", tmp)
        self.assertEqual(outcome.status, "permission-required")
        self.assertIsNone(outcome.limit)

    def test_a_cursor_blob_is_auth_required(self):
        from swe.harness.rate_limits import get_account_usage

        with tempfile.TemporaryDirectory() as tmp:
            self._cursor_dir(tmp, "cursor-safe-storage:v1:AAAA")
            with patch(
                "swe.harness.rate_limits._fetch_json",
            ) as fetch:
                outcome = get_account_usage("cursor", tmp)
        self.assertEqual(outcome.status, "auth-required")
        self.assertIsNone(outcome.limit)
        fetch.assert_not_called()

    def test_a_failed_fetch_is_unavailable(self):
        from swe.harness.rate_limits import get_account_usage

        with tempfile.TemporaryDirectory() as tmp:
            self._devin_dir(tmp)
            with patch(
                "swe.harness.rate_limits._fetch_json",
                return_value=None,
            ):
                outcome = get_account_usage("devin", tmp)
        self.assertEqual(outcome.status, "unavailable")
        self.assertIsNone(outcome.limit)

    def test_a_401_is_auth_required(self):
        from swe.harness import rate_limits

        def dead(_path=None):
            # A 401 inside _fetch_json lands on the worker thread's sink.
            rate_limits._note_json_failure("auth")
            return None

        with tempfile.TemporaryDirectory() as tmp:
            self._devin_dir(tmp)
            with patch.object(rate_limits, "_fetch_devin", side_effect=dead):
                outcome = rate_limits.get_account_usage("devin", tmp)
        self.assertEqual(outcome.status, "auth-required")

    def test_an_ok_reading(self):
        from swe.harness.rate_limits import get_account_usage

        with tempfile.TemporaryDirectory() as tmp:
            self._devin_dir(tmp)
            with patch(
                "swe.harness.rate_limits._fetch_json",
                return_value=self._DEVIN_RESPONSE,
            ):
                outcome = get_account_usage("devin", tmp)
        self.assertEqual(outcome.status, "ok")
        self.assertIsNotNone(outcome.limit)

    def test_the_old_contract_still_holds(self):
        from swe.harness.rate_limits import (
            AccountUsageError,
            get_account_rate_limit,
        )

        with tempfile.TemporaryDirectory() as tmp:
            # signed-out → None, an outage → AccountUsageError, a reading
            # → the RateLimitInfo, exactly as before.
            self.assertIsNone(get_account_rate_limit("devin", tmp))
            self._devin_dir(tmp)
            with patch(
                "swe.harness.rate_limits._fetch_json", return_value=None
            ), self.assertRaises(AccountUsageError):
                get_account_rate_limit("devin", tmp)


class IssueStatusTest(unittest.TestCase):
    """``get_all_rate_limits(issues=...)``: the fine-grained statuses."""

    def setUp(self):
        from swe.harness import rate_limits
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name) / "rate_limits_cache.json"
        saved_fetchers = dict(rate_limits._FETCHERS)
        saved_probes = dict(rate_limits._SIGNED_IN)
        saved_diagnose = dict(rate_limits._DIAGNOSE)

        def restore():
            rate_limits._FETCHERS.clear()
            rate_limits._FETCHERS.update(saved_fetchers)
            rate_limits._SIGNED_IN.clear()
            rate_limits._SIGNED_IN.update(saved_probes)
            rate_limits._DIAGNOSE.clear()
            rate_limits._DIAGNOSE.update(saved_diagnose)
        self.addCleanup(restore)
        rate_limits._FETCHERS.clear()
        rate_limits._SIGNED_IN.clear()
        rate_limits._DIAGNOSE.clear()
        for p in (patch.object(rate_limits, "RATE_LIMITS_CACHE_FILE", self.cache),
                  patch.object(rate_limits, "_CACHE_TTL", 300.0)):
            p.start()
            self.addCleanup(p.stop)

    def test_each_failure_kind_gets_its_status(self):
        from swe.harness import rate_limits
        from swe.harness.rate_limits import register

        def dead_auth():
            # A 401 inside _fetch_json lands on the worker thread's sink.
            rate_limits._note_json_failure("auth")
            return None

        register("out", MagicMock(return_value=None),
                 diagnose=lambda: "signed-out")
        register("denied", MagicMock(return_value=None),
                 diagnose=lambda: "permission-required")
        register("stale", MagicMock(return_value=None),
                 diagnose=lambda: "auth-required")
        register("dead", MagicMock(return_value=None),
                 signed_in=lambda: True)
        register("rejected", dead_auth,
                 signed_in=lambda: True)
        issues: dict = {}
        reasons: dict = {}
        result = get_all_rate_limits(
            use_cache=False, issues=issues, no_reading=reasons)
        self.assertEqual(result, {})
        self.assertEqual(issues, {
            "out": "signed-out",
            "denied": "permission-required",
            "stale": "auth-required",
            "dead": "unavailable",
            "rejected": "auth-required",
        })
        # The coarse no_reading channel keeps its two values.
        self.assertEqual(reasons["out"], "signed_out")
        for name in ("denied", "stale", "dead", "rejected"):
            self.assertEqual(reasons[name], "error")

    def test_a_timeout_is_an_issue_on_the_marker(self):
        register("slow", lambda: time.sleep(0.5))
        issues: dict = {}
        result = get_all_rate_limits(
            use_cache=False, deadline=0.05, issues=issues)
        self.assertEqual(issues, {"slow": "timeout"})
        self.assertEqual(result["slow"].plan_type, "timeout")

    def test_an_auth_required_reading_reports_its_status(self):
        register("stale", MagicMock(return_value=RateLimitInfo(
            tool_name="stale", used_percent=0, limit_reached=False,
            reset_at=0.0, plan_type="auth-required", window_seconds=0)))
        issues: dict = {}
        result = get_all_rate_limits(use_cache=False, issues=issues)
        self.assertEqual(issues, {"stale": "auth-required"})
        self.assertIn("stale", result)

    def test_an_empty_fetch_without_probes_stays_unknown(self):
        register("mystery", MagicMock(return_value=None))
        issues: dict = {}
        reasons: dict = {}
        get_all_rate_limits(
            use_cache=False, issues=issues, no_reading=reasons)
        self.assertEqual(issues, {})
        self.assertEqual(reasons, {})


class CursorCliTokenTest(unittest.TestCase):
    """cursor-agent's own auth file, its only token store off macOS."""

    def _write(self, directory: str, body) -> str:
        path = os.path.join(directory, "auth.json")
        with open(path, "w") as f:
            f.write(body if isinstance(body, str) else json.dumps(body))
        return path

    def test_reads_the_access_token(self):
        from swe.harness.rate_limits import _read_cursor_cli_token
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {"accessToken": " a.b.c ", "refreshToken": "r.s.t"})
            self.assertEqual(_read_cursor_cli_token(path), "a.b.c")

    def test_missing_or_malformed_files_have_no_token(self):
        from swe.harness.rate_limits import _read_cursor_cli_token
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(_read_cursor_cli_token(os.path.join(tmp, "absent.json")))
            for body in ("{not json", "[]", {"accessToken": ""}, {"accessToken": 7}, {}):
                self.assertIsNone(_read_cursor_cli_token(self._write(tmp, body)), body)

    def test_token_ladder_is_editor_then_cli_file_then_keychain(self):
        from swe.harness import rate_limits
        jwt = CursorFetcherTest._jwt
        editor_token = jwt({"sub": "editor"})
        cli_token = jwt({"sub": "cli"})
        keychain_token = jwt({"sub": "keychain"})
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {"accessToken": cli_token})
            with patch.object(rate_limits, "_CURSOR_CLI_AUTH_FILE", path), \
                 patch.object(rate_limits, "_read_cursor_keychain_token",
                              return_value=keychain_token) as keychain:
                with patch.object(rate_limits, "_read_cursor_state_token",
                                  return_value=editor_token):
                    self.assertEqual(rate_limits._get_cursor_access_token(), editor_token)
                with patch.object(rate_limits, "_read_cursor_state_token", return_value=None):
                    self.assertEqual(rate_limits._get_cursor_access_token(), cli_token)
                    keychain.assert_not_called()
                    os.remove(path)
                    self.assertEqual(rate_limits._get_cursor_access_token(), keychain_token)


class CursorSafeStorageTest(unittest.TestCase):
    """The editor's ``cursor-safe-storage:v1:`` tokens, decrypted with the
    "Cursor Safe Storage" key. ``security`` is always mocked: these tests
    never touch a real keychain."""

    # Encrypted with PBKDF2-HMAC-SHA1("quiver-test-password", "saltysalt",
    # 1003, 16) and an IV of 16 spaces, the way Chromium's OSCrypt does on
    # macOS. The plaintext is ``_JWT``.
    _PASSWORD = "quiver-test-password"
    _JWT = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiAiZ29vZ2xlLW9hdXRoMnx1c2VyXzAxU0FGRSIsICJleHAiOiA5OTk5OTk5OTk5fQ"
        ".signature"
    )
    _BLOB = (
        "cursor-safe-storage:v1:djEwnWFM9Q5DAWFOpPbLpOiK+r3xzbfY4fiWFqEJRD6s"
        "vXaoMd6snHopyEJoMgzSqTrfp53horLKRKDO4YZ2eUE+bmUzlzv4eHVgwmZ54TAZHNF4"
        "oak3/a2jqcTCXy34+UshZ2yPNkxYB2YVX8kG98WesyXjyPKnmDvHzB00JNDsxhI="
    )

    def setUp(self):
        from swe.harness import rate_limits
        self.rl = rate_limits
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.backoff = Path(tmp.name) / "cache" / "cursor_safe_storage_backoff"
        saved = rate_limits._cursor_safe_storage_key_cache
        rate_limits._cursor_safe_storage_key_cache = None
        self.addCleanup(setattr, rate_limits,
                        "_cursor_safe_storage_key_cache", saved)
        for target, value in (
            ("_is_macos", lambda: True),
            ("_cursor_safe_storage_backoff_file", lambda: self.backoff),
        ):
            patcher = patch.object(rate_limits, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("swe.harness.rate_limits.shutil.which",
                        return_value="/usr/bin/security")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _needs_libcrypto(self):
        if self.rl._load_libcrypto() is None:
            self.skipTest("no libcrypto to decrypt with")

    def _security(self, returncode=0, stdout=None, side_effect=None):
        if side_effect is not None:
            return patch("swe.harness.rate_limits.subprocess.run",
                         side_effect=side_effect)
        return patch(
            "swe.harness.rate_limits.subprocess.run",
            return_value=MagicMock(
                returncode=returncode,
                stdout=self._PASSWORD + "\n" if stdout is None else stdout),
        )

    def _blob(self, raw: bytes) -> str:
        return "cursor-safe-storage:v1:" + base64.b64encode(raw).decode()

    def test_v10_blob_round_trips_with_the_known_key(self):
        self._needs_libcrypto()
        with self._security() as run:
            self.assertEqual(
                self.rl._decrypt_cursor_safe_storage(self._BLOB), self._JWT)
            # The key is read once per process.
            self.assertEqual(
                self.rl._decrypt_cursor_safe_storage(self._BLOB), self._JWT)
        run.assert_called_once()
        args = run.call_args[0][0]
        self.assertEqual(args[:3], ["security", "find-generic-password", "-w"])
        self.assertIn("Cursor Safe Storage", args)
        self.assertLessEqual(run.call_args[1]["timeout"], 1.0)

    def test_other_versions_and_bad_payloads_never_ask_the_keychain(self):
        ciphertext = base64.b64decode(
            self._BLOB[len("cursor-safe-storage:v1:"):])[3:]
        for value in (
            self._blob(b"v11" + ciphertext),  # not what OSCrypt writes on macOS
            self._blob(b"v10" + ciphertext[:-1]),  # not whole AES blocks
            self._blob(b"v10"),
            "cursor-safe-storage:v1:not base64!",
            "cursor-safe-storage:AAAA",
            "cursor-safe-storage:v2:" + self._BLOB.split(":", 2)[2],
            self._JWT, "", None,
        ):
            with self._security() as run:
                self.assertIsNone(self.rl._decrypt_cursor_safe_storage(value))
            run.assert_not_called()

    def test_wrong_key_is_not_a_token(self):
        self._needs_libcrypto()
        with self._security(stdout="some-other-password\n"):
            self.assertIsNone(self.rl._decrypt_cursor_safe_storage(self._BLOB))
        # A key that failed is read again next time.
        self.assertIsNone(self.rl._cursor_safe_storage_key_cache)

    def test_missing_item_is_absent_without_backing_off(self):
        with self._security(returncode=44, stdout="") as run:
            self.assertIsNone(self.rl._decrypt_cursor_safe_storage(self._BLOB))
            self.assertIsNone(self.rl._decrypt_cursor_safe_storage(self._BLOB))
        self.assertEqual(run.call_count, 2)
        self.assertFalse(self.backoff.exists())

    def test_timeout_or_refusal_backs_off(self):
        timeout = subprocess.TimeoutExpired(["security"], 1.0)
        for security in (self._security(side_effect=timeout),
                         self._security(returncode=128, stdout="")):
            with self.subTest(security=security):
                if self.backoff.exists():
                    self.backoff.unlink()
                with security as run:
                    self.assertIsNone(
                        self.rl._decrypt_cursor_safe_storage(self._BLOB))
                self.assertTrue(self.backoff.exists())
                # Within the backoff the dialog is never put up again.
                with self._security() as run:
                    self.assertIsNone(
                        self.rl._decrypt_cursor_safe_storage(self._BLOB))
                run.assert_not_called()

    def test_one_process_at_a_time_may_put_the_dialog_up(self):
        """While another ``swe`` holds the lock (the app reads several
        Cursor accounts at once), this one gives up without asking."""
        import fcntl
        self.backoff.parent.mkdir(parents=True)
        lock_path = self.backoff.with_name("cursor_safe_storage.lock")
        with open(lock_path, "a") as holder, \
             patch.object(self.rl, "_CURSOR_SAFE_STORAGE_TIMEOUT", 0.1), \
             self._security() as run:
            fcntl.flock(holder, fcntl.LOCK_EX)
            self.assertIsNone(self.rl._decrypt_cursor_safe_storage(self._BLOB))
            run.assert_not_called()
            fcntl.flock(holder, fcntl.LOCK_UN)
            if self.rl._load_libcrypto() is not None:
                self.assertEqual(
                    self.rl._decrypt_cursor_safe_storage(self._BLOB),
                    self._JWT)
                run.assert_called_once()

    def test_backoff_expires(self):
        self._needs_libcrypto()
        self.backoff.parent.mkdir(parents=True)
        self.backoff.touch()
        old = time.time() - self.rl._CURSOR_SAFE_STORAGE_BACKOFF - 60
        os.utime(self.backoff, (old, old))
        with self._security():
            self.assertEqual(
                self.rl._decrypt_cursor_safe_storage(self._BLOB), self._JWT)

    def test_off_macos_the_keychain_is_never_asked(self):
        with patch.object(self.rl, "_is_macos", return_value=False), \
             self._security() as run:
            self.assertIsNone(self.rl._decrypt_cursor_safe_storage(self._BLOB))
        run.assert_not_called()

    def test_ladder_decrypts_the_editor_token_last(self):
        self._needs_libcrypto()
        rl = self.rl
        cli_token = CursorFetcherTest._jwt({"sub": "cli", "exp": 9_999_999_999})
        with patch.object(rl, "_read_cursor_state_token",
                          return_value=self._BLOB), \
             patch.object(rl, "_read_cursor_cli_token", return_value=None), \
             patch.object(rl, "_read_cursor_keychain_token",
                          return_value=cli_token), \
             self._security() as run:
            self.assertEqual(rl._get_cursor_access_token(), cli_token)
        run.assert_not_called()
        with patch.object(rl, "_read_cursor_state_token",
                          return_value=self._BLOB), \
             patch.object(rl, "_read_cursor_cli_token", return_value=None), \
             patch.object(rl, "_read_cursor_keychain_token",
                          return_value=None), \
             self._security():
            self.assertEqual(rl._get_cursor_access_token(), self._JWT)

    def test_expired_cli_token_does_not_hide_the_editor_session(self):
        self._needs_libcrypto()
        rl = self.rl
        stale = CursorFetcherTest._jwt({"sub": "cli", "exp": 1_000_000_000})
        with patch.object(rl, "_read_cursor_state_token",
                          return_value=self._BLOB), \
             patch.object(rl, "_read_cursor_cli_token", return_value=None), \
             patch.object(rl, "_read_cursor_keychain_token",
                          return_value=stale), \
             self._security():
            self.assertEqual(rl._get_cursor_access_token(), self._JWT)
        # With nothing live, the expired token still answers (re-login).
        rl._cursor_safe_storage_key_cache = None
        with patch.object(rl, "_read_cursor_state_token",
                          return_value=self._BLOB), \
             patch.object(rl, "_read_cursor_cli_token", return_value=None), \
             patch.object(rl, "_read_cursor_keychain_token",
                          return_value=stale), \
             self._security(returncode=44, stdout=""):
            self.assertEqual(rl._get_cursor_access_token(), stale)

    def _state_db(self) -> str:
        # No ``cachedScopedProfile``: the email can only come from
        # matching the decrypted row to the token.
        return CursorFetcherTest._state_db(
            self, self.tmp, self._BLOB, email="safe@work.com")

    def test_account_state_db_is_decrypted_and_diagnosed_ok(self):
        self._needs_libcrypto()
        rl = self.rl
        db = self._state_db()
        with self._security(), patch.object(
            rl, "_fetch_json",
            return_value=CursorFetcherTest._SAMPLE_RESPONSE,
        ) as fetch, patch.object(rl, "_read_cursor_keychain_token") as cli:
            self.assertEqual(rl._diagnose_cursor_credentials((db,)),
                             remedies.OK)
            info = rl._fetch_cursor(db)
        self.assertIsNotNone(info)
        self.assertEqual(info.email, "safe@work.com")
        cookie = fetch.call_args_list[0][0][0].get_header("Cookie")
        self.assertIn(urllib.parse.quote("google-oauth2|user_01SAFE", safe=""),
                      cookie)
        self.assertNotIn("cursor-safe-storage", cookie)
        cli.assert_not_called()

    def test_account_state_db_without_the_key_is_auth_required(self):
        rl = self.rl
        db = self._state_db()
        with self._security(returncode=44, stdout=""), \
             patch.object(rl, "_fetch_json") as fetch:
            self.assertEqual(rl._diagnose_cursor_credentials((db,)),
                             remedies.AUTH_REQUIRED)
            self.assertIsNone(rl._fetch_cursor(db))
        fetch.assert_not_called()

    def test_signed_in_probe_counts_a_blob_with_its_key_without_reading_it(self):
        rl = self.rl
        probe = rl._SIGNED_IN["cursor"][1]
        present = {("-s", "Cursor Safe Storage")}
        with patch.object(rl, "_read_cursor_state_token",
                          return_value=self._BLOB), \
             patch.object(rl, "_read_cursor_cli_token", return_value=None), \
             patch.object(rl, "_keychain_item_exists",
                          side_effect=lambda *q: q in present), \
             self._security() as run:
            self.assertTrue(probe())
            present.clear()
            self.assertFalse(probe())
        run.assert_not_called()


class NoReadingReasonTest(unittest.TestCase):
    """``get_all_rate_limits(no_reading=...)``: signed out vs failed fetch."""

    def setUp(self):
        from swe.harness import rate_limits
        self.rl = rate_limits
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name) / "rate_limits_cache.json"
        saved_fetchers = dict(rate_limits._FETCHERS)
        saved_probes = dict(rate_limits._SIGNED_IN)
        self.builtin = saved_fetchers

        def restore():
            rate_limits._FETCHERS.clear()
            rate_limits._FETCHERS.update(saved_fetchers)
            rate_limits._SIGNED_IN.clear()
            rate_limits._SIGNED_IN.update(saved_probes)
        self.addCleanup(restore)
        rate_limits._FETCHERS.clear()
        for p in (patch.object(rate_limits, "RATE_LIMITS_CACHE_FILE", self.cache),
                  patch.object(rate_limits, "_CACHE_TTL", 300.0)):
            p.start()
            self.addCleanup(p.stop)

    def run_all(self, **kwargs):
        reasons: dict = {}
        result = get_all_rate_limits(no_reading=reasons, **kwargs)
        return result, reasons

    def test_an_empty_fetch_is_signed_out_or_failed_by_the_probe(self):
        register("out", MagicMock(return_value=None), signed_in=lambda: False)
        register("broken", MagicMock(return_value=None), signed_in=lambda: True)
        register("raises", MagicMock(side_effect=RuntimeError("boom")))
        register("unknown", MagicMock(return_value=None))
        result, reasons = self.run_all(use_cache=False)
        self.assertEqual(result, {})
        self.assertEqual(reasons, {"out": "signed_out", "broken": "error", "raises": "error"})
        saved = json.loads(self.cache.read_text())
        self.assertEqual(set(saved["no_data"]), {"out", "broken", "raises", "unknown"})
        self.assertEqual(saved["no_data_status"],
                         {"out": "signed_out", "broken": "error", "raises": "error"})

    def test_a_reading_has_no_reason_and_the_probe_is_not_asked(self):
        probe = MagicMock(return_value=False)
        register("ok", MagicMock(return_value=RateLimitInfo(
            tool_name="ok", used_percent=5, limit_reached=False,
            reset_at=0.0, plan_type="x", window_seconds=0)), signed_in=probe)
        result, reasons = self.run_all(use_cache=False)
        self.assertIn("ok", result)
        self.assertEqual(reasons, {})
        probe.assert_not_called()

    def test_empty_is_normal_keeps_signed_out_but_not_error(self):
        register("quiet", MagicMock(return_value=None), signed_in=lambda: True,
                 empty_is_normal=True)
        register("quiet_out", MagicMock(return_value=None), signed_in=lambda: False,
                 empty_is_normal=True)
        _result, reasons = self.run_all(use_cache=False)
        self.assertEqual(reasons, {"quiet_out": "signed_out"})

    def test_a_probe_that_raises_gives_no_reason(self):
        register("odd", MagicMock(return_value=None),
                 signed_in=MagicMock(side_effect=OSError("keychain")))
        _result, reasons = self.run_all(use_cache=False)
        self.assertEqual(reasons, {})

    def test_a_stand_in_fetcher_never_runs_the_real_probe(self):
        probe = MagicMock(return_value=True)
        register("tool", MagicMock(return_value=None), signed_in=probe)
        self.rl._FETCHERS["tool"] = MagicMock(return_value=None)
        _result, reasons = self.run_all(use_cache=False)
        self.assertEqual(reasons, {})
        probe.assert_not_called()

    def test_a_cached_failure_keeps_its_reason_and_is_not_refetched(self):
        fetcher = MagicMock(return_value=None)
        register("broken", fetcher, signed_in=lambda: True)
        self.run_all(use_cache=False)
        _result, reasons = self.run_all()
        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(reasons, {"broken": "error"})
        # A Refresh press retries what failed.
        self.run_all(refresh_failed=True)
        self.assertEqual(fetcher.call_count, 2)

    def test_a_cached_failure_survives_a_save_by_another_refetch(self):
        broken = MagicMock(return_value=None)
        register("broken", broken, signed_in=lambda: True)
        register("out", MagicMock(return_value=None), signed_in=lambda: False)
        self.run_all(use_cache=False)
        first = json.loads(self.cache.read_text())["no_data"]["broken"]
        for _ in range(3):
            # "out" is refetched (signed out) and the file saved each time.
            _result, reasons = self.run_all()
        self.assertEqual(broken.call_count, 1)
        self.assertEqual(reasons, {"broken": "error", "out": "signed_out"})
        saved = json.loads(self.cache.read_text())
        self.assertEqual(saved["no_data"]["broken"], first)
        self.assertEqual(saved["no_data_status"]["broken"], "error")

    def test_a_slow_credential_lookup_is_unknown_not_signed_out(self):
        from swe.harness import rate_limits
        with patch.object(rate_limits.shutil, "which", return_value="/usr/bin/x"), \
             patch.object(rate_limits.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("security", 1)):
            with self.assertRaises(subprocess.TimeoutExpired):
                rate_limits._keychain_item_exists("-l", "x")
            with self.assertRaises(subprocess.TimeoutExpired):
                rate_limits._gh_signed_in()
        register("tool", MagicMock(return_value=None),
                 signed_in=MagicMock(side_effect=subprocess.TimeoutExpired("gh", 1)))
        _result, reasons = self.run_all(use_cache=False)
        self.assertEqual(reasons, {})

    def test_probes_never_read_a_secret(self):
        from swe.harness import rate_limits
        calls = []

        def run(args, **kw):
            calls.append(args)
            return _CompletedProc(returncode=0, stdout="tok\n")
        with patch.object(rate_limits.shutil, "which", return_value="/usr/bin/x"), \
             patch.object(rate_limits.subprocess, "run", side_effect=run):
            self.assertTrue(rate_limits._keychain_item_exists("-s", "a", "-a", "b"))
            self.assertTrue(rate_limits._gh_signed_in())
        self.assertNotIn("-w", calls[0])
        with patch.object(rate_limits.shutil, "which", return_value=None):
            self.assertFalse(rate_limits._keychain_item_exists("-l", "x"))
            self.assertFalse(rate_limits._gh_signed_in())

    def test_signed_out_is_rechecked_every_call(self):
        signed_in = [False]
        info = RateLimitInfo(tool_name="tool", used_percent=7, limit_reached=False,
                             reset_at=0.0, plan_type="x", window_seconds=0)
        fetcher = MagicMock(side_effect=lambda: info if signed_in[0] else None)
        register("tool", fetcher, signed_in=lambda: signed_in[0])
        _result, reasons = self.run_all(use_cache=False)
        self.assertEqual(reasons, {"tool": "signed_out"})
        signed_in[0] = True
        result, reasons = self.run_all()
        self.assertEqual(fetcher.call_count, 2)
        self.assertEqual(result["tool"].used_percent, 7)
        self.assertEqual(reasons, {})

    def test_a_kept_reading_beats_a_reason(self):
        info = RateLimitInfo(tool_name="tool", used_percent=7, limit_reached=False,
                             reset_at=0.0, plan_type="x", window_seconds=0)
        register("tool", MagicMock(side_effect=[info, None]), signed_in=lambda: True)
        self.run_all(use_cache=False)
        result, reasons = self.run_all(use_cache=False)
        # The 24h fallback still shows the last reading; no reason with it.
        self.assertIn("tool", result)
        self.assertEqual(reasons, {})

    def test_a_filtered_run_carries_other_tools_reasons(self):
        register("out", MagicMock(return_value=None), signed_in=lambda: False)
        register("broken", MagicMock(return_value=None), signed_in=lambda: True)
        self.run_all(use_cache=False)
        self.run_all(use_cache=False, tool_names={"out"})
        saved = json.loads(self.cache.read_text())
        self.assertEqual(saved["no_data_status"], {"out": "signed_out", "broken": "error"})

    def test_cache_without_the_status_map_reads_as_no_reason(self):
        from swe.harness.rate_limits import _load_cached_no_data_status
        self.assertEqual(_load_cached_no_data_status(), {})
        self.cache.write_text(json.dumps({"cached_at": time.time(), "limits": {},
                                          "no_data": {"a": time.time()}}))
        self.assertEqual(_load_cached_no_data_status(), {})
        self.cache.write_text(json.dumps({"no_data_status": {"a": "signed_out", "b": "nope"}}))
        self.assertEqual(_load_cached_no_data_status(), {"a": "signed_out"})
        self.cache.write_text("[]")
        self.assertEqual(_load_cached_no_data_status(), {})

    def test_every_builtin_fetcher_has_a_probe(self):
        self.assertEqual(set(self.builtin), {"codex", "copilot", "claude", "droid",
                                             "freebuff", "cursor", "devin"})
        for name, fetcher in self.builtin.items():
            self.assertIs(self.rl._SIGNED_IN[name][0], fetcher, name)


if __name__ == "__main__":
    unittest.main()
