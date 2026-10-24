"""The Doctor page: ``GET /v1/app/doctor``, ``POST /v1/app/doctor/run`` and
``POST /v1/app/doctor/replace``.

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
"error", "blocked", "report"}``: the action, what went wrong (``null``
when nothing did), the paths a real file or folder still holds after a
setup or init (``blocked_paths``) and the report read again after it,
since even a failed run may have fixed something.

``replace`` is the Replace and back up button those blocked paths get:
``swe init --force``, which copies each one to ~/.quiver/backups and
links the shared copy in its place. It takes no body. The AGENTS.md
page runs the same two inits through ``run_init_now``.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
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
    if action == "replace":
        home = _home(ctx)
        error = run_init_now(home, force=True)
        return {"ran": "replace", "error": error, "blocked": _blocked_now(home), "report": _report(ctx)}
    if action != "run":
        raise PageError(404, "unknown_action", f"unknown doctor action: {action}")
    check, message = body.get("check"), body.get("message")
    for value in (check, message):
        if not isinstance(value, str) or not value or len(value) > TEXT_MAX:
            raise PageError(400, "bad_finding", "check and message must name a finding")
    with _one_at_a_time():
        report = _report(ctx)
        finding = next((f for f in report["findings"]
                        if f["check"] == check and f["message"] == message), None)
        if finding is None:
            raise PageError(404, "unknown_finding",
                            "That finding isn't in the report any more. Refresh to see the current one.")
        run = RUNNERS.get(finding.get("action") or "")
        if run is None:
            raise PageError(422, "no_action", "swe can't run this fix itself. Copy the command and run it on the hub.")
        home = _home(ctx)
        error = _guarded(run, home)
        # Setup and init link files, and leave a real one in the way alone.
        blocked = _blocked_now(home) if finding["action"] in ("setup", "init") else []
        return {"ran": finding["action"], "error": error, "blocked": blocked, "report": _report(ctx)}


@contextmanager
def _one_at_a_time():
    if not _RUN_LOCK.acquire(blocking=False):
        raise PageError(409, "busy", "Another fix is still running. Wait for it, then try again.")
    try:
        yield
    finally:
        _RUN_LOCK.release()


def _guarded(run, home: Path) -> str | None:
    try:
        # These rewrite harness.json too: wait for the Harnesses page's
        # writes, which hold the same lock.
        with REGISTRY_LOCK:
            return run(home)
    except Exception as exc:  # noqa: BLE001 - the row shows it, as the app does
        return str(exc) or type(exc).__name__


def run_init_now(home: Path, *, force: bool = False) -> str | None:
    """``swe init`` (``--force`` when ``force``) as a Doctor fix: one at a
    time with the others, 409 while one runs. Returns what went wrong."""
    with _one_at_a_time():
        return _guarded(lambda h: _run_init(h, force=force), home)


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


def _run_init(home: Path, force: bool = False) -> str | None:
    """A real file in the way is not an error: the reply's ``blocked``
    lists it, with Replace and back up. Only a forced run that still left
    one alone failed, since --force never touches a path that resolves
    outside the home or into ~/.quiver."""
    from quiver.init.commands import run_init

    blocked = blocked_paths(run_init(home, quiet=True, ask=False, force=force), home)
    if not (force and blocked):
        return None
    return ("Left alone, since each resolves outside the home folder or into ~/.quiver: "
            + ", ".join(blocked) + ".")


def blocked_paths(run, home: Path) -> list[str]:
    """The paths an init run left alone, or a check would, because a real
    file or folder is in the way: the ones ``swe init --force`` backs up
    and replaces. A ``keep`` folder (the only copy of some skills) counts."""
    rows = run.inst_rows + run.skill_rows + run.hook_rows
    return [_short(status.path, home) for status, result in rows
            if result in ("blocked", "protected", "would-conflict", "keep")]


def _blocked_now(home: Path) -> list[str]:
    from quiver.init.commands import run_init

    try:
        return blocked_paths(run_init(home, check_only=True, quiet=True), home)
    except Exception:  # noqa: BLE001 - an unreadable .linkignore: nothing to offer
        return []


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
