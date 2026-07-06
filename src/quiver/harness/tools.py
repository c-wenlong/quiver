"""Helpers for detecting installed CLI tools."""

import shutil
import subprocess


def is_installed(command: str) -> bool:
    return shutil.which(command) is not None


def live_version(command: str) -> str | None:
    for flag in ("--version", "-v", "version", "-V"):
        try:
            result = subprocess.run(
                [command, flag],
                capture_output=True,
                text=True,
                timeout=3,
            )
            out = (result.stdout + result.stderr).strip()
            if out:
                return out.splitlines()[0][:60]
        except Exception:
            pass
    return None
