"""The Tasks page: the work items the hub's agents are on.

``GET /v1/app/tasks`` is ``GET /v1/tasks`` built in process
(``quiver.remote.tasks.list_view``): the task store overlaid on the
sources' open items, plus two joins the page's detail view would
otherwise read for itself — ``session_rows``, each linked session
key's catalog row trimmed to what a row draws (``gone`` when the
catalog no longer has it), and ``launch_rows``, the live launch view
for each linked launch the hub still tracks. ``?harnesses=1`` answers
what ``POST /v1/sessions`` can start on the hub, for the New agent
session panel — the same answer the Repositories page's PR sheet
reads.

Writes stay on the routes the phone already uses: link and unlink a
session through ``POST``/``DELETE /v1/sessions/{key}/task``, start an
agent through ``POST /v1/issues/resolve`` or
``POST /v1/pulls/<o>/<r>/<n>/open`` (both stamp the launch's
``origin``, so the new session links itself on adoption).
"""

from __future__ import annotations

from quiver.remote import tasks as tasks_mod
from quiver.webapp.pages import PageError
from quiver.webapp.pages import _github as G

# The catalog fields a session row in the Sessions card draws: the
# harness logo, the display title, the status badge, where it runs,
# and when it last moved. ``key`` is always joined on.
_SESSION_FIELDS = ("id", "harness", "agent", "title", "status", "cwd",
                   "machine", "machine_id", "remote", "ownership",
                   "attachable", "last_active_at", "trivial")


def get(ctx, args: dict) -> dict:
    if G.flag(args, "harnesses"):
        return {"harnesses": harnesses(),
                "machine": (ctx.machine or {}).get("name")}
    # The hub's query dict maps each parameter to a list; the page's
    # flat args keep only the first value of each.
    status, body = tasks_mod.list_view(
        ctx.state, {k: [v] for k, v in args.items()})
    if status != 200:
        error = body.get("error") if isinstance(body, dict) else None
        raise PageError(
            status, (error or {}).get("code") or "tasks_failed",
            (error or {}).get("message") or "The task list failed.")
    _join(ctx.state, body)
    return body


def _join(state, body: dict) -> None:
    """Fill each task row's ``session_rows`` and ``launch_rows`` in
    place, in the order the store links them."""
    from quiver.remote import catalog as catalog_mod

    catalog = {str(r.get("key")): r for r in state.catalog.rows()}
    # Satellite rows carry ``machine_id``, not the machine's name; the
    # same join ``/v1/app/sessions`` makes.
    machines = catalog_mod.load_machines(state.home)
    for task in body.get("tasks") or []:
        task["session_rows"] = [
            _session_row(catalog.get(key), key, machines)
            for key in task.get("sessions") or []]
        launches = []
        for lid in task.get("launches") or []:
            view = state.launch_view(lid)
            if view is None:
                # A launch the hub no longer tracks is old history;
                # the session link it left is what the row shows.
                continue
            launches.append({
                "id": lid, "state": view.get("state"),
                "harness": view.get("harness"),
                "note": view.get("note") or "",
                "key": view.get("key") or "",
                "cwd": view.get("cwd") or "",
                "machine": view.get("machine") or "",
            })
        task["launch_rows"] = launches


def _session_row(row: dict | None, key: str, machines: dict) -> dict:
    if row is None:
        return {"key": key, "gone": True}
    brief = {k: row[k] for k in _SESSION_FIELDS if k in row}
    brief["key"] = key
    if row.get("remote"):
        machine = machines.get(str(row.get("machine_id") or "")) or {}
        brief["machine"] = machine.get("name") or row.get("machine_id")
    return brief


def harnesses() -> list[dict]:
    """``{name, display_name, tracked, launch_model, launch_effort,
    last_model, recent_models}`` for each harness the hub can start, in Provider
    visibility order — the same answer ``/v1/app/repositories?harnesses=1``
    gives the PR sheet. ``recent_models`` is the harness's recently used
    models, newest first (at most 5); ``last_model`` is its head (""
    when none), the picker's default."""
    from quiver.remote import catalog as catalog_mod

    launchable = catalog_mod.launchable_harnesses()
    recent = catalog_mod.recent_models(
        None, [h["name"] for h in launchable])
    return [{"name": h["name"], "display_name": h["display_name"],
             "tracked": h["tracked"],
             "launch_model": h["launch_model"],
             "launch_effort": h["launch_effort"],
             "last_model": (recent.get(h["name"]) or [""])[0],
             "recent_models": recent.get(h["name"], [])}
            for h in launchable]
