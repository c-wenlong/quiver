"""Tests for interactive prompt helpers."""

import io
import unittest
from unittest.mock import patch

from quiver.prompt import read_line


class ReadLineTest(unittest.TestCase):
    def test_non_tty_accepts_lf(self):
        with patch("sys.stdin", io.StringIO("description\n")):
            with patch("sys.stdin.isatty", return_value=False):
                # re-bind isatty on the StringIO
                pass
        fake = io.StringIO("description\n")
        fake.isatty = lambda: False  # type: ignore[method-assign]
        with patch("sys.stdin", fake):
            self.assertEqual(read_line(""), "description")

    def test_non_tty_accepts_cr(self):
        fake = io.StringIO("save\r")
        fake.isatty = lambda: False  # type: ignore[method-assign]
        with patch("sys.stdin", fake):
            self.assertEqual(read_line(""), "save")

    def test_non_tty_accepts_crlf(self):
        fake = io.StringIO("tags\r\n")
        fake.isatty = lambda: False  # type: ignore[method-assign]
        with patch("sys.stdin", fake):
            self.assertEqual(read_line(""), "tags")

    def test_non_tty_multiple_cr_lines(self):
        fake = io.StringIO("description\rHello CR\rsave\r")
        fake.isatty = lambda: False  # type: ignore[method-assign]
        with patch("sys.stdin", fake):
            self.assertEqual(read_line(""), "description")
            self.assertEqual(read_line(""), "Hello CR")
            self.assertEqual(read_line(""), "save")

    def test_pushback_does_not_drop_byte_after_cr(self):
        """CR followed by a non-LF byte must not eat the next character."""
        from quiver import prompt as prompt_mod

        prompt_mod._pushback.clear()
        # "ab\rcd\n" — after CR, 'c' must start next line, not be dropped
        data = list(b"ab\rcd\n")

        def fake_read(_fd, _n=1):
            if not data:
                return b""
            return bytes([data.pop(0)])

        # Patch builtins used inside _read_line_bytes (local import of os)
        import os as real_os

        with patch.object(prompt_mod, "_restore_cooked_tty"), patch.object(
            prompt_mod, "_tty_echo_on", return_value=False
        ), patch.object(real_os, "read", side_effect=fake_read), patch(
            "select.select", return_value=([0], [], [])
        ), patch("sys.stdout", new=io.StringIO()):
            prompt_mod._pushback.clear()
            line1 = prompt_mod._read_line_bytes(0)
            line2 = prompt_mod._read_line_bytes(0)
        self.assertEqual(line1, "ab")
        self.assertEqual(line2, "cd")


    def test_eof_raises(self):
        fake = io.StringIO("")
        fake.isatty = lambda: False  # type: ignore[method-assign]
        with patch("sys.stdin", fake):
            with self.assertRaises(EOFError):
                read_line("")


if __name__ == "__main__":
    unittest.main()
