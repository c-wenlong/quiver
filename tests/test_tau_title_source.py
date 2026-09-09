"""``title_source`` for tau sessions read from ``~/.tau/sessions/*/index.jsonl``.

Tau has no auto-titler, so a non-null index ``title`` is a ``/name`` rename,
except for the ``"Default session"`` placeholder that
``get_or_create_default_session`` stamps on ``default-<hash>`` ids.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quiver.sessions.parsers import _tau_index_title, parse_tau


def _transcript(first_prompt: str) -> str:
    return "\n".join(
        [
            json.dumps(
                {
                    "type": "session_info",
                    "cwd": "/Users/test",
                    "timestamp": 1784324840.1,
                }
            ),
            json.dumps(
                {
                    "type": "message",
                    "timestamp": 1784324840.3,
                    "message": {"role": "user", "content": first_prompt},
                }
            ),
        ]
    )


def _write_project(base: Path, records: list[tuple[str, object, str]]) -> None:
    """``records`` is ``(session_id, index_title, first_prompt)`` triples."""
    proj = base / "home-2bddf4"
    proj.mkdir()
    lines = []
    for sid, title, prompt in records:
        sess_file = proj / f"{sid}.jsonl"
        sess_file.write_text(_transcript(prompt))
        lines.append(
            json.dumps(
                {
                    "id": sid,
                    "path": str(sess_file),
                    "cwd": "/Users/test",
                    "model": "claude-sonnet-4",
                    "provider_name": "anthropic",
                    "title": title,
                    "created_at": 1784324840.3,
                    "updated_at": 1784324840.3,
                }
            )
        )
    (proj / "index.jsonl").write_text("\n".join(lines) + "\n")


def _parse(base: Path):
    with mock.patch(
        "quiver.sessions.parsers.os.path.expanduser",
        side_effect=lambda p: str(base) if p.endswith("sessions") else p,
    ):
        return {s.session_id: s for s in parse_tau()}


class TauTitleSourceTest(unittest.TestCase):
    def test_named_session_is_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_project(Path(tmp), [("sess-named", "Fix the parser", "hello there")])
            sessions = _parse(Path(tmp))
        self.assertEqual(len(sessions), 1)
        sess = sessions["sess-named"]
        self.assertEqual(sess.title, "Fix the parser")
        self.assertEqual(sess.title_source, "rename")

    def test_null_title_falls_back_to_first_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_project(Path(tmp), [("sess-null", None, "what is the version")])
            sessions = _parse(Path(tmp))
        sess = sessions["sess-null"]
        self.assertEqual(sess.title, "what is the version")
        self.assertEqual(sess.title_source, "")

    def test_default_placeholder_on_default_id_is_not_a_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_project(
                Path(tmp),
                [("default-2bddf4", "Default session", "run the tests")],
            )
            sessions = _parse(Path(tmp))
        sess = sessions["default-2bddf4"]
        # The placeholder is dropped so the transcript's first prompt stands in.
        self.assertEqual(sess.title, "run the tests")
        self.assertEqual(sess.title_source, "")

    def test_default_literal_on_other_id_is_a_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_project(
                Path(tmp),
                [("sess-plain", "Default session", "run the tests")],
            )
            sessions = _parse(Path(tmp))
        sess = sessions["sess-plain"]
        self.assertEqual(sess.title, "Default session")
        self.assertEqual(sess.title_source, "rename")

    def test_mixed_index_keeps_records_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_project(
                Path(tmp),
                [
                    ("sess-named", "Ship it", "one"),
                    ("sess-null", None, "two"),
                    ("default-2bddf4", "Default session", "three"),
                    ("sess-plain", "Default session", "four"),
                ],
            )
            sessions = _parse(Path(tmp))
        self.assertEqual(len(sessions), 4)
        self.assertEqual(
            {sid: s.title_source for sid, s in sessions.items()},
            {
                "sess-named": "rename",
                "sess-null": "",
                "default-2bddf4": "",
                "sess-plain": "rename",
            },
        )


class TauIndexTitleHelperTest(unittest.TestCase):
    def test_odd_values_never_raise(self):
        self.assertEqual(_tau_index_title({}), ("", ""))
        self.assertEqual(_tau_index_title({"title": None}), ("", ""))
        self.assertEqual(_tau_index_title({"title": ""}), ("", ""))
        self.assertEqual(_tau_index_title({"title": "   "}), ("", ""))
        self.assertEqual(_tau_index_title({"title": 42}), ("", ""))
        self.assertEqual(_tau_index_title({"title": ["x"]}), ("", ""))
        self.assertEqual(
            _tau_index_title({"title": "Default session", "id": 7}),
            ("Default session", "rename"),
        )
        self.assertEqual(
            _tau_index_title({"title": "Named", "id": "default-abc"}),
            ("Named", "rename"),
        )


if __name__ == "__main__":
    unittest.main()
