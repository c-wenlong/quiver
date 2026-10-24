"""The Doctor page: ``GET /v1/app/doctor`` and ``POST /v1/app/doctor/run``.

A port of the Mac app's Settings › Doctor (``DoctorSection`` and
``DoctorModel``). ``get`` is the ``swe doctor --json`` report, built in
process by ``harness.commands.doctor_report``. ``run`` is the row's Run
button: it takes ``{"check", "message"}``, finds that finding in a fresh
report, and runs the fix the finding itself names in ``action``, through
the code its swe command runs:

- ``setup``: ``swe setup --apply --json`` (``setup.commands.apply_setup``),
  with no --adopt and no --starred.
- ``init``: ``swe init`` with nobody to ask (``init.commands.run_init``,
  ``ask=False``): new skills folders wait, real files in the way stay.
- ``discover``: ``swe harness discover --apply --json``: the
  high-confidence matches (``discover_harnesses`` + ``apply_findings``).

Nothing in the request says what to run: a finding that is not in the
report, or that has no action, is refused. One fix runs at a time, as in
the app; a second while one runs is a 409. The reply is ``{"ran",
"error", "report"}``: the action, what went wrong (``null`` when
nothing did) and the report read again after it, since even a failed run
may have fixed something.
"""

from __future__ import annotations

import threading
from pathlib import Path

from quiver.webapp.pages import PageError
from quiver.webapp.pages.harness import REGISTRY_LOCK

TEXT_MAX = 4000
# One repair at a time, like DoctorModel.run. Held without waiting: a
# second Run while one is in flight is refused, not queued.
_RUN_LOCK = threading.Lock()


def get(ctx, args) -> dict:
    return _report(ctx)


def post(ctx, action: str, body: dict) -> dict:
    if action != "run":
        raise PageError(404, "unknown_action", f"unknown doctor action: {action}")
    check, message = body.get("check"), body.get("message")
    for value in (check, message):
        if not isinstance(value, str) or not value or len(value) > TEXT_MAX:
            raise PageError(400, "bad_finding", "check and message must name a finding")
    if not _RUN_LOCK.acquire(blocking=False):
        raise PageError(409, "busy", "Another fix is still running. Wait for it, then try again.")
    try:
        report = _report(ctx)
        finding = next((f for f in report["findings"]
                        if f["check"] == check and f["message"] == message), None)
        if finding is None:
            raise PageError(404, "unknown_finding",
                            "That finding isn't in the report any more. Refresh to see the current one.")
        run = RUNNERS.get(finding.get("action") or "")
        if run is None:
            raise PageError(422, "no_action", "swe can't run this fix itself. Copy the command and run it on the hub.")
        try:
            # These rewrite harness.json too: wait for the Harnesses page's
            # writes, which hold the same lock.
            with REGISTRY_LOCK:
                error = run(_home(ctx))
        except Exception as exc:  # noqa: BLE001 - the row shows it, as the app does
            error = str(exc) or type(exc).__name__
        return {"ran": finding["action"], "error": error, "report": _report(ctx)}
    finally:
        _RUN_LOCK.release()


def _home(ctx) -> Path:
    return (ctx.home if ctx is not None and ctx.home else None) or Path.home()


def _report(ctx) -> dict:
    from quiver.harness.commands import doctor_report

    report = doctor_report(_home(ctx)).to_json()
    # The machine the checks ran on, for the confirm line ("on <machine>").
    report["machine"] = ((ctx.machine or {}).get("name") if ctx is not None else None)
    return report


# ------------------------------------------------------------------ fixes
# Each returns what went wrong, for the row, or None.

def _run_setup(home: Path) -> str | None:
    from quiver.setup.commands import apply_setup

    applied = apply_setup(home)
    # The app shows a linking pass that stopped (links.error); a step that
    # failed outright (errors) is shown too rather than dropped.
    return applied.get("links", {}).get("error") or "; ".join(applied.get("errors") or []) or None


def _run_init(home: Path) -> str | None:
    from quiver.init.commands import run_init

    run = run_init(home, quiet=True, ask=False)
    rows = run.inst_rows + run.skill_rows + run.hook_rows
    blocked = [_short(status.path, home) for status, result in rows
               if result in ("blocked", "would-conflict")]
    if not blocked:
        return None
    # swe init's own words for the paths it left alone (it exits 1 then).
    return ("Blocked, a real file or directory is in the way: " + ", ".join(blocked)
            + ". Re-run with --force to back up and replace the blocked paths.")


def _short(path: Path, home: Path) -> str:
    try:
        return "~/" + str(Path(path).relative_to(home))
    except ValueError:
        return str(path)


def _run_discover(home: Path) -> str | None:
    from quiver.harness.discover import apply_findings, discover_harnesses

    apply_findings(discover_harnesses(), min_confidence="high")
    return None


RUNNERS = {"setup": _run_setup, "init": _run_init, "discover": _run_discover}
