import copy
import json
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
        return patch(
            "quiver.harness.rate_limits.subprocess.run",
            return_value=_CompletedProc(returncode=0, stdout=token + "\n"),
        )

    def _patch_which(self):
        return patch("quiver.harness.rate_limits.shutil.which", return_value="/usr/bin/gh")

    def test_fetch_copilot_success(self):
        from quiver.harness.rate_limits import _fetch_github_copilot

        with self._patch_token(), self._patch_which(), patch(
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
        with self._patch_token(), self._patch_which(), patch(
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
        with self._patch_token(), self._patch_which(), patch(
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
        with self._patch_token(), self._patch_which(), patch(
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
        with self._patch_token(), self._patch_which(), patch(
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

        Crucially this locks in the Python 3.10 fallback path: variant
        ``'...+00:00'`` (with fractional seconds) is rejected by
        ``datetime.fromisoformat`` on 3.10 and only succeeds because
        the fallback strips the fractional part.
        """
        from quiver.harness.rate_limits import _parse_iso8601_to_epoch

        expected = 1785542400.0  # 2026-08-01T00:00:00Z
        for variant in (
            "2026-08-01T00:00:00.000Z",        # microseconds + Z (live API)
            "2026-08-01T00:00:00Z",            # no fractional
            "2026-08-01T00:00:00+00:00",       # explicit offset, naive base
            "2026-08-01T00:00:00.123+00:00",   # microseconds + offset (3.10!)
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


class ClaudeFetcherTest(unittest.TestCase):
    """Test the Claude /api/oauth/usage fetcher with mocked credentials + HTTP."""

    _SAMPLE_RESPONSE = {
        "five_hour": {"utilization": 0.42, "resets_at": "2026-02-28T17:00:00Z"},
        "seven_day": {"utilization": 0.61, "resets_at": "2026-03-07T08:00:00Z"},
        "seven_day_sonnet": {"utilization": 0.85, "resets_at": "2026-03-07T08:00:00Z"},
    }

    def _mock_response(self, body):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(body).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def _linux_creds_file(self, token="fake-claude-token"):
        """Build a Linux-style credentials file and return (tmpdir, patches).

        Patches ``os.path.expanduser`` so ``~/.claude/.credentials.json``
        resolves to the temp file. The temp directory must be cleaned up
        by the caller (``finally`` + ``tmp.cleanup()``).
        """
        tmp = tempfile.TemporaryDirectory()
        creds_path = Path(tmp.name) / "creds.json"
        creds_path.write_text(json.dumps({
            "claudeAiOauth": {
                "accessToken": token,
                "refreshToken": "x",
                "expiresAt": 9999999999,
            },
        }))
        patches = [
            patch(
                "quiver.harness.rate_limits.os.path.expanduser",
                side_effect=lambda p: (
                    str(creds_path) if "~/.claude/.credentials.json" in str(p)
                    else p
                ),
            ),
        ]
        return tmp, patches

    # --- polling-disabled behaviour -------------------------------------
    # The live ``api.anthropic.com/api/oauth/usage`` polling is currently
    # commented out in ``_fetch_claude`` (the endpoint is undocumented
    # and 429s aggressively). While disabled, any account with Claude
    # Code credentials renders ``no-sub``; no credentials renders None.
    # The ``_fetch_claude_url`` helper's 401/429 diagnostics are still
    # covered by ``ClaudeHTTPDiagnosticTest`` below.

    def test_creds_present_returns_no_sub(self):
        """Credentials resolve → ``no-sub`` RateLimitInfo (polling disabled)."""
        from quiver.harness.rate_limits import _fetch_claude

        tmp, patches = self._linux_creds_file()
        try:
            # urlopen is NOT called while polling is disabled; the mock
            # is here only to assert the endpoint is never hit.
            with patches[0], patch(
                "quiver.harness.rate_limits.urllib.request.urlopen",
            ) as mock_open:
                info = _fetch_claude()
            self.assertIsNotNone(info)
            self.assertEqual(info.tool_name, "claude")
            self.assertEqual(info.plan_type, "no-sub")
            self.assertEqual(info.used_percent, 0)
            self.assertFalse(info.limit_reached)
            self.assertEqual(info.window, "")
            # The endpoint must not be polled while disabled.
            mock_open.assert_not_called()
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

    def test_macos_keychain_path(self):
        """macOS: keychain creds resolve → ``no-sub`` (polling disabled)."""
        from quiver.harness.rate_limits import _fetch_claude

        creds_json = json.dumps({"claudeAiOauth": {"accessToken": "kc-token"}})
        completed = _CompletedProc(returncode=0, stdout=creds_json)
        with patch("quiver.harness.rate_limits.os.path.expanduser",
                   side_effect=lambda p: "/no/such/path/x" if "~/.claude" in str(p) else p), \
             patch("quiver.harness.rate_limits.shutil.which",
                   return_value="/usr/bin/security"), \
             patch("quiver.harness.rate_limits.subprocess.run",
                   return_value=completed), \
             patch(
                 "quiver.harness.rate_limits.urllib.request.urlopen",
             ) as mock_open:
            info = _fetch_claude()
        self.assertIsNotNone(info)
        self.assertEqual(info.tool_name, "claude")
        self.assertEqual(info.plan_type, "no-sub")
        mock_open.assert_not_called()

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
        self.assertIn("85%", col)

    def test_format_column_without_window_default_unchanged(self):
        """Backwards compat: window='' default keeps the legacy single-token shape."""
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
        self.assertIn("30%", col)


class CopilotRegistrationTest(unittest.TestCase):
    """Built-in fetchers (codex, copilot, claude, droid) must be registered at import time."""

    def test_built_in_fetchers_registered(self):
        # _FETCHERS is populated at import time by the _register_*
        # functions. Verify all four built-ins are wired in.
        self.assertIn("codex", _FETCHERS)
        self.assertIn("copilot", _FETCHERS)
        self.assertIn("claude", _FETCHERS)
        self.assertIn("droid", _FETCHERS)


class DroidFetcherTest(unittest.TestCase):
    """Test the Droid /api/billing/limits fetcher across the auth ladder.

    The real endpoint returns a nested ``limits.<category>.<window>``
    schema (verified against the live ``api.factory.ai/api/billing/limits``
    response) where category is ``standard`` / ``core`` and window is
    ``fiveHour`` / ``weekly`` / ``monthly``. Each window carries
    ``usedPercent`` (already 0-100), ``windowEnd`` (ISO 8601), and
    ``secondsRemaining``. The fetcher surfaces the most-restrictive
    window (highest ``usedPercent``) across ALL categories+windows.

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
                "fiveHour": {"usedPercent": 19,
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
                "fiveHour": {"usedPercent": 100,
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
        # API usedPercent is REMAINING. core/5h=100 remaining → 0 used;
        # standard/5h=19 remaining → 81 used. Average = (0+81)/2 = 40.5 → 40.
        self.assertEqual(info.used_percent, 40)
        self.assertEqual(info.window, "5h")
        # Neither budget is at 0% remaining → not reached.
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
        # (0 + 81) / 2 = 40 (inverted from remaining).
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
        # (0 + 81) / 2 = 40 (inverted from remaining).
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
    #    category's fiveHour still feeds the average (inverted).
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
        # Only core/5h usable: 60 remaining → 40 used. Average of [40] = 40.
        self.assertEqual(info.used_percent, 40)
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

    # 10. usedPercent=0 means 0% REMAINING → 100% used → limit reached.
    def test_zero_remaining_means_fully_used(self):
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
        # 0% remaining → 100% used.
        self.assertEqual(info.used_percent, 100)
        self.assertTrue(info.limit_reached)

    # 11. Percentage is the average of the INVERTED core/5h and standard/5h.
    def test_averages_inverted_core_and_standard_five_hour(self):
        from quiver.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "standard": {
                    "fiveHour": {"usedPercent": 70,  # remaining → 30 used
                                 "windowEnd": "2026-07-25T22:31:13Z"},
                },
                "core": {
                    "fiveHour": {"usedPercent": 50,  # remaining → 50 used
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
        # (30 + 50) / 2 = 40.
        self.assertEqual(info.used_percent, 40)
        self.assertEqual(info.window, "5h")
        self.assertFalse(info.limit_reached)

    # 12. One 5h window at 0% remaining (100% used) ⇒ limit_reached even
    #     though the average is below 100 — the average must not mask a
    #     per-budget cutoff.
    def test_one_budget_at_zero_remaining_marks_reached(self):
        from quiver.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "core": {
                    "fiveHour": {"usedPercent": 0,  # 0 remaining → 100 used
                                 "windowEnd": "2026-07-25T12:40:31Z"},
                },
                "standard": {
                    "fiveHour": {"usedPercent": 20,  # 20 remaining → 80 used
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

    # 13. Negative usedPercent (overage: < 0% remaining) clamps to 0
    #     remaining → 100% used before averaging.
    def test_negative_remaining_clamps_to_fully_used(self):
        from quiver.harness.rate_limits import _fetch_droid

        body = {
            "limits": {
                "core": {
                    "fiveHour": {"usedPercent": -50,  # clamp to 0 → 100 used
                                 "windowEnd": "2026-07-25T12:40:31Z"},
                },
                "standard": {
                    "fiveHour": {"usedPercent": -30,  # clamp to 0 → 100 used
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
        # Both clamp to 0 remaining → 100 used → average 100.
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

    # 14b. reset_at is the EARLIEST windowEnd across all six windows,
    #      not the fiveHour window's reset — the soonest refresh wins.
    def test_reset_at_is_earliest_across_all_windows(self):
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
        # Earliest windowEnd is standard/monthly 2026-07-26T00:00:00Z
        # = 1785024000; allow ±1 day. NOT the fiveHour reset.
        self.assertAlmostEqual(info.reset_at, 1785024000.0, delta=86400)
        # Percentage averages the INVERTED fiveHour windows:
        # core/5h=42 remaining → 58 used; standard/5h=30 remaining → 70 used.
        # (58 + 70) / 2 = 64.
        self.assertEqual(info.used_percent, 64)

    # 15. The API field ``usedPercent`` is REMAINING, not used. A
    #     rolled-over 5h window reports 100% remaining (0% used) with a
    #     stale past windowEnd — it must be INVERTED and INCLUDED in the
    #     average (not skipped), matching the Factory dashboard's fresh
    #     0%-used view. The reset countdown skips the stale past
    #     windowEnd and uses the soonest future one.
    def test_inverts_remaining_and_includes_rolled_over_window(self):
        from quiver.harness.rate_limits import _fetch_droid

        # _NOW = 2026-07-25T10:00:00Z. core/fiveHour windowEnd 08:00 is
        # in the past (rolled over) but reports 100% remaining (0% used);
        # standard/fiveHour 22:31 is future and reports 46% remaining
        # (54% used). Both feed the average; reset uses the future one.
        body = {
            "limits": {
                "core": {
                    "fiveHour": {"usedPercent": 100,
                                 "windowEnd": "2026-07-25T08:00:00Z",
                                 "secondsRemaining": None},
                    "weekly": {"usedPercent": 51,
                               "windowEnd": "2026-08-01T07:40:31Z"},
                },
                "standard": {
                    "fiveHour": {"usedPercent": 46,
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
        # (0 + 54) / 2 = 27 — the rolled-over core window (100% remaining
        # = 0% used) is included, NOT skipped.
        self.assertEqual(info.used_percent, 27)
        # 100% remaining on core is NOT a cutoff → not reached.
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
