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

    def test_linked_legacy_dir_survives_a_failed_mirror(self):
        target = temp_dir(self) / "elsewhere"
        target.mkdir()
        (self.home / ".quiver").symlink_to(target)
        with unittest.mock.patch.object(Path, "symlink_to",
                                      side_effect=OSError("denied")):
            # The mirror failed, so the still-serving old path is the
            # answer — never a ~/.swe that does not exist.
            self.assertEqual(paths.swe_dir_for(self.home), self.home / ".quiver")
        self.assertFalse((self.home / ".swe").exists())
        self.assertTrue((self.home / ".quiver").is_symlink())

    def test_a_legacy_file_is_left_alone(self):
        (self.home / ".quiver").write_text("not a dir\n")
        self.assertEqual(paths.swe_dir_for(self.home), self.home / ".swe")
        self.assertTrue((self.home / ".quiver").is_file())
        self.assertFalse((self.home / ".swe").exists())

    def test_a_moved_root_moves_back_when_the_compat_link_fails(self):
        old = self._seed_hub()
        with unittest.mock.patch.object(Path, "symlink_to",
                                      side_effect=OSError("denied")):
            root = paths.swe_dir_for(self.home)
        # With no symlink to leave behind, keeping ~/.swe would strand
        # every ~/.quiver reference — so the move is undone instead.
        self.assertEqual(root, self.home / ".quiver")
        self.assertTrue((old / "AGENTS.md").is_file())
        self.assertFalse((self.home / ".swe").exists() or old.is_symlink())

    def test_a_moved_root_survives_a_failed_rewrite(self):
        self._seed_hub()
        with unittest.mock.patch.object(paths, "_rewrite_migrated_root",
                                        side_effect=RuntimeError("boom")):
            root = paths.swe_dir_for(self.home)
        self.assertEqual(root, self.home / ".swe")
        self.assertTrue((self.home / ".swe" / "AGENTS.md").is_file())

    def test_rewrite_skips_what_it_must_not_touch(self):
        old = self._seed_hub()
        (old / ".git").mkdir()
        # Only .git/config carries worktree paths; other git files stay.
        (old / ".git" / "description").write_text(f"{old} hooks\n")
        # Ignored names and extensions stay byte-identical.
        (old / ".linkignore").write_text(f"{old}\n")
        (old / "notes.log").write_text(f"{old}\n")
        # A file too big to read safely stays.
        big = old / "big.json"
        big.write_text(f'{{"a": "{old}"}}\n')
        with big.open("ab") as fh:
            fh.truncate(2_000_001)
        # A non-UTF-8 file stays.
        (old / "blob.json").write_bytes(b"\xff\xfe" + str(old).encode() + b"\x00")
        # A file symlinked into the tree is never written through: the
        # target belongs to whoever owns the link, not to the move.
        outside = temp_dir(self) / "outside.json"
        outside.write_text(f'{{"p": "{old}"}}\n')
        (old / "linked.json").symlink_to(outside)
        # Service names keep their com.*.quiver.* spelling until the app
        # renames them: the module-token rule must not reach them.
        (old / "services.txt").write_text(f"com.cwenlong.quiver.remote-hub {old}\n")
        # A read-only file: the rewrite is atomic (tmp + rename), which
        # needs only a writable directory, so it still applies.
        ro = old / "ro.json"
        ro.write_text(f'{{"p": "{old}"}}\n')
        ro.chmod(0o444)
        try:
            root = paths.swe_dir_for(self.home)
        finally:
            ro.chmod(0o644)
        self.assertEqual(root, self.home / ".swe")
        self.assertIn(str(root), ro.read_text())
        self.assertIn(str(self.home / ".quiver"),
                      (root / ".git" / "description").read_text())
        self.assertIn(str(self.home / ".quiver"),
                      (root / ".linkignore").read_text())
        self.assertIn(str(self.home / ".quiver"),
                      (root / "notes.log").read_text())
        self.assertIn(str(self.home / ".quiver"), big.read_text())
        self.assertIn(str(self.home / ".quiver"), outside.read_text())
        services = (root / "services.txt").read_text()
        self.assertIn("com.cwenlong.quiver.remote-hub", services)
        self.assertIn(str(root), services)

    def test_sqlite_stores_get_their_path_columns_respelled(self):
        import sqlite3

        old = self._seed_hub()
        store = old / "data" / "ledger" / "ledger.sqlite"
        store.parent.mkdir(parents=True)
        db = sqlite3.connect(store)
        db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                   " account TEXT, session TEXT, n INTEGER)")
        db.execute("INSERT INTO events(account, session, n) VALUES (?, ?, 7)",
                   (f"{old}/accounts/claude/a1", f"{old}/data/x.jsonl"))
        db.execute("INSERT INTO events(account, session, n)"
                   " VALUES ('other', 'notes', 1)")
        # A path that is also a key, already present in both spellings —
        # the .swe row wins and the stale row stays rather than aborting
        # the file (OR IGNORE).
        db.execute("CREATE TABLE files (path TEXT PRIMARY KEY, n INTEGER)")
        db.execute("INSERT INTO files VALUES (?, 1)", (f"{old}/dup",))
        db.execute("INSERT INTO files VALUES (?, 2)",
                   (f"{old}/dup".replace("/.quiver/", "/.swe/"),))
        db.commit()
        db.close()
        # A .db that is not sqlite is skipped, not fatal; a directory
        # named like one fails connect() the same way.
        (old / "cache").mkdir()
        (old / "cache" / "notadb.db").write_text("plain text\n")
        (old / "cache" / "dir.db").mkdir()
        root = paths.swe_dir_for(self.home)
        db = sqlite3.connect(root / "data" / "ledger" / "ledger.sqlite")
        rows = db.execute(
            "SELECT account, session, n FROM events ORDER BY n DESC").fetchall()
        files = db.execute("SELECT path, n FROM files ORDER BY n").fetchall()
        db.close()
        self.assertEqual(rows[0], (f"{root}/accounts/claude/a1",
                                 f"{root}/data/x.jsonl", 7))
        self.assertEqual(rows[1], ("other", "notes", 1))
        # The colliding row keeps its old spelling; the rest still rewrote.
        self.assertEqual(files, [(str(self.home / ".quiver") + "/dup", 1),
                                 (str(root) + "/dup", 2)])
        self.assertEqual((root / "cache" / "notadb.db").read_text(),
                         "plain text\n")

    def test_a_store_that_cannot_open_is_skipped(self):
        import sqlite3

        old = self._seed_hub()
        (old / "cache").mkdir()
        (old / "cache" / "locked.db").write_text("x\n")
        with unittest.mock.patch("sqlite3.connect",
                                 side_effect=sqlite3.Error("no")):
            root = paths.swe_dir_for(self.home)
        self.assertEqual(root, self.home / ".swe")

    def test_a_bad_sqlite_pass_never_rolls_back_the_move(self):
        old = self._seed_hub()
        with unittest.mock.patch.object(paths, "_rewrite_migrated_sqlite",
                                        side_effect=Exception("boom")):
            root = paths.swe_dir_for(self.home)
        self.assertEqual(root, self.home / ".swe")
        self.assertTrue(old.is_symlink())
        self.assertTrue((root / "AGENTS.md").is_file())

    def test_a_failed_rewrite_leaves_the_move_and_the_rest(self):
        old = self._seed_hub()
        (old / "a.json").write_text(f'{{"p": "{old}"}}\n')
        real_write = paths.atomic_write_text

        def flaky(path, text, **kw):
            if path.name == "a.json":
                raise OSError("read-only")
            return real_write(path, text, **kw)

        with unittest.mock.patch.object(paths, "atomic_write_text",
                                        side_effect=flaky):
            root = paths.swe_dir_for(self.home)
        self.assertEqual(root, self.home / ".swe")
        self.assertIn(str(self.home / ".quiver"),
                      (root / "a.json").read_text())
        self.assertIn("~/.swe/skills", (root / "AGENTS.md").read_text())

    def test_a_failed_move_back_still_serves_the_new_root(self):
        old = self._seed_hub()
        real_rename = Path.rename
        calls = []

        def rename_once(path, target):
            calls.append(path)
            if len(calls) > 1:
                raise OSError("still denied")
            return real_rename(path, target)

        with unittest.mock.patch.object(Path, "symlink_to",
                                        side_effect=OSError("denied")), \
             unittest.mock.patch.object(Path, "rename", rename_once):
            root = paths.swe_dir_for(self.home)
        # Neither link nor undo worked: the moved root is what exists.
        self.assertEqual(root, self.home / ".swe")
        self.assertTrue((root / "AGENTS.md").is_file())
        self.assertFalse(old.exists() or old.is_symlink())

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
