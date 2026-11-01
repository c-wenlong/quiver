"""``~/.quiver`` -> ``~/.swe`` first-run migration in ``swe.paths``.

The move is a rename plus a ``~/.quiver`` compat symlink, with path and
module tokens inside authored files rewritten. A machine where the move
can't run keeps serving the old root rather than reading an empty one.
"""

import os
import unittest
import unittest.mock
from pathlib import Path

from swe import paths
from tests._tempdir import temp_dir


def _reset():
    paths._RESOLVED.clear()


class MigrateDataDirTest(unittest.TestCase):
    def setUp(self):
        _reset()
        self.home = temp_dir(self)

    def _seed_hub(self, name=".quiver"):
        root = self.home / name
        (root / "skills").mkdir(parents=True)
        (root / "AGENTS.md").write_text("see ~/.quiver/skills\n", encoding="utf-8")
        (root / "mcp.json").write_text(
            '{"mcpServers": {"hist": {"command": "python", "args":'
            ' ["-m", "quiver.mcp_server", "--dir", "' + str(root) + '/mcp"]}}}\n',
            encoding="utf-8")
        (root / "secrets").mkdir()
        (root / "secrets" / ".api_keys").write_text(
            'export DIR="~/.quiver"\n', encoding="utf-8")
        return root

    def test_real_dir_moves_and_leaves_a_symlink(self):
        old = self._seed_hub()
        root = paths.swe_dir_for(self.home)
        self.assertEqual(root, self.home / ".swe")
        self.assertTrue((self.home / ".swe" / "AGENTS.md").is_file())
        self.assertTrue(old.is_symlink())
        self.assertEqual(os.readlink(old), ".swe")
        # Tokens inside authored files point at the new root.
        self.assertIn("~/.swe/skills", (root / "AGENTS.md").read_text())
        mcp = (root / "mcp.json").read_text()
        self.assertIn("swe.mcp_server", mcp)
        self.assertIn(str(root) + "/mcp", mcp)
        self.assertNotIn("quiver.mcp_server", mcp)
        # secrets/ is never rewritten.
        self.assertIn("~/.quiver", (root / "secrets" / ".api_keys").read_text())

    def test_git_config_worktree_path_is_rewritten(self):
        old = self._seed_hub()
        (old / ".git").mkdir()
        (old / ".git" / "config").write_text(
            f"[core]\n\tworktree = {old}\n", encoding="utf-8")
        root = paths.swe_dir_for(self.home)
        self.assertIn(str(root), (root / ".git" / "config").read_text())

    def test_a_failed_move_keeps_the_old_root_all_run(self):
        self._seed_hub()
        original = Path.rename
        with unittest.mock.patch.object(Path, "rename",
                                      side_effect=OSError("denied")):
            self.assertEqual(paths.swe_dir_for(self.home), self.home / ".quiver")
        # The fallback is cached too: it does not retry every call.
        self.assertEqual(paths.swe_dir_for(self.home), self.home / ".quiver")
        self.assertIs(Path.rename, original)

    def test_new_root_wins_over_a_real_legacy_dir(self):
        self._seed_hub()
        (self.home / ".swe").mkdir()
        _reset()
        self.assertEqual(paths.swe_dir_for(self.home), self.home / ".swe")
        # The legacy dir was left alone for a person to merge.
        self.assertTrue((self.home / ".quiver" / "AGENTS.md").is_file())
        self.assertFalse((self.home / ".quiver").is_symlink())

    def test_linked_legacy_dir_is_mirrored_not_moved(self):
        target = temp_dir(self) / "elsewhere"
        target.mkdir()
        (self.home / ".quiver").symlink_to(target)
        root = paths.swe_dir_for(self.home)
        self.assertEqual(root, self.home / ".swe")
        self.assertTrue(root.is_symlink())
        self.assertEqual(os.readlink(root), str(target))
        self.assertTrue(target.is_dir())  # the target stayed put

    def test_no_legacy_dir_gives_the_new_root(self):
        self.assertEqual(paths.swe_dir_for(self.home), self.home / ".swe")

    def test_doctor_flags_coexisting_roots(self):
        from swe.harness.drift import check_data_root

        self._seed_hub()
        (self.home / ".swe").mkdir()
        findings = check_data_root(self.home)
        self.assertEqual([f.area for f in findings], ["layout"])
        self.assertIn(".quiver", findings[0].message)

    def test_doctor_flags_an_unmigrated_root(self):
        from swe.harness.drift import check_data_root

        self._seed_hub()
        findings = check_data_root(self.home)
        self.assertEqual(len(findings), 1)
        self.assertIn("still on", findings[0].message)

    def test_doctor_quiet_on_the_expected_states(self):
        from swe.harness.drift import check_data_root

        self.assertEqual(check_data_root(self.home), [])
        self._seed_hub()
        paths.swe_dir_for(self.home)
        self.assertEqual(check_data_root(self.home), [])


if __name__ == "__main__":
    unittest.main()
