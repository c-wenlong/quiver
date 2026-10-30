"""Drive polling with a fake clock rather than real sleeps."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quiver.instructions import watcher
from quiver.paths import agents_file_for, instructions_dir_for, instructions_index_for


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.directory = instructions_dir_for(self.home)
        self.directory.mkdir(parents=True)
        self.index = instructions_index_for(self.home)
        self.index.write_text("one.md\n")
        self.chapter = self.directory / "one.md"
        self.chapter.write_text("first")
        self.output = agents_file_for(self.home)

    def run_watch(self, actions, *, results=None, errors=None):
        ticks = iter(actions)
        now = 0.0

        def sleep(_):
            nonlocal now
            now += 0.5
            try:
                action = next(ticks)
            except StopIteration:
                raise KeyboardInterrupt
            if action:
                action()

        with patch.object(watcher.time, "sleep", side_effect=sleep), \
                patch.object(watcher.time, "monotonic", side_effect=lambda: now):
            with self.assertRaises(KeyboardInterrupt):
                watcher.watch(self.home, on_result=results, on_error=errors)

    def test_initial_build_and_no_work_when_unchanged(self):
        results = []
        self.run_watch([None] * 5, results=results.append)
        self.assertEqual(results, [True])
        self.assertIn("first", self.output.read_text())

    def test_debounce_compiles_only_last_edit(self):
        results = []
        self.run_watch([
            lambda: self.chapter.write_text("second"),
            lambda: self.chapter.write_text("third"), None, None,
        ], results=results.append)
        self.assertEqual(results, [True, True])
        self.assertIn("third", self.output.read_text())

    def test_same_size_edit_with_restored_mtime(self):
        results = []

        def edit():
            info = self.chapter.stat()
            self.chapter.write_text("other")
            os.utime(self.chapter, ns=(info.st_atime_ns, info.st_mtime_ns))

        self.run_watch([edit, None, None], results=results.append)
        self.assertEqual(results, [True, True])
        self.assertIn("other", self.output.read_text())

    def test_reorders_index_and_repairs_deleted_or_tampered_output(self):
        results = []
        (self.directory / "two.md").write_text("second")
        self.run_watch([
            lambda: self.index.write_text("two.md\none.md\n"), None,
            lambda: self.output.unlink(), None,
            lambda: self.output.write_text("tampered"), None, None,
        ], results=results.append)
        self.assertEqual(results, [True] * 4)
        content = self.output.read_text()
        self.assertLess(content.index("second"), content.index("first"))
        self.assertNotIn("tampered", content)

    def test_invalid_sources_keep_last_good_and_recover_suppressing_same_error(self):
        results, errors = [], []

        def broken():
            self.index.write_text("missing.md\n")

        def still_broken():
            self.index.write_text("# changed but same error\nmissing.md\n")

        def recover():
            self.assertIn("first", self.output.read_text())
            (self.directory / "missing.md").write_text("recovered")

        self.run_watch([broken, None, still_broken, None, recover, None, None],
                       results=results.append, errors=errors.append)
        self.assertEqual(results, [True, True])
        self.assertEqual(len(errors), 1)
        self.assertIn("missing.md", str(errors[0]))
        self.assertIn("recovered", self.output.read_text())

    def test_initial_error_recovers_when_index_is_created(self):
        self.index.unlink()
        results, errors = [], []
        self.run_watch([lambda: self.index.write_text("one.md\n"), None, None],
                       results=results.append, errors=errors.append)
        self.assertEqual(results, [True])
        self.assertEqual(len(errors), 1)

    def test_success_resets_error_suppression(self):
        errors = []
        self.run_watch([
            lambda: self.index.write_text("missing.md\n"), None,
            lambda: self.index.write_text("one.md\n"), None,
            lambda: self.index.write_text("missing.md\n"), None, None,
        ], errors=errors.append)
        self.assertEqual(len(errors), 2)

    def test_callback_exceptions_propagate(self):
        def fail(_):
            raise ValueError("callback failed")

        with self.assertRaisesRegex(ValueError, "callback failed"):
            watcher.watch(self.home, on_result=fail)

    def test_supported_symlink_directory_chapter_edits_are_detected(self):
        physical = self.directory / "physical"
        physical.mkdir()
        chapter = physical / "chapter.md"
        chapter.write_text("original")
        (self.directory / "linked").symlink_to(physical, target_is_directory=True)
        self.index.write_text("linked/chapter.md\n")
        results = []
        self.run_watch([lambda: chapter.write_text("changed"), None, None],
                       results=results.append)
        self.assertEqual(results, [True, True])
        self.assertIn("changed", self.output.read_text())

    def test_supported_source_root_symlink_edits_are_detected(self):
        physical = self.directory.with_name("chapters")
        self.directory.rename(physical)
        self.directory.symlink_to(physical, target_is_directory=True)
        results = []
        self.run_watch([lambda: self.chapter.write_text("changed"), None, None],
                       results=results.append)
        self.assertEqual(results, [True, True])
        self.assertIn("changed", self.output.read_text())


if __name__ == "__main__":
    unittest.main()
