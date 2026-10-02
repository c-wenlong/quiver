"""The one escape-sequence reader every raw-mode widget goes through.

Driven over a real pipe wherever possible, because the bug this module
exists to kill was about how many bytes came off the wire: a reader that
took a fixed two after the Esc turned one wheel notch into a cancel plus a
handful of stray letters. A pipe reproduces that; a mocked os.read that
hands over exactly the right number of bytes cannot.

Only the two cases a pipe cannot express are patched: a bare Esc needs the
write end held open so the poll times out, and a closed stdin needs an
empty read.
"""

import io
import os
import unittest
from unittest import mock
from unittest.mock import patch

from quiver import keys


def feed(data: bytes, letters=None, sequences=None) -> str:
    """Run read_key over ``data`` on a real pipe whose writer is closed."""
    r, w = os.pipe()
    os.write(w, data)
    os.close(w)
    try:
        return keys.read_key(r, letters, sequences)
    finally:
        os.close(r)


class DefaultSequenceTest(unittest.TestCase):
    """Both spellings of every cursor key, and the navigation keys."""

    def test_normal_cursor_keys(self):
        for seq, want in ((b"\x1b[A", "up"), (b"\x1b[B", "down"),
                          (b"\x1b[C", "right"), (b"\x1b[D", "left")):
            self.assertEqual(feed(seq), want, seq)

    def test_application_cursor_keys(self):
        # DECCKM is on in plenty of terminals, and then an arrow is Esc O A.
        for seq, want in ((b"\x1bOA", "up"), (b"\x1bOB", "down"),
                          (b"\x1bOC", "right"), (b"\x1bOD", "left")):
            self.assertEqual(feed(seq), want, seq)

    def test_page_keys(self):
        self.assertEqual(feed(b"\x1b[5~"), "pageup")
        self.assertEqual(feed(b"\x1b[6~"), "pagedown")

    def test_home_and_end_in_all_three_spellings(self):
        for seq in (b"\x1b[H", b"\x1bOH", b"\x1b[1~"):
            self.assertEqual(feed(seq), "top", seq)
        for seq in (b"\x1b[F", b"\x1bOF", b"\x1b[4~"):
            self.assertEqual(feed(seq), "bottom", seq)


class DefaultLetterTest(unittest.TestCase):
    def test_the_shared_plain_bytes(self):
        for byte, want in ((b"\r", "enter"), (b"\n", "enter"),
                           (b"\x03", "cancel"), (b"q", "cancel"),
                           (b" ", "space"), (b"k", "up"), (b"j", "down")):
            self.assertEqual(feed(byte), want, byte)

    def test_digits_are_not_bound_here(self):
        """Only the session picker jumps to a row by number, so binding a
        digit in the shared map would swallow it in every other widget."""
        for byte in (b"0", b"3", b"9"):
            self.assertEqual(feed(byte), "", byte)

    def test_an_unbound_letter_is_ignored_rather_than_a_cancel(self):
        self.assertEqual(feed(b"z"), "")


class OverrideTest(unittest.TestCase):
    """The caller wins over the defaults, in both maps."""

    def test_a_caller_letter_beats_the_default(self):
        self.assertEqual(feed(b" ", letters={b" ": "next"}), "next")
        self.assertEqual(feed(b"q", letters={b"q": "quiet"}), "quiet")

    def test_a_caller_letter_can_add_a_binding(self):
        self.assertEqual(feed(b"a", letters={b"a": "all"}), "all")

    def test_a_caller_sequence_beats_the_default(self):
        arrows = {b"[C": "open", b"[D": "back"}
        self.assertEqual(feed(b"\x1b[C", sequences=arrows), "open")
        self.assertEqual(feed(b"\x1b[D", sequences=arrows), "back")

    def test_an_override_does_not_disturb_the_rest_of_the_map(self):
        arrows = {b"[C": "open"}
        self.assertEqual(feed(b"\x1b[A", sequences=arrows), "up")
        self.assertEqual(feed(b"j", letters={b"a": "all"}), "down")

    def test_the_default_maps_are_not_mutated_by_a_call(self):
        before = dict(keys.SEQUENCES), dict(keys.LETTERS)
        feed(b"\x1b[C", letters={b"z": "zap"}, sequences={b"[C": "open"})
        self.assertEqual((keys.SEQUENCES, keys.LETTERS), before)


class EscapeTest(unittest.TestCase):
    def test_a_bare_escape_with_nothing_pending_is_an_escape(self):
        # The writer stays open, so the poll finds nothing queued and times
        # out, which is the only thing that separates Esc from a sequence.
        r, w = os.pipe()
        os.write(w, b"\x1b")
        try:
            self.assertEqual(keys.read_key(r), "escape")
        finally:
            os.close(w)
            os.close(r)

    def test_escape_and_a_plain_byte_is_ignored(self):
        self.assertEqual(feed(b"\x1ba"), "")

    def test_an_unknown_sequence_is_ignored_not_a_cancel(self):
        self.assertEqual(feed(b"\x1b[1;5C"), "")      # ctrl-right
        self.assertEqual(feed(b"\x1b[200~"), "")      # bracketed paste
        self.assertEqual(feed(b"\x1b[?1;2c"), "")     # a device reply

    def test_a_sequence_that_never_ends_hits_the_cap_and_is_dropped(self):
        self.assertEqual(feed(b"\x1b[" + b"0" * 64), "")

    def test_a_sequence_cut_short_by_a_closed_stdin_is_dropped(self):
        self.assertEqual(feed(b"\x1b["), "")

    def test_a_sequence_left_open_mid_way_does_not_block(self):
        """A terminal that dies after "Esc [" used to hang the widget on a
        read for a final byte that would never come; now every byte of the
        sequence gets the same short poll the introducer got."""
        import threading

        r, w = os.pipe()
        os.write(w, b"\x1b[")
        try:
            out = []
            t = threading.Thread(target=lambda: out.append(keys.read_key(r)))
            t.start()
            t.join(5)
            self.assertFalse(t.is_alive())
            self.assertEqual(out, [""])
        finally:
            os.close(w)
            os.close(r)


class MouseTest(unittest.TestCase):
    def test_the_wheel_reads_as_one_line_up_or_down(self):
        self.assertEqual(feed(b"\x1b[<64;10;5M"), "up")
        self.assertEqual(feed(b"\x1b[<65;10;5M"), "down")

    def test_wide_coordinates_still_read(self):
        # SGR is what lifts the 223-column clamp, so the tail can be long.
        self.assertEqual(feed(b"\x1b[<65;204;118M"), "down")

    def test_clicks_and_releases_are_dropped_rather_than_acted_on(self):
        self.assertEqual(feed(b"\x1b[<0;10;5M"), "")     # left press
        self.assertEqual(feed(b"\x1b[<0;10;5m"), "")     # its release
        self.assertEqual(feed(b"\x1b[<64;10;5m"), "")    # wheel release
        self.assertEqual(feed(b"\x1b[<2;10;5M"), "")     # right press

    def test_a_click_is_dropped_even_when_the_caller_rebinds_arrows(self):
        self.assertEqual(feed(b"\x1b[<0;10;5M",
                              sequences={b"[C": "open"}), "")

    def test_the_tracking_switches_are_a_matched_pair(self):
        self.assertEqual(keys.MOUSE_ON, "\x1b[?1000h\x1b[?1006h")
        self.assertEqual(keys.MOUSE_OFF, "\x1b[?1000l\x1b[?1006l")


class ClosedStdinTest(unittest.TestCase):
    def test_an_empty_read_is_a_cancel(self):
        """A loop that kept going here would spin on an endless stream of
        empty reads rather than exiting."""
        r, w = os.pipe()
        os.close(w)
        try:
            self.assertEqual(keys.read_key(r), "cancel")
        finally:
            os.close(r)

    def test_it_is_a_cancel_however_the_read_comes_back_empty(self):
        with patch("os.read", side_effect=[b""]):
            self.assertEqual(keys.read_key(0), "cancel")


class LayeringTest(unittest.TestCase):
    def test_the_module_imports_nothing_from_the_project(self):
        """It sits at the bottom beside console and paths, so every widget
        can reach it without inventing a cycle."""
        from pathlib import Path

        source = Path("src/quiver/keys.py").read_text()
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                self.assertNotIn("quiver", stripped, line)


class WidgetCancelTest(unittest.TestCase):
    """A bare Esc cancels both checklist widgets, same as q and Ctrl-C.

    read_key says "escape" for a lone Esc (pending bytes distinguish a
    sequence), so a widget that only listens for "cancel" swallows it.
    """

    def _run_widget(self, widget, choice, first_key):
        import sys
        import io
        from contextlib import redirect_stdout
        from quiver import multiselect

        with patch.object(multiselect, "_supported", return_value=True), \
                patch.object(sys, "stdin", **{"fileno.return_value": 0}), \
                patch("termios.tcgetattr", return_value=[]), \
                patch("termios.tcsetattr"), \
                patch("tty.setraw"), \
                patch.object(keys, "read_key", side_effect=first_key), \
                redirect_stdout(io.StringIO()):
            return widget([choice])

    def test_escape_cancels_the_checklist(self):
        from quiver import multiselect
        result = self._run_widget(
            multiselect.multiselect,
            multiselect.Choice("a", "a"), ["escape"])
        self.assertIsNone(result)

    def test_escape_cancels_the_statepicker(self):
        from quiver import multiselect
        result = self._run_widget(
            multiselect.statepicker,
            multiselect.StateChoice("a", "a"), ["escape"])
        self.assertIsNone(result)

    def test_enter_still_confirms_the_checklist(self):
        from quiver import multiselect
        result = self._run_widget(
            multiselect.multiselect,
            multiselect.Choice("a", "a"), ["enter"])
        self.assertEqual(result, [])

    def test_no_choices_is_an_empty_selection_not_a_crash(self):
        """An empty list used to meet "% 0" on the first arrow key."""
        from quiver import multiselect

        self.assertEqual(multiselect.multiselect([]), [])
        self.assertEqual(multiselect.statepicker([]), [])


class RawTerminalTest(unittest.TestCase):
    """The shared setraw/restore wrapper every widget enters through."""

    def _pty(self):
        try:
            import pty
        except ImportError:
            self.skipTest("no pty module")
        return pty.openpty()

    def _assert_attrs_restored(self, fd, before):
        """lflag carries read-only status bits (PENDIN and friends) that
        tcgetattr reports but tcsetattr ignores, so the comparison is the
        flags setraw actually changes rather than a whole-list equality."""
        import termios

        after = termios.tcgetattr(fd)
        self.assertEqual(after[:3], before[:3])
        mask = (termios.ICANON | termios.ECHO | termios.ECHONL |
                termios.ISIG | termios.IEXTEN)
        self.assertEqual(after[3] & mask, before[3] & mask)
        self.assertEqual(after[4:], before[4:])

    def test_termios_is_restored_on_exit(self):
        import termios

        master, slave = self._pty()
        try:
            before = termios.tcgetattr(slave)
            with patch("sys.stdout", io.StringIO()):
                with keys.raw_terminal(slave):
                    self.assertFalse(
                        termios.tcgetattr(slave)[3] & termios.ICANON)
            self._assert_attrs_restored(slave, before)
        finally:
            os.close(master)
            os.close(slave)

    def test_cleanup_sequences_are_written_before_the_restore(self):
        master, slave = self._pty()
        try:
            out = io.StringIO()
            with patch("sys.stdout", out):
                with keys.raw_terminal(slave):
                    pass
            self.assertIn(keys.MOUSE_OFF + "\x1b[?25h", out.getvalue())
        finally:
            os.close(master)
            os.close(slave)

    def test_signal_handlers_come_off_with_the_context(self):
        import signal

        master, slave = self._pty()
        try:
            before = signal.getsignal(signal.SIGTERM)
            with patch("sys.stdout", io.StringIO()):
                with keys.raw_terminal(slave):
                    self.assertNotEqual(
                        signal.getsignal(signal.SIGTERM), before)
                self.assertEqual(signal.getsignal(signal.SIGTERM), before)
        finally:
            os.close(master)
            os.close(slave)

    def test_a_fatal_signal_still_hands_the_terminal_back(self):
        if not hasattr(os, "fork"):
            self.skipTest("no fork")
        import signal
        import termios

        master, slave = self._pty()
        try:
            before = termios.tcgetattr(slave)
            pid = os.fork()
            if pid == 0:
                try:
                    with keys.raw_terminal(slave):
                        os.kill(os.getpid(), signal.SIGTERM)
                finally:
                    os._exit(0)
                os._exit(0)
            _, status = os.waitpid(pid, 0)
            self.assertTrue(os.WIFSIGNALED(status))
            self.assertEqual(os.WTERMSIG(status), signal.SIGTERM)
            self._assert_attrs_restored(slave, before)
        finally:
            os.close(master)
            os.close(slave)

    def test_the_signal_handler_restores_and_reraises(self):
        """The fork test proves the real path; driving the installed
        handler directly covers its body in the parent process, where
        coverage can see it."""
        import signal

        master, slave = self._pty()
        saved = {s: signal.getsignal(s)
                 for s in (signal.SIGTERM, signal.SIGHUP)}
        try:
            with patch("sys.stdout", io.StringIO()):
                with keys.raw_terminal(slave):
                    handler = signal.getsignal(signal.SIGTERM)
                    with patch("os.isatty", return_value=True), \
                         patch("os.write") as wr, \
                         patch("os.kill") as kill:
                        handler(signal.SIGTERM, None)
                    kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
                    self.assertEqual(
                        wr.call_args[0][1],
                        (keys.MOUSE_OFF + "\x1b[?25h").encode())
                    # Not a tty, or the write itself failing: neither may
                    # stop the restore + reraise.
                    kill.reset_mock()
                    with patch("os.isatty", return_value=False), \
                         patch("os.kill") as kill:
                        handler(signal.SIGHUP, None)
                    with patch("os.isatty", return_value=True), \
                         patch("os.write", side_effect=OSError("gone")), \
                         patch("os.kill") as kill:
                        handler(signal.SIGHUP, None)
        finally:
            # Put back what was there, not defaults: the runner may own
            # these handlers.
            for s, h in saved.items():
                signal.signal(s, h)
            os.close(master)
            os.close(slave)

    def test_a_fatal_signal_delegates_to_the_previous_handler(self):
        """SIG_IGN stays ignored, a Python handler runs instead of a
        duplicate delivery, and only a real SIG_DFL re-raises fatally."""
        import signal

        master, slave = self._pty()
        ran = []
        saved = {s: signal.getsignal(s) for s in (
            signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGTERM)}
        try:
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            signal.signal(signal.SIGQUIT, lambda s, f: ran.append(s))
            with patch("sys.stdout", io.StringIO()):
                with keys.raw_terminal(slave):
                    # Ignored: no kill, and a failed handler-restore is
                    # still tolerated.
                    with patch("os.isatty", return_value=False), \
                         patch("os.kill") as kill, \
                         patch("signal.signal", side_effect=ValueError):
                        signal.getsignal(signal.SIGHUP)(signal.SIGHUP, None)
                    kill.assert_not_called()
                    # A Python-level handler is invoked, not re-delivered.
                    with patch("os.isatty", return_value=False), \
                         patch("os.kill") as kill:
                        signal.getsignal(signal.SIGQUIT)(signal.SIGQUIT, None)
                    self.assertEqual(ran, [signal.SIGQUIT])
                    kill.assert_not_called()
        finally:
            for s, h in saved.items():
                signal.signal(s, h)
            os.close(master)
            os.close(slave)

    def test_a_failed_restore_is_swallowed(self):
        """tcsetattr failing inside _restore must not mask the widget's
        own exception or escape the context."""
        import termios

        master, slave = self._pty()

        def boom(fd, when, attrs):
            raise termios.error(5, "boom")

        try:
            # tty.setraw keeps its own binding of tcsetattr, so the patch
            # only lands on _restore's call — which is the one under test.
            with patch("sys.stdout", io.StringIO()), \
                 patch("termios.tcsetattr", side_effect=boom):
                with keys.raw_terminal(slave):
                    pass
        finally:
            os.close(master)
            os.close(slave)

    def test_a_cleanup_write_failure_is_swallowed(self):
        master, slave = self._pty()
        out = mock.Mock()
        out.write.side_effect = OSError("dead stdout")
        try:
            with patch("sys.stdout", out):
                with keys.raw_terminal(slave):
                    pass
        finally:
            os.close(master)
            os.close(slave)

    def test_signal_install_and_restore_failures_are_tolerated(self):
        """Not the main thread, or a handler already gone: neither may
        break the widget."""
        import signal

        master, slave = self._pty()
        saved = {s: signal.getsignal(s)
                 for s in (signal.SIGTERM, signal.SIGINT, signal.SIGQUIT,
                           signal.SIGHUP)}
        real_signal = signal.signal
        term_calls = [0]

        def flaky(sig, handler):
            if sig == signal.SIGHUP:
                raise ValueError("not the main thread")
            if sig == signal.SIGTERM:
                term_calls[0] += 1
                if term_calls[0] == 2:
                    raise ValueError("handler gone")
            return real_signal(sig, handler)

        try:
            with patch("sys.stdout", io.StringIO()), \
                 patch("signal.signal", side_effect=flaky):
                with keys.raw_terminal(slave):
                    pass
        finally:
            # Restore whatever the runner had, never bare defaults.
            for s, h in saved.items():
                signal.signal(s, h)
            os.close(master)
            os.close(slave)


class WidgetsShareItTest(unittest.TestCase):
    """Every raw-mode reader in the tree is a wrapper over this one."""

    def test_the_three_widgets_delegate_rather_than_hand_rolling(self):
        from quiver.find import browser
        from quiver import multiselect
        from quiver.sessions import picker

        readers = [picker._read_key, multiselect._read_key,
                   multiselect._read_state_key, browser._read_key]
        for reader in readers:
            with patch.object(keys, "read_key", return_value="sentinel") as m:
                self.assertEqual(reader(0), "sentinel", reader)
            self.assertEqual(m.call_count, 1, reader)

    def test_a_wheel_notch_is_a_move_in_every_widget(self):
        from quiver.find import browser
        from quiver import multiselect
        from quiver.sessions import picker

        for reader in (picker._read_key, multiselect._read_key,
                       multiselect._read_state_key, browser._read_key):
            r, w = os.pipe()
            os.write(w, b"\x1b[<65;10;5M")
            os.close(w)
            try:
                self.assertEqual(reader(r), "down", reader)
            finally:
                os.close(r)


if __name__ == "__main__":
    unittest.main()
