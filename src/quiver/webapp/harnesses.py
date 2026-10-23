"""The Harnesses page's rows: ``swe list --scope=all --json``, built in
process for the hub machine. Read only; star, archive and edit stay in
``swe`` and the Mac app for now."""

from __future__ import annotations

from quiver.remote.usage import WINDOW_DAYS


def rows() -> list[dict]:
    """Every registered harness, archived ones included, in registry
    order, with its 100-day session count and no limit reading (the
    Accounts page reads limits through ``/v1/usage``)."""
    from quiver.harness.archive import load_archive
    from quiver.harness.models import HarnessRow
    from quiver.harness.registry import load_registry_readonly
    from quiver.harness.stars import load_stars
    from quiver.sessions.usage import session_counts

    archived = load_archive()
    starred = set(load_stars())
    counts = session_counts(WINDOW_DAYS)
    return [
        HarnessRow.of(name, info, starred, archived, counts, None).to_json()
        for name, info in load_registry_readonly().items()
    ]
