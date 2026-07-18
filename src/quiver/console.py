"""Terminal output helpers (ANSI colors, padding, truncation)."""

import re

COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "blue": "\033[34m",
    # Neon accents for favourited harnesses (xterm-256)
    "neon": "\033[38;5;51m",
    "neon_pink": "\033[38;5;201m",
    "neon_green": "\033[38;5;118m",
}


def c(color: str, text: str) -> str:
    return f"{COLORS[color]}{text}{COLORS['reset']}"


def truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 3] + "..."


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def visible_len(text: str) -> int:
    return len(strip_ansi(text))


def lpad(text: str, width: int) -> str:
    return strip_ansi(text) + " " * (width - visible_len(text))


def rpad(text: str, width: int) -> str:
    return " " * (width - visible_len(text)) + strip_ansi(text)


def cpad(color: str, text: str, width: int) -> str:
    plain = strip_ansi(text)
    return c(color, plain + " " * (width - len(plain)))
