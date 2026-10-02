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

class ByteReaderTest(unittest.TestCase):
    """_read_line_bytes on a real pipe: CR pushback, editing keys, echo."""

    def _pipe(self, payload: bytes) -> int:
        import os

        r, w = os.pipe()
        os.write(w, payload)
        os.close(w)
        return r

    def test_a_cr_followed_by_a_letter_pushes_the_letter_back(self):
        import os

        from quiver import prompt

        fd = self._pipe(b"a\rx")
        try:
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(prompt._read_line_bytes(fd), "a")
                self.assertEqual(prompt._read_line_bytes(fd), "x")
        finally:
            prompt._pushback.clear()
            os.close(fd)

    def test_a_select_failure_after_cr_still_ends_the_line(self):
        import os

        from quiver import prompt

        fd = self._pipe(b"a\r")
        try:
            with patch("sys.stdout", io.StringIO()), \
                 patch("select.select", side_effect=OSError("no poll")):
                self.assertEqual(prompt._read_line_bytes(fd), "a")
        finally:
            os.close(fd)

    def test_backspace_edits_and_echoes_on_a_tty_without_echo(self):
        import os

        from quiver import prompt

        fd = self._pipe(b"ab\x7fc\n")
        out = io.StringIO()
        try:
            with patch("sys.stdout", out), \
                 patch.object(prompt, "_tty_echo_on", return_value=False):
                self.assertEqual(prompt._read_line_bytes(fd, echo=True), "ac")
            self.assertIn("\b \b", out.getvalue())
        finally:
            os.close(fd)

    def test_ctrl_c_raises_and_ctrl_d_ends_like_eof(self):
        import os

        from quiver import prompt

        fd = self._pipe(b"\x03")
        try:
            with patch("sys.stdout", io.StringIO()):
                self.assertRaises(KeyboardInterrupt,
                                  prompt._read_line_bytes, fd)
        finally:
            os.close(fd)

        fd = self._pipe(b"\x04")
        try:
            with patch("sys.stdout", io.StringIO()):
                self.assertRaises(EOFError, prompt._read_line_bytes, fd)
        finally:
            os.close(fd)

        fd = self._pipe(b"ab\x04")
        try:
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(prompt._read_line_bytes(fd), "ab")
        finally:
            os.close(fd)

    def test_a_cr_at_a_still_open_pipe_ends_the_line(self):
        """Writer not closed: select reports nothing readable after the
        CR, so no partner byte is consumed and the line still ends."""
        import os

        from quiver import prompt

        r, w = os.pipe()
        os.write(w, b"a\r")
        try:
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(prompt._read_line_bytes(r), "a")
            os.write(w, b"b\n")
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(prompt._read_line_bytes(r), "b")
        finally:
            os.close(w)
            os.close(r)

    def test_backspace_on_an_empty_line_is_a_noop(self):
        import os

        from quiver import prompt

        fd = self._pipe(b"\x7fok\n")
        try:
            with patch("sys.stdout", io.StringIO()), \
                 patch.object(prompt, "_tty_echo_on", return_value=True):
                self.assertEqual(prompt._read_line_bytes(fd, echo=True), "ok")
        finally:
            os.close(fd)

        # echo=False (a pipe): the erasure is not written to stdout.
        fd = self._pipe(b"ab\x7fc\n")
        out = io.StringIO()
        try:
            with patch("sys.stdout", out):
                self.assertEqual(prompt._read_line_bytes(fd, echo=False), "ac")
            self.assertEqual(out.getvalue(), "")
        finally:
            os.close(fd)

    def test_control_characters_are_skipped(self):
        import os

        from quiver import prompt

        fd = self._pipe(b"a\x01b\n")
        try:
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(prompt._read_line_bytes(fd), "ab")
        finally:
            os.close(fd)

    def test_cr_only_line_ends_write_the_newline_the_tty_skipped(self):
        """A CR without a paired LF and without terminal echo still needs
        the cursor moved off the row."""
        import os

        from quiver import prompt

        for tty_echo in (False, True):
            out = io.StringIO()
            fd2 = self._pipe(b"x\r")
            try:
                with patch("sys.stdout", out), \
                     patch.object(prompt, "_tty_echo_on",
                                  return_value=tty_echo):
                    self.assertEqual(prompt._read_line_bytes(fd2), "x")
                self.assertIn("\n", out.getvalue())
            finally:
                os.close(fd2)

    def test_crlf_piped_input_does_not_eat_every_other_line(self):
        """A TextIOWrapper on a pipe has no peek() and its tell()/seek()
        raise OSError, so the old reader consumed the LF of every CRLF and
        could not put it back: every second read_line saw that leftover LF
        and returned an empty string."""
        import os

        r, w = os.pipe()
        os.write(w, b"a\r\nb\r\nlast\r\n")
        os.close(w)
        fake = io.TextIOWrapper(io.FileIO(r, "r"))
        try:
            with patch("sys.stdin", fake), patch("sys.stdout", io.StringIO()):
                self.assertEqual(read_line(""), "a")
                self.assertEqual(read_line(""), "b")
                self.assertEqual(read_line(""), "last")
                self.assertRaises(EOFError, read_line, "")
        finally:
            fake.close()

    def test_pipe_input_is_not_echoed_to_stdout(self):
        import os

        r, w = os.pipe()
        os.write(w, b"quiet\n")
        os.close(w)
        fake = io.TextIOWrapper(io.FileIO(r, "r"))
        out = io.StringIO()
        try:
            with patch("sys.stdin", fake), patch("sys.stdout", out):
                self.assertEqual(read_line(""), "quiet")
        finally:
            fake.close()
        self.assertEqual(out.getvalue(), "")

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
