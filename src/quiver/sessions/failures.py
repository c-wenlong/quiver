"""Record parser failures instead of swallowing them.

Session parsers catch broadly on purpose: one harness with a corrupt file
should not take down `swe session` for the other nineteen. The cost is that a
crashing parser looks exactly like a harness you have never used, which is how
a NameError in the cursor parser hid 84 sessions for as long as it did.

So failures are still caught, but recorded here and surfaced in the footer.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_failures: dict[str, str] = {}


def record(tool: str, exc: BaseException) -> None:
    """Note that ``tool``'s parser raised. Last error per tool wins."""
    with _lock:
        _failures[tool] = f"{type(exc).__name__}: {exc}"


def snapshot() -> dict[str, str]:
    """Failures recorded so far, tool -> message."""
    with _lock:
        return dict(_failures)


def clear() -> None:
    with _lock:
        _failures.clear()
