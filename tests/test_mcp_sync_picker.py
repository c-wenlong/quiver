"""``swe mcp sync`` drives the shared ``multiselect`` widget, not getch.

The old ``interactive_select`` hand-rolled raw-mode key handling and hung
on a bare Esc. These tests pin the replacement contract at the seam:
``cmd_sync`` calls ``quiver.mcp.cli.multiselect`` with the server names as
``Choice`` rows, treats ``None`` (q/Esc/Ctrl-C) as an empty selection, and
pre-ticks the rows the old widget defaulted to.
"""

import io
import sys
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from quiver.mcp import cli as cli_mod
from quiver.mcp.cli import cmd_sync


class SyncPickerTest(unittest.TestCase):
    def _run_sync(self, tmp_path, source_servers, target_servers,
                  picker, args=("src", "dst")):
        """Run cmd_sync with every real-config seam patched to a tmp dir."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch.object(cli_mod, "load_registry", return_value={}))
        stack.enter_context(patch.object(
            cli_mod, "resolve_tool_arg", side_effect=lambda reg, a: a))
        stack.enter_context(
            patch.object(cli_mod, "is_hub", return_value=False))
        stack.enter_context(patch.object(
            cli_mod, "servers_for_source", return_value=source_servers))
        stack.enter_context(patch.object(
            cli_mod, "get_tool_servers", return_value=target_servers))
        stack.enter_context(patch.object(
            cli_mod, "get_tool_config",
            return_value={
                "path": tmp_path / "dst.json", "key": "mcpServers"}))
        stack.enter_context(patch.object(
            cli_mod, "convert_server_for_target",
            side_effect=lambda srv, s, t: srv))
        stack.enter_context(
            patch.object(cli_mod, "server_summary", return_value="s"))
        ms = stack.enter_context(
            patch.object(cli_mod, "multiselect", side_effect=picker))
        saver_fn = stack.enter_context(
            patch.object(cli_mod, "get_tool_saver"))
        stack.enter_context(
            patch.object(sys, "stdin", **{"isatty.return_value": True}))

        # The captured buffer must also claim to be a TTY, or cmd_sync
        # takes the non-interactive branch and never calls the picker.
        class TtyBuf(io.StringIO):
            def isatty(self):
                return True

        buf = TtyBuf()
        with redirect_stdout(buf):
            code = cmd_sync(list(args))
        return code, buf.getvalue(), ms, saver_fn

    def test_interactive_selection_uses_shared_multiselect(self):
        src = {"s1": {"command": "a"}, "s2": {"command": "b"}}
        with TemporaryDirectory() as tmp:
            code, out, ms, saver_fn = self._run_sync(
                Path(tmp), src, {}, lambda *a, **k: ["s1"])

        self.assertEqual(code, 0, msg=out)
        (choices,), kwargs = ms.call_args
        self.assertEqual([ch.key for ch in choices], ["s1", "s2"])
        saver = saver_fn.return_value
        self.assertEqual(saver.call_count, 1)
        self.assertEqual(sorted(saver.call_args[0][0]), ["s1"])

    def test_cancel_means_nothing_selected(self):
        with TemporaryDirectory() as tmp:
            code, out, _, saver_fn = self._run_sync(
                Path(tmp), {"s1": {"command": "a"}}, {},
                lambda *a, **k: None)

        self.assertEqual(code, 0)
        self.assertIn("Nothing selected", out)
        saver_fn.assert_not_called()

    def test_existing_target_servers_are_preticked(self):
        src = {"s1": {"command": "a"}, "s2": {"command": "b"}}
        with TemporaryDirectory() as tmp:
            code, out, ms, _ = self._run_sync(
                Path(tmp), src, {"s2": {"command": "old"}},
                lambda *a, **k: [])

        self.assertEqual(code, 0)
        _, kwargs = ms.call_args
        self.assertEqual(kwargs["selected"], {"s2"})

    def test_conflict_picker_only_overwrites_picked(self):
        src = {"s1": {"command": "new-a"}, "s2": {"command": "new-b"}}
        tgt = {"s1": {"command": "old-a"}, "s2": {"command": "old-b"}}
        picks = iter([["s1", "s2"], ["s2"]])
        with TemporaryDirectory() as tmp:
            code, out, ms, saver_fn = self._run_sync(
                Path(tmp), src, tgt, lambda *a, **k: next(picks))

        self.assertEqual(code, 0, msg=out)
        # Second call is the conflict picker: every conflict pre-ticked,
        # matching the old widget's default-all behaviour.
        _, kwargs = ms.call_args_list[1]
        self.assertEqual(kwargs["selected"], ["s1", "s2"])
        written = saver_fn.return_value.call_args[0][0]
        self.assertEqual(written["s1"], {"command": "old-a"})
        self.assertEqual(written["s2"], {"command": "new-b"})

    def test_conflict_cancel_overwrites_nothing(self):
        src = {"s1": {"command": "new-a"}}
        tgt = {"s1": {"command": "old-a"}}
        picks = iter([["s1"], None])
        with TemporaryDirectory() as tmp:
            code, out, _, saver_fn = self._run_sync(
                Path(tmp), src, tgt, lambda *a, **k: next(picks))

        self.assertEqual(code, 0, msg=out)
        saver_fn.return_value.assert_not_called()
        self.assertIn("skipped", out)


if __name__ == "__main__":
    unittest.main()
