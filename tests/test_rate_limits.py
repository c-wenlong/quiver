import copy
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


class CopilotRegistrationTest(unittest.TestCase):
    """The copilot fetcher must be registered at import time."""

    def test_copilot_registered(self):
        # _FETCHERS is populated at import time by _register_codex /
        # _register_github_copilot. Verify both are present.
        self.assertIn("codex", _FETCHERS)
        self.assertIn("copilot", _FETCHERS)
