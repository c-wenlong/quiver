"""Drive polling with a fake clock rather than real sleeps."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from swe.instructions import watcher
from swe.paths import agents_file_for, instructions_dir_for, instructions_index_for


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

    def test_failed_write_retries_without_source_or_output_edits(self):
        self.output.write_text("last good")
        original = os.replace
        failures = 0

        def transient_failure(*args):
            nonlocal failures
            failures += 1
            if failures == 1:
                raise OSError("disk full")
            return original(*args)

        results, errors = [], []
        with patch.object(watcher.os, "replace", side_effect=transient_failure):
            self.run_watch([None] * 15, results=results.append, errors=errors.append)
        self.assertEqual(failures, 2)
        self.assertEqual(results, [True])
        self.assertEqual(len(errors), 1)
        self.assertIn("disk full", str(errors[0]))
        self.assertIn("first", self.output.read_text())

    def test_repeat_failures_retry_on_delay_without_spam_and_success_clears_retry(self):
        original = watcher.build
        attempts = []

        def fail_then_recover(home):
            attempts.append(watcher.time.monotonic())
            if len(attempts) <= 3:
                raise watcher.InstructionError("disk full")
            return original(home)

        results, errors = [], []
        with patch.object(watcher, "build", side_effect=fail_then_recover):
            self.run_watch([None] * 40, results=results.append, errors=errors.append)
        self.assertEqual(attempts, [0.0, 5.0, 10.0, 15.0])
        self.assertEqual(results, [True])
        self.assertEqual(len(errors), 1)

    def test_retry_due_during_edits_waits_for_debounce(self):
        original = watcher.build
        attempts = []

        def fail_once(home):
            attempts.append(watcher.time.monotonic())
            if len(attempts) == 1:
                raise watcher.InstructionError("disk full")
            return original(home)

        with patch.object(watcher, "build", side_effect=fail_once):
            self.run_watch([
                *([None] * 8),
                lambda: self.chapter.write_text("second"),
                lambda: self.chapter.write_text("third"),
                None, None,
            ])
        self.assertEqual(attempts, [0.0, 5.5])
        self.assertIn("third", self.output.read_text())

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

    def test_unlisted_large_file_is_never_opened_or_watched(self):
        backup = self.directory / "backup.bin"
        with backup.open("wb") as handle:
            handle.seek(8 * 1024 * 1024)
            handle.write(b"x")
        original_open = Path.open
        original_os_open = os.open

        def guarded_open(path, *args, **kwargs):
            self.assertNotEqual(path, backup, "opened an unlisted backup")
            return original_open(path, *args, **kwargs)

        def guarded_os_open(path, *args, **kwargs):
            self.assertNotEqual(Path(path), backup, "opened an unlisted backup")
            return original_os_open(path, *args, **kwargs)

        results = []
        with patch.object(Path, "open", guarded_open), \
                patch.object(watcher.os, "open", side_effect=guarded_os_open):
            self.run_watch([
                lambda: os.utime(backup, None), None, None,
            ], results=results.append)
        self.assertEqual(results, [True])

    def test_symlink_chapter_content_and_retargeting_are_detected(self):
        other = self.directory / "other.md"
        other.write_text("first")
        link = self.directory / "linked.md"
        link.symlink_to(self.chapter)
        self.index.write_text("linked.md\n")

        def retarget():
            link.unlink()
            link.symlink_to(other)

        def edit():
            before = other.stat()
            other.write_text("other")
            os.utime(other, ns=(before.st_atime_ns, before.st_mtime_ns))

        results = []
        self.run_watch([retarget, None, edit, None, None], results=results.append)
        self.assertEqual(results, [True, False, True])
        self.assertIn("other", self.output.read_text())

    def test_missing_symlink_target_and_invalid_encoding_recover(self):
        link = self.directory / "linked.md"
        target = self.directory / "missing.md"
        link.symlink_to(target)
        self.index.write_text("linked.md\n")
        results, errors = [], []
        self.run_watch([
            lambda: target.write_bytes(b"\xff"), None,
            lambda: target.write_text("recovered"), None, None,
        ], results=results.append, errors=errors.append)
        self.assertEqual(results, [True])
        self.assertEqual(len(errors), 2)
        self.assertIn("recovered", self.output.read_text())

    def test_escaped_and_nonregular_sources_are_not_opened(self):
        outside = self.home / "outside.md"
        outside.write_text("outside")
        escaped = self.directory / "escaped.md"
        escaped.symlink_to(outside)
        fifo = self.directory / "fifo.md"
        os.mkfifo(fifo)
        original = os.open

        def guarded_open(path, *args, **kwargs):
            self.assertNotIn(Path(path), (outside, fifo))
            return original(path, *args, **kwargs)

        with patch.object(watcher.os, "open", side_effect=guarded_open):
            for name in ("escaped.md", "fifo.md"):
                with self.subTest(name=name):
                    self.index.write_text(name + "\n")
                    errors = []
                    self.run_watch([None, None], errors=errors.append)
                    self.assertEqual(len(errors), 1)

    def test_output_replaced_with_fifo_does_not_block_fingerprint(self):
        self.output.write_text("old")
        original = os.open

        def replace_before_open(path, flags):
            self.output.unlink()
            os.mkfifo(self.output)
            self.assertTrue(flags & os.O_NONBLOCK)
            return original(path, flags)

        with patch.object(watcher.os, "open", side_effect=replace_before_open):
            self.assertIsInstance(watcher._fingerprint(self.output), bytes)

    def test_output_symlink_target_is_not_opened(self):
        self.output.symlink_to(self.chapter)
        with patch.object(watcher.os, "open", side_effect=AssertionError("opened alias")):
            self.assertIsInstance(watcher._fingerprint(self.output), bytes)

    def test_unsupported_platform_fails_before_polling(self):
        from swe.instructions import compiler

        with patch.object(compiler, "fcntl", None), \
                patch.object(watcher.time, "sleep", side_effect=AssertionError("polled")):
            with self.assertRaisesRegex(compiler.InstructionError, "macOS/Linux"):
                watcher.watch(self.home)


if __name__ == "__main__":
    unittest.main()
