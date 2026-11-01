"""`swe harness usage-daily codex` against a fake `codex` on PATH.

The fake is a tiny Python script that speaks the app-server JSON-RPC
handshake over stdio: answer `initialize`, then answer
`account/usage/read` however the test configures it via env vars.
"""

import io
import json
import os
import signal
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

# The shebang is filled with this interpreter's absolute path: the Nix
# build sandbox has no /usr/bin/env, so `#!/usr/bin/env python3` can't run.
FAKE_CODEX = """#!{python}
import json
import os
import sys
import time

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except ValueError:
        continue
    echo = os.environ.get("FAKE_CODEX_ECHO")
    if echo:
        with open(echo, "w") as f:
            f.write(os.environ.get("CODEX_HOME", ""))
    mode = os.environ.get("FAKE_CODEX_MODE", "ok")
    if message.get("method") == "initialize":
        if mode == "exit-early":
            sys.exit(0)
        if mode == "init-error":
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {
                "code": -32000, "message": "server is shutting down"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                          "result": {"ok": True}}), flush=True)
    elif message.get("method") == "account/usage/read":
        mode = os.environ.get("FAKE_CODEX_MODE", "ok")
        if mode == "sleep":
            pid_file = os.environ.get("FAKE_CODEX_PID")
            if pid_file:
                with open(pid_file, "w") as f:
                    f.write(str(os.getpid()))
            time.sleep(60)
        elif mode == "noise":
            print("not json at all", flush=True)
            print(json.dumps({"jsonrpc": "2.0", "method": "some/notification"}),
                  flush=True)
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {
                "summary": {"lifetimeTokens": 1234, "peakDailyTokens": 500,
                            "currentStreakDays": 3, "longestStreakDays": 9},
                "dailyUsageBuckets": [
                    {"startDate": "2026-10-12", "tokens": 100},
                    {"startDate": "2026-10-13", "tokens": 400}]}}),
                flush=True)
        elif mode == "auth":
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {
                "code": -32000,
                "message": "codex account authentication required to read "
                           "token usage"}}), flush=True)
        elif mode == "partial-buckets":
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {
                "summary": {},
                "dailyUsageBuckets": [
                    {"startDate": "2026-10-12"},
                    {"tokens": 7},
                    {"startDate": "2026-10-13", "tokens": 400}]}}), flush=True)
        elif mode == "unknown-method":
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {
                "code": -32601, "message": "method not found"}}), flush=True)
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {
                "summary": {"lifetimeTokens": 1234, "peakDailyTokens": 500,
                            "currentStreakDays": 3, "longestStreakDays": 9},
                "dailyUsageBuckets": [
                    {"startDate": "2026-10-12", "tokens": 100},
                    {"startDate": "2026-10-13", "tokens": 400}]}}), flush=True)
"""


class CodexUsageDailyTest(unittest.TestCase):
    """`fetch_codex_usage_daily` and the command that wraps it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin_dir = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin_dir)
        self.echo = os.path.join(self.tmp.name, "codex_home.txt")

    def _fake_codex(self, mode="ok"):
        path = os.path.join(self.bin_dir, "codex")
        with open(path, "w") as f:
            f.write(FAKE_CODEX.replace("{python}", sys.executable, 1))
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP
                 | stat.S_IXOTH)
        env = dict(os.environ)
        env["PATH"] = self.bin_dir + os.pathsep + env.get("PATH", "")
        env["FAKE_CODEX_MODE"] = mode
        env["FAKE_CODEX_ECHO"] = self.echo
        return env

    def _fetch(self, env, account_dir=None, timeout=5.0):
        from swe.harness.codex_usage import fetch_codex_usage_daily

        with patch.dict(os.environ, env, clear=True):
            return fetch_codex_usage_daily(account_dir, timeout=timeout)

    def _run(self, args):
        from swe.harness.commands import cmd_harness_usage_daily

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cmd_harness_usage_daily(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_ok_maps_summary_and_days(self):
        report = self._fetch(self._fake_codex())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["summary"], {
            "lifetime_tokens": 1234, "peak_daily_tokens": 500,
            "current_streak_days": 3, "longest_streak_days": 9})
        self.assertEqual(report["days"], [
            {"date": "2026-10-12", "tokens": 100},
            {"date": "2026-10-13", "tokens": 400}])

    def test_codex_home_propagates_to_the_child(self):
        account_dir = os.path.join(self.tmp.name, "codex-profile")
        os.makedirs(account_dir)
        self._fetch(self._fake_codex(), account_dir=account_dir)
        with open(self.echo) as f:
            self.assertEqual(f.read(), account_dir)

    def test_default_home_is_tilde_codex(self):
        home = os.path.join(self.tmp.name, "home")
        os.makedirs(home)
        env = self._fake_codex()
        env["HOME"] = home
        self._fetch(env)
        with open(self.echo) as f:
            self.assertEqual(f.read(), os.path.join(home, ".codex"))

    def test_auth_required_maps_its_status(self):
        report = self._fetch(self._fake_codex("auth"))
        self.assertEqual(report["status"], "auth-required")
        self.assertIsNone(report["summary"])
        self.assertEqual(report["days"], [])

    def test_method_not_found_is_unsupported(self):
        report = self._fetch(self._fake_codex("unknown-method"))
        self.assertEqual(report["status"], "unsupported")

    def test_an_initialize_error_is_classified(self):
        report = self._fetch(self._fake_codex("init-error"))
        self.assertEqual(report["status"], "unavailable")
        self.assertIsNone(report["summary"])

    def test_a_child_that_exits_before_answering_is_unavailable(self):
        report = self._fetch(self._fake_codex("exit-early"))
        self.assertEqual(report["status"], "unavailable")

    def test_buckets_missing_a_date_or_tokens_are_skipped(self):
        report = self._fetch(self._fake_codex("partial-buckets"))
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["days"], [{"date": "2026-10-13", "tokens": 400}])
        self.assertEqual(report["summary"]["lifetime_tokens"], 0)

    def test_classify_error_falls_back_to_unavailable(self):
        from swe.harness.codex_usage import _classify_error

        self.assertEqual(_classify_error(None), "unavailable")
        self.assertEqual(_classify_error({"code": -32000, "message": "boom"}),
                         "unavailable")
        self.assertEqual(_classify_error({"message": "Unknown method foo"}),
                         "unsupported")

    def test_human_mode_unsupported_and_unavailable_exit_one(self):
        from swe.harness.codex_usage import print_codex_usage_daily

        for status in ("unsupported", "unavailable"):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out):
                code = print_codex_usage_daily(
                    {"status": status, "summary": None, "days": []}, None, err=err)
            self.assertEqual(code, 1)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("codex", err.getvalue())

    def test_a_timeout_is_unavailable_and_the_child_dies(self):
        pid_file = os.path.join(self.tmp.name, "fake.pid")
        env = self._fake_codex("sleep")
        env["FAKE_CODEX_PID"] = pid_file
        report = self._fetch(env, timeout=1.0)
        self.assertEqual(report["status"], "unavailable")
        with open(pid_file) as f:
            pid = int(f.read())

        def reap():
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self.addCleanup(reap)
        # Killed and waited for before the call returned: the pid is gone.
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_argument_errors_exit_one(self):
        missing = os.path.join(self.tmp.name, "nope")
        for args in (["codex", "--account-dir"],
                     ["codex", "--bogus"],
                     ["codex", "extra"],
                     ["codex", f"--account-dir={missing}"]):
            code, out, err = self._run(args)
            self.assertEqual(code, 1, args)
            # Human mode reports on stdout like the other harness commands.
            self.assertTrue(out or err, args)

    def test_harness_dispatch_reaches_usage_daily(self):
        from swe.setup.commands import cmd_harness

        env = self._fake_codex()
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), redirect_stdout(out), \
                redirect_stderr(err):
            code = cmd_harness(["usage-daily", "codex", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["data"]["status"], "ok")

    def test_noise_on_stdout_is_ignored(self):
        report = self._fetch(self._fake_codex("noise"))
        self.assertEqual(report["status"], "ok")
        self.assertEqual(len(report["days"]), 2)

    def test_a_missing_binary_is_unavailable(self):
        env = dict(os.environ)
        env["PATH"] = self.bin_dir  # nothing inside
        with patch.dict(os.environ, env, clear=True):
            from swe.harness.codex_usage import fetch_codex_usage_daily

            report = fetch_codex_usage_daily(timeout=2.0)
        self.assertEqual(report["status"], "unavailable")

    def test_json_emits_the_report(self):
        account_dir = os.path.join(self.tmp.name, "acct")
        os.makedirs(account_dir)
        env = self._fake_codex()
        env["PATH"] = self.bin_dir + os.pathsep + env["PATH"]
        with patch.dict(os.environ, env, clear=True):
            code, out, _ = self._run(
                ["codex", f"--account-dir={account_dir}", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["status"], "ok")
        self.assertEqual(len(data["days"]), 2)

    def test_only_codex_is_supported(self):
        code, out, err = self._run(["claude", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("codex", err)

    def test_human_mode_prints_the_last_days(self):
        account_dir = os.path.join(self.tmp.name, "acct")
        os.makedirs(account_dir)
        env = self._fake_codex()
        with patch.dict(os.environ, env, clear=True):
            code, out, _ = self._run(
                ["codex", f"--account-dir={account_dir}"])
        self.assertEqual(code, 0)
        self.assertIn("2026-10-13", out)
        self.assertIn("1,234", out)

    def test_human_mode_auth_required_exits_zero(self):
        account_dir = os.path.join(self.tmp.name, "acct")
        os.makedirs(account_dir)
        with patch.dict(os.environ, self._fake_codex("auth"), clear=True):
            code, out, _ = self._run(
                ["codex", f"--account-dir={account_dir}"])
        self.assertEqual(code, 0)
        self.assertIn("not signed in", out)

    def test_human_mode_unavailable_exits_nonzero(self):
        account_dir = os.path.join(self.tmp.name, "acct")
        os.makedirs(account_dir)
        env = self._fake_codex("sleep")
        with patch.dict(os.environ, env, clear=True):
            with patch("swe.harness.codex_usage.DEFAULT_TIMEOUT", 0.2):
                code, out, err = self._run(
                    ["codex", f"--account-dir={account_dir}"])
        self.assertEqual(code, 1)
        self.assertIn("could not be read", err)


if __name__ == "__main__":
    unittest.main()
