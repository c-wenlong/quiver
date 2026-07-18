"""Helpers for detecting installed CLI tools."""

import re
import shutil
import subprocess

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_ERRORISH = re.compile(
    r"(?i)\b(error|unknown flag|unknown shorthand|usage:|fatal|command not found|"
    r"not found at|failed to|traceback)\b"
)
# Core version token: 1.2 / 1.2.3 / 0.0.1777-gd887d5 / 2026.06.24-00-45-58-9f61de7
_VERSION_TOKEN_RE = re.compile(
    r"""
    (?<![A-Za-z0-9])          # left boundary
    v?                        # optional leading v (stripped later)
    (
      \d+
      (?:\.\d+)+              # require at least one dotted segment
      (?:[-._+][A-Za-z0-9]+)* # pre-release / build / commit suffix
    )
    (?![A-Za-z])              # don't eat trailing letters of words
    """,
    re.VERBOSE,
)


def is_installed(command: str) -> bool:
    return shutil.which(command) is not None


def extract_version_number(text: str) -> str | None:
    """Pull a bare version number out of a CLI version banner.

    Examples:
      "codex-cli 0.144.1"              -> "0.144.1"
      "forge 2.12.10"                  -> "2.12.10"
      "crush version v0.62.0"          -> "0.62.0"
      "2.1.126 (Claude Code)"          -> "2.1.126"
      "GitHub Copilot CLI 1.0.70."     -> "1.0.70"
      "Hermes Agent v0.12.0 (2026.4.30)" -> "0.12.0"
      "kimi, version 1.39.0"           -> "1.39.0"
    """
    if not text:
        return None
    cleaned = _ANSI_RE.sub("", text).strip()
    if not cleaned or _ERRORISH.search(cleaned):
        return None
    match = _VERSION_TOKEN_RE.search(cleaned)
    if not match:
        return None
    return match.group(1)[:60]


def live_version(command: str) -> str | None:
    """Probe a CLI for a bare version number (no harness/tool name prefix)."""
    candidates = ("version", "--version", "-v", "-V")
    for flag in candidates:
        try:
            result = subprocess.run(
                [command, flag],
                capture_output=True,
                text=True,
                timeout=3,
                stdin=subprocess.DEVNULL,  # avoid CLIs that block on piped/empty stdin
            )
        except Exception:
            continue

        streams = [result.stdout or ""]
        if result.returncode == 0:
            streams.append(result.stderr or "")

        for stream in streams:
            for line in stream.splitlines():
                version = extract_version_number(line)
                if version:
                    return version
    return None
