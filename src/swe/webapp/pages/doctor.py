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
- ``install``: ``swe install <name> --via=<via>`` with its output captured
  (``harness.commands.install_harness``). ``swe doctor`` gives an
  off-PATH harness this action when swe knows an installer for it,
  reinstalling the way the stray copy was installed
  (``harness.doctor.install_method``), so it lands on PATH.

Nothing in the request says what to run: a finding that is not in the
report, or that has no action, is refused. One fix runs at a time, as in
the app; a second while one runs is a 409. The reply is ``{"ran",
"error", "blocked", "report"}``: the action, what went wrong (``null``
when nothing did), the paths a real file or folder still holds after a
setup or init (``blocked_paths``) and the report read again after it,
since even a failed run may have fixed something.

``replace`` is the Replace and back up button those blocked paths get:
``swe init --force``, which copies each one to ~/.swe/backups and
links the shared copy in its place. It takes no body. The AGENTS.md
page runs the same two inits through ``run_init_now``.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from functools import partial
from pathlib import Path

from swe.harness import doctor_fix
from swe.webapp.pages import PageError, home_path, tilde
from swe.webapp.pages.harness import REGISTRY_LOCK

TEXT_MAX = 4000
# One repair at a time, like DoctorModel.run. Held without waiting: a
# second Run while one is in flight is refused, not queued.
_RUN_LOCK = threading.Lock()


def get(ctx, args) -> dict:
    return _report(ctx)


def post(ctx, action: str, body: dict) -> dict:
    if action == "replace":
        home = home_path(ctx)
        error = run_init_now(home, force=True)
        return {"ran": "replace", "error": error, "blocked": blocked_now(home), "report": _report(ctx)}
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
        # A remedy names its own action; the finding's top-level action
        # stays the one older requests mean. An ``action`` that matches
        # no remedy is refused rather than run: the report changed under
        # the click, and the finding's own action may be a different fix.
        wanted = body.get("action")
        remedy = next((r for r in finding.get("remedies") or []
                       if r.get("kind") == "action"
                       and r.get("action") == wanted), None)
        if wanted is not None and remedy is None:
            raise PageError(404, "unknown_remedy",
                            "That fix isn't in the report any more. Refresh to see the current one.")
        action_name = remedy["action"] if remedy else finding.get("action")
        run = RUNNERS.get(action_name or "")
        if run is None:
            raise PageError(422, "no_action", "swe can't run this fix itself. Copy the command and run it on the hub.")
        if action_name == "install":
            run = partial(run, name=finding["harness"],
                          via=(remedy or finding).get("via"))
        elif action_name == "undeclare":
            settings = remedy.get("settings")
            targets = remedy.get("targets")
            if (not isinstance(settings, list) or not settings
                    or not isinstance(targets, list) or not targets):
                raise PageError(404, "unknown_remedy",
                                "That fix isn't in the report any more. Refresh to see the current one.")
            run = partial(run, name=finding["harness"],
                          targets=targets, settings=settings)
        elif action_name in ("plugin-fix", "plugin-reinstall", "dangling-link", "mcp-sync"):
            # A Run without an action names the finding's own: its remedy
            # carries the targets (the drift id, the link, the server).
            run = partial(run, finding=finding,
                          remedy=remedy or doctor_fix.resolve(finding)[1])
        elif action_name == "marketplace-drop":
            in_file = remedy.get("settings_entries") or []
            cli = remedy.get("cli_entries") or []
            if not in_file and not cli:
                raise PageError(404, "unknown_remedy",
                                "That fix isn't in the report any more. Refresh to see the current one.")
            run = partial(run, in_file=in_file, cli=cli)
        home = home_path(ctx)
        error = _guarded(run, home)
        # Setup and init link files, and leave a real one in the way alone.
        blocked = blocked_now(home) if action_name in ("setup", "init") else []
        return {"ran": action_name, "error": error, "blocked": blocked, "report": _report(ctx)}


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


def install_now(home: Path, name: str, via: str) -> str | None:
    """``swe install <name> --via=<via>`` as a Doctor fix, one at a time
    with the others. Returns what went wrong."""
    with _one_at_a_time():
        return _guarded(partial(_run_install, name=name, via=via), home)


def run_init_now(home: Path, *, force: bool = False) -> str | None:
    """``swe init`` (``--force`` when ``force``) as a Doctor fix: one at a
    time with the others, 409 while one runs. Returns what went wrong."""
    with _one_at_a_time():
        return _guarded(lambda h: _run_init(h, force=force), home)


def _report(ctx) -> dict:
    from swe.harness.commands import doctor_report
    from swe.webapp.pages.repositories import harnesses

    home = home_path(ctx)
    # swe doctor names an off-PATH harness's install method itself
    # (action "install", "via"), for the Mac app's Run as well as this one.
    report = doctor_report(home).to_json()
    # The machine the checks ran on, for the confirm line ("on <machine>").
    report["machine"] = ((ctx.machine or {}).get("name") if ctx is not None else None)
    # What a "Fix with agent" remedy needs to launch: the findings are
    # about this home, and the picker lists what the hub can start.
    report["home"] = str(home)
    report["launchable"] = harnesses(home)
    return report


# ------------------------------------------------------------------ fixes
# Each returns what went wrong, for the row, or None.

def _run_setup(home: Path) -> str | None:
    return doctor_fix.RUNNERS["setup"](home, {}, None)


def _run_init(home: Path, force: bool = False) -> str | None:
    """A real file in the way is not an error: the reply's ``blocked``
    lists it, with Replace and back up. Only a forced run that still left
    one alone failed, since --force never touches a path that resolves
    outside the home or into ~/.swe."""
    from swe.init.commands import run_init

    blocked = blocked_paths(run_init(home, quiet=True, ask=False, force=force), home)
    if not (force and blocked):
        return None
    return ("Left alone, since each resolves outside the home folder or into ~/.swe: "
            + ", ".join(blocked) + ".")


def blocked_paths(run, home: Path) -> list[str]:
    """The paths an init run left alone, or a check would, because a real
    file or folder is in the way: the ones ``swe init --force`` backs up
    and replaces. A ``keep`` folder (the only copy of some skills) counts."""
    rows = run.inst_rows + run.skill_rows + run.hook_rows
    return [_short(status.path, home) for status, result in rows
            if result in ("blocked", "protected", "would-conflict", "keep")]


def blocked_now(home: Path) -> list[str]:
    """The paths ``swe init --force`` would replace, as the Doctor,
    Hooks and AGENTS.md pages list them; a check read that fails offers
    nothing."""
    from swe.init.commands import run_init

    try:
        return blocked_paths(run_init(home, check_only=True, quiet=True), home)
    except Exception:  # noqa: BLE001 - an unreadable .linkignore: nothing to offer
        return []


def _short(path: Path, home: Path) -> str:
    return tilde(path, home)


def _run_discover(home: Path) -> str | None:
    return doctor_fix.RUNNERS["discover"](home, {}, None)


def _run_install(home: Path, name: str, via: str) -> str | None:
    return doctor_fix.RUNNERS["install"](home, {"harness": name, "via": via}, None)


def _run_undeclare(
    home: Path, name: str, targets: list, settings: list
) -> str | None:
    """Drop the hook entries invoking a ``targets`` token, per file, behind
    the Plugins page's write lock (both rewrite the settings file)."""
    return _with_plugin_lock(lambda: doctor_fix.RUNNERS["undeclare"](
        home, {"harness": name}, {"targets": targets, "settings": settings}))


def _run_marketplace_drop(home: Path, in_file: list, cli: list) -> str | None:
    return _with_plugin_lock(lambda: doctor_fix.RUNNERS["marketplace-drop"](
        home, {}, {"settings_entries": in_file, "cli_entries": cli}))


def _run_plugin_fix(home: Path, finding: dict, remedy: dict | None) -> str | None:
    """A plugin drift fix runs the harness CLI, which rewrites the files
    the Plugins page writes: one at a time with it."""
    from swe.webapp.pages import plugins

    try:
        return _with_plugin_lock(lambda: doctor_fix.RUNNERS["plugin-fix"](home, finding, remedy))
    finally:
        # Even a failed fix may have changed something: the Plugins page
        # reads afresh next time rather than from its minute-long cache.
        plugins.clear_cache()


def _run_dangling_link(home: Path, finding: dict, remedy: dict | None) -> str | None:
    return doctor_fix.RUNNERS["dangling-link"](home, finding, remedy)


def _run_mcp_sync(home: Path, finding: dict, remedy: dict | None) -> str | None:
    """Behind the MCP page's lock (``CONFIG_LOCK``, the Plugins page's
    too): both rewrite the harness MCP configs, and two read-modify-write
    passes at once would drop one's change."""
    return _with_plugin_lock(lambda: doctor_fix.RUNNERS["mcp-sync"](home, finding, remedy))


def _with_plugin_lock(run) -> str | None:
    from swe.webapp.pages import plugins

    if not plugins._WRITE_LOCK.acquire(blocking=False):
        return "A plugin or MCP change is still running. Wait for it, then try again."
    try:
        return run()
    finally:
        plugins._WRITE_LOCK.release()


# The one implementation lives with the CLI's runners.
install_problem = doctor_fix.install_problem


RUNNERS = {"setup": _run_setup, "init": _run_init, "discover": _run_discover,
           "install": _run_install, "undeclare": _run_undeclare,
           "marketplace-drop": _run_marketplace_drop,
           "plugin-fix": _run_plugin_fix, "plugin-reinstall": _run_plugin_fix,
           "dangling-link": _run_dangling_link, "mcp-sync": _run_mcp_sync}
