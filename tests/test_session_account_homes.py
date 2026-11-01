"""Sessions in SWE's extra account homes are listed too.

SWE's extra Claude and Codex accounts keep a harness-shaped config dir
at ``~/.swe/accounts/<vendor>/<id>/home`` — a ``projects`` or ``sessions``
tree inside parses exactly like the default home's. A session found there
is tagged with its ``account_home`` so the listing can name the account
and resume under it; ``resume_json`` carries the env var that points the
CLI at it.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.sessions.models import Session, SessionRow
from swe.sessions.parsers import parse_claude, parse_codex
from swe.sessions.resume import resume_json

REAL_EXPANDUSER = os.path.expanduser
PROJECT = "-Users-dev-project"
CWD = "/Users/dev/project"
THREAD_ID = "01a084f0-b4fe-72d0-9b7b-1a5214957f73"


def _claude_user(text):
    return {
        "type": "user",
        "cwd": CWD,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def _codex_meta():
    return {
        "type": "session_meta",
        "payload": {"id": THREAD_ID, "session_id": THREAD_ID, "cwd": CWD},
    }


def _codex_user(text):
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


class _FakeHome:
    """Redirects ``~`` wholesale to a temp dir, so both the default homes
    and ``~/.swe/accounts`` land inside it."""

    def __init__(self, root):
        self.root = root

    def __call__(self, path):
        if path.startswith("~"):
            return str(self.root) + path[1:]
        return REAL_EXPANDUSER(path)


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


class ClaudeAccountHomesTest(unittest.TestCase):
    def test_sessions_in_an_account_home_are_listed_with_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            default_projects = home / ".claude" / "projects" / PROJECT
            _write_jsonl(default_projects / "s1.jsonl", [_claude_user("Fix login")])
            account = home / ".swe" / "accounts" / "claude" / "claude-work" / "home"
            account_projects = account / "projects" / PROJECT
            _write_jsonl(account_projects / "s2.jsonl", [_claude_user("Ship the parser")])
            # A stray folder that is not an account home is ignored.
            (home / ".swe" / "accounts" / "claude" / "retired" / "data").mkdir(parents=True)

            with patch(
                "swe.sessions.parsers.os.path.expanduser",
                side_effect=_FakeHome(home),
            ):
                sessions = {s.session_id: s for s in parse_claude()}

        self.assertEqual({"s1", "s2"}, set(sessions))
        self.assertEqual("", sessions["s1"].account_home)
        self.assertEqual(str(account), sessions["s2"].account_home)
        self.assertEqual("Ship the parser", sessions["s2"].title)
        self.assertEqual(CWD, sessions["s2"].path)

    def test_no_accounts_dir_lists_the_default_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_jsonl(
                home / ".claude" / "projects" / PROJECT / "s1.jsonl",
                [_claude_user("Fix login")],
            )
            with patch(
                "swe.sessions.parsers.os.path.expanduser",
                side_effect=_FakeHome(home),
            ):
                sessions = parse_claude()
        self.assertEqual(["s1"], [s.session_id for s in sessions])


    def test_the_same_id_in_two_homes_keeps_both_rows(self):
        """A transcript copied into another profile is each account's own
        conversation, so dedup is scoped by (id, account_home)."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            record = [_claude_user("Fix login")]
            _write_jsonl(home / ".claude" / "projects" / PROJECT / "s1.jsonl", record)
            account = home / ".swe" / "accounts" / "claude" / "claude-work" / "home"
            _write_jsonl(account / "projects" / PROJECT / "s1.jsonl", record)

            with patch(
                "swe.sessions.parsers.os.path.expanduser",
                side_effect=_FakeHome(home),
            ):
                sessions = {s.account_home: s for s in parse_claude()}

        self.assertEqual({"", str(account)}, set(sessions))
        self.assertEqual("s1", sessions[str(account)].session_id)

    def test_account_home_survives_the_session_cache(self):
        from swe.sessions import aggregator

        session = Session(
            timestamp=1_000, agent="Claude Code", path="/c/quiver",
            title="Account work", session_id="s2", tool_name="claude",
            account_home="/x/.swe/accounts/claude/claude-work/home",
        )
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "sessions.json"
            with patch.object(aggregator, "SESSION_CACHE_FILE", cache_file):
                aggregator._save_cached_sessions([session])
                loaded = aggregator._load_cached_sessions()
        self.assertEqual(session.account_home, loaded[0].account_home)


class CodexAccountHomesTest(unittest.TestCase):
    def test_sessions_in_an_account_home_are_listed_with_it(self):
        rollout = f"rollout-2026-09-09T14-52-42-{THREAD_ID}.jsonl"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            account = home / ".swe" / "accounts" / "codex" / "codex-nus" / "home"
            day_dir = account / "sessions" / "2026" / "09" / "09"
            _write_jsonl(day_dir / rollout, [_codex_meta(), _codex_user("Fix the login bug")])

            with patch(
                "swe.sessions.parsers.os.path.expanduser",
                side_effect=_FakeHome(home),
            ):
                sessions = parse_codex()

        self.assertEqual(1, len(sessions))
        self.assertEqual(str(account), sessions[0].account_home)
        self.assertEqual("Fix the login bug", sessions[0].title)

    def test_a_profile_thread_renamed_in_its_own_home_wins(self):
        """The thread store and session index live per home; a rename in the
        profile's own ``session_index.jsonl`` reaches its session."""
        rollout = f"rollout-2026-09-09T14-52-42-{THREAD_ID}.jsonl"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            account = home / ".swe" / "accounts" / "codex" / "codex-nus" / "home"
            day_dir = account / "sessions" / "2026" / "09" / "09"
            _write_jsonl(
                day_dir / rollout,
                [_codex_meta(), _codex_user("injected plugin roster")],
            )
            _write_jsonl(
                account / "session_index.jsonl",
                [{"id": THREAD_ID, "thread_name": "vendor-table",
                  "updated_at": "2026-09-09T14:52:50Z"}],
            )

            with patch(
                "swe.sessions.parsers.os.path.expanduser",
                side_effect=_FakeHome(home),
            ):
                sessions = parse_codex()

        self.assertEqual(1, len(sessions))
        self.assertEqual("vendor-table", sessions[0].title)

    def test_the_same_thread_in_two_homes_keeps_both_rows(self):
        rollout = f"rollout-2026-09-09T14-52-42-{THREAD_ID}.jsonl"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            records = [_codex_meta(), _codex_user("Fix the login bug")]
            _write_jsonl(home / ".codex" / "sessions" / "2026" / "09" / "09" / rollout, records)
            account = home / ".swe" / "accounts" / "codex" / "codex-nus" / "home"
            _write_jsonl(account / "sessions" / "2026" / "09" / "09" / rollout, records)

            with patch(
                "swe.sessions.parsers.os.path.expanduser",
                side_effect=_FakeHome(home),
            ):
                sessions = parse_codex()

        self.assertEqual({"", str(account)}, {s.account_home for s in sessions})


class AccountHomeInJsonTest(unittest.TestCase):
    def test_row_carries_account_home_and_resume_env(self):
        session = Session(
            timestamp=1_000, agent="Claude Code", path="/c/quiver",
            title="Account work", session_id="s2", tool_name="claude",
            account_home="/Users/k/.swe/accounts/claude/claude-work/home",
        )
        row = SessionRow.of(session, "done", resume_json(session)).to_json()
        self.assertEqual(
            "/Users/k/.swe/accounts/claude/claude-work/home", row["account_home"]
        )
        self.assertEqual(
            {"CLAUDE_CONFIG_DIR": "/Users/k/.swe/accounts/claude/claude-work/home"},
            row["resume"]["env"],
        )

    def test_default_home_row_has_no_account_or_env(self):
        session = Session(
            timestamp=1_000, agent="Claude Code", path="/c/quiver",
            title="Default work", session_id="s1", tool_name="claude",
        )
        row = SessionRow.of(session, "done", resume_json(session)).to_json()
        self.assertIsNone(row["account_home"])
        self.assertNotIn("env", row["resume"])


if __name__ == "__main__":
    unittest.main()
