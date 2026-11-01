"""The Harnesses page's rows: ``swe list --scope=all --json``, built in
process for the hub machine. Star, unstar, archive, restore, edit,
Detect's register and Add are the ``harness`` page plugin's writes
(``swe.webapp.pages.harness``)."""

from __future__ import annotations

from swe.remote.usage import WINDOW_DAYS


def rows() -> list[dict]:
    """Every registered harness, archived ones included, in registry
    order, with its 100-day session count and no limit reading (the
    Accounts page reads limits through ``/v1/usage``)."""
    from swe.harness.archive import load_archive
    from swe.harness.models import HarnessRow
    from swe.harness.registry import load_registry_readonly
    from swe.harness.stars import load_stars
    from swe.sessions.usage import session_counts

    archived = load_archive()
    starred = set(load_stars())
    counts = session_counts(WINDOW_DAYS)
    return [
        HarnessRow.of(name, info, starred, archived, counts, None).to_json()
        for name, info in load_registry_readonly().items()
    ]
