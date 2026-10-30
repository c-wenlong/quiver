"""Debounced content polling for instruction sources and generated output."""

from collections.abc import Callable
import hashlib
import os
from pathlib import Path
import stat
import time

from quiver.paths import agents_file_for, instructions_dir_for
from .compiler import InstructionError, build

POLL_INTERVAL = 0.5
SETTLE_INTERVAL = 0.5


def _fingerprint(path: Path, *, tree: bool = False) -> bytes:
    """Hash bytes as well as metadata, including broken or retargeted links."""
    digest = hashlib.sha256()

    def add(candidate: Path) -> None:
        digest.update(os.fsencode(str(candidate)))
        try:
            info = candidate.lstat()
            digest.update(str((info.st_mode, info.st_mtime_ns, info.st_ctime_ns,
                               info.st_ino, info.st_size)).encode())
            if stat.S_ISLNK(info.st_mode):
                digest.update(os.fsencode(os.readlink(candidate)))
            elif stat.S_ISREG(info.st_mode):
                digest.update(candidate.read_bytes())
        except OSError as exc:
            digest.update(str((type(exc).__name__, exc.errno)).encode())

    add(path)
    # A supported root symlink can point elsewhere inside the hub. Walk its
    # physical tree, without following any nested directory symlinks: their
    # accepted targets also live in this tree and are visited directly.
    try:
        root = path.resolve()
    except (OSError, RuntimeError) as exc:
        digest.update(str(exc).encode())
        return digest.digest()
    if tree and path.is_dir() and root.is_relative_to(path.parent.resolve()):
        for directory, folders, files in os.walk(root, followlinks=False):
            folders.sort()
            for name in sorted(folders + files):
                add(Path(directory) / name)
    return digest.digest()


def watch(home: Path | None = None, *,
          on_result: Callable[[bool], None] | None = None,
          on_error: Callable[[InstructionError], None] | None = None) -> None:
    """Build immediately, then rebuild after changes settle until interrupted.

    Errors leave the last good output intact. A repeated identical error is
    reported once until a successful build resets it. Callbacks own all output;
    KeyboardInterrupt and callback exceptions propagate to the caller.
    """
    directory, output = instructions_dir_for(home), agents_file_for(home)
    last_error: tuple[type[Exception], str] | None = None

    def attempt() -> tuple[bytes, bytes]:
        nonlocal last_error
        source_state = _fingerprint(directory, tree=True)
        try:
            changed = build(home)
        except InstructionError as exc:
            error = type(exc), str(exc)
            if error != last_error and on_error is not None:
                on_error(exc)
            last_error = error
        else:
            last_error = None
            if on_result is not None:
                on_result(changed)
        return source_state, _fingerprint(output)

    observed = attempt()
    pending: tuple[bytes, bytes] | None = None
    changed_at = 0.0
    while True:
        time.sleep(POLL_INTERVAL)
        current = _fingerprint(directory, tree=True), _fingerprint(output)
        if current == observed:
            pending = None
        elif current != pending:
            pending, changed_at = current, time.monotonic()
        elif time.monotonic() - changed_at >= SETTLE_INTERVAL:
            observed = attempt()
            pending = None
