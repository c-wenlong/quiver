"""Sessions in Quiver's extra account homes are listed too.

Quiver's extra Claude and Codex accounts keep a harness-shaped config dir
at ``~/.quiver/accounts/<vendor>/<id>/home`` — a ``projects`` or ``sessions``
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

from quiver.sessions.models import Session, SessionRow
from quiver.sessions.parsers import parse_claude, parse_codex
from quiver.sessions.resume import resume_json

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
    and ``~/.quiver/accounts`` land inside it."""

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
            account = home / ".quiver" / "accounts" / "claude" / "claude-work" / "home"
            account_projects = account / "projects" / PROJECT
            _write_jsonl(account_projects / "s2.jsonl", [_claude_user("Ship the parser")])
            # A stray folder that is not an account home is ignored.
            (home / ".quiver" / "accounts" / "claude" / "retired" / "data").mkdir(parents=True)

            with patch(
                "quiver.sessions.parsers.os.path.expanduser",
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
                "quiver.sessions.parsers.os.path.expanduser",
                side_effect=_FakeHome(home),
            ):
                sessions = parse_claude()
        self.assertEqual(["s1"], [s.session_id for s in sessions])


class CodexAccountHomesTest(unittest.TestCase):
    def test_sessions_in_an_account_home_are_listed_with_it(self):
        rollout = f"rollout-2026-09-09T14-52-42-{THREAD_ID}.jsonl"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            account = home / ".quiver" / "accounts" / "codex" / "codex-nus" / "home"
            day_dir = account / "sessions" / "2026" / "09" / "09"
            _write_jsonl(day_dir / rollout, [_codex_meta(), _codex_user("Fix the login bug")])

            with patch(
                "quiver.sessions.parsers.os.path.expanduser",
                side_effect=_FakeHome(home),
            ):
                sessions = parse_codex()

        self.assertEqual(1, len(sessions))
        self.assertEqual(str(account), sessions[0].account_home)
        self.assertEqual("Fix the login bug", sessions[0].title)


class AccountHomeInJsonTest(unittest.TestCase):
    def test_row_carries_account_home_and_resume_env(self):
        session = Session(
            timestamp=1_000, agent="Claude Code", path="/c/quiver",
            title="Account work", session_id="s2", tool_name="claude",
            account_home="/Users/k/.quiver/accounts/claude/claude-work/home",
        )
        row = SessionRow.of(session, "done", resume_json(session)).to_json()
        self.assertEqual(
            "/Users/k/.quiver/accounts/claude/claude-work/home", row["account_home"]
        )
        self.assertEqual(
            {"CLAUDE_CONFIG_DIR": "/Users/k/.quiver/accounts/claude/claude-work/home"},
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
