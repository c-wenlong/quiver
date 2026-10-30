"""Page-side helpers over ``quiver.github.shared`` (the ``gh`` cache,
write lock and pins the Repositories, Inbox and Tasks pages share with
the hub's ``/v1`` routes): request flags, and pin errors as
``PageError``.

A leading underscore keeps this module off ``/v1/app/<page>``: the
plugin loader refuses private names.
"""

from __future__ import annotations

from pathlib import Path

from quiver.github import shared
from quiver.webapp.pages import PageError


def flag(args: dict, name: str) -> bool:
    return str(args.get(name) or "") in {"1", "true"}


def pinned_name(home: Path | None, raw: str) -> str:
    """``shared.pinned_name`` with its errors as a page's 400/404."""
    try:
        return shared.pinned_name(home, raw)
    except shared.PinError as exc:
        raise PageError(exc.status, exc.code, exc.message) from exc
