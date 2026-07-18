"""Interactive prompt helpers that tolerate broken TTY line discipline.

Some host shells leave stdin without ICRNL/ICANON, so Enter arrives as ``\\r``
(``^M``) and Python's ``input()`` never sees a newline. These helpers restore
sane cooked mode when possible and accept CR or LF as line terminators.
"""

from __future__ import annotations

import sys

# 1-byte pushback for TTY reader (avoids dropping non-LF after CR)
_pushback: list[int] = []


def _restore_cooked_tty(fd: int) -> None:
    """Best-effort: ensure Enter maps to newline and line buffering is on."""
    try:
        import termios
    except ImportError:
        return
    try:
        attrs = termios.tcgetattr(fd)
    except Exception:
        return
    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
    # Map CR -> NL on input; enable canonical mode + echo
    iflag |= termios.ICRNL
    lflag |= termios.ICANON | termios.ECHO
    try:
        termios.tcsetattr(
            fd,
            termios.TCSANOW,
            [iflag, oflag, cflag, lflag, ispeed, ospeed, cc],
        )
    except Exception:
        pass


def _tty_echo_on(fd: int) -> bool:
    try:
        import termios

        return bool(termios.tcgetattr(fd)[3] & termios.ECHO)
    except Exception:
        return False


def read_line(prompt: str = "") -> str:
    """Read one line from stdin, accepting CR or LF as terminator.

    Raises EOFError on EOF (same as ``input()``).
    """
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()

    if not sys.stdin.isatty():
        return _read_line_stream(sys.stdin)

    fd = sys.stdin.fileno()
    _restore_cooked_tty(fd)

    try:
        return _read_line_bytes(fd)
    except EOFError:
        raise
    except Exception:
        return _read_line_stream(sys.stdin)


def _read_line_stream(stream) -> str:
    """Read one line from a text stream, treating CR or LF as end-of-line."""
    buf: list[str] = []
    while True:
        ch = stream.read(1)
        if ch == "":
            if not buf:
                raise EOFError
            break
        if ch in ("\n", "\r"):
            if ch == "\r":
                # swallow optional LF of CRLF without blocking forever on pipes
                try:
                    if hasattr(stream, "peek"):
                        nxt = stream.peek(1)
                        if isinstance(nxt, (bytes, bytearray)):
                            nxt = nxt[:1].decode(stream.encoding or "utf-8", "replace")
                        if nxt.startswith("\n"):
                            stream.read(1)
                    elif hasattr(stream, "tell") and hasattr(stream, "seek"):
                        pos = stream.tell()
                        nxt = stream.read(1)
                        if nxt != "\n":
                            stream.seek(pos)
                except Exception:
                    pass
            break
        buf.append(ch)
    return "".join(buf)


def _read_byte(fd: int) -> bytes:
    """Read one byte, honoring pushback."""
    import os

    if _pushback:
        return bytes([_pushback.pop()])
    try:
        return os.read(fd, 1)
    except InterruptedError:
        return _read_byte(fd)


def _unread_byte(b: int) -> None:
    _pushback.append(b)


def _read_line_bytes(fd: int) -> str:
    """Read until CR, LF, or EOF.

    - Accepts CR, LF, or CRLF as Enter.
    - Non-LF byte after CR is pushed back (not dropped).
    - Avoids double-newline when the TTY already echoed the terminator.
    """
    buf = bytearray()
    saw_cr = False
    saw_lf = False

    while True:
        chunk = _read_byte(fd)
        if not chunk:
            if not buf:
                raise EOFError
            break
        b = chunk[0]
        if b in (10, 13):  # \n or \r
            if b == 13:
                saw_cr = True
                # Swallow optional paired \n of CRLF only; push back anything else
                try:
                    import select

                    if select.select([fd], [], [], 0)[0] or _pushback:
                        nxt = _read_byte(fd)
                        if nxt == b"\n":
                            saw_lf = True
                        elif nxt:
                            _unread_byte(nxt[0])
                except Exception:
                    pass
            else:
                saw_lf = True
            break
        if b == 127 or b == 8:  # backspace / DEL
            if buf:
                buf.pop()
                if not _tty_echo_on(fd):
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
            continue
        if b == 3:  # Ctrl-C
            raise KeyboardInterrupt
        if b == 4:  # Ctrl-D
            if not buf:
                raise EOFError
            break
        # Skip other control chars
        if b < 32 and b not in (9,):  # allow tab
            continue
        buf.append(b)
        # Echo ourselves only when the TTY is not already echoing
        if not _tty_echo_on(fd):
            sys.stdout.write(chr(b) if b < 128 else "?")
            sys.stdout.flush()

    # TTY with ECHO already printed the line terminator for LF (and usually CR
    # after ICRNL). Only force a newline for CR-only paths that did not echo.
    if not (saw_lf or (saw_cr and _tty_echo_on(fd))):
        sys.stdout.write("\n")
        sys.stdout.flush()
    elif not saw_lf and saw_cr and _tty_echo_on(fd):
        # Some TTYs echo CR as cursor-to-col0 without advancing row
        sys.stdout.write("\n")
        sys.stdout.flush()

    return buf.decode(sys.stdin.encoding or "utf-8", errors="replace")
