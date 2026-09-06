"""One escape-sequence reader for every raw-mode widget in the tree.

A terminal in raw mode hands over bytes, not keys. An arrow is three of
them, a wheel notch is eleven, and both start with the same Esc the user
presses to back out. Every widget here used to guess at that on its own by
reading a fixed two bytes after the Esc and calling anything it did not
recognise a cancel, which is how a single scroll of the wheel closed the
picker and left the tail of the report to be re-read as loose letters.

So the parsing lives here once. A CSI ("[") or SS3 ("O") sequence ends at
its first byte in 0x40-0x7E, which is what lets a three-byte arrow and a
ten-byte wheel report come off the same wire without guessing a length.
Only a bare Esc, one with nothing queued behind it, reads as "escape".
Anything else the caller has no meaning for returns "", which every loop
ignores, because a terminal sends far more kinds of sequence than a widget
knows and none of them mean "quit".

Callers keep their own vocabulary. ``read_key`` takes a ``letters`` map for
plain bytes and a ``sequences`` map for post-Esc bytes; both are merged over
the defaults below with the caller winning, so the browser can bind right to
"open" while the picker leaves it alone.

This module imports nothing else from the project.
"""

from __future__ import annotations

import os
import select

# Normal button tracking plus SGR encoding. 1000 alone reports the wheel as
# buttons 64 and 65; 1006 is what keeps the column and row from being clamped
# at 223, and gives the report a final byte the sequence reader can stop on.
# A widget writes MOUSE_ON after tty.setraw and MOUSE_OFF in its finally,
# before termios comes back: a shell left reporting the wheel spews escape
# sequences at the prompt on every scroll.
MOUSE_ON = "\x1b[?1000h\x1b[?1006h"
MOUSE_OFF = "\x1b[?1000l\x1b[?1006l"

ESCAPE_TIMEOUT = 0.05     # long enough for a queued sequence, short for a human
SEQUENCE_CAP = 32         # a real sequence is a handful of bytes

# The bytes after the Esc, introducer included. Both spellings of every
# cursor key: DECCKM is on in plenty of terminals, and then an arrow arrives
# as Esc O A rather than Esc [ A.
SEQUENCES = {
    b"[A": "up", b"OA": "up",
    b"[B": "down", b"OB": "down",
    b"[C": "right", b"OC": "right",
    b"[D": "left", b"OD": "left",
    b"[5~": "pageup",
    b"[6~": "pagedown",
    b"[H": "top", b"OH": "top", b"[1~": "top",
    b"[F": "bottom", b"OF": "bottom", b"[4~": "bottom",
}

# Plain bytes every widget agrees on. Deliberately small: digits are left
# out because only the picker jumps to a row by number, and binding them
# here would swallow them everywhere else.
LETTERS = {
    b" ": "space",
    b"\r": "enter", b"\n": "enter",
    b"\x03": "cancel", b"q": "cancel",
    b"k": "up", b"j": "down",
}


def _read_sequence(fd: int) -> bytes:
    """The bytes of one escape sequence after the Esc, or b"" if it is not one.

    The cap stops a terminal that never sends a final byte from spinning
    here, and a short read (stdin closed mid-sequence) drops the sequence
    rather than returning half of one.
    """
    intro = os.read(fd, 1)
    if intro not in (b"[", b"O"):
        return b""                        # Esc plus a plain byte: an Alt chord
    seq = intro
    for _ in range(SEQUENCE_CAP):
        byte = os.read(fd, 1)
        if not byte:
            return b""
        seq += byte
        if 0x40 <= byte[0] <= 0x7E:
            return seq
    return b""


def _mouse_key(seq: bytes) -> str:
    """An SGR mouse report as a move: Esc [ < button ; col ; row M-or-m.

    Only the wheel moves anything. Buttons 64 and 65 are one notch up and
    down; a click, a drag and the release that follows every press are all
    dropped, so the mouse can never select or cancel by accident.
    """
    if seq[-1:] != b"M":                  # "m" is the release of a press
        return ""
    button = seq[2:-1].split(b";")[0]
    return {b"64": "up", b"65": "down"}.get(button, "")


def read_key(fd: int, letters: dict[bytes, str] | None = None,
             sequences: dict[bytes, str] | None = None) -> str:
    """One keypress from ``fd``, collapsed to a word the caller's loop knows.

    Returns "cancel" when stdin closes under us, so a loop cannot spin on an
    endless stream of empty reads. Returns "escape" only for a bare Esc, and
    "" for anything unrecognised.
    """
    ch = os.read(fd, 1)
    if not ch:
        return "cancel"                   # stdin closed under us
    if ch == b"\x1b":
        # Whatever follows an Esc is already queued, so nothing pending means
        # the user pressed Esc itself. Polling first is what keeps that lone
        # Esc from blocking in os.read until the next keypress.
        ready, _, _ = select.select([fd], [], [], ESCAPE_TIMEOUT)
        if not ready:
            return "escape"
        seq = _read_sequence(fd)
        if seq[:2] == b"[<":
            return _mouse_key(seq)
        if sequences and seq in sequences:
            return sequences[seq]
        return SEQUENCES.get(seq, "")
    if letters and ch in letters:
        return letters[ch]
    return LETTERS.get(ch, "")
