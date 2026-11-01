"""``harness/remedies.py``: the per-harness fix table the CLI, Doctor and
the app all read."""

import unittest

from swe.harness import remedies
from swe.harness.remedies import (
    AUTH_REQUIRED,
    PERMISSION_REQUIRED,
    SIGNED_OUT,
    TIMEOUT,
    UNAVAILABLE,
    remedy_for,
    remedy_for_account,
)


class RemedyTableTest(unittest.TestCase):
    def test_every_harness_row_has_what_the_fix_sheet_needs(self):
        for name, rows in remedies.REMEDIES.items():
            for status, row in rows.items():
                with self.subTest(harness=name, status=status):
                    for key in ("kind", "title", "summary", "steps"):
                        self.assertIn(key, row)
                    self.assertTrue(row["steps"])

    def test_login_failures_name_the_harness_login_command(self):
        expected = {
            "claude": "claude auth login",
            "codex": "codex login",
            "copilot": "gh auth login",
            "cursor": "cursor-agent login",
            "devin": "devin auth login",
            "freebuff": "freebuff login",
        }
        for name, command in expected.items():
            with self.subTest(harness=name):
                self.assertEqual(remedy_for(name, SIGNED_OUT)["command"], command)
        # A stale login is renewed by the same command, except Cursor's,
        # whose CLI renews the session the editor's copy can't.
        self.assertEqual(remedy_for("codex", AUTH_REQUIRED)["command"], "codex login")
        self.assertEqual(remedy_for("cursor", AUTH_REQUIRED)["command"],
                         "cursor-agent status")

    def test_an_unknown_harness_gets_the_generic_row_with_its_name(self):
        remedy = remedy_for("oddball", SIGNED_OUT, display_name="Oddball")
        self.assertEqual(remedy["summary"], "Oddball isn't signed in")
        self.assertNotIn("command", remedy)

    def test_a_permission_fix_opens_the_privacy_pane(self):
        for name in ("cursor", "devin", "codex"):
            with self.subTest(harness=name):
                remedy = remedy_for(name, PERMISSION_REQUIRED)
                self.assertEqual(remedy["kind"], "grant-permission")
                self.assertTrue(remedy["url"].startswith("x-apple.systempreferences:"))

    def test_ok_has_no_remedy(self):
        self.assertIsNone(remedy_for("cursor", "ok"))
        self.assertIsNone(remedy_for("cursor", "no-sub"))


class AccountRemedyTest(unittest.TestCase):
    def test_a_login_fix_targets_the_accounts_own_files(self):
        devin = remedy_for_account("devin", SIGNED_OUT, "/acct")
        self.assertEqual(devin["command"], "XDG_DATA_HOME=/acct devin auth login")
        self.assertIn("isn't signed in", devin["summary"])
        stale = remedy_for_account("devin", AUTH_REQUIRED, "/acct")
        self.assertIn("rejected", stale["summary"])
        cursor = remedy_for_account("cursor", AUTH_REQUIRED, "/acct")
        self.assertEqual(cursor["kind"], "open-app")
        self.assertIn("--user-data-dir=/acct", cursor["command"])

    def test_the_account_dir_is_shell_quoted(self):
        """Interpolated inside double quotes, a `"` ended the string and a
        `$(...)` or backtick in the path ran when the command did."""
        import shlex

        hostile = '/acct/it"s $(touch x) `id`'
        for name in ("devin", "cursor"):
            with self.subTest(harness=name):
                command = remedy_for_account(name, SIGNED_OUT, hostile)["command"]
                words = shlex.split(command)
                self.assertTrue(any(w.endswith("=" + hostile) for w in words), words)

    def test_an_outage_or_permission_keeps_the_generic_fix(self):
        for name in ("devin", "cursor"):
            for status in (UNAVAILABLE, TIMEOUT, PERMISSION_REQUIRED):
                with self.subTest(harness=name, status=status):
                    self.assertEqual(remedy_for_account(name, status, "/acct"),
                                     remedy_for(name, status))


if __name__ == "__main__":
    unittest.main()
