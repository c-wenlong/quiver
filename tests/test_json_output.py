"""`--json` output: the contract the macOS app reads.

Every command that takes --json prints exactly one JSON document on stdout,
shaped ``{"schema": 1, "data": ...}``. Nothing else may reach stdout: the
app decodes the whole stream, so a stray header line or ANSI escape breaks
every page that depends on it.

These run the real CLI in a subprocess against a throwaway HOME, because
quiver resolves ~/.quiver once at import time. An in-process test would
read the developer's own registry.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def run_swe(home: Path, *args: str) -> subprocess.CompletedProcess:
    # A clean environment, so the developer's CLAUDE_CONFIG_DIR, CODEX_HOME
    # and friends cannot point the child at real configs. Coverage's own
    # variables pass through: .coveragerc patches subprocesses, and without
    # them the child's lines would go unmeasured.
    env = {
        key: value for key, value in os.environ.items()
        if key.startswith("COVERAGE_")
    }
    env.update({
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(SRC),
        "NO_COLOR": "1",
    })
    return subprocess.run(
        [sys.executable, "-m", "quiver.cli", *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


def write_registry(home: Path, registry: dict) -> None:
    config = home / ".quiver" / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "harness.json").write_text(json.dumps(registry))


def decode(result: subprocess.CompletedProcess) -> dict:
    """The whole of stdout must be one JSON document."""
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - failure path
        raise AssertionError(
            f"stdout is not JSON ({exc}):\n{result.stdout}\n"
            f"stderr:\n{result.stderr}") from exc


REGISTRY = {
    "claude": {
        "command": "claude", "description": "Anthropic CLI",
        "aliases": ["cc"], "tags": ["vendor"],
        "state": "starred", "pin": 1,
    },
    "codex": {
        "command": "codex", "description": "OpenAI CLI",
        "aliases": ["cx"], "tags": ["vendor"],
    },
}


class ListJsonTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        write_registry(self.home, REGISTRY)

    def test_list_json_is_an_enveloped_list_of_harnesses(self):
        result = run_swe(self.home, "list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = decode(result)
        self.assertEqual(doc["schema"], 1)
        rows = {row["name"]: row for row in doc["data"]["harnesses"]}
        self.assertEqual(set(rows), {"claude", "codex"})
        self.assertEqual(rows["claude"]["command"], "claude")
        self.assertEqual(rows["claude"]["aliases"], ["cc"])
        self.assertEqual(rows["claude"]["state"], "starred")
        self.assertEqual(rows["codex"]["state"], "active")

    def test_list_json_without_usage_carries_no_rate_limits(self):
        doc = decode(run_swe(self.home, "list", "--json"))
        for row in doc["data"]["harnesses"]:
            self.assertIsNone(row["rate_limit"])

    def test_a_tag_filter_narrows_the_rows(self):
        registry = dict(REGISTRY, gemini={"command": "gemini", "tags": []})
        write_registry(self.home, registry)
        doc = decode(run_swe(self.home, "list", "vendor", "--json"))
        self.assertEqual(
            sorted(row["name"] for row in doc["data"]["harnesses"]),
            ["claude", "codex"])

    def test_refusals_leave_stdout_empty(self):
        for args in (["--json", "--bogus"], ["--json", "--scope=bogus"],
                     ["--json", "--scope"], ["a", "b", "--json"]):
            with self.subTest(args=args):
                result = run_swe(self.home, "list", *args)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertNotEqual(result.stderr.strip(), "")

    def test_json_with_edit_or_legend_is_refused_before_any_write(self):
        config = self.home / ".quiver" / "config" / "config.json"
        config.write_text(json.dumps({"list": {"session_window": 7}}))
        before = config.read_text()
        for args in (["edit", "--reset", "--json"], ["legend", "--json"]):
            with self.subTest(args=args):
                result = run_swe(self.home, "list", *args)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("--json", result.stderr)
        self.assertEqual(config.read_text(), before)

    def test_session_count_is_100_days_whatever_the_table_window(self):
        import time

        # The table can be set to a 7-day window; the JSON field promises 100.
        (self.home / ".quiver" / "config" / "config.json").write_text(
            json.dumps({"list": {"session_window": 7}}))
        session = (self.home / ".claude" / "projects" / "-work" /
                   "0b7c2a52-9f1e-4f43-8f43-3a8e6b1c2d01.jsonl")
        session.parent.mkdir(parents=True)
        session.write_text(json.dumps({
            "type": "user", "cwd": "/work",
            "sessionId": "0b7c2a52-9f1e-4f43-8f43-3a8e6b1c2d01",
            "message": {"role": "user", "content": "hi"},
        }) + "\n")
        thirty_days_ago = time.time() - 30 * 86400
        os.utime(session, (thirty_days_ago, thirty_days_ago))
        rows = {row["name"]: row for row in
                decode(run_swe(self.home, "list", "--json"))["data"]["harnesses"]}
        self.assertEqual(rows["claude"]["sessions_100d"], 1)


def write_rate_cache(home: Path, readings: dict, taken_at: float) -> None:
    """A fresh rate-limit cache, so `--usage` never reaches the network."""
    cache = home / ".quiver" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "rate_limits_cache.json").write_text(json.dumps({
        "cached_at": taken_at,
        "limits": readings,
        "updated_at": {name: taken_at for name in readings},
        "no_data": {},
    }))


def reading(**overrides) -> dict:
    base = {
        "tool_name": "claude", "used_percent": 28, "limit_reached": False,
        "reset_at": 1_900_000_000.0, "plan_type": "max",
        "window_seconds": 18000, "window": "5h",
        "remaining_units": None, "total_units": None, "fetched_at": 0.0,
    }
    base.update(overrides)
    return base


class ListUsageJsonTest(unittest.TestCase):
    def setUp(self):
        import time

        self.home = Path(tempfile.mkdtemp())
        write_registry(self.home, REGISTRY)
        self.taken_at = time.time() - 60

    def usage_rows(self, readings: dict) -> dict:
        write_rate_cache(self.home, readings, self.taken_at)
        result = run_swe(self.home, "list", "--usage", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return {row["name"]: row for row in decode(result)["data"]["harnesses"]}

    def test_starred_harness_carries_its_reading(self):
        rows = self.usage_rows({"claude": reading()})
        rate = rows["claude"]["rate_limit"]
        self.assertEqual(rate["status"], "ok")
        self.assertEqual(rate["used_percent"], 28)
        self.assertEqual(rate["remaining_percent"], 72)
        self.assertEqual(rate["window"], "5h")
        self.assertEqual(rate["plan_type"], "max")
        self.assertEqual(rate["reset_at"], 1_900_000_000.0)

    def test_cached_reading_keeps_the_time_it_was_taken(self):
        # A reused reading must not look fresh: the app shows its age.
        rows = self.usage_rows({"claude": reading()})
        self.assertAlmostEqual(
            rows["claude"]["rate_limit"]["fetched_at"], self.taken_at, delta=1)

    def test_unstarred_harness_is_not_polled(self):
        rows = self.usage_rows({"claude": reading()})
        self.assertIsNone(rows["codex"]["rate_limit"])

    def test_a_placeholder_plan_is_no_plan(self):
        rows = self.usage_rows({"claude": reading(plan_type="\u2014")})
        self.assertEqual(rows["claude"]["rate_limit"]["status"], "ok")
        self.assertIsNone(rows["claude"]["rate_limit"]["plan_type"])

    def test_excluded_harnesses_are_listed_but_not_polled(self):
        write_rate_cache(self.home, {"claude": reading()}, self.taken_at)
        result = run_swe(self.home, "list", "--usage", "--json", "--exclude=claude,codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["name"]: row for row in decode(result)["data"]["harnesses"]}
        self.assertEqual(set(rows), {"claude", "codex"})
        self.assertIsNone(rows["claude"]["rate_limit"])

    def test_sentinel_plan_types_become_a_status(self):
        rows = self.usage_rows(
            {"claude": reading(plan_type="no-sub", used_percent=0)})
        self.assertEqual(rows["claude"]["rate_limit"]["status"], "no-sub")
        self.assertIsNone(rows["claude"]["rate_limit"]["plan_type"])



class ExcludeSkipsTheFetcherTest(unittest.TestCase):
    """--exclude must keep a provider from being asked at all, refresh or not.

    In-process, with the network boundary (`get_all_rate_limits`) recording
    which providers it was asked for. Both the table and the JSON path build
    that set, so both are checked.
    """

    def run_list(self, *args):
        import io
        from contextlib import redirect_stdout
        from unittest import mock

        from quiver.harness import commands

        asked = []

        def fake_get_all(use_cache=True, tool_names=None, deadline=None):
            asked.append(set(tool_names))
            return {}

        registry = {name: {"command": name, "tags": []} for name in ("claude", "codex", "devin")}
        patches = [
            mock.patch.object(commands, "load_registry", return_value=registry),
            mock.patch.object(commands, "load_stars", return_value=["claude", "codex", "devin"]),
            mock.patch.object(commands, "_session_counts", return_value={}),
            mock.patch.object(commands, "_broken_tools", return_value=set()),
            mock.patch.object(commands, "is_installed", return_value=False),
            mock.patch("quiver.harness.archive.load_archive", dict),
            mock.patch("quiver.harness.rate_limits.get_all_rate_limits", side_effect=fake_get_all),
            # -r would otherwise clear the developer's real session caches.
            mock.patch("quiver.sessions.aggregator.invalidate_cache"),
            mock.patch("quiver.sessions.usage.invalidate_counts_cache"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with redirect_stdout(io.StringIO()):
            # The table path ends without an explicit status; JSON returns 0.
            self.assertIn(commands.cmd_list(list(args)), (0, None))
        return asked

    def test_json_refresh_never_asks_an_excluded_provider(self):
        asked = self.run_list("--usage", "--json", "-r", "--exclude=claude,codex")
        self.assertEqual(asked, [{"devin"}])

    def test_table_refresh_never_asks_an_excluded_provider(self):
        asked = self.run_list("--usage", "-r", "--exclude=claude")
        self.assertEqual(asked, [{"codex", "devin"}])


if __name__ == "__main__":
    unittest.main()
