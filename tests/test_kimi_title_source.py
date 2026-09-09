"""Kimi session titles: the state.json sidecar decides ``title_source``."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quiver.sessions.parsers import parse_kimi

WORK_PATH = "/Users/test/code"


class KimiTitleSourceTest(unittest.TestCase):
    def _run(self, sidecar_name: str | None, sidecar_body: str | None):
        """Build one kimi session under a temp root and parse it.

        ``sidecar_name`` is ``state.json`` / ``metadata.json`` / ``None``;
        ``sidecar_body`` is written verbatim so malformed JSON can be tested.
        """
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            digest = hashlib.md5(WORK_PATH.encode()).hexdigest()
            sessions_root = home / "sessions"
            sess_dir = sessions_root / digest / "sid-1"
            sess_dir.mkdir(parents=True)
            (sess_dir / "context.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"role": "_system_prompt", "content": "You are Kimi"}),
                        json.dumps({"role": "user", "content": "refactor the parser"}),
                    ]
                )
            )
            if sidecar_name is not None:
                (sess_dir / sidecar_name).write_text(sidecar_body or "")
            (home / "kimi.json").write_text(
                json.dumps({"work_dirs": [{"path": WORK_PATH, "kaos": "local"}]})
            )

            def expand(p: str) -> str:
                if p.endswith("kimi.json"):
                    return str(home / "kimi.json")
                if p.endswith("sessions"):
                    return str(sessions_root)
                return p

            with mock.patch("quiver.sessions.parsers.os.path.expanduser", side_effect=expand):
                sessions = parse_kimi()
            self.assertEqual(len(sessions), 1)
            return sessions[0]

    def test_state_json_title_generated_true_is_rename(self):
        sess = self._run(
            "state.json",
            json.dumps({"version": 1, "custom_title": "Parser cleanup", "title_generated": True}),
        )
        self.assertEqual(sess.title, "Parser cleanup")
        self.assertEqual(sess.title_source, "rename")

    def test_state_json_title_generated_false_keeps_blank_source(self):
        sess = self._run(
            "state.json",
            json.dumps({"version": 1, "custom_title": "refactor the parser", "title_generated": False}),
        )
        self.assertEqual(sess.title, "refactor the parser")
        self.assertEqual(sess.title_source, "")

    def test_state_json_null_custom_title_leaves_first_message(self):
        sess = self._run(
            "state.json",
            json.dumps({"version": 1, "custom_title": None, "title_generated": False}),
        )
        self.assertIn("refactor", sess.title)
        self.assertEqual(sess.title_source, "")

    def test_missing_sidecar_leaves_first_message(self):
        sess = self._run(None, None)
        self.assertIn("refactor", sess.title)
        self.assertEqual(sess.title_source, "")

    def test_malformed_state_json_leaves_first_message(self):
        sess = self._run("state.json", "{not json")
        self.assertIn("refactor", sess.title)
        self.assertEqual(sess.title_source, "")

    def test_metadata_json_fallback(self):
        sess = self._run(
            "metadata.json",
            json.dumps({"title": "Legacy name", "title_generated": True}),
        )
        self.assertEqual(sess.title, "Legacy name")
        self.assertEqual(sess.title_source, "rename")

    def test_metadata_json_without_generated_flag_is_not_rename(self):
        sess = self._run("metadata.json", json.dumps({"title": "Legacy auto"}))
        self.assertEqual(sess.title, "Legacy auto")
        self.assertEqual(sess.title_source, "")

    def test_custom_title_is_cleaned(self):
        sess = self._run(
            "state.json",
            json.dumps({"custom_title": "  <b>Spaced</b>   title \n", "title_generated": True}),
        )
        self.assertEqual(sess.title, "Spaced title")
        self.assertEqual(sess.title_source, "rename")


if __name__ == "__main__":
    unittest.main()
