"""Debounced content polling for instruction sources and generated output."""

from collections.abc import Callable
import hashlib
import os
from pathlib import Path
import stat
import time

from quiver.paths import agents_file_for
from .compiler import (
    InstructionError, build, require_supported_platform, source_fingerprint,
)

POLL_INTERVAL = 0.5
SETTLE_INTERVAL = 0.5
RETRY_INTERVAL = 5.0


def _fingerprint(path: Path) -> bytes:
    """Hash the output without following aliases or opening special files."""
    digest = hashlib.sha256()
    digest.update(os.fsencode(str(path)))
    try:
        info = path.lstat()
        digest.update(str((info.st_mode, info.st_mtime_ns, info.st_ctime_ns,
                           info.st_ino, info.st_size)).encode())
        if stat.S_ISLNK(info.st_mode):
            digest.update(os.fsencode(os.readlink(path)))
        elif stat.S_ISREG(info.st_mode):
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as handle:
                # A regular file can become a FIFO between lstat and open.
                if stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    digest.update(handle.read())
    except OSError as exc:
        digest.update(str((type(exc).__name__, exc.errno)).encode())
    return digest.digest()


def watch(home: Path | None = None, *,
          on_result: Callable[[bool], None] | None = None,
          on_error: Callable[[InstructionError], None] | None = None) -> None:
    """Build immediately, then rebuild after changes settle until interrupted.

    Errors leave the last good output intact. A repeated identical error is
    reported once until a successful build resets it. Failed builds retry after
    a bounded delay even when no file changes. Callbacks own all output;
    KeyboardInterrupt and callback exceptions propagate to the caller.
    """
    require_supported_platform()
    output = agents_file_for(home)
    last_error: tuple[type[Exception], str] | None = None
    retry_at: float | None = None

    def attempt() -> tuple[bytes, bytes]:
        nonlocal last_error, retry_at
        source_state = source_fingerprint(home)
        try:
            changed = build(home)
        except InstructionError as exc:
            error = type(exc), str(exc)
            if error != last_error and on_error is not None:
                on_error(exc)
            last_error = error
            retry_at = time.monotonic() + RETRY_INTERVAL
        else:
            last_error = None
            retry_at = None
            if on_result is not None:
                on_result(changed)
        return source_state, _fingerprint(output)

    observed = attempt()
    pending: tuple[bytes, bytes] | None = None
    changed_at = 0.0
    while True:
        time.sleep(POLL_INTERVAL)
        current = source_fingerprint(home), _fingerprint(output)
        now = time.monotonic()
        if current == observed:
            pending = None
            if retry_at is not None and now >= retry_at:
                observed = attempt()
        elif current != pending:
            pending, changed_at = current, now
        elif now - changed_at >= SETTLE_INTERVAL:
            observed = attempt()
            pending = None
