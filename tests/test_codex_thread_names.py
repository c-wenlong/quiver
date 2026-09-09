"""parse_codex titles come from codex's thread store, not the transcript.

A codex thread's name lives outside its rollout file, in two places that say
different halves of the truth:

- ``~/.codex/state_<n>.sqlite`` holds the live name, codex's own note of the
  first message the user really sent (``preview``), and by that field's
  emptiness whether the thread was ever used at all.
- ``~/.codex/session_index.jsonl`` holds one append per name change, which is
  the only thing that separates a ``/name`` the user typed from the title
  codex generated for them.

Without either, the title falls back to the transcript's first user-role item,
which for codex is usually injected context (a plugin roster, an AGENTS.md)
rather than anything the user wrote.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.sessions.commands import _codex_resume_args
from quiver.sessions.parsers import _codex_is_rename, codex_thread_id, parse_codex

THREAD_ID = "01a084f0-b4fe-72d0-9b7b-1a5214957f73"
ROLLOUT = f"rollout-2026-09-09T14-52-42-{THREAD_ID}.jsonl"
REAL_EXPANDUSER = os.path.expanduser


def _session_meta(cwd="/Users/kaichen/project"):
    return {
        "type": "session_meta",
        "payload": {"id": THREAD_ID, "session_id": THREAD_ID, "cwd": cwd},
    }


def _user_message(text):
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


def _index(name, at="2026-09-09T14:52:50Z", tid=THREAD_ID):
    return {"id": tid, "thread_name": name, "updated_at": at}


def _write_store(path, rows):
    """``rows`` is a list of ``(id, name, preview)``."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE threads (id TEXT, name TEXT, preview TEXT, "
        "first_user_message TEXT)"
    )
    for tid, name, preview in rows:
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?)", (tid, name, preview, preview)
        )
    conn.commit()
    conn.close()


class ParseCodexTitleTest(unittest.TestCase):
    def _run(self, index_lines=None, store_rows=None, records=None,
             rollout_name=ROLLOUT, expect=1):
        if records is None:
            records = [_session_meta(), _user_message("Fix the login bug")]
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            day_dir = codex_home / "sessions" / "2026" / "09" / "09"
            day_dir.mkdir(parents=True)
            with open(day_dir / rollout_name, "w") as f:
                for rec in records:
                    f.write(json.dumps(rec) + "\n")

            if index_lines is not None:
                with open(codex_home / "session_index.jsonl", "w") as f:
                    for line in index_lines:
                        f.write(line if isinstance(line, str) else json.dumps(line))
                        f.write("\n")
            if store_rows is not None:
                _write_store(str(codex_home / "state_5.sqlite"), store_rows)

            def fake_expanduser(path):
                # Redirect the whole codex home, and never call back into the
                # patched name: the mock would recurse into itself.
                if path.startswith("~/.codex"):
                    return str(codex_home) + path[len("~/.codex"):]
                return REAL_EXPANDUSER(path)

            with patch(
                "quiver.sessions.parsers.os.path.expanduser", side_effect=fake_expanduser
            ):
                sessions = parse_codex()
        self.assertEqual(expect, len(sessions))
        return sessions[0] if sessions else None

    # -- falling back ----------------------------------------------------

    def test_no_store_and_no_index_uses_the_first_prompt(self):
        sess = self._run()
        self.assertEqual(sess.tool_name, "codex")
        self.assertEqual(sess.path, "/Users/kaichen/project")
        self.assertEqual(sess.title, "Fix the login bug")
        self.assertEqual(sess.title_source, "")

    def test_thread_the_store_never_heard_of_is_kept(self):
        sess = self._run(store_rows=[("some-other-thread", "x", "y")])
        self.assertEqual(sess.title, "Fix the login bug")

    # -- naming ----------------------------------------------------------

    def test_store_name_beats_the_first_prompt(self):
        sess = self._run(store_rows=[(THREAD_ID, "  vendor-table  ", "Fix the login bug")])
        self.assertEqual(sess.title, "vendor-table")

    def test_index_name_stands_in_when_the_store_has_none(self):
        sess = self._run(index_lines=[_index("vendor-table")])
        self.assertEqual(sess.title, "vendor-table")

    def test_last_index_record_for_a_thread_wins(self):
        sess = self._run(
            index_lines=[
                _index("fix the login bu"),
                _index("Login redirect fix"),
                _index("unrelated", tid="some-other-thread"),
            ]
        )
        self.assertEqual(sess.title, "Login redirect fix")

    def test_uppercase_store_id_still_matches(self):
        sess = self._run(store_rows=[(THREAD_ID.upper(), "vendor-table", "prompt")])
        self.assertEqual(sess.title, "vendor-table")

    def test_filename_without_a_uuid_is_left_alone(self):
        sess = self._run(
            store_rows=[(THREAD_ID, "vendor-table", "prompt")],
            rollout_name="rollout-legacy.jsonl",
        )
        self.assertEqual(sess.title, "Fix the login bug")

    def test_blank_and_malformed_index_records_are_ignored(self):
        sess = self._run(
            index_lines=[
                "not json at all",
                "[1, 2, 3]",
                "",
                _index("kept"),
                _index("   "),
                _index(None),
                {"id": THREAD_ID},
                {"thread_name": "no id"},
            ]
        )
        self.assertEqual(sess.title, "kept")

    # -- the preview fallback -------------------------------------------

    def test_preview_beats_an_injected_first_user_item(self):
        # The transcript's first user-role item is the plugin roster codex
        # injects; the user's real prompt is what the store recorded.
        sess = self._run(
            store_rows=[(THREAD_ID, "", "Review the auth refactor")],
            records=[
                _session_meta(),
                _user_message("Here is a list of plugins that are available..."),
                _user_message("Review the auth refactor"),
            ],
        )
        self.assertEqual(sess.title, "Review the auth refactor")
        # Nobody chose that text as a title, so it is not marked as one.
        self.assertEqual(sess.title_source, "")

    # -- hiding threads codex considers empty ----------------------------

    def test_thread_with_an_empty_preview_is_dropped(self):
        self._run(store_rows=[(THREAD_ID, "", "")], expect=0)

    def test_empty_preview_is_dropped_even_with_a_name(self):
        self._run(store_rows=[(THREAD_ID, "leftover", "")], expect=0)

    def test_nothing_is_dropped_when_the_store_is_unreadable(self):
        sess = self._run(index_lines=[_index("vendor-table")])
        self.assertEqual(sess.title, "vendor-table")


class CodexRenameHeuristicTest(unittest.TestCase):
    """``_codex_is_rename`` on the shapes seen in a real session index."""

    PROMPT = "can you check whether there is a loopback issue with the proxy"

    def test_placeholder_then_generated_title_is_not_a_rename(self):
        # Codex stamps a prefix of the prompt, then replaces it seconds later.
        self.assertFalse(
            _codex_is_rename(
                ["can you check whether there is a loo", "Check Docker proxy loopback"],
                5_000,
                self.PROMPT,
            )
        )

    def test_a_third_name_after_the_generated_one_is_a_rename(self):
        self.assertTrue(
            _codex_is_rename(
                ["can you check whether there is a loo", "Check Docker proxy loopback",
                 "docker-loopback"],
                600_000,
                self.PROMPT,
            )
        )

    def test_second_name_is_a_rename_when_codex_skipped_the_placeholder(self):
        # No prompt-prefix record, so the first entry is already codex's
        # generated title and the second has to be the user's.
        self.assertTrue(
            _codex_is_rename(
                ["Map role-based access", "permissions-map"],
                20_000,
                "run through all files in toolbox and egocentric-data-process",
            )
        )

    def test_a_late_name_is_a_rename_even_inside_codex_s_two_entries(self):
        self.assertTrue(
            _codex_is_rename(
                ["can you use gcloud cli to check out", "vendor-table"],
                451_000,
                "can you use gcloud cli to check out which tables store heights",
            )
        )

    def test_a_lone_placeholder_is_not_a_rename(self):
        self.assertFalse(
            _codex_is_rename(["use clerk cli to send an inv"], 0.0,
                             "use clerk cli to send an invitation for a test user")
        )

    def test_no_records_is_not_a_rename(self):
        self.assertFalse(_codex_is_rename([], 999_999, "anything"))

    def test_missing_prompt_leaves_codex_a_single_entry_budget(self):
        # With no prompt to compare against, nothing can be a placeholder, so
        # a second name is already one more than codex writes.
        self.assertTrue(_codex_is_rename(["Some title", "renamed"], 0.0, ""))
        self.assertFalse(_codex_is_rename(["Some title"], 0.0, ""))


class CodexResumeArgsTest(unittest.TestCase):
    """`codex resume <uuid>`, not `codex --resume <rollout stem>`.

    Codex resumes through a subcommand whose argument is a thread uuid or a
    thread name. Quiver's session id is the rollout file's stem, because that
    is what the transcript readers look a session up by, so the uuid has to be
    lifted back out of it.
    """

    STEM = f"rollout-2026-09-09T14-52-42-{THREAD_ID}"

    def test_uuid_is_lifted_out_of_the_rollout_stem(self):
        self.assertEqual(THREAD_ID, codex_thread_id(self.STEM))

    def test_uuid_lookup_is_case_insensitive_and_normalised(self):
        self.assertEqual(THREAD_ID, codex_thread_id(self.STEM.upper()))

    def test_a_bare_uuid_passes_straight_through(self):
        self.assertEqual(THREAD_ID, codex_thread_id(THREAD_ID))

    def test_no_uuid_yields_nothing(self):
        for value in ("rollout-legacy", "", None, "not-a-uuid-at-all"):
            self.assertEqual("", codex_thread_id(value))

    def test_resume_uses_the_subcommand_and_the_uuid(self):
        self.assertEqual(["resume", THREAD_ID], _codex_resume_args(self.STEM))

    def test_resume_falls_back_to_a_bare_launch_without_a_uuid(self):
        # Better to open codex in the session's directory than to hand it an
        # argument it rejects outright, which is what `--resume <stem>` did.
        self.assertEqual([], _codex_resume_args("rollout-legacy"))
        self.assertEqual([], _codex_resume_args(""))


if __name__ == "__main__":
    unittest.main()
