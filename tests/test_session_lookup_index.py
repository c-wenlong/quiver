"""Transcript lookups walk a harness root once, not once per session.

``reports/transcripts.py::_find_session_file`` used to glob ``root/**`` for
every lookup, and the ``swe session`` status probes globbed
``~/.claude/projects/*/<id>.jsonl`` per row (726 rows x 241 project dirs was
175k ``lstat`` calls). Both now index the tree once. These tests pin that
the answers are the ones the per-lookup glob gave, that new and deleted
files are still seen, and that N lookups cost one walk.
"""

import glob
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.reports import transcripts
from swe.reports.transcripts import _find_session_file
from swe.sessions import status
from swe.sessions.models import Session

NOW = 1_700_000_000.0


def _old_find_session_file(root: Path, session_id: str, patterns=("*.jsonl",)):
    """The per-lookup glob this replaced, kept verbatim as the reference."""
    if session_id:
        direct = Path(os.path.expanduser(session_id))
        if direct.is_file():
            return direct
    sid = Path(session_id).stem if session_id else ""
    if not sid:
        return None
    if not root.exists():
        return None
    candidates = []
    for pattern in patterns:
        candidates.extend(root.glob(f"**/{pattern}"))
    for path in candidates:
        if sid == path.stem or sid in path.parts:
            return path
    return None


def _touch(path: Path, text: str = "{}\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class FindSessionFileTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "root"
        self.root.mkdir()

    def test_matches_the_per_lookup_glob(self):
        _touch(self.root / "a" / "s1.jsonl")
        _touch(self.root / "b" / "s1.jsonl")  # same stem twice
        _touch(self.root / "b" / "s2.jsonl")
        _touch(self.root / "s3" / "log.jsonl")  # matched by a path part
        _touch(self.root / "s3" / "other.jsonl")
        _touch(self.root / "c" / "d" / "deep.jsonl")
        ids = ["s1", "s2", "s3", "log", "deep", "d", "c", "missing", "s1.jsonl",
               self.root.name]
        for patterns in (("*.jsonl",), ("log.jsonl",), ("log.jsonl", "*.jsonl")):
            for sid in ids:
                with self.subTest(patterns=patterns, sid=sid):
                    self.assertEqual(
                        _find_session_file(self.root, sid, patterns),
                        _old_find_session_file(self.root, sid, patterns),
                    )

    def test_n_lookups_walk_the_root_once(self):
        for i in range(20):
            _touch(self.root / f"p{i % 3}" / f"s{i}.jsonl")
        real_glob = Path.glob
        with patch.object(Path, "glob", autospec=True, side_effect=real_glob) as walk:
            for i in range(20):
                self.assertIsNotNone(_find_session_file(self.root, f"s{i}"))
        self.assertEqual(walk.call_count, 1)

    def test_a_file_created_after_the_walk_is_found(self):
        _touch(self.root / "p" / "first.jsonl")
        self.assertIsNotNone(_find_session_file(self.root, "first"))
        late = _touch(self.root / "q" / "late.jsonl")
        self.assertEqual(_find_session_file(self.root, "late"), late)

    def test_a_deleted_file_is_not_returned(self):
        path = _touch(self.root / "p" / "gone.jsonl")
        self.assertEqual(_find_session_file(self.root, "gone"), path)
        path.unlink()
        self.assertIsNone(_find_session_file(self.root, "gone"))

    def test_a_moved_file_is_found_at_its_new_place(self):
        path = _touch(self.root / "p" / "mover.jsonl")
        self.assertEqual(_find_session_file(self.root, "mover"), path)
        moved = self.root / "q" / "mover.jsonl"
        moved.parent.mkdir()
        path.rename(moved)
        self.assertEqual(_find_session_file(self.root, "mover"), moved)

    def test_a_copy_appearing_beside_the_hit_is_seen_after_the_ttl(self):
        # Whatever the per-lookup glob would pick once both copies exist,
        # the index picks too once its walk is older than the TTL.
        first = _touch(self.root / "b" / "dup.jsonl")
        self.assertEqual(_find_session_file(self.root, "dup"), first)
        _touch(self.root / "a" / "dup.jsonl")
        now = transcripts.time.monotonic()
        later = now + transcripts._SESSION_FILE_INDEX_TTL_S + 1
        with patch.object(transcripts.time, "monotonic", return_value=later):
            self.assertEqual(
                _find_session_file(self.root, "dup"),
                _old_find_session_file(self.root, "dup"),
            )

    def test_lookups_within_the_ttl_share_one_walk(self):
        _touch(self.root / "p" / "s1.jsonl")
        _touch(self.root / "p" / "s2.jsonl")
        real_glob = Path.glob
        with patch.object(Path, "glob", autospec=True, side_effect=real_glob) as walk:
            _find_session_file(self.root, "s1")
            start = transcripts.time.monotonic()
            with patch.object(transcripts.time, "monotonic",
                              return_value=start + transcripts._SESSION_FILE_INDEX_TTL_S / 2):
                _find_session_file(self.root, "s2")
        self.assertEqual(walk.call_count, 1)

    def test_direct_path_empty_id_and_missing_root(self):
        path = _touch(self.root / "p" / "direct.jsonl")
        self.assertEqual(_find_session_file(self.root, str(path)), path)
        self.assertIsNone(_find_session_file(self.root, ""))
        self.assertIsNone(_find_session_file(self.root / "nope", "direct"))

    def test_index_is_per_root_and_pattern_set(self):
        _touch(self.root / "s9" / "log.jsonl")
        _touch(self.root / "s9" / "chat_history.jsonl")
        self.assertEqual(
            _find_session_file(self.root, "s9", ("log.jsonl",)).name, "log.jsonl")
        self.assertEqual(
            _find_session_file(self.root, "s9", ("chat_history.jsonl",)).name,
            "chat_history.jsonl")

    def tearDown(self):
        transcripts._SESSION_FILE_INDEX.clear()


def _claude_finished(path: Path) -> None:
    _touch(path, json.dumps({"type": "assistant", "message": {
        "content": [{"type": "text", "text": "Done."}],
        "stop_reason": "end_turn"}}) + "\n")


def _session(tool, sid):
    return Session(timestamp=NOW * 1000, agent=tool, path="/w", title="t",
                   session_id=sid, tool_name=tool)


class StatusProbeIndexTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        patcher = patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.projects = self.home / ".claude" / "projects"

    def test_claude_index_matches_the_glob(self):
        _claude_finished(self.projects / "-a" / "s1.jsonl")
        _claude_finished(self.projects / "-b" / "s1.jsonl")
        _claude_finished(self.projects / ".hidden" / "s2.jsonl")  # glob skips it
        _touch(self.projects / "not-a-dir.jsonl")
        index = status._claude_transcript_index(str(self.projects))
        for sid in ("s1", "s2", "s3"):
            with self.subTest(sid=sid):
                self.assertEqual(
                    index.get(sid + ".jsonl", []),
                    glob.glob(os.path.join(str(self.projects), "*", sid + ".jsonl")),
                )

    def test_cursor_index_matches_the_glob(self):
        root = self.home / ".cursor" / "projects"
        _touch(root / "p1" / "agent-transcripts" / "c1" / "c1.jsonl")
        _touch(root / "p2" / "agent-transcripts" / "c1" / "c1.jsonl")
        (root / "p3" / "agent-transcripts" / "c2").mkdir(parents=True)  # no file
        _touch(root / ".hidden" / "agent-transcripts" / "c3" / "c3.jsonl")
        index = status._cursor_transcript_index()
        for sid in ("c1", "c2", "c3", "c4"):
            with self.subTest(sid=sid):
                ours = [
                    p for p in (
                        os.path.join(t, sid, sid + ".jsonl") for t in index.get(sid, ())
                    ) if os.path.lexists(p)
                ]
                self.assertEqual(ours, glob.glob(os.path.join(
                    str(root), "*", "agent-transcripts", sid, sid + ".jsonl")))

    def test_statuses_scan_the_projects_dir_once(self):
        sessions = []
        for i in range(10):
            _claude_finished(self.projects / f"-p{i % 3}" / f"s{i}.jsonl")
            sessions.append(_session("claude", f"s{i}"))
        real = status._claude_transcript_index
        with patch.object(status, "_claude_transcript_index", side_effect=real) as build, \
                patch.object(status.glob, "glob", side_effect=glob.glob) as globbed:
            labels = status.session_statuses(sessions, now=NOW)
        self.assertEqual(labels, [status.DONE] * 10)
        self.assertEqual(build.call_count, 1)
        claude_globs = [c for c in globbed.call_args_list if "projects" in str(c)]
        self.assertEqual(claude_globs, [])

    def test_newest_copy_still_wins(self):
        old = self.projects / "-a" / "s1.jsonl"
        new = self.projects / "-b" / "s1.jsonl"
        _touch(old, json.dumps({"type": "assistant", "message": {
            "content": [{"type": "tool_use"}], "stop_reason": "tool_use"}}) + "\n")
        _claude_finished(new)
        os.utime(old, (NOW - 1000, NOW - 1000))
        os.utime(new, (NOW, NOW))
        self.assertEqual(status.session_status(_session("claude", "s1"), now=NOW),
                         status.DONE)


if __name__ == "__main__":
    unittest.main()
