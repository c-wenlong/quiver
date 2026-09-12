import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.sessions.parsers import _cursor_session_dir


class CursorSessionDirTest(unittest.TestCase):
    """The directory reported for a Cursor CLI session, in signal priority order."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.cursor_home = self.home / ".cursor"
        (self.cursor_home / "hooks").mkdir(parents=True)
        self.project = self.home / "Desktop" / "Work" / "Cortex AI" / "egocentric"
        self.project.mkdir(parents=True)
        (self.project / "main.py").write_text("print('hi')\n")
        self.enc_entry = self.cursor_home / "projects" / "some-encoded-name"
        self.enc_entry.mkdir(parents=True)

        def fake_expanduser(path: str) -> str:
            if path == "~":
                return str(self.home)
            if path.startswith("~/"):
                return str(self.home / path[2:])
            return os.path.expanduser(path)

        self._patch = patch(
            "quiver.sessions.parsers.os.path.expanduser", side_effect=fake_expanduser
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _encoded_project_entry(self) -> str:
        """An enc_entry_path whose basename decodes (slashes only) to self.project."""
        # The encoding turns every "/" into "-" and decoding reverses all of
        # them, so a real path containing "-" cannot round-trip. A nix build
        # runs under /nix/var/nix/builds/nix-<pid>-<n>, which is exactly that.
        if "-" in str(self.project):
            self.skipTest("temp dir contains '-', which the encoding cannot round-trip")
        name = str(self.project).lstrip("/").replace("/", "-")
        entry = self.cursor_home / "projects" / name
        entry.mkdir(parents=True, exist_ok=True)
        return str(entry)

    def test_meta_cwd_wins_over_tool_paths(self):
        other = self.home / "elsewhere"
        other.mkdir()
        meta = {"cwd": str(self.project)}
        result = _cursor_session_dir(str(self.enc_entry), [str(other / "a.txt")], meta)
        self.assertEqual(result, str(self.project))

    def test_meta_cwd_pointing_at_file_collapses_to_parent(self):
        meta = {"cwd": str(self.project / "main.py")}
        result = _cursor_session_dir(str(self.enc_entry), [], meta)
        self.assertEqual(result, str(self.project))

    def test_empty_or_missing_meta_cwd_falls_back_to_tool_paths(self):
        paths = [str(self.project / "main.py"), str(self.project / "other.py")]
        for meta in ({}, {"cwd": ""}, {"cwd": "   "}, {"title": "no cwd key"}, None):
            with self.subTest(meta=meta):
                result = _cursor_session_dir(str(self.enc_entry), paths, meta)
                self.assertEqual(result, str(self.project))

    def test_single_file_tool_path_yields_parent_directory(self):
        result = _cursor_session_dir(str(self.enc_entry), [str(self.project / "main.py")], {})
        self.assertEqual(result, str(self.project))
        self.assertTrue(os.path.isdir(result))
        self.assertFalse(os.path.isfile(result))

    def test_tool_paths_under_cursor_home_lose_to_decoded_folder(self):
        hook_paths = [
            str(self.cursor_home / "hooks" / "state.json"),
            str(self.cursor_home / "hooks" / "log.txt"),
        ]
        result = _cursor_session_dir(self._encoded_project_entry(), hook_paths, {})
        self.assertEqual(result, str(self.project))

    def test_tool_paths_under_cursor_home_kept_when_nothing_decodes(self):
        hook_paths = [str(self.cursor_home / "hooks" / "state.json")]
        result = _cursor_session_dir(str(self.enc_entry), hook_paths, {})
        self.assertEqual(result, str(self.cursor_home / "hooks"))
        self.assertTrue(os.path.isdir(result))

    def test_decoded_folder_used_when_no_tool_paths(self):
        result = _cursor_session_dir(self._encoded_project_entry(), [], {})
        self.assertEqual(result, str(self.project))

    def test_nothing_decodable_falls_back_to_enc_entry_path(self):
        for name in ("empty-window", "1783559962810", "var-folders-zz-nope"):
            with self.subTest(name=name):
                entry = self.cursor_home / "projects" / name
                entry.mkdir(parents=True, exist_ok=True)
                result = _cursor_session_dir(str(entry), [], {})
                self.assertEqual(result, str(entry))

    def test_tool_paths_with_no_common_root_are_ignored(self):
        # os.path.commonpath raises on a mix of absolute and relative paths.
        result = _cursor_session_dir(str(self.enc_entry), ["/abs/path", "rel/path"], {})
        self.assertEqual(result, str(self.enc_entry))


if __name__ == "__main__":
    unittest.main()
